"""Tripwires for the Wave 5 shopify_push package split (api/services/shopify_push/).

Two things can silently break when a sub-module is added, moved or renamed:

1. A NAME DROPS OFF THE SURFACE. The flat module exposed 131 top-level names
   and ~25 test files, routers/online_store_push, online_store_collections,
   admin, buy_desk, online_catalog, online_sync_health, shopify_payouts,
   shopify_stock_parity, shopify_fulfillment_push and two scripts read them as
   ``api.services.shopify_push.<name>``. __init__.py re-exports every one; a
   forgotten re-export is an AttributeError at the first LIVE press, not at
   import.
2. A MONKEYPATCH STOPS BITING. Tests patch ``_graphql`` -- the ONLY Shopify
   network call -- and the gate functions by the package path. __init__.py
   forwards those writes into every sub-module that binds the name. Drop the
   forwarding and the patched tests still pass their setattr -- then the push
   functions call the REAL ``_graphql`` (a live Shopify POST from the suite)
   or read the REAL gates instead of the test's fake.
"""

import os
import sys

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services import shopify_push  # noqa: E402
from api.services.shopify_push import (  # noqa: E402
    _shared,
    collections,
    media,
    menus,
    product,
    publish,
    transport,
    variants,
    webhooks,
)

# The surface the flat module had (checked mechanically against origin/main's
# file when the split landed). A name can be ADDED here; none may go missing.
_FLAT_SURFACE_COUNT = 131
_LOAD_BEARING = (
    "push_product",
    "push_product_delist",
    "push_variant_prices",
    "push_collection",
    "push_menu",
    "push_image",
    "push_mode_status",
    "push_mode_status_resolved",
    "push_lock_reason",
    "register_webhooks",
    "delete_webhook_subscription",
    "CUTOVER_WEBHOOK_TOPICS",
    "PUBLISH_SCOPE_MISSING",
    "PushResult",
    "MODE_SIMULATED",
    "MODE_LIVE",
    "MODE_BLOCKED",
    "_graphql",
    "_post_once",
    "_user_errors",
    "_has_shopify_creds",
    "_live_or_reason",
    "_writeback_product",
    "_writeback_image",
    "_publication_id_cache",
    "_MAX_RETRIES",
    "product_photo_urls",
    "build_product_input",
    "build_variant_seed_rows",
    "build_variant_price_inputs",
    "ims_shopify_writes_enabled",
    "shopify_dispatch_mode",
    "resolve_shopify_credentials",
)


def test_export_surface_is_intact():
    public = [
        n
        for n in vars(shopify_push)
        if not n.startswith("__")
        and n not in ("sys", "types", "_SUBMODULES", "_ShopifyPushNamespace")
        and n not in {m.__name__.rsplit(".", 1)[-1] for m in shopify_push._SUBMODULES}
    ]
    assert len(public) >= _FLAT_SURFACE_COUNT, (
        "shopify_push package exposes %d names, the flat module had %d"
        % (len(public), _FLAT_SURFACE_COUNT)
    )
    for name in _LOAD_BEARING:
        assert hasattr(shopify_push, name), "shopify_push.%s went missing" % name


def test_logger_is_the_flat_modules_logger():
    # caplog / handler config keys on the flat name; every sub-module shares it.
    assert shopify_push.logger.name == "api.services.shopify_push"
    assert _shared.logger is shopify_push.logger


def test_publication_cache_is_one_object():
    # push_mode_status (gate reader) and _resolve_online_store_publication_id
    # (publish resolver) must read/write the SAME per-process dict.
    assert _shared._publication_id_cache is publish._publication_id_cache
    assert shopify_push._publication_id_cache is publish._publication_id_cache


def test_patching_graphql_on_the_package_reaches_every_caller():
    original = shopify_push._graphql
    sentinel = object()

    async def fake(_db, _query, _variables):  # pragma: no cover - never awaited
        return sentinel

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(shopify_push, "_graphql", fake)
        for mod in (transport, product, publish, variants, collections, menus, media, webhooks):
            assert mod._graphql is fake, "%s still holds the real _graphql" % mod.__name__

    # monkeypatch's undo goes through the same forwarding.
    assert shopify_push._graphql is original
    for mod in (transport, product, publish, variants, collections, menus, media, webhooks):
        assert mod._graphql is original, "%s not unwound" % mod.__name__


def test_patching_the_gates_on_the_package_reaches_the_gate_module():
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: True)
        mp.setattr(shopify_push, "shopify_dispatch_mode", lambda: "live")
        mp.setattr(shopify_push, "_has_shopify_creds", lambda _db, storefront_id="BV": True)
        assert _shared.ims_shopify_writes_enabled() is True
        assert _shared.shopify_dispatch_mode() == "live"
        # the gate reads its own module globals: all three must have moved.
        assert _shared._live_or_reason(None) == (True, None)
    assert _shared._live_or_reason(None)[0] is False
