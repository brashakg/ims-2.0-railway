"""Shopify push -- writeback

Write-back helpers (idempotency) -- store the Shopify gid on
the IMS doc. `_writeback_product` is the ONLY thing that clears
ecom.locally_modified (the ping-pong hazard).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ._shared import logger
from .transport import _now

# ===========================================================================
# Write-back helpers (idempotency) -- store the Shopify gid on the IMS doc
# ===========================================================================


def _writeback_product(
    db,
    product_id: str,
    shopify_id: str,
    variant_gid: Optional[str] = None,
    inventory_item_gid: Optional[str] = None,
    status: Optional[str] = None,
) -> None:
    """Persist ecom.shopify_product_id (+ stamps) on the catalog_products doc and
    clear the dirty flag, for idempotent re-push.

    SYNC PATH -- this write only ever sets `locally_modified` to False, and must
    NEVER set it to True. It is the push's own book-keeping (the Shopify gid it
    just minted), not a human catalogue edit. Queuing from anywhere on the
    Shopify sync side is the ping-pong hazard: push -> write-back marks dirty ->
    the next sweep pushes it again -> forever, against a LIVE storefront. The
    catalogue-edit doors that DO queue are product_master._build_pim_doc (born
    dirty on create) and catalog._save_catalog_product (dirty by default, with an
    explicit mark_dirty=False on the non-catalogue writes).

    `variant_gid` (optional) additionally persists ecom.shopify_variant_id -- the
    DEFAULT Shopify variant of this product. Without it a product created by IMS
    could never have its price corrected: ProductInput carries no price, so the
    only handle on the money is the variant gid. It is only ever SET, never
    cleared, so a call without it leaves an existing mapping intact.

    `inventory_item_gid` (optional) additionally persists
    ecom.shopify_inventory_item_id -- the oversell-guard stock target for a
    product with NO catalog_variants rows. This is the documented resolver
    fallback (online_catalog.inventory_items_for_skus /
    online_variant_targets_for_skus and online_sync_health.
    _inventory_item_id_for_sku all read ecom.shopify_inventory_item_id when no
    variant row matches a SKU). The caller only ever passes it for the
    product-level pseudo-variant, and it too is set-only, never cleared.

    `status` (optional) records what the storefront now shows: "PUBLISHED" after
    a successful sales-channel publish, "DRAFT" after a take-down. ecom.status is
    READ in six places (the Online Store screen's DRAFT / PUBLISHED cards, the
    storefront-visibility helpers in online_catalog / buy_desk) and before this
    was written "PUBLISHED" in ZERO -- so IMS said DRAFT about a product that was
    live. Set-only: None leaves the stored value alone.

    We READ-MERGE-WRITE the whole `ecom` sub-doc (read the doc, mutate the ecom
    dict in Python, $set ecom back) rather than `$set {"ecom.shopify_product_id":
    ...}`. Both are correct on real Mongo, but the merge-write also works on the
    in-memory MockCollection (which doesn't model dot-notation $set) AND keeps the
    sibling ecom fields intact (status/handle/seo) because the merge is explicit.
    Fail-soft: any error is logged, never raised (the Shopify write already
    succeeded)."""
    try:
        coll = db["catalog_products"]
        doc = coll.find_one({"id": product_id})
        if doc is None:
            return
        ecom = dict(doc.get("ecom") or {})
        ecom["shopify_product_id"] = shopify_id
        if variant_gid:
            ecom["shopify_variant_id"] = variant_gid
        if inventory_item_gid:
            ecom["shopify_inventory_item_id"] = inventory_item_gid
        if status:
            ecom["status"] = status
            # THE TAKE-DOWN MARKER. Delist writes DRAFT and clears the dirty
            # flag, but build_product_input maps everything except ARCHIVED to
            # ACTIVE -- so the only thing holding a product down was the flag
            # being false, and any catalogue edit re-queues it for the next
            # sweep to silently re-list mid-fix. Stamped here (and cleared by a
            # successful publish, the only writer of PUBLISHED) so the SWEEP can
            # skip it until a human presses that one product explicitly.
            if status == "DRAFT":
                ecom["taken_down_at"] = _now()
            elif status == "PUBLISHED":
                ecom.pop("taken_down_at", None)
        ecom["last_pushed_at"] = _now()
        ecom["locally_modified"] = False
        coll.update_one({"id": product_id}, {"$set": {"ecom": ecom}})
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[SHOPIFY_PUSH] product write-back failed {product_id}: {e}")


def _requeue_unpublished(db, product_id: str) -> None:
    """Put a row BACK in the push queue after a press that reached Shopify but
    did NOT make the product visible (publish withheld: unpriced, the photograph
    did not attach, the Online Store publication could not be resolved).

    THIS IS NOT THE PING-PONG HAZARD. _writeback_product must never set the flag,
    because that write is the push's own book-keeping and would re-queue a press
    that fully succeeded -- forever. This is the opposite case: the press did NOT
    do what it was pressed for. Clearing the flag anyway would leave the product
    ON Shopify, INVISIBLE, and OUT of the queue -- `pending: 0` next to an empty
    brand page, which is the exact lie this whole change exists to end. It
    re-queues ONLY on the not-published branch, so a successful publish still
    drains the queue (the control test), and the batch cap bounds how often a
    stubbornly-unpublishable row can be retried per press.

    Read-merge-write of the whole ecom sub-doc (the _writeback_product idiom).
    Fail-soft: any error is logged, never raised."""
    try:
        coll = db["catalog_products"]
        doc = coll.find_one({"id": product_id})
        if doc is None:
            return
        ecom = dict(doc.get("ecom") or {})
        ecom["locally_modified"] = True
        coll.update_one({"id": product_id}, {"$set": {"ecom": ecom}})
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[SHOPIFY_PUSH] re-queue failed {product_id}: {e}")


def _writeback_variant(
    db,
    variant: Dict[str, Any],
    shopify_variant_id: str,
    inventory_item_gid: Optional[str] = None,
) -> bool:
    """Persist shopify_variant_id (+ shopify_inventory_item_id when the response
    carried it) on the catalog_variants row, keyed on `sku` (its primary
    identity; `variant_id` is the fallback). The variant gid is what makes the
    EXISTING price push (build_variant_price_inputs, which skips gid-less
    variants) able to repair a price later; the InventoryItem gid is what the
    oversell-guard stock write-back resolver reads
    (catalog_variants.shopify_inventory_item_id) to sync the listed quantity
    down after an in-store sale. `inventory_item_gid` is set-only: when None
    (an old canned response, a partial node) any existing mapping is left
    untouched, never cleared. Returns True iff a row was written.
    Fail-soft: never raises -- the Shopify write already succeeded."""
    filt: Optional[Dict[str, Any]] = None
    if (variant or {}).get("sku"):
        filt = {"sku": variant["sku"]}
    elif (variant or {}).get("variant_id"):
        filt = {"variant_id": variant["variant_id"]}
    if filt is None:
        return False
    update: Dict[str, Any] = {
        "shopify_variant_id": shopify_variant_id,
        "updated_at": _now(),
    }
    if inventory_item_gid:
        update["shopify_inventory_item_id"] = inventory_item_gid
    try:
        res = db["catalog_variants"].update_one(filt, {"$set": update})
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[SHOPIFY_PUSH] variant write-back failed {filt}: {e}")
        return False
    touched = getattr(res, "matched_count", None)
    if touched is None:
        touched = getattr(res, "modified_count", 0)
    return bool(touched)


def _writeback_simple(
    db,
    collection_name: str,
    id_field: str,
    doc_id: str,
    shopify_field: str,
    shopify_id: str,
) -> None:
    """Generic gid write-back for collection/menu docs: set the shopify id field,
    clear locally_modified, stamp last_synced_at. Fail-soft."""
    try:
        coll = db[collection_name]
        coll.update_one(
            {id_field: doc_id},
            {
                "$set": {
                    shopify_field: shopify_id,
                    "locally_modified": False,
                    "last_synced_at": _now(),
                }
            },
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            f"[SHOPIFY_PUSH] {collection_name} write-back failed {doc_id}: {e}"
        )

