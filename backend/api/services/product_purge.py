"""Permanent delete of an ARCHIVED product (owner 2026-10-24: "i will need an
option to delete archived products").

The catalogue's Delete is an archive (catalog._archive_product): the rows stay,
so an archived product still holds its identity_key (the unique duplicate key
on `products`) and clutters the catalogue. This is the ONE rule for removing
one for good -- the single door (DELETE /catalog/products/{id}/permanent) and
the Catalog page's bulk door (POST /catalog/products/bulk-purge) both call
`purge_refusal` then `purge_product`. SUPERADMIN only (the routes' gate).

Refused (nothing deleted):
  * not archived                 -> 409 "Archive it first"
  * linked to a Shopify listing  -> 409 (a gid on the twin / spine, or a
                                    catalog_variants row with a variant gid)
  * leads a model family         -> 409 (other products' variant_of points here)
  * ANY history a record or tax document points at -> 409, it stays archived
  * a read that failed           -> 503 (never "no history")

Allowed: every doc keyed only to this product (spine, twin, its
catalog_variants rows, product_images records, vendor_sku_aliases,
collection_products membership rows) is FIRST copied whole into
`deleted_products_backup` (one doc per product); a failed backup deletes
nothing. Stored image FILES are left alone: the codebase has no helper that
removes a stored file (DELETE /online-store/images/{id} removes the record
only), so neither does this.

No emoji (Windows cp1252).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

ARCHIVE_FIRST = "Archive it first: only an archived product can be deleted permanently."
UNREADABLE = (
    "Could not check this product's history, so nothing was deleted. Try again in a minute."
)
ON_SHOPIFY = (
    "It is linked to a listing on the website (Shopify), so it stays archived "
    "to keep the website and its records matched."
)
LEADS_FAMILY = (
    "Other colours or sizes of this model are linked to it, so it stays archived. "
    "Delete those first."
)
BACKUP_COLLECTION = "deleted_products_backup"

# Every collection that records or a tax document keep a product reference in
# (grepped 2026-10-24: product_id / sku / ims_product_id on the doc, or on its
# items / lines). A hit in any of them keeps the product archived.
HISTORY: List[Tuple[str, str]] = [
    ("stock_units", "stock units"),
    ("orders", "sales and invoices"),
    ("revised_invoices", "revised invoices"),
    ("returns", "customer returns"),
    ("credit_note_ledger", "credit notes"),
    ("shopify_refund_review", "online refunds"),
    ("online_stock_miss", "online orders"),
    ("purchase_orders", "purchase orders"),
    ("grns", "goods receipts"),
    ("goods_receipt_notes", "goods receipts"),
    ("vendor_bills", "purchase bills"),
    ("vendor_returns", "returns to vendor"),
    ("vendor_rmas", "returns to vendor"),
    ("vendor_debit_notes", "debit notes"),
    ("debit_notes", "debit notes"),
    ("stock_transfers", "stock transfers"),
    ("stock_counts", "stock counts"),
    ("stock_shrinkage", "stock adjustments"),
    ("item_events", "stock movements"),
    ("opening_stock_batches", "opening stock"),
    ("serial_numbers", "serial numbers"),
    ("workshop_jobs", "workshop jobs"),
    ("product_incentive_log", "sales incentives"),
    ("display_placements", "display placements"),
]
# Where a product reference sits on those docs. Each is matched against the
# product's ids AND skus: over-matching only ever refuses (the safe side).
# ponytail: unindexed $or scans per collection; fine for a rare SUPERADMIN
# action on this data size, add indexes if it ever runs in bulk at scale.
REF_FIELDS = (
    "product_id",
    "sku",
    "ims_product_id",
    "items.product_id",
    "items.ims_product_id",
    "items.sku",
    "lines.product_id",
    "lines.sku",
    "unresolved_lines.product_id",
    "proposed_restock.product_id",
    "proposed_restock.sku",
    "lens_details.product_id",
    "lens_product_id",
)


def _keys(spine: Dict, twin: Optional[Dict]) -> Tuple[List[str], List[str]]:
    """(ids, skus) a reference to this product can carry."""
    ids = {spine.get("product_id"), spine.get("pim_product_id"), (twin or {}).get("id")}
    skus = {spine.get("sku"), (twin or {}).get("sku")}
    return sorted(i for i in ids if i), sorted(s for s in skus if s)


def _ref_query(keys: List[str]) -> Dict:
    return {"$or": [{f: {"$in": keys}} for f in REF_FIELDS]}


def _own_variant_rows(db, twin_ids: List[str], skus: List[str]) -> List[Dict]:
    ors: List[Dict] = []
    if twin_ids:
        ors.append({"parent_product_id": {"$in": twin_ids}})
    if skus:
        ors.append({"sku": {"$in": skus}})
    return list(db["catalog_variants"].find({"$or": ors})) if ors else []


def purge_refusal(db, spine: Optional[Dict], twin: Optional[Dict]) -> Optional[Tuple[int, str]]:
    """Why this product cannot be deleted permanently, as (status, reason);
    None when it can. A read that fails refuses (503) -- never "no history"."""
    if spine is None:
        return 404, "Product not found"
    if not spine.get("deleted_at") or (twin is not None and not twin.get("deleted_at")):
        return 409, ARCHIVE_FIRST
    if db is None:
        return 503, UNREADABLE
    pid = spine["product_id"]
    ids, skus = _keys(spine, twin)
    twin_id = (twin or {}).get("id")
    try:
        if (
            ((twin or {}).get("ecom") or {}).get("shopify_product_id")
            or spine.get("shopify_product_id")
            or (spine.get("ecom") or {}).get("shopify_product_id")
            or any(
                r.get("shopify_variant_id")
                for r in _own_variant_rows(db, [twin_id] if twin_id else [], skus)
            )
        ):
            return 409, ON_SHOPIFY
        lead_q: List[Dict] = [{"ecom.variant_of.product_id": pid}]
        if twin_id:
            lead_q.append({"ecom.variant_of.twin_id": twin_id})
        if db["products"].find_one({"variant_of": pid, "product_id": {"$ne": pid}}) or db[
            "catalog_products"
        ].find_one({"$or": lead_q, "id": {"$ne": twin_id}}):
            return 409, LEADS_FAMILY
        query = _ref_query(ids + skus)
        found: List[str] = []
        for coll, label in HISTORY:
            if db[coll].find_one(query) is not None and label not in found:
                found.append(label)
    except Exception:  # noqa: BLE001 - unreadable is "cannot check", never "none"
        logger.error("[PURGE] history check could not read for %s", pid, exc_info=True)
        return 503, UNREADABLE
    if found:
        return 409, (
            f"It has sales/stock/purchase history ({', '.join(found)}), so it stays "
            "archived to keep the records complete."
        )
    return None


def purge_product(
    db, spine: Dict, twin: Optional[Dict], actor: Dict, *, run_id: str, audit_repo=None
) -> str:
    """Back up, then delete, every doc keyed only to this product. Call only
    after purge_refusal answered None. Returns the backup id. A backup that
    fails raises before anything is deleted. The spine goes LAST: if a delete
    fails midway the archived spine is still there, so a retry finishes it."""
    pid = spine["product_id"]
    ids, skus = _keys(spine, twin)
    twin_ids = [twin["id"]] if twin and twin.get("id") else []
    docs: Dict[str, List[Dict]] = {
        "catalog_variants": _own_variant_rows(db, twin_ids, skus),
        "product_images": list(db["product_images"].find({"product_id": {"$in": ids}})),
        "vendor_sku_aliases": list(db["vendor_sku_aliases"].find({"product_id": {"$in": ids}})),
        "collection_products": list(
            db["collection_products"].find(
                {"$or": [{"product_id": {"$in": ids}}, {"sku": {"$in": skus}}]}
            )
        ),
        "catalog_products": list(db["catalog_products"].find({"id": {"$in": twin_ids}})),
        "products": list(db["products"].find({"product_id": pid})),
    }
    backup_id = f"DPB-{uuid.uuid4().hex[:12].upper()}"
    now = datetime.now(timezone.utc)
    db[BACKUP_COLLECTION].insert_one(
        {
            "backup_id": backup_id,
            "run_id": run_id,
            "product_id": pid,
            "sku": spine.get("sku"),
            "actor": actor.get("user_id"),
            "deleted_at": now,
            "docs": docs,
        }
    )
    for coll, rows in docs.items():  # dict order: the spine last
        if rows:
            db[coll].delete_many({"_id": {"$in": [r["_id"] for r in rows]}})
    try:
        if audit_repo is not None:
            audit_repo.create(
                {
                    "action": "product.deleted_permanently",
                    "actor": actor.get("user_id"),
                    "user_id": actor.get("user_id"),
                    "entity_type": "product",
                    "entity_id": pid,
                    "timestamp": now,
                    "ts": now.isoformat(),
                    "detail": {
                        "sku": spine.get("sku"),
                        "twin_id": twin_ids[0] if twin_ids else None,
                        "backup_id": backup_id,
                        "run_id": run_id,
                        "deleted": {c: len(r) for c, r in docs.items()},
                    },
                }
            )
    except Exception:  # noqa: BLE001 - the audit never undoes the delete
        logger.warning("[PURGE] audit row failed for %s", pid, exc_info=True)
    return backup_id
