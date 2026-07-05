"""
Tests for the Settings integration write path after convergence.

The bug these guard against: the Settings UI used to write an encrypted,
`integration_type`-keyed document that the provider clients could not read
(they query `{type:<lower>}` and expect plaintext). Saving therefore failed
to activate anything. The fix writes the same canonical document the
providers and /api/v1/admin/integrations/* use.
"""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.routers import settings as settings_router  # noqa: E402
from api.routers.settings import (  # noqa: E402
    IntegrationConfig,
    get_integration,
    update_integration,
)
# Aliased so pytest does not collect the endpoint named `test_integration`
# as a test case.
from api.routers.settings import test_integration as integration_test_endpoint  # noqa: E402


class _FakeColl:
    def __init__(self):
        self.store = {}
        self.last_update = None

    def update_one(self, flt, update, upsert=False):
        self.last_update = {"filter": flt, "update": update, "upsert": upsert}
        key = flt.get("type")
        self.store.setdefault(key, {})
        self.store[key].update(update.get("$set", {}))

    def find_one(self, flt):
        d = self.store.get(flt.get("type"))
        return dict(d) if d else None


SUPER = {"roles": ["SUPERADMIN"]}


def test_update_writes_canonical_type_keyed_encrypted(monkeypatch):
    coll = _FakeColl()
    monkeypatch.setattr(settings_router, "_get_settings_collection", lambda name: coll)

    cfg = IntegrationConfig(
        integration_type="RAZORPAY",  # deliberately upper to prove normalization
        enabled=True,
        config={"key_id": "rzp_live_abc", "key_secret": "supersecretvalue"},
    )
    resp = asyncio.run(update_integration("RAZORPAY", cfg, SUPER))

    # Stored under the lowercase `type` key (what providers query on)
    assert coll.last_update["filter"] == {"type": "razorpay"}
    stored = coll.last_update["update"]["$set"]
    assert stored["type"] == "razorpay"
    assert stored["enabled"] is True
    # BUG-155: the secret is ENCRYPTED at rest (Fernet `fernet:` prefix), the
    # raw value is never persisted, and the non-sensitive field is untouched.
    assert stored["config"]["key_id"] == "rzp_live_abc"
    assert stored["config"]["key_secret"].startswith("fernet:")
    assert "supersecretvalue" not in stored["config"]["key_secret"]
    # ...and it round-trips via the shared decrypt (providers read plaintext).
    from api.services import cred_crypto

    assert cred_crypto.decrypt_config(stored["config"]) == {
        "key_id": "rzp_live_abc",
        "key_secret": "supersecretvalue",
    }

    # Response masks secrets - never echoes the raw secret value
    assert "supersecretvalue" not in str(resp)


def test_get_reads_back_masked(monkeypatch):
    coll = _FakeColl()
    monkeypatch.setattr(settings_router, "_get_settings_collection", lambda name: coll)

    cfg = IntegrationConfig(
        integration_type="shopify",
        enabled=True,
        config={"shop_url": "store.myshopify.com", "access_token": "shpat_supersecret"},
    )
    asyncio.run(update_integration("shopify", cfg, SUPER))

    got = asyncio.run(get_integration("shopify", SUPER))
    assert got["type"] == "shopify"
    assert got["is_configured"] is True
    assert got["is_enabled"] is True
    # access_token returned masked, raw secret absent
    assert "shpat_supersecret" not in str(got)


def test_test_endpoint_is_honest_not_placebo(monkeypatch):
    coll = _FakeColl()
    monkeypatch.setattr(settings_router, "_get_settings_collection", lambda name: coll)
    monkeypatch.setenv("DISPATCH_MODE", "off")

    # Not configured yet -> honest not_configured (old code returned success)
    resp = asyncio.run(integration_test_endpoint("razorpay", SUPER))
    assert resp["status"] == "not_configured"
    assert resp["live"] is False

    # Configure it, then it reports configured but not live (DISPATCH_MODE=off)
    cfg = IntegrationConfig(
        integration_type="razorpay",
        enabled=True,
        config={"key_id": "id", "key_secret": "sec"},
    )
    asyncio.run(update_integration("razorpay", cfg, SUPER))
    resp = asyncio.run(integration_test_endpoint("razorpay", SUPER))
    assert resp["status"] == "configured"
    assert resp["dispatch_mode"] == "off"
    assert resp["live"] is False


def test_update_requires_admin_role(monkeypatch):
    coll = _FakeColl()
    monkeypatch.setattr(settings_router, "_get_settings_collection", lambda name: coll)
    cfg = IntegrationConfig(integration_type="razorpay", enabled=True, config={})

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        asyncio.run(update_integration("razorpay", cfg, {"roles": ["SALES_STAFF"]}))
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# Owner-hit bugs 2026-07-05 (Shopify enable during the go-live prep):
# 1. A save that omits secret fields (the modal always renders them blank and
#    the FE drops blank fields) used to REPLACE the whole config -> flipping
#    the Enabled toggle silently wiped the stored access_token. The write path
#    must MERGE submitted keys over the stored config.
# 2. GET /settings/integrations returned raw docs (with `enabled`) while the
#    IntegrationsHub cards key on `is_enabled`/`is_configured` -> every card
#    rendered OFF regardless of the saved state.
# ---------------------------------------------------------------------------


def test_enable_only_save_keeps_stored_secrets(monkeypatch):
    """Flipping Enabled without retyping secrets must not wipe them."""
    coll = _FakeColl()
    monkeypatch.setattr(settings_router, "_get_settings_collection", lambda name: coll)

    # 1. Initial save: full creds, disabled.
    asyncio.run(
        update_integration(
            "shopify",
            IntegrationConfig(
                integration_type="shopify",
                enabled=False,
                config={"shop_url": "x.myshopify.com", "access_token": "shpat_secret1"},
            ),
            SUPER,
        )
    )
    # 2. Enable-only save: secret field blank -> FE omits it entirely.
    asyncio.run(
        update_integration(
            "shopify",
            IntegrationConfig(
                integration_type="shopify",
                enabled=True,
                config={"shop_url": "x.myshopify.com"},
            ),
            SUPER,
        )
    )
    doc = coll.store["shopify"]
    assert doc["enabled"] is True
    stored = settings_router._decrypt_config(doc["config"])
    assert stored.get("access_token") == "shpat_secret1"  # survived the toggle
    assert stored.get("shop_url") == "x.myshopify.com"


def test_submitted_secret_still_overwrites_stored(monkeypatch):
    """Merging must not prevent a deliberate secret rotation."""
    coll = _FakeColl()
    monkeypatch.setattr(settings_router, "_get_settings_collection", lambda name: coll)

    for token in ("shpat_old", "shpat_new"):
        asyncio.run(
            update_integration(
                "shopify",
                IntegrationConfig(
                    integration_type="shopify",
                    enabled=True,
                    config={"access_token": token},
                ),
                SUPER,
            )
        )
    stored = settings_router._decrypt_config(coll.store["shopify"]["config"])
    assert stored.get("access_token") == "shpat_new"


def test_list_integrations_normalizes_is_enabled(monkeypatch):
    """The list endpoint carries is_enabled/is_configured like the single-get.

    Hermetic AND reload-proof: under the full CI suite `api.routers.settings`
    can exist as a SECOND module object (another suite re-imports the app), so
    patching this file's import-time binding while calling a function imported
    separately hits two different copies (the previous version failed with
    KeyError only in the full run). Resolve BOTH the patch target and the
    endpoint from sys.modules at runtime so they are always the same object.
    The test guards the NORMALIZATION of raw docs into the IntegrationsHub
    shape -- not the Mongo plumbing."""
    mod = sys.modules.get("api.routers.settings") or settings_router

    monkeypatch.setattr(
        mod,
        "_get_integrations_from_db",
        lambda: [
            {"type": "shopify", "enabled": True, "config": {"shop_url": "x"}},
            {"type": "razorpay", "enabled": False, "config": {}},
        ],
    )
    res = asyncio.run(mod.list_integrations(SUPER))
    rows = {r["type"]: r for r in res["integrations"]}
    assert rows["shopify"]["is_enabled"] is True
    assert rows["shopify"]["is_configured"] is True
    assert rows["razorpay"]["is_enabled"] is False
    assert rows["razorpay"]["is_configured"] is False
