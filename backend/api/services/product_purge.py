"""Permanent delete of an ARCHIVED product (owner 2026-10-24: "i will need an
option to delete archived products").

The catalogue's Delete is an archive (catalog._archive_product): the rows stay,
so an archived product still holds its identity_key (the unique duplicate key
on `products`) and clutters the catalogue. This is the ONE rule for removing
one for good -- the single door (DELETE /catalog/products/{id}/permanent) and
the Catalog page's bulk door (POST /catalog/products/bulk-purge) both use it:
`purge_check` answers the dry run, `purge` does the delete (and checks again
itself). SUPERADMIN only (the routes' gate, and its own capability key).

Refused (nothing deleted):
  * not archived (its spine, or ANY catalogue row of it)      -> 409
  * more than one catalogue row claims it, or one by SKU only -> 409
  * leads a model family (another product's variant_of) not in the same batch -> 409
  * ANY history a record or tax document points at (HISTORY)  -> 409
  * named in a promotion                                      -> 409
  * another SKU's website row sits under it                   -> 409
  * a website order for it not yet booked in IMS              -> 409
  * on Shopify while website writes are switched off (DARK)   -> 409
  * its Shopify link cannot be resolved safely                -> 409
  * a permanent delete of it is already running               -> 409
  * Shopify did not remove it, or Shopify says the listing holds other colours -> 502
  * a read that failed                                        -> 503

On Shopify (owner ruling 2026-10-24 on #1273, "want that yes") it comes off
the website through the push engine (shopify_push.removal_plan /
push_product_remove, the same LIVE gates as every push): its own listing with
productDelete; a colour/size of a model only its own variant (and its photos
on that listing), never the model's product. Shopify is READ first
(shopify_push.shopify_removal_truth): only a listing that holds nothing but
this product's SKU, or a variant whose SKU and listing are this product's, is
deleted. Already gone counts as done.

`purge` order, one product at a time, the Mongo work off the event loop:
CLAIM the spine (the lock: exclusive, still archived; Restore cannot take it)
and the twin (off the auto-sync queue) -> check again, fresh -> BACKUP every
doc it will delete or change into `deleted_products_backup` -> re-assert the
claim and check again (the plan must not have changed) -> Shopify -> check
again -> gather again and add anything new to the backup -> change / delete
(the spine LAST; spine and twin only while still archived and still ours) ->
backup done -> audit row. A failed backup changes nothing; a Shopify failure
deletes nothing in IMS. While a backup is deleting, Restore refuses, so a
part-deleted product never comes back broken; pressing Delete permanently
again finishes it.

Stored image FILES are left alone: the codebase has no helper that removes a
stored file (DELETE /online-store/images/{id} removes the record only).

No emoji (Windows cp1252).
"""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

ARCHIVE_FIRST = "Archive it first: only an archived product can be deleted permanently."
UNREADABLE = (
    "Could not check this product's history, so nothing was deleted. Try again in a minute."
)
WEBSITE_DARK = (
    "It is on the website, and website writes are switched off, so it stays archived."
)
LEADS_FAMILY = (
    "Other colours or sizes of this model are linked to it, so it stays archived. "
    "Delete or move the other colours first."
)
TWO_ROWS = (
    "More than one catalogue row belongs to it, so it stays archived. "
    "This needs sorting out by hand."
)
SKU_ONLY_ROW = (
    "Its catalogue row is linked to it only by SKU, so it stays archived. "
    "This needs sorting out by hand."
)
OTHER_SKUS_UNDER = (
    "Other sizes' website rows sit under it, so it stays archived. "
    "Delete or move those first."
)
UNBOOKED_ORDER = (
    "A website order for it has not been brought into IMS yet, so it stays archived. "
    "Try again once the order shows in Online Orders."
)
CHANGED_MEANWHILE = "It was restored or changed meanwhile, so nothing was deleted."
RUNNING = "A permanent delete of it is already running. Refresh in a minute; do not press again."
BACKUP_FAILED = "Could not write the backup, so nothing was deleted. Try again in a minute."
PART_DELETED = (
    "Deleting stopped part-way. Nothing is lost: the backup holds every record. "
    "Press Delete permanently again to finish."
)
LEAD_AFTER_COLOURS = "Goes after its colours in this same delete."
BACKUP_COLLECTION = "deleted_products_backup"
# A backup in one of these states means a delete is under way or stopped
# part-way (restore_refusal).
OPEN_STATES = ("removing", "deleting")
# A claim older than this belongs to a run that died (a crash, a killed
# worker): a new press may take it over, and Restore is no longer held by it.
CLAIM_STALE = timedelta(minutes=15)

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

_ARCHIVED = {"$nin": [None, ""]}


def _keys(spine: Dict, twin: Optional[Dict]) -> Tuple[List[str], List[str]]:
    """(ids, skus) a reference to this product can carry."""
    ids = {spine.get("product_id"), spine.get("pim_product_id"), (twin or {}).get("id")}
    skus = {spine.get("sku"), (twin or {}).get("sku")}
    return sorted(i for i in ids if i), sorted(s for s in skus if s)


def _ref_query(keys: List[str]) -> Dict:
    return {"$or": [{f: {"$in": keys}} for f in REF_FIELDS]}


def _gid_tail(gid: Optional[str]) -> Optional[str]:
    tail = str(gid or "").rsplit("/", 1)[-1]
    return tail if tail.isdigit() else None


def _unbooked_order(db, skus: List[str], plan: Optional[Dict]) -> bool:
    """A Shopify ORDER delivery in webhook_inbox naming this product (its
    sku, its variant, or -- own listing only -- its product) whose order is
    not booked in IMS yet. A booked one is history (HISTORY: orders)."""
    nums: Dict[str, List[str]] = {"variant_id": [], "product_id": []}
    if plan:
        if _gid_tail(plan.get("variant_gid")):
            nums["variant_id"].append(_gid_tail(plan["variant_gid"]))
        if plan.get("kind") == "product" and _gid_tail(plan.get("product_gid")):
            nums["product_id"].append(_gid_tail(plan["product_gid"]))
    ors: List[Dict] = [{"payload.line_items.sku": {"$in": skus}}] if skus else []
    for field, vals in nums.items():
        if vals:
            ors.append({f"payload.line_items.{field}": {"$in": vals + [int(v) for v in vals]}})
    if not ors:
        return False
    for row in db["webhook_inbox"].find({"vendor": "shopify", "$or": ors}):
        topic = str(((row.get("headers") or {}).get("x-shopify-topic")) or "").lower()
        if topic and not topic.startswith("orders/"):
            continue
        sid = (row.get("payload") or {}).get("id")
        if sid is None:
            continue
        if db["orders"].find_one({"shopify_order_id": {"$in": [str(sid), sid]}}) is None:
            return True
    return False



def _now() -> datetime:
    return datetime.now(timezone.utc)


def _free_claim() -> Dict[str, Any]:
    """Mongo filter: nobody holds the purge claim, or its holder died."""
    return {"$or": [{"purge_run": None}, {"purge_run_at": {"$lt": _now() - CLAIM_STALE}}]}


def linked_members(db, pid: str, twin_id: Optional[str]) -> Tuple[Set[str], bool]:
    """The products linked to this one as its model's colours/sizes, as spine
    ids, and whether every link could be put to a spine (a twin-only member
    cannot be in a batch of spine ids)."""
    members = {d["product_id"] for d in db["products"].find({"variant_of": pid, "product_id": {"$ne": pid}})}
    lead_q: List[Dict] = [{"ecom.variant_of.product_id": pid}]
    if twin_id:
        lead_q.append({"ecom.variant_of.twin_id": twin_id})
    complete = True
    for t in db["catalog_products"].find({"$or": lead_q, "id": {"$ne": twin_id}}):
        s = db["products"].find_one({"$or": [{"pim_product_id": t.get("id")}, {"product_id": t.get("id")}]})
        if s and s.get("product_id"):
            members.add(s["product_id"])
        else:
            complete = False
    return members, complete


def purge_check(
    db,
    spine: Optional[Dict],
    twin: Optional[Dict],
    *,
    gate: bool = True,
    batch: FrozenSet[str] = frozenset(),
    website_done: bool = False,
) -> Tuple[Optional[Tuple[int, str]], Optional[Dict]]:
    """(refusal, website plan). refusal is (status, reason), None when it may
    go; the plan (shopify_push.removal_plan) is what must come off Shopify
    first, None when it is on no listing. A read that fails refuses (503) --
    never "no history", never "not on Shopify". `gate` False skips the
    website-writes-off refusal; `batch` = the products going before it in the
    same delete (a model lead may go after ALL its colours); `website_done` =
    an earlier run of this delete already took it off Shopify."""
    from . import shopify_push
    from .online_delist import _resolve_twin

    if spine is None:
        return (404, "Product not found"), None
    if not spine.get("deleted_at") or (twin is not None and not twin.get("deleted_at")):
        return (409, ARCHIVE_FIRST), None
    if db is None:
        return (503, UNREADABLE), None
    pid = spine["product_id"]
    ids, skus = _keys(spine, twin)
    twin_id = (twin or {}).get("id")
    try:
        # Every catalogue row that could be its twin: by its ids (the
        # convergence and create-door links) and by the delist rule (which
        # falls back to the SKU, as has_listing and the stock writer do).
        rows = list(db["catalog_products"].find(
            {"id": {"$in": [i for i in (pid, spine.get("pim_product_id")) if i]}}
        ))
        link = {k: spine.get(k) for k in ("pim_product_id", "product_id", "sku")}
        resolved = _resolve_twin(db, link, strict=True)
        if any(not r.get("deleted_at") for r in rows) or (resolved and not resolved.get("deleted_at")):
            return (409, ARCHIVE_FIRST), None
        row_ids = {r.get("id") for r in rows} | {(resolved or {}).get("id")} | {twin_id}
        if len({i for i in row_ids if i}) > 1:
            return (409, TWO_ROWS), None
        if twin is None and (rows or resolved):
            return (409, SKU_ONLY_ROW), None
        members, complete = linked_members(db, pid, twin_id)
        if members and not (complete and members <= set(batch)):
            return (409, LEADS_FAMILY), None
        query = _ref_query(ids + skus)
        found: List[str] = []
        for coll, label in HISTORY:
            if db[coll].find_one(query) is not None and label not in found:
                found.append(label)
        if found:
            return (
                409,
                f"It has sales/stock/purchase history ({', '.join(found)}), so it stays "
                "archived to keep the records complete.",
            ), None
        promo = db["promo_rules"].find_one({"product_ids": {"$in": ids}})
        if promo is not None:
            return (
                409,
                f"It is in the promotion '{promo.get('name') or promo.get('promo_id')}', so it "
                "stays archived. Take it out of the promotion first.",
            ), None
        under = [twin_id, pid] if twin_id else [pid]
        if db["catalog_variants"].find_one({
            "$or": [{"parent_product_id": {"$in": under}}, {"parent_sku": {"$in": skus}}],
            "sku": {"$nin": skus},
        }):
            return (409, OTHER_SKUS_UNDER), None
        plan = None if website_done else shopify_push.removal_plan(db, twin, spine)
        if _unbooked_order(db, skus, plan):
            return (409, UNBOOKED_ORDER), None
    except shopify_push.RemovalUnclear as exc:
        return (409, f"Its website link is unclear ({exc}), so it stays archived."), None
    except Exception:  # noqa: BLE001 - unreadable is "cannot check", never "none"
        logger.error("[PURGE] check could not read for %s", pid, exc_info=True)
        return (503, UNREADABLE), None
    if gate and plan is not None and not shopify_push._live_or_reason(db)[0]:
        return (409, WEBSITE_DARK), plan
    return None, plan


def restore_refusal(db, spine: Optional[Dict]) -> Optional[str]:
    """Why Restore must refuse: a permanent delete of it holds the claim, is
    taking it off the website, or stopped part-way with records already
    deleted. A claim or a 'removing' backup older than CLAIM_STALE belongs to
    a run that died: then Restore may go (nothing in IMS was deleted yet).
    Raises on a read error (the restore door answers that)."""
    product_id = (spine or {}).get("product_id")
    if not product_id or db is None:
        return None
    stale = _now() - CLAIM_STALE
    at = spine.get("purge_run_at")
    if spine.get("purge_run") and not (isinstance(at, datetime) and _aware(at) < stale):
        return "A permanent delete of it is running, so it cannot be restored."
    for b in db[BACKUP_COLLECTION].find({"product_id": product_id, "status": {"$in": list(OPEN_STATES)}}):
        started = b.get("deleted_at")
        if b.get("status") == "removing" and isinstance(started, datetime) and _aware(started) < stale:
            continue
        return (
            "It is being deleted permanently (or that stopped part-way), so it cannot be "
            "restored. Press Delete permanently again to finish."
        )
    return None


def _aware(at: datetime) -> datetime:
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def _website_done(db, pid: str) -> bool:
    """An earlier run of this delete took it off Shopify and then stopped
    part-way: its open 'deleting' backup records the removal."""
    return db[BACKUP_COLLECTION].find_one(
        {"product_id": pid, "status": "deleting", "shopify.result.ok": True}
    ) is not None


def _fresh(db, pid: str, twin_id: Optional[str], *, website_done: bool):
    """purge_check on freshly read docs: (refusal, plan, spine, twin)."""
    spine = db["products"].find_one({"product_id": pid})
    twin = db["catalog_products"].find_one({"id": twin_id}) if twin_id else None
    if twin_id and twin is None:
        return (409, CHANGED_MEANWHILE), None, spine, twin
    refusal, plan = purge_check(db, spine, twin, gate=not website_done, website_done=website_done)
    return refusal, plan, spine, twin


def _gather(db, spine: Dict, twin: Optional[Dict], plan: Optional[Dict]) -> Dict[str, Any]:
    """Every doc the delete removes (`docs`) or changes (`updated`), read
    whole, for the backup. Only rows that are THIS product's own: variant
    rows by its own SKU (purge_check refused rows of other SKUs under it)."""
    pid = spine["product_id"]
    ids, skus = _keys(spine, twin)
    twin_ids = [twin["id"]] if twin and twin.get("id") else []
    items = [
        g for g in {(plan or {}).get("inventory_item_gid")}
        | {r.get("shopify_inventory_item_id") for r in db["catalog_variants"].find({"sku": {"$in": skus}})}
        if g
    ]
    guard_q = {"$or": [{"_id": {"$regex": "^" + re.escape(g) + r"\|"}} for g in items]}
    docs: Dict[str, List[Dict]] = {
        "catalog_variants": list(db["catalog_variants"].find({"sku": {"$in": skus}})) if skus else [],
        "product_images": list(db["product_images"].find({"product_id": {"$in": ids}})),
        "vendor_sku_aliases": list(db["vendor_sku_aliases"].find({"product_id": {"$in": ids}})),
        "collection_products": list(
            db["collection_products"].find(
                {"$or": [{"product_id": {"$in": ids}}, {"sku": {"$in": skus}}]}
            )
        ),
        "shopify_stock_write_guard": list(db["shopify_stock_write_guard"].find(guard_q)) if items else [],
        "catalog_products": list(db["catalog_products"].find({"id": {"$in": twin_ids}})),
        "products": list(db["products"].find({"product_id": pid})),
    }
    parent_id = (plan or {}).get("parent_twin_id") if (plan or {}).get("kind") == "variant" else None
    parent = db["catalog_products"].find_one({"id": parent_id}) if parent_id else None
    updated: Dict[str, List[Dict]] = {
        "ecom_collections": list(db["ecom_collections"].find({"products.sku": {"$in": skus}})) if skus else [],
        "tasks": list(db["tasks"].find({"payload.product_ids": pid})),
        "catalog_products": [parent] if parent else [],
    }
    return {"docs": docs, "updated": updated, "skus": skus}


def _claim(db, pid: str, twin_id: Optional[str], run_id: str) -> Optional[Tuple[int, str]]:
    """Take the purge claim: the SPINE is the lock (exclusive; a live claim
    of another run refuses, only a dead one older than CLAIM_STALE is taken
    over; Restore's spine write needs it free), then the twin, taken OFF the
    auto-sync queue so the 01:00 / 09:00 sync cannot re-create it mid-delete.
    The queue flag it had BEFORE any purge touched it is kept on the twin
    (purge_prior_queue), so a take-over never gives back a stale False."""
    from pymongo import ReturnDocument

    now = _now()
    spine = db["products"].find_one_and_update(
        {"product_id": pid, "deleted_at": _ARCHIVED, **_free_claim()},
        {"$set": {"purge_run": run_id, "purge_run_at": now}},
        return_document=ReturnDocument.BEFORE,
    )
    if spine is None:
        held = db["products"].find_one({"product_id": pid}) or {}
        if not held.get("deleted_at"):
            return 409, ARCHIVE_FIRST
        return (409, RUNNING) if held.get("purge_run") else (409, CHANGED_MEANWHILE)
    if not twin_id:
        return None
    before = db["catalog_products"].find_one_and_update(
        {"id": twin_id, "deleted_at": _ARCHIVED, **_free_claim()},
        {"$set": {"purge_run": run_id, "purge_run_at": now}},
        return_document=ReturnDocument.BEFORE,
    )
    if before is None:
        _release(db, pid, None, run_id)
        held = db["catalog_products"].find_one({"id": twin_id}) or {}
        return (409, RUNNING) if held.get("purge_run") and held.get("deleted_at") else (409, CHANGED_MEANWHILE)
    ecom = before.get("ecom")
    if isinstance(ecom, dict):
        prior = before.get("purge_prior_queue", ecom.get("locally_modified"))
        db["catalog_products"].update_one(
            {"id": twin_id, "purge_run": run_id},
            {"$set": {"ecom.locally_modified": False, "purge_prior_queue": prior}},
        )
    return None


def _release(db, pid: str, twin_id: Optional[str], run_id: str) -> None:
    """Give the claim back (only our own), and the twin its queue flag."""
    try:
        db["products"].update_one(
            {"product_id": pid, "purge_run": run_id}, {"$set": {"purge_run": None, "purge_run_at": None}}
        )
        if twin_id:
            twin = db["catalog_products"].find_one({"id": twin_id, "purge_run": run_id})
            if twin is not None:
                patch: Dict[str, Any] = {"$unset": {"purge_run": "", "purge_run_at": "", "purge_prior_queue": ""}}
                if isinstance(twin.get("ecom"), dict) and "purge_prior_queue" in twin:
                    patch["$set"] = {"ecom.locally_modified": twin["purge_prior_queue"]}
                db["catalog_products"].update_one({"id": twin_id, "purge_run": run_id}, patch)
    except Exception:  # noqa: BLE001
        logger.warning("[PURGE] could not release the claim on %s", pid, exc_info=True)


def _still_ours(db, pid: str, twin_id: Optional[str], run_id: str) -> bool:
    """The claim is still this run's and both rows are still archived."""
    if db["products"].find_one({"product_id": pid, "purge_run": run_id, "deleted_at": _ARCHIVED}) is None:
        return False
    return not twin_id or db["catalog_products"].find_one(
        {"id": twin_id, "purge_run": run_id, "deleted_at": _ARCHIVED}
    ) is not None


def _forget_shopify(db, twin_id: Optional[str], plan: Dict) -> None:
    """The listing (or variant) is gone from Shopify but the IMS delete was
    refused afterwards: drop the dead Shopify ids so the archived product
    reads "not on the website" (a later push or restore never aims at them)."""
    if twin_id:
        gone = {"ecom.shopify_variant_id": "", "ecom.shopify_inventory_item_id": ""}
        if plan.get("kind") == "product":
            gone["ecom.shopify_product_id"] = ""
        db["catalog_products"].update_one({"id": twin_id}, {"$unset": gone})
    if plan.get("sku"):
        db["catalog_variants"].update_one(
            {"sku": plan["sku"]}, {"$unset": {"shopify_variant_id": "", "shopify_inventory_item_id": ""}}
        )


def _refused(code: int, reason: str, **extra: Any) -> Dict[str, Any]:
    return {"status": "refused", "code": code, "reason": reason, **extra}


def _prepare(db, pid: str, twin_id: Optional[str], run_id: str, backup_id: str, actor: Dict):
    """Steps 1-3 (sync, off the loop): claim, fresh check, backup. Returns
    (refusal_row, plan, spine, twin, found)."""
    try:
        refused = _claim(db, pid, twin_id, run_id)
    except Exception:  # noqa: BLE001
        logger.error("[PURGE] claim failed for %s", pid, exc_info=True)
        return _refused(503, UNREADABLE), None, None, None, None
    if refused is not None:
        return _refused(*refused), None, None, None, None
    try:
        done = _website_done(db, pid)
        again, plan, spine, twin = _fresh(db, pid, twin_id, website_done=done)
    except Exception:  # noqa: BLE001
        logger.error("[PURGE] re-check failed for %s", pid, exc_info=True)
        _release(db, pid, twin_id, run_id)
        return _refused(503, UNREADABLE), None, None, None, None
    if again is not None:
        _release(db, pid, twin_id, run_id)
        return _refused(*again, website=plan is not None), plan, None, None, None
    try:
        found = _gather(db, spine, twin, plan)
        db[BACKUP_COLLECTION].insert_one(
            {
                "backup_id": backup_id,
                "run_id": run_id,
                "product_id": pid,
                "sku": spine.get("sku"),
                "actor": actor.get("user_id"),
                "deleted_at": _now(),
                "status": "removing" if plan else "deleting",
                "docs": found["docs"],
                "updated_docs": found["updated"],
                "shopify": {"plan": plan} if plan else None,
            }
        )
    except Exception:  # noqa: BLE001
        logger.error("[PURGE] backup failed for %s", pid, exc_info=True)
        _release(db, pid, twin_id, run_id)
        return _refused(503, BACKUP_FAILED, website=plan is not None), plan, None, None, None
    return None, plan, spine, twin, found


def _mark(db, backup_id: str, status: str, **extra: Any) -> None:
    try:
        db[BACKUP_COLLECTION].update_one({"backup_id": backup_id}, {"$set": {"status": status, **extra}})
    except Exception:  # noqa: BLE001
        logger.warning("[PURGE] backup %s not marked %s", backup_id, status, exc_info=True)


def _abort(db, pid: str, twin_id: Optional[str], run_id: str, backup_id: str, **extra: Any) -> None:
    _mark(db, backup_id, "aborted", **extra)
    _release(db, pid, twin_id, run_id)


def _before_shopify(db, pid: str, twin_id: Optional[str], run_id: str, plan: Dict) -> Optional[Tuple[int, str]]:
    """Immediately before the Shopify call: the claim is still ours, both rows
    still archived, and a fresh check gives the SAME plan."""
    if not _still_ours(db, pid, twin_id, run_id):
        return 409, CHANGED_MEANWHILE
    again, fresh_plan, _, _ = _fresh(db, pid, twin_id, website_done=False)
    if again is not None:
        return again
    if fresh_plan != plan:
        return 409, CHANGED_MEANWHILE
    return None


def _after_shopify(db, pid: str, twin_id: Optional[str], run_id: str, backup_id: str,
                   spine: Dict, twin: Optional[Dict], plan: Optional[Dict], found: Dict):
    """Steps 5-6 (sync, off the loop): check again, gather again (anything
    new goes into the backup too), change, delete. Returns a refusal row or
    None plus the docs deleted."""
    again, _, _, _ = _fresh(db, pid, twin_id, website_done=bool(plan) or _website_done(db, pid))
    if again is None and not _still_ours(db, pid, twin_id, run_id):
        again = (409, CHANGED_MEANWHILE)
    if again is not None:
        return again, None
    fresh = _gather(db, spine, twin, plan)
    for part in ("docs", "updated"):
        for coll, rows in fresh[part].items():
            have = {r["_id"] for r in found[part].get(coll, [])}
            new = [r for r in rows if r["_id"] not in have]
            if new:
                db[BACKUP_COLLECTION].update_one(
                    {"backup_id": backup_id},
                    {"$push": {f"{'docs' if part == 'docs' else 'updated_docs'}.{coll}": {"$each": new}}},
                )
    docs, updated, skus = fresh["docs"], fresh["updated"], fresh["skus"]
    if updated["ecom_collections"]:
        db["ecom_collections"].update_many(
            {"_id": {"$in": [c["_id"] for c in updated["ecom_collections"]]}},
            {"$pull": {"products": {"sku": {"$in": skus}}}},
        )
    if updated["tasks"]:
        db["tasks"].update_many(
            {"_id": {"$in": [t["_id"] for t in updated["tasks"]]}},
            {"$pull": {"payload.product_ids": pid}},
        )
    for parent in updated["catalog_products"]:
        db["catalog_products"].update_one(
            {"_id": parent["_id"]},
            {"$unset": {f"ecom.online_stock.quantities.{s}": "" for s in skus}},
        )
    for coll, rows in docs.items():  # dict order: the spine last
        if not rows:
            continue
        flt: Dict[str, Any] = {"_id": {"$in": [r["_id"] for r in rows]}}
        if coll in ("catalog_products", "products"):
            # Only while still archived AND still this run's (a Restore or
            # another run that got in has the row now).
            flt["deleted_at"] = _ARCHIVED
            flt["purge_run"] = run_id
        if db[coll].delete_many(flt).deleted_count != len(rows) and coll in (
            "catalog_products",
            "products",
        ):
            raise RuntimeError(f"{coll}: not every row was still archived and claimed")
    return None, docs


def _finish(db, pid: str, spine: Dict, twin_id: Optional[str], backup_id: str, run_id: str,
            plan: Optional[Dict], shopify: Optional[Dict], docs: Dict, actor: Dict, audit_repo) -> None:
    """Step 7 (sync): every open backup of it done, the model's options, the
    audit row."""
    try:
        db[BACKUP_COLLECTION].update_many(
            {"product_id": pid, "status": {"$in": list(OPEN_STATES)}}, {"$set": {"status": "done"}}
        )
    except Exception:  # noqa: BLE001
        logger.warning("[PURGE] backup %s not marked done", backup_id, exc_info=True)
    lead_id = spine.get("variant_of")
    if lead_id:
        try:
            from .product_master import refresh_family_options

            refresh_family_options(db, lead_id)
        except Exception:  # noqa: BLE001
            logger.warning("[PURGE] model options not refreshed for %s", lead_id, exc_info=True)
    plan = plan or {}
    removed = [
        g
        for g in (
            plan.get("product_gid") if plan.get("kind") == "product" else None,
            plan.get("variant_gid") if plan.get("kind") == "variant" else None,
        )
        if g
    ]
    try:
        if audit_repo is not None:
            from .name_resolver import actor_name

            now = _now()
            audit_repo.create(
                {
                    "action": "product.deleted_permanently",
                    "actor": actor.get("user_id"),
                    "user_id": actor.get("user_id"),
                    "actor_name": actor_name(db, actor),
                    "entity_type": "product",
                    "entity_id": pid,
                    "timestamp": now,
                    "ts": now.isoformat(),
                    "detail": {
                        "sku": spine.get("sku"),
                        "twin_id": twin_id,
                        "backup_id": backup_id,
                        "run_id": run_id,
                        "deleted": {c: len(r) for c, r in docs.items()},
                        "shopify_removed": removed,
                        "shopify_action": ((shopify or {}).get("result") or {}).get("action"),
                    },
                }
            )
    except Exception:  # noqa: BLE001 - the audit never undoes the delete
        logger.warning("[PURGE] audit row failed for %s", pid, exc_info=True)


async def purge(
    db,
    pid: str,
    twin_id: Optional[str],
    actor: Dict,
    *,
    run_id: str,
    audit_repo=None,
) -> Dict[str, Any]:
    """Delete one product for good: {status: done, backup_id, website} or
    {status: refused, code, reason, website}. Checks everything again itself.
    Every Mongo step runs off the event loop (asyncio.to_thread), so a long
    bulk run never freezes the worker serving the tills. Never raises."""
    from . import shopify_push

    backup_id = f"DPB-{uuid.uuid4().hex[:12].upper()}"
    refused, plan, spine, twin, found = await asyncio.to_thread(
        _prepare, db, pid, twin_id, run_id, backup_id, actor
    )
    if refused is not None:
        return refused

    shopify: Optional[Dict[str, Any]] = None
    if plan:
        try:
            moved = await asyncio.to_thread(_before_shopify, db, pid, twin_id, run_id, plan)
        except Exception:  # noqa: BLE001
            moved = (503, UNREADABLE)
        if moved is not None:
            await asyncio.to_thread(_abort, db, pid, twin_id, run_id, backup_id)
            return _refused(*moved, website=True)
        try:
            res = await shopify_push.push_product_remove(db, plan, target_id=pid, product=twin)
        except Exception as exc:  # noqa: BLE001 -- it never raises; nothing deleted if it does
            logger.error("[PURGE] website removal raised for %s", pid, exc_info=True)
            await asyncio.to_thread(_abort, db, pid, twin_id, run_id, backup_id)
            return _refused(502, f"The website (Shopify) did not remove it, so it stays archived: {exc}",
                            website=True)
        shopify = {"plan": plan, "result": res.to_dict()}
        if res.mode != shopify_push.MODE_LIVE:
            await asyncio.to_thread(_abort, db, pid, twin_id, run_id, backup_id, shopify=shopify)
            return _refused(409, WEBSITE_DARK, website=True)  # the gate closed after the check
        if not res.ok:
            await asyncio.to_thread(_abort, db, pid, twin_id, run_id, backup_id, shopify=shopify)
            return _refused(
                502, f"The website (Shopify) did not remove it, so it stays archived: {res.error}",
                website=True,
            )
        await asyncio.to_thread(_mark, db, backup_id, "deleting", shopify=shopify)

    try:
        again, docs = await asyncio.to_thread(
            _after_shopify, db, pid, twin_id, run_id, backup_id, spine, twin, plan, found
        )
    except Exception:  # noqa: BLE001
        logger.error("[PURGE] delete stopped part-way for %s", pid, exc_info=True)
        # The backup stays 'deleting' (Restore refuses it); the claim is
        # given back so pressing again can finish at once.
        await asyncio.to_thread(_release, db, pid, twin_id, run_id)
        return _refused(503, PART_DELETED, website=bool(plan))
    if again is not None:
        def _keep() -> None:
            if plan:
                try:
                    _forget_shopify(db, twin_id, plan)
                except Exception:  # noqa: BLE001
                    logger.warning("[PURGE] dead Shopify ids kept on %s", pid, exc_info=True)
            _abort(db, pid, twin_id, run_id, backup_id)

        await asyncio.to_thread(_keep)
        note = " It was already taken off the website." if plan else ""
        return _refused(again[0], again[1] + note, website=bool(plan))
    await asyncio.to_thread(
        _finish, db, pid, spine, twin_id, backup_id, run_id, plan, shopify, docs, actor, audit_repo
    )
    return {"status": "done", "backup_id": backup_id, "website": bool(plan)}
