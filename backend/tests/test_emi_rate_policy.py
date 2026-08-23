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


# ===========================================================================
# THE CHANNEL THE POS SCREEN READS, and who may read it.
# ===========================================================================
# Round 1 of PR #997 fetched the rate via GET /settings/policies/{key} -- a
# route closed to SALES_CASHIER and SALES_STAFF. Their 403 died in the
# screen's silent catch, so every cashier quoted the 12% fallback while the
# order charged the configured rate: the exact quote-vs-charge defect the PR
# exists to close, surviving for the roles that do most of the billing. The
# rate now rides on GET /stores/{store_id}, which is AUTHENTICATED and
# store-scoped. These tests pin (a) that access, by role name, and (b) that
# the store read and the order engine share ONE resolver, so they cannot
# drift.

from api.routers import stores as stores_module  # noqa: E402
from api.services.rbac_policy import check_access  # noqa: E402


@pytest.mark.parametrize(
    "role", ["SALES_CASHIER", "SALES_STAFF", "CASHIER", "STORE_MANAGER", "ADMIN"]
)
def test_every_billing_role_may_read_the_store_detail_rate_channel(role):
    """THE REQUIREMENT. If this route ever narrows, the POS screen silently
    falls back to 12% for the excluded role while the order charges the
    configured rate -- and no frontend test can see it, because they mock the
    API below the RBAC layer. This is the backend half of that proof."""
    assert check_access("GET", "/api/v1/stores/S1", [role]) is True, (
        f"{role} lost the store-detail read -- their POS EMI quote is now the "
        "fallback while the order charges the configured rate"
    )


def test_the_policies_route_stays_closed_to_cashiers_the_other_side():
    """The deliberate least-privilege choice, pinned from the other side: we
    did NOT widen the policy table to cashiers to deliver one number."""
    assert check_access(
        "GET", "/api/v1/settings/policies/pos.emi_annual_rate_percent", ["SALES_CASHIER"]
    ) is False


def test_store_detail_and_order_engine_share_one_resolver_by_identity():
    """The twin tripwire. Two copies of 'what is the EMI rate' is how the
    screen and the charge drift apart -- the nine-times-bitten defect shape.
    Function IDENTITY, not behavioural equality: a re-inlined copy with the
    same behaviour today still drifts tomorrow."""
    assert orders_module._emi_annual_rate is policy_registry.resolve_emi_annual_rate
    assert stores_module.resolve_emi_annual_rate is policy_registry.resolve_emi_annual_rate


def test_store_detail_carries_the_resolved_rate(monkeypatch):
    """Drive the real get_store handler: the response must carry the resolved
    planted rate -- that field IS the screen's data source."""
    import asyncio

    class _Repo:
        def find_by_id(self, sid):
            return {"store_id": sid, "name": "Bokaro"}

    monkeypatch.setattr(stores_module, "get_store_repository", lambda: _Repo())
    monkeypatch.setattr(
        stores_module, "resolve_emi_annual_rate", lambda sid: 14.5
    )
    body = asyncio.run(
        stores_module.get_store(
            "S1",
            current_user={"user_id": "u1", "roles": ["SALES_CASHIER"],
                          "active_store_id": "S1", "store_ids": ["S1"]},
        )
    )
    assert body["emi_annual_rate_percent"] == 14.5
