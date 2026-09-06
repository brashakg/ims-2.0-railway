"""IMS 2.0 - Shopify GraphQL push engine  (BVI Phase 5 -- IMS -> Shopify PUSH)
==========================================================================
The IMS -> Shopify PUSH side of the "Online Store" module: take the IMS ecom
models (catalog_products + ecom sub-doc, catalog_variants, ecom_collections,
ecom_menus, APPROVED product_images) and push them to the Shopify storefront
(bettervision.in) via the Shopify GraphQL Admin API.

Full target architecture + the single-writer invariant: docs/reference/BVI_MERGE_PLAN.md
section A.3 + Phase 5.

***** THIS IS BUILT DARK (the non-negotiable safety contract) *****
Every push is SIMULATED -- it returns a dry-run PLAN and makes NO network call --
UNLESS ALL THREE hold:
  1. ims_shopify_writes_enabled()  -- IMS_SHOPIFY_WRITES is on (default OFF). Per
     #262 BVI is the SINGLE Shopify writer; the IMS push stays retired until the
     owner flips this gate as part of the Phase-6 baton cutover.
  2. shopify_dispatch_mode() == "live" -- SHOPIFY_DISPATCH_MODE when set (owner
     2026-07-05: lets Shopify go live WITHOUT arming the global DISPATCH_MODE,
     which would also arm WhatsApp/SMS), else the global DISPATCH_MODE.
  3. Shopify creds present         -- resolvable shop_url + access_token via
     shopify_auth.resolve_shopify_credentials(db): OAuth client-credentials
     (minted from SHOPIFY_CLIENT_ID/SECRET) preferred, else the Mongo vault or
     env static token.
Default / missing-creds / gate-off  ->  mode="SIMULATED", no Shopify call.

We REUSE the existing, code-verified safety primitives rather than reinvent them:
  - nexus_providers.ims_shopify_writes_enabled()  (the single-writer kill-switch)
  - shopify_auth.resolve_shopify_credentials(db)  (OAuth-preferred creds resolver)
  - nexus_providers.shopify_dispatch_mode() / _as_shopify_gid()  (live gate + GID helper)

IDEMPOTENT: on a LIVE push the Shopify gid returned by the mutation is written
BACK onto the IMS doc (ecom.shopify_product_id / shopify_variant_id /
shopify_collection_id / shopify_menu_id / shopify_image_id), keyed on the IMS
join key (never Mongo _id), so a re-push UPDATES the same Shopify object instead
of creating a duplicate. The presence of a stored Shopify id is what selects
create-vs-update in the mutation.

VARIANT SEEDING ON CREATE (2026-07 fix -- IMS is the sole Shopify writer):
ProductInput carries NO price and NO sku (the 2024-04+ product model moved both
onto the variant), so a bare productCreate lands a product whose default variant
is price 0.00 with no SKU. Every CREATE therefore runs a second step:
productCreate returns its auto-created variant(s); we set price /
compareAtPrice / barcode / inventoryItem.sku on them via
productVariantsBulkUpdate, create any REMAINING IMS variants via
productVariantsBulkCreate, and write every returned ProductVariant gid back
(ecom.shopify_variant_id + catalog_variants.shopify_variant_id) so a later price
push can find them. UPDATES of already-mapped products are UNCHANGED by default
-- seeding a price onto the ~4,400 live products is opt-in via
SHOPIFY_PUSH_PRICE_ON_UPDATE=1.

INVENTORY-ITEM CAPTURE (oversell-guard publish precondition, stacks on the
seeding fix): the same returned variants also select inventoryItem { id }, and
that gid is persisted ALONGSIDE the variant gid --
catalog_variants.shopify_inventory_item_id per variant row, plus
ecom.shopify_inventory_item_id for a product with NO catalog_variants rows
(its single "Default Title" variant IS the product). Those are exactly the two
fields the stock write-back resolver reads
(online_catalog.online_variant_targets_for_skus / inventory_items_for_skus,
online_sync_health._inventory_item_id_for_sku): without them a product IMS
creates on Shopify can never have its listed quantity synced down after an
in-store sale -- unguardable against oversell. shopify_location_id is NOT
captured here: the create/update response carries no location (this push never
sets stock), and the resolver sources the location from
SHOPIFY_ONLINE_LOCATION_ID / the integrations config.

Optional env flags (all default OFF -- nothing changes unless the owner sets them):
  SHOPIFY_PUSH_PRICE_ON_UPDATE=1  also seed price/sku + capture variant gids on
                                  an UPDATE of an already-mapped product.

Sales-channel publish is NOT optional and has no flag (owner ruling 2026-08-25,
"one press, goes live"): every product push publishes to the Online Store
channel, because an ACTIVE product published to no channel is invisible on the
storefront. Publish is still WITHHELD unless the price is provably right and the
product has a photograph ON SHOPIFY. The publication id can be pinned with
SHOPIFY_ONLINE_STORE_PUBLICATION_ID (else it is looked up once via `publications`,
which needs the read_publications scope); publishablePublish itself needs
write_publications, which is granted in the Shopify app, not in this repo. When
it cannot be resolved the publish is WITHHELD and the press reports
reason="publish_withheld" -- it never claims success. Owner-facing setup steps:
docs/SHOPIFY_PUBLISH_GO_LIVE.md.

FAIL-SOFT: every function returns a structured PushResult and NEVER raises. A
Shopify/GraphQL error becomes {ok: False, error: ...}; a missing doc becomes a
404-style {ok: False}. A push must never take down the caller.

The single network boundary is `_graphql()`. It is the ONLY thing that talks to
Shopify; tests monkeypatch it so no real Shopify call ever happens in a default
or test code path (belt-and-suspenders on top of the gate, which already blocks
the live branch by default).

PACKAGE (Wave 5 split, 2026-09-06): the flat api/services/shopify_push.py became
this package. Code was MOVED, not rewritten; every name the flat module
exposed is re-exported below, so ``from api.services.shopify_push import X``
and ``shopify_push.X`` keep working -- and so does monkeypatching by the
package path (see _ShopifyPushNamespace at the bottom).
"""

from __future__ import annotations

import sys
import types

from . import (
    _shared,
    transport,
    queries,
    product_input,
    variants,
    publish,
    collections,
    menus,
    media,
    writeback,
    inventory,
    tags,
    product,
    webhooks,
)

# Re-exports: the module-level surface the single file used to have,
# in the flat file's order, grouped by the sub-module that now owns each name.
from ._shared import (  # noqa: F401
    dataclass,
    asdict,
    datetime,
    timezone,
    Any,
    Dict,
    List,
    Optional,
    Tuple,
    asyncio,
    logging,
    os,
    random,
    httpx,
    ims_shopify_writes_enabled,
    shopify_dispatch_mode,
    _as_shopify_gid,
    SHOPIFY_API_VERSION,
    resolve_shopify_credentials,
    sanitise_gtin,
    ims_to_shopify_type,
    generate_attribute_tags,
    merge_tag_lists,
    logger,
    PROVIDER_TIMEOUT,
    MODE_SIMULATED,
    MODE_LIVE,
    MODE_BLOCKED,
    PushResult,
    _env_on,
    price_on_update_enabled,
    push_mode_status,
    _has_shopify_creds,
    _live_or_reason,
    push_lock_reason,
    _blocked_result,
    PRICE_NOT_SYNCED,
    _PRICE_NOT_SYNCED_MSG,
)
from .transport import (  # noqa: F401
    _MAX_RETRIES,
    _RETRY_BASE_DELAY,
    _RETRY_MAX_DELAY,
    _is_throttled_body,
    _retry_delay,
    _post_once,
    _graphql,
    _user_errors,
    PUBLISH_SCOPE_MISSING,
    _PUBLISH_SCOPE_MISSING_MSG,
    _is_access_denied,
    _now,
)
from .queries import (  # noqa: F401
    _PRODUCT_CREATE,
    _PRODUCT_UPDATE,
    _COLLECTION_CREATE,
    _COLLECTION_UPDATE,
    _COLLECTION_ADD_PRODUCTS,
    _COLLECTION_PRODUCTS_PER_CALL,
    _MENU_CREATE,
    _MENU_UPDATE,
    _PRODUCT_CREATE_MEDIA,
    _VARIANTS_BULK_UPDATE,
    _VARIANTS_PER_CALL,
    _VARIANTS_BULK_CREATE,
    _PUBLISHABLE_PUBLISH,
    _PUBLICATIONS_QUERY,
    _ONLINE_STORE_PUBLICATION_NAME,
    _publication_id_cache,
    _LOCATIONS_QUERY,
    _VARIANTS_INVENTORY_UPDATE,
    _INVENTORY_SET_QUANTITIES,
    _INVENTORY_SET_MAX,
    _online_location_cache,
)
from .inventory import (  # noqa: F401
    ONLINE_LOCATION_UNRESOLVED,
    ONLINE_LOCATION_AMBIGUOUS,
    STOCK_ONHAND_UNKNOWN,
    STOCK_TARGET_MISSING,
    stored_online_location_id,
    pick_online_location,
    resolve_online_location_id,
    list_locations,
    inventory_policy_for,
    product_skus,
    product_variant_gids,
    stock_changed,
    plan_product_stock,
    set_inventory_quantities,
    sync_product_stock,
    sync_stock_levels,
)
from .product_input import (  # noqa: F401
    build_product_input,
    _METAFIELD_NAMESPACE,
    _METAFIELDS_PER_CALL,
    _MAX_METAFIELDS,
    _METAFIELDS_SET,
    build_product_metafields,
    _set_product_metafields,
    _derive_options,
    _dedupe,
    _price_float,
    _resolve_variant_pricing,
    _has_publishable_price,
    _variants_for_price_push,
    _publishable_gtin,
    build_variant_price_inputs,
    ims_product_tags,
)
from .tags import (  # noqa: F401
    TAGS_SENT_FIELD,
    TAGS_CODE,
    sent_tags,
    plan_product_tags,
    sync_product_tags,
)
from .variants import (  # noqa: F401
    _norm_opt,
    _variant_option_key,
    _node_option_key,
    _node_inventory_item_gid,
    _variant_option_values,
    build_variant_seed_rows,
    plan_variant_seed,
    _row_has_gid,
    _needs_repair,
    build_repair_seed_rows,
    _assign_seed_rows,
    _seed_variants_after_write,
    push_variant_prices,
)
from .publish import (  # noqa: F401
    push_mode_status_resolved,
    _resolve_online_store_publication_id,
    _publish_to_online_store,
)
from .collections import (  # noqa: F401
    build_collection_input,
    _RULE_COLUMN,
    _RULE_RELATION,
    _build_rule_set,
    _member_product_gids,
    _push_collection_membership,
    push_collection,
)
from .menus import (  # noqa: F401
    build_menu_items,
    push_menu,
)
from .media import (  # noqa: F401
    _APP_IMAGE_PATH,
    TOMBSTONES_COLLECTION,
    MEDIA_LIMIT_CODE,
    product_photo_urls,
    _attach_product_photos,
    owned_media,
    plan_product_media,
    sync_product_media,
    build_media_inputs,
    push_image,
    _user_errors_media,
    _resolve_product_doc,
    _resolve_product_gid,
    _image_writeback_filter,
    _writeback_image,
)
from .writeback import (  # noqa: F401
    _writeback_product,
    _requeue_unpublished,
    _writeback_variant,
    _writeback_simple,
)
from .product import (  # noqa: F401
    push_product,
    push_product_delist,
)
from .webhooks import (  # noqa: F401
    _WEBHOOK_SUBSCRIPTIONS_QUERY,
    _WEBHOOK_PAGE_SIZE,
    _WEBHOOK_MAX_PAGES,
    _WEBHOOK_SUBSCRIPTION_CREATE,
    _WEBHOOK_SUBSCRIPTION_DELETE,
    _WEBHOOK_RECEIVER_PATH,
    CUTOVER_WEBHOOK_TOPICS,
    _topic_enum,
    delete_webhook_subscription,
    register_webhooks,
)

_SUBMODULES = (
    _shared,
    transport,
    queries,
    product_input,
    variants,
    publish,
    collections,
    menus,
    media,
    writeback,
    inventory,
    tags,
    product,
    webhooks,
)


class _ShopifyPushNamespace(types.ModuleType):
    """Make ``setattr`` on this package reach every sub-module that binds the name.

    ~25 test files patch ``shopify_push._graphql`` (the ONLY network call), the
    gate functions ``ims_shopify_writes_enabled`` / ``shopify_dispatch_mode`` /
    ``_has_shopify_creds`` and ``resolve_shopify_credentials`` by the package
    path, and shopify_fulfillment_push / online_sync_health / shopify_payouts /
    shopify_stock_parity read ``shopify_push._graphql`` the same way. While
    shopify_push was ONE module, that rebound the single global the push
    functions read. After the split each sub-module holds its own reference,
    so a patch on the package alone would silently miss them -- and the suite
    would try a REAL Shopify call. Forwarding the write to every sub-module
    that binds the name restores exactly the single-module behaviour,
    monkeypatch's undo included (undo is another setattr).

    tests/test_shopify_push_package_split.py fails if this is removed.
    """

    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        for mod in _SUBMODULES:
            if name in vars(mod):
                setattr(mod, name, value)


sys.modules[__name__].__class__ = _ShopifyPushNamespace
