"""
POS EMI rate policy (owner ruling 2026-08-21: "wire the screen to the real
setting").

The order add-payment endpoint resolves the EMI annual rate through
orders._emi_annual_rate -> policy `pos.emi_annual_rate_percent`
(store > entity > global > registry default 12.0). The POS payment screen
reads the SAME policy key over GET /settings/policies/{key}, so these tests
pin BOTH ends of the contract:

  1. a planted non-default rate (14.5) resolves through the real policy
     engine into the value the schedule is built from;
  2. nothing planted -> the registry default 12.0 (the frontend fallback
     constant EMI_ANNUAL_RATE_PERCENT_FALLBACK in POSPayment.tsx mirrors it);
  3. the golden instalment figure the frontend deciding test asserts
     (25000 @ 14.5% / 12m -> 2250.56) is exactly what build_emi_schedule
     produces, so the cross-language anchor cannot silently drift.

No emoji in this file (Windows cp1252).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.routers import orders as orders_module  # noqa: E402
from api.services import policy_engine  # noqa: E402
from api.services import policy_registry  # noqa: E402


@pytest.fixture()
def planted_rate(monkeypatch):
    """Plant a stored override (14.5) at every scope doc so the REAL
    resolution path (_chain -> _nested_get -> _coerce) runs; only the Mongo
    read is stubbed."""
    monkeypatch.setattr(
        policy_engine,
        "_scope_doc_values",
        lambda addr: {"pos": {"emi_annual_rate_percent": 14.5}},
    )


@pytest.fixture()
def no_override(monkeypatch):
    monkeypatch.setattr(policy_engine, "_scope_doc_values", lambda addr: {})
    monkeypatch.delenv("POS_EMI_ANNUAL_RATE_PERCENT", raising=False)


class TestEmiRatePolicyKey:
    def test_registry_carries_the_key_with_default_12(self):
        spec = policy_registry.REGISTRY.get("pos.emi_annual_rate_percent")
        assert spec is not None, "pos.emi_annual_rate_percent must be registered"
        assert spec.default == 12.0
        assert spec.type == "percent"
        assert "store" in spec.scopes  # per-store rates must be possible


class TestEmiAnnualRateResolution:
    def test_planted_non_default_rate_is_what_the_order_uses(self, planted_rate):
        """REQUIREMENT: the rate the backend applies is the configured policy
        value, not a hardcoded 12."""
        assert orders_module._emi_annual_rate("BV-BOK-01") == 14.5

    def test_unset_policy_resolves_to_registry_default(self, no_override):
        assert orders_module._emi_annual_rate("BV-BOK-01") == 12.0

    def test_no_store_id_still_resolves(self, planted_rate):
        # A token without active_store_id must not crash the payment path.
        assert orders_module._emi_annual_rate(None) == 14.5

    def test_policy_engine_failure_falls_back_to_default(self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("policy engine down")

        monkeypatch.setattr(policy_engine, "get_policy", _boom)
        assert orders_module._emi_annual_rate("BV-BOK-01") == 12.0


class TestFrontendGoldenAnchor:
    def test_golden_instalment_matches_frontend_deciding_test(self):
        """The frontend test POSEmiRate.test.tsx asserts the SCREEN shows
        2250.56 for a 25000 loan @ 14.5% over 12 months. Pin the backend
        figure so the two ends cannot drift apart silently."""
        s = orders_module.build_emi_schedule(25000.0, 14.5, 12)
        assert s["monthly_emi"] == 2250.56

    def test_golden_default_control(self):
        s = orders_module.build_emi_schedule(25000.0, 12.0, 12)
        assert s["monthly_emi"] == 2221.22
