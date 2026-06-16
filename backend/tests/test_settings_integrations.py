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
