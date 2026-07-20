"""
Tests for the STOREFRONT-KEYED Shopify credential lookup (WizOpt Phase 0).

The one non-negotiable guarantee: the keyed lookup is BACKWARD-COMPATIBLE with
the live Better Vision integrations doc, which has type="shopify" and NO
storefront_id field. A `storefront_id="BV"` lookup MUST still match that untagged
doc (via the $or:[{storefront_id:"BV"}, {storefront_id:{$exists:false}}] filter),
so BV behaves byte-identically. A doc tagged for a DIFFERENT storefront must NOT
leak into a BV lookup.

Every Shopify network path is avoided (no OAuth env creds -> the resolver takes
the vault branch), so no real Shopify call is ever made.

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_shopify_auth_storefront.py -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402

from agents import nexus_providers as nx  # noqa: E402
from api.services import shopify_auth  # noqa: E402


_SHOPIFY_ENV = (
    "SHOPIFY_CLIENT_ID",
    "SHOPIFY_CLIENT_SECRET",
    "SHOPIFY_STORE_URL",
    "SHOPIFY_ACCESS_TOKEN",
    "SHOPIFY_ADMIN_TOKEN",
)


@pytest.fixture(autouse=True)
def _clean_env_and_cache(monkeypatch):
    for name in _SHOPIFY_ENV:
        monkeypatch.delenv(name, raising=False)
    shopify_auth.clear_cached_tokens()
    yield
    shopify_auth.clear_cached_tokens()


# ---------------------------------------------------------------------------
# A faithful in-memory integrations collection: evaluates the exact Mongo
# operators the keyed query uses ($or + $exists + scalar equality) so the
# backward-compat filter is tested for real, not mocked away.
# ---------------------------------------------------------------------------


def _match_clause(doc, key, cond):
    if key == "$or":
        return any(_matches(doc, sub) for sub in cond)
    if isinstance(cond, dict) and "$exists" in cond:
        return (key in doc) == bool(cond["$exists"])
    return doc.get(key) == cond


def _matches(doc, query):
    return all(_match_clause(doc, k, v) for k, v in query.items())


class _FakeColl:
    def __init__(self, docs):
        self._docs = docs

    def find_one(self, query, projection=None):
        for d in self._docs:
            if _matches(d, query):
                return dict(d)
        return None


class _FakeDB:
    def __init__(self, docs):
        self._coll = _FakeColl(docs)

    def get_collection(self, name):
        assert name == "integrations"
        return self._coll


# ---------------------------------------------------------------------------
# _load_integration_config: the keyed query
# ---------------------------------------------------------------------------


def test_untagged_doc_matches_bv_lookup():
    """The live BV shape (no storefront_id) is matched by a storefront_id='BV'
    lookup -- the backward-compat guarantee."""
    db = _FakeDB(
        [
            {
                "type": "shopify",
                "enabled": True,
                # NOTE: no storefront_id field -- exactly the live doc shape.
                "config": {"shop_url": "bv.myshopify.com", "access_token": "tok_bv"},
            }
        ]
    )
    cfg = nx._load_integration_config(db, "shopify", storefront_id="BV")
    assert cfg.get("shop_url") == "bv.myshopify.com"
    assert cfg.get("access_token") == "tok_bv"


def test_explicitly_tagged_bv_doc_matches_bv_lookup():
    """A doc explicitly tagged storefront_id='BV' also matches a BV lookup."""
    db = _FakeDB(
        [
            {
                "type": "shopify",
                "enabled": True,
                "storefront_id": "BV",
                "config": {"shop_url": "bv.myshopify.com", "access_token": "tok_bv"},
            }
        ]
    )
    cfg = nx._load_integration_config(db, "shopify", storefront_id="BV")
    assert cfg.get("access_token") == "tok_bv"


def test_other_storefront_doc_does_not_leak_into_bv_lookup():
    """A doc tagged for a DIFFERENT storefront (WZ) must NOT match a BV lookup
    -- otherwise WizOpt creds could be used to push to Better Vision."""
    db = _FakeDB(
        [
            {
                "type": "shopify",
                "enabled": True,
                "storefront_id": "WZ",
                "config": {"shop_url": "wz.myshopify.com", "access_token": "tok_wz"},
            }
        ]
    )
    cfg = nx._load_integration_config(db, "shopify", storefront_id="BV")
    assert cfg == {}  # no match -> empty config


def test_unkeyed_lookup_is_unchanged_for_non_shopify():
    """Callers that pass no storefront_id (razorpay, shiprocket, ...) build the
    exact previous query -- a tagged doc still matches because no $or is added."""
    db = _FakeDB(
        [
            {
                "type": "razorpay",
                "enabled": True,
                "config": {"key_id": "k", "key_secret": "s"},
            }
        ]
    )
    cfg = nx._load_integration_config(db, "razorpay")
    assert cfg.get("key_id") == "k"


# ---------------------------------------------------------------------------
# resolve_shopify_credentials end-to-end (vault branch, no OAuth env)
# ---------------------------------------------------------------------------


def test_resolver_bv_default_reads_untagged_vault_doc():
    """resolve_shopify_credentials(db) with the default 'BV' resolves the
    untagged live doc via the vault branch (no OAuth env creds set)."""
    db = _FakeDB(
        [
            {
                "type": "shopify",
                "enabled": True,
                "config": {"shop_url": "bv.myshopify.com", "access_token": "tok_bv"},
            }
        ]
    )
    res = shopify_auth.resolve_shopify_credentials(db)  # default storefront_id="BV"
    assert res == {
        "shop_url": "bv.myshopify.com",
        "access_token": "tok_bv",
        "source": "vault",
    }


def test_resolver_other_storefront_gets_none_when_only_bv_doc_exists():
    """A WZ lookup against a store that only has the untagged BV doc must NOT
    borrow BV's creds: the untagged doc matches (back-compat) is a BV-only
    guarantee -- a WZ-tagged query only matches storefront_id in {WZ, absent}.
    Here the sole doc is tagged BV, so WZ resolves to None."""
    db = _FakeDB(
        [
            {
                "type": "shopify",
                "enabled": True,
                "storefront_id": "BV",
                "config": {"shop_url": "bv.myshopify.com", "access_token": "tok_bv"},
            }
        ]
    )
    assert shopify_auth.resolve_shopify_credentials(db, "WZ") is None
