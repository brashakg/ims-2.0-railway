"""
Audit L1-F34: the integration status screen must report Shopify from Shopify's
OWN gate and credential resolver, not from the messaging gate and a hand-kept
copy of one credential path.

Reproduction (audit lane1/integration-status-gate-probe.py):
  global DISPATCH_MODE off  + SHOPIFY_DISPATCH_MODE=live -> reported
      simulated / armed=false while Shopify writes were live;
  global DISPATCH_MODE live + SHOPIFY_DISPATCH_MODE=off  -> reported
      live / armed=true while Shopify writes were off.
Source-confirmed alongside it: Shopify configured only when a static token
sat in the integrations collection (OAuth client credentials ignored), and
Anthropic reported as environment-only although its key is read DB-first.

Process-local synthetic config only. No Shopify call is made: the status
screen must never mint a token (asserted below).

Run: JWT_SECRET_KEY=test python -m pytest \
       backend/tests/test_audit_b04_integration_status.py -q
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402

from agents import nexus_providers  # noqa: E402
from agents import providers as _providers  # noqa: E402
from api.services import shopify_auth  # noqa: E402
from api.services.integration_status import build_integration_status  # noqa: E402

_SHOPIFY_ENV = (
    "SHOPIFY_DISPATCH_MODE", "IMS_SHOPIFY_WRITES", "SHOPIFY_CLIENT_ID",
    "SHOPIFY_CLIENT_SECRET", "SHOPIFY_STORE_URL", "SHOPIFY_ACCESS_TOKEN",
    "SHOPIFY_ADMIN_TOKEN",
)


class _Coll:
    def __init__(self, docs):
        self._docs = docs

    def find_one(self, query):
        doc = self._docs.get(query.get("type"))
        if not doc:
            return None
        if "enabled" in query and bool(doc.get("enabled")) != query["enabled"]:
            return None
        return dict(doc)


class _DB:
    def __init__(self, docs=None):
        self._docs = docs or {}

    def get_collection(self, name):
        return _Coll(self._docs)


_VAULT = {"shopify": {"type": "shopify", "enabled": True,
                      "config": {"shop_url": "audit.invalid", "access_token": "SYNTHETIC"}}}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in _SHOPIFY_ENV + ("ANTHROPIC_API_KEY",):
        monkeypatch.delenv(key, raising=False)
    shopify_auth.clear_cached_tokens()

    def no_network(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("the status screen must not mint a Shopify token")

    monkeypatch.setattr(shopify_auth, "_mint_oauth_token", no_network)


def _global_mode(monkeypatch, mode):
    monkeypatch.setenv("DISPATCH_MODE", mode)
    monkeypatch.setattr(_providers, "DISPATCH_MODE", mode)


def _row(report, integration_id):
    return next(i for i in report["integrations"] if i["id"] == integration_id)


# ---------------------------------------------------------------------------
# The gate (the audit probe's two cases, plus the unset-override default)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "global_mode,override,state,armed",
    [
        ("off", "live", "live", True),        # audit case 1: was simulated / False
        ("live", "off", "simulated", False),  # audit case 2: was live / True
        ("live", "test", "simulated", False),
        ("live", None, "live", True),         # no override -> the global gate
        ("off", None, "simulated", False),
        ("test", None, "simulated", False),
    ],
)
def test_shopify_row_reports_shopifys_effective_gate(
    monkeypatch, global_mode, override, state, armed
):
    _global_mode(monkeypatch, global_mode)
    if override is not None:
        monkeypatch.setenv("SHOPIFY_DISPATCH_MODE", override)

    report = build_integration_status(db=_DB(_VAULT))
    row = _row(report, "shopify")

    # The reporter and the gate the write path asks must agree, always.
    assert (row["state"] == "live") is nexus_providers._is_shopify_write_allowed()
    assert row["state"] == state
    assert row["armed"] is armed
    assert row["configured"] is True
    # The messaging rows still follow the GLOBAL gate, untouched by the override.
    assert report["dispatch_mode"] == global_mode


@pytest.mark.parametrize(
    "global_mode,override,gate_mode",
    [("off", "live", "live"), ("live", "off", "off"), ("test", None, "test")],
)
def test_shopify_row_shows_the_gate_it_is_using(
    monkeypatch, global_mode, override, gate_mode
):
    """The header shows the GLOBAL gate. The Shopify row must carry its own
    gate value and say which switch it is, or the screen contradicts itself
    in exactly the two audit cases (header 'off', Shopify badge 'Live')."""
    _global_mode(monkeypatch, global_mode)
    if override is not None:
        monkeypatch.setenv("SHOPIFY_DISPATCH_MODE", override)

    report = build_integration_status(db=_DB(_VAULT))
    row = _row(report, "shopify")

    assert row["gate"]["mode"] == gate_mode == nexus_providers.shopify_dispatch_mode()
    assert row["gate"]["label"] == "Shopify gate"
    assert "SHOPIFY_DISPATCH_MODE" in row["gate"]["rule"]
    assert "SHOPIFY_DISPATCH_MODE" in row["notes"]
    differs = f"({global_mode}) does not apply to Shopify"
    assert (differs in row["notes"]) is (gate_mode != global_mode)
    # Rows on the global gate carry no gate of their own.
    assert "gate" not in _row(report, "msg91_whatsapp")


def test_unconfigured_shopify_row_still_names_its_gate(monkeypatch):
    _global_mode(monkeypatch, "live")
    monkeypatch.setenv("SHOPIFY_DISPATCH_MODE", "off")
    row = _row(build_integration_status(db=_DB()), "shopify")
    assert row["state"] == "dormant"
    assert row["gate"]["mode"] == "off" and "SHOPIFY_DISPATCH_MODE" in row["notes"]


def test_messaging_rows_ignore_the_shopify_override(monkeypatch):
    _global_mode(monkeypatch, "off")
    monkeypatch.setenv("SHOPIFY_DISPATCH_MODE", "live")
    monkeypatch.setenv("MSG91_API_KEY", "SYNTHETIC")
    monkeypatch.setenv("MSG91_WHATSAPP_INTEGRATED_NUMBER", "SYNTHETIC")
    row = _row(build_integration_status(db=_DB()), "msg91_whatsapp")
    assert row["state"] == "simulated" and row["armed"] is False


def test_shopify_row_says_when_the_writes_switch_is_off(monkeypatch):
    """Live gate + IMS_SHOPIFY_WRITES off: order intake is live, catalogue
    pushes are not. The row must not read as plainly 'everything fires'."""
    _global_mode(monkeypatch, "off")
    monkeypatch.setenv("SHOPIFY_DISPATCH_MODE", "live")
    row = _row(build_integration_status(db=_DB(_VAULT)), "shopify")
    assert "IMS_SHOPIFY_WRITES" in row["notes"]

    monkeypatch.setenv("IMS_SHOPIFY_WRITES", "1")
    row = _row(build_integration_status(db=_DB(_VAULT)), "shopify")
    assert "IMS_SHOPIFY_WRITES" not in row["notes"]


# ---------------------------------------------------------------------------
# Credentials: the resolver's paths, by presence only
# ---------------------------------------------------------------------------


def test_oauth_only_configuration_reads_as_configured(monkeypatch):
    """No saved token at all - the working production path."""
    _global_mode(monkeypatch, "off")
    monkeypatch.setenv("SHOPIFY_CLIENT_ID", "SYNTHETIC-ID")
    monkeypatch.setenv("SHOPIFY_CLIENT_SECRET", "SYNTHETIC-SECRET")
    monkeypatch.setenv("SHOPIFY_STORE_URL", "audit.invalid")
    row = _row(build_integration_status(db=_DB()), "shopify")
    assert row["configured"] is True
    assert row["credential_source"] == "oauth"
    assert row["state"] == "simulated"
    present = {k["key"] for k in row["env_keys"] if k["present"]}
    assert {"SHOPIFY_CLIENT_ID", "SHOPIFY_CLIENT_SECRET", "SHOPIFY_STORE_URL"} <= present


def test_oauth_with_the_shop_saved_on_the_screen_reads_as_configured(monkeypatch):
    monkeypatch.setenv("SHOPIFY_CLIENT_ID", "SYNTHETIC-ID")
    monkeypatch.setenv("SHOPIFY_CLIENT_SECRET", "SYNTHETIC-SECRET")
    db = _DB({"shopify": {"type": "shopify", "enabled": True,
                          "config": {"shop_url": "audit.invalid"}}})
    row = _row(build_integration_status(db=db), "shopify")
    assert row["configured"] is True and row["credential_source"] == "oauth"


def test_saved_token_only_configuration_reads_as_configured():
    row = _row(build_integration_status(db=_DB(_VAULT)), "shopify")
    assert row["configured"] is True and row["credential_source"] == "vault"


def test_static_env_token_configuration_reads_as_configured(monkeypatch):
    monkeypatch.setenv("SHOPIFY_ACCESS_TOKEN", "SYNTHETIC")
    monkeypatch.setenv("SHOPIFY_STORE_URL", "audit.invalid")
    row = _row(build_integration_status(db=_DB()), "shopify")
    assert row["configured"] is True and row["credential_source"] == "env"


@pytest.mark.parametrize(
    "env,docs",
    [
        ({}, {}),                                                # nothing
        ({"SHOPIFY_CLIENT_ID": "X", "SHOPIFY_CLIENT_SECRET": "Y"}, {}),  # no shop
        ({"SHOPIFY_CLIENT_ID": "X", "SHOPIFY_STORE_URL": "audit.invalid"}, {}),
        ({}, {"shopify": {"type": "shopify", "enabled": False,   # switched off
                          "config": {"shop_url": "a.invalid", "access_token": "T"}}}),
        ({}, {"shopify": {"type": "shopify", "enabled": True,
                          "config": {"shop_url": "a.invalid"}}}),  # no token
    ],
)
def test_incomplete_shopify_credentials_stay_dormant(monkeypatch, env, docs):
    _global_mode(monkeypatch, "live")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    row = _row(build_integration_status(db=_DB(docs)), "shopify")
    assert row["configured"] is False
    assert row["state"] == "dormant" and row["armed"] is False
    assert row["credential_source"] is None


@pytest.mark.parametrize(
    "env,docs",
    [
        ({"SHOPIFY_CLIENT_ID": "X", "SHOPIFY_CLIENT_SECRET": "Y",
          "SHOPIFY_STORE_URL": "audit.invalid"}, {}),
        ({}, _VAULT),
        ({"SHOPIFY_ADMIN_TOKEN": "T", "SHOPIFY_STORE_URL": "audit.invalid"}, {}),
        ({"SHOPIFY_CLIENT_ID": "X", "SHOPIFY_CLIENT_SECRET": "Y"}, _VAULT),
    ],
)
def test_reported_source_is_the_one_the_resolver_would_use(monkeypatch, env, docs):
    """The presence report and the real resolver walk the same order. (Here
    the mint is stubbed to succeed, to ask the resolver which path it takes.)"""
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    db = _DB(docs)
    reported = _row(build_integration_status(db=db), "shopify")["credential_source"]

    monkeypatch.setattr(
        shopify_auth, "_mint_oauth_token",
        lambda shop, cid, secret: {"access_token": "MINTED", "expires_in": 3600},
    )
    resolved = shopify_auth.resolve_shopify_credentials(db)
    shopify_auth.clear_cached_tokens()
    assert reported == resolved["source"]


def test_anthropic_key_saved_on_the_screen_reads_as_configured():
    """get_anthropic_config reads the integrations collection first."""
    db = _DB({"anthropic": {"type": "anthropic", "enabled": True,
                            "config": {"api_key": "SYNTHETIC", "model": "m"}}})
    row = _row(build_integration_status(db=db), "anthropic")
    assert row["configured"] is True and row["state"] == "active"
    assert row["collection"]["present_keys"] == ["api_key", "model"]


def test_anthropic_env_key_still_reads_as_configured(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "SYNTHETIC")
    row = _row(build_integration_status(db=_DB()), "anthropic")
    assert row["configured"] is True and row["state"] == "active"


# ---------------------------------------------------------------------------
# Presence only: never a value, never "verified"
# ---------------------------------------------------------------------------


def test_no_shopify_secret_value_leaves_the_report(monkeypatch):
    monkeypatch.setenv("SHOPIFY_CLIENT_ID", "TOPSECRET-ID")
    monkeypatch.setenv("SHOPIFY_CLIENT_SECRET", "TOPSECRET-SECRET")
    monkeypatch.setenv("SHOPIFY_ACCESS_TOKEN", "TOPSECRET-TOKEN")
    monkeypatch.setenv("SHOPIFY_STORE_URL", "topsecret-shop.invalid")
    db = _DB({"shopify": {"type": "shopify", "enabled": True,
                          "config": {"shop_url": "topsecret-vault.invalid",
                                     "access_token": "TOPSECRET-VAULT"}}})
    blob = json.dumps(build_integration_status(db=db)).lower()
    assert "topsecret" not in blob


def test_configured_is_presence_not_a_verified_connection(monkeypatch):
    monkeypatch.setenv("SHOPIFY_CLIENT_ID", "X")
    monkeypatch.setenv("SHOPIFY_CLIENT_SECRET", "Y")
    monkeypatch.setenv("SHOPIFY_STORE_URL", "audit.invalid")
    row = _row(build_integration_status(db=_DB()), "shopify")
    assert "does not test the connection" in row["notes"]
    assert "verified" not in json.dumps(row).lower()
