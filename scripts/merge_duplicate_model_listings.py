#!/usr/bin/env python3
"""
IMS 2.0 - One model, ONE website listing: fold a duplicate listing in (G2)
=========================================================================
Runbook-only script. NOT in CI. ASCII only (Windows cp1252). Owner ruling
2026-10-18: same model number in other colours / sizes = variants of ONE
product on the website.

THE CASE. Two colours of one model went to the website as two listings
before models existed (prod 2026-10-18: Carrera CARDUC 038/S, and
CA PRW 5/S/IN). G1 (#1226) cannot link them: a product with its own live
listing is refused by product_master.link_to_family.

WHAT --apply DOES, per model with two or more listings:
  1. KEEPS the listing of the FIRST catalogued item (the model's lead).
  2. For every other listed item, in this order, stopping that item at the
     first step that fails (nothing after it runs):
       a. reads both listings' real handles from Shopify;
       b. takes the other listing DOWN to Draft (push_product_delist --
          the Shopify product is NEVER deleted, its id is kept);
       c. creates a URL redirect /products/<old handle> -> /products/<kept
          handle> (urlRedirectCreate; one already there counts as done), so
          old links and Google keep landing on the model;
       d. backs up every IMS document it is about to write
          (`model_listing_merge_backup`, one run id -- --revert restores);
       e. releases the old listing in IMS: its Shopify ids move to
          ecom.former_listing on the item's twin (kept for the record, read
          by nothing), its catalog_variants row drops the old variant's ids;
       f. links the item to the kept model (product_master.link_to_family)
          and queues the kept listing -- its colour is ADDED to the kept
          listing by the next press or scheduled sync (shopify_push.listing).
  A model whose first catalogued item is not one of the listed ones, or
  whose items lead variants of their own, is reported and left alone.

DRY-RUN (DEFAULT): prints the plan from IMS alone -- NO Shopify call, NO
write. --apply refuses unless the Shopify push is LIVE (IMS_SHOPIFY_WRITES +
SHOPIFY_DISPATCH_MODE=live + credentials): a dark take-down would change IMS
while the website stayed as it was.

USAGE
    railway run --service MongoDB -- ".venv\\Scripts\\python.exe" scripts/merge_duplicate_model_listings.py
    ... scripts/merge_duplicate_model_listings.py --apply
    ... scripts/merge_duplicate_model_listings.py --revert [--run-id <id>]   (IMS only)
        puts back ONLY the fields --apply wrote; refuses (and lists them) when
        any of those fields was changed after the apply
        refuses (and lists them) when the kept listing already sells a folded
        colour: add --take-down-added (off sale first, needs a LIVE push) or
        --leave-added-unmanaged
  An item marked 'This is a different model' is never folded (shown in the
  dry run; its flag is never cleared).
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
)
from api.services import product_master as pm  # noqa: E402

BACKUP = "model_listing_merge_backup"
_HANDLE_QUERY = """
query imsListingHandle($id: ID!) {
  product(id: $id) { id handle status }
}
"""
_ENGINE_ROW_FIELDS = ("shopify_variant_id", "shopify_inventory_item_id", "discounted_price", "compare_at_price")


def _twin(db, spine: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    cat = db["catalog_products"]
    return cat.find_one({"id": spine.get("pim_product_id") or spine.get("product_id")}) or (
        cat.find_one({"sku": spine["sku"]}) if spine.get("sku") else None
    )


def plan(db) -> Dict[str, Any]:
    """Every model with two or more website listings, and what --apply does."""
    groups: Dict[Any, List[Dict[str, Any]]] = {}
    for spine in db["products"].find({}):
        if spine.get("deleted_at") or not spine.get("sku"):
            continue
        key = pm.family_key(spine)
        if key is not None:
            groups.setdefault(key, []).append(spine)
    report: Dict[str, Any] = {"merges": [], "left": []}
    for key, items in groups.items():
        label = " ".join(str(k) for k in key)
        listed = []
        for spine in items:
            twin = _twin(db, spine)
            ecom = (twin or {}).get("ecom") or {}
            if not (ecom.get("shopify_product_id") and not ecom.get("variant_of")):
                continue
            if spine.get("family_opt_out"):
                # 'This is a different model' (a person said so): its listing
                # is its own -- never folded, and the flag is never cleared.
                report["left"].append({
                    "model": label,
                    "reason": f"{spine.get('sku')} is marked 'This is a different model' -- its listing stays its own",
                })
                continue
            listed.append((spine, twin))
        if len(listed) < 2:
            continue
        keep_spine, keep_twin = min(listed, key=lambda st: pm.lead_order(st[0]))
        lead = pm.lead_of(items)
        if lead is not None and lead.get("product_id") != keep_spine.get("product_id"):
            report["left"].append(
                {"model": label, "reason": f"the model's lead {lead.get('sku')} is not one of its listings -- a person decides"}
            )
            continue
        folds = []
        for spine, twin in listed:
            if spine is keep_spine:
                continue
            if db["products"].find_one({"variant_of": spine.get("product_id")}) is not None:
                report["left"].append(
                    {"model": label, "reason": f"{spine.get('sku')} leads colours of its own -- a person decides"}
                )
                continue
            folds.append(
                {
                    "product_id": spine.get("product_id"),
                    "sku": spine.get("sku"),
                    "twin_id": twin.get("id"),
                    "shopify_product_id": twin["ecom"]["shopify_product_id"],
                    "handle": twin["ecom"].get("handle"),
                }
            )
        if folds:
            report["merges"].append(
                {
                    "model": label,
                    "keep": {
                        "product_id": keep_spine.get("product_id"),
                        "sku": keep_spine.get("sku"),
                        "twin_id": keep_twin.get("id"),
                        "shopify_product_id": keep_twin["ecom"]["shopify_product_id"],
                        "handle": keep_twin["ecom"].get("handle"),
                    },
                    "fold": folds,
                }
            )
    return report


async def _handle_of(db, gid: str) -> Optional[str]:
    from api.services import shopify_push

    body = await shopify_push._graphql(db, _HANDLE_QUERY, {"id": gid})
    return (((body or {}).get("data") or {}).get("product") or {}).get("handle")


async def _redirect(db, old_handle: str, keep_handle: str) -> Optional[str]:
    """The redirect, or the reason it failed. One already there counts as done."""
    from api.services import shopify_push
    from api.services.shopify_push.queries import _URL_REDIRECT_CREATE

    body = await shopify_push._graphql(
        db, _URL_REDIRECT_CREATE, {"urlRedirect": {"path": f"/products/{old_handle}", "target": f"/products/{keep_handle}"}}
    )
    errors = (((body or {}).get("data") or {}).get("urlRedirectCreate") or {}).get("userErrors") or []
    if (body or {}).get("errors"):
        return str(body["errors"])[:300]
    # Shopify answers a path that already redirects with CREATE_FAILED
    # "Path has already been taken": a re-run, the redirect is there.
    hard = [
        e
        for e in errors
        if not any(t in str((e or {}).get("message") or "").lower() for t in ("already been taken", "already exists"))
    ]
    return "; ".join(str(e.get("message")) for e in hard) or None


_MISSING = {"__missing__": True}
# Queue bookkeeping is never a field a revert puts back or checks: a later
# press re-queues and clears it on its own.
_NOT_RESTORED = {"updated_at", "ecom.edit_rev", "ecom.locally_modified"}
# What a later push of the KEPT listing writes onto a folded colour's row
# when it adds the colour there (G2): put back with --take-down-added /
# --leave-added-unmanaged, never compared.
_ROW_LISTING_FIELDS = (
    "parent_product_id", "parent_sku", "option_color", "option_size",
    "shopify_variant_id", "shopify_inventory_item_id",
    "discounted_price", "compare_at_price", "online_price_meta",
)


def _snapshot(db, collection: str, flt: Dict[str, Any]) -> Dict[str, Any]:
    doc = db[collection].find_one(flt)
    doc = copy.deepcopy(doc) if doc is not None else {}
    doc.pop("_id", None)
    return doc


def _flat(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Top-level fields, and the ecom sub-doc's one level down ('ecom.X')."""
    out = {k: v for k, v in doc.items() if k != "ecom"}
    for k, v in (doc.get("ecom") or {}).items():
        out[f"ecom.{k}"] = v
    return out


def _written(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """The fields --apply wrote on one document: {path: {before, after}}
    (_MISSING when absent)."""
    b, a = _flat(before), _flat(after)
    return {
        path: {"before": b.get(path, _MISSING), "after": a.get(path, _MISSING)}
        for path in sorted(set(b) | set(a))
        if path not in _NOT_RESTORED and b.get(path, _MISSING) != a.get(path, _MISSING)
    }


def _record_fold(db, run_id: str, fold: Dict[str, Any], keep: Dict[str, Any], before: Dict[str, Any]) -> None:
    """One record per folded colour: exactly the fields --apply wrote on its
    twin, row and spine (before and after), and its row as it was (the
    listing fields a later push moves)."""
    docs = []
    for collection, flt in before["filters"]:
        after = _snapshot(db, collection, flt)
        docs.append({"collection": collection, "filter": flt,
                     "fields": _written(before["docs"][collection], after)})
    row = before["docs"]["catalog_variants"]
    db[BACKUP].insert_one({
        "run_id": run_id,
        "at": datetime.now(timezone.utc),
        "fold": {"sku": fold["sku"], "keep_twin_id": keep["twin_id"],
                 "keep_shopify_product_id": keep["shopify_product_id"]},
        "docs": docs,
        "row_before": {k: row.get(k, _MISSING) for k in _ROW_LISTING_FIELDS},
    })


async def apply(db, report: Dict[str, Any], *, actor: str = "runbook:merge_duplicate_model_listings") -> Dict[str, Any]:
    """--apply. Refuses unless the push is LIVE. Returns {run_id, merged, stopped}."""
    from api.services import shopify_push

    live, reason = shopify_push._live_or_reason(db)
    if not live:
        raise SystemExit(f"Refused: the Shopify push is not LIVE ({reason}) -- nothing was changed.")
    run_id = uuid.uuid4().hex
    out: Dict[str, Any] = {"run_id": run_id, "merged": [], "stopped": []}
    for merge in report["merges"]:
        keep = merge["keep"]
        try:
            keep_handle = await _handle_of(db, keep["shopify_product_id"])
        except Exception as exc:  # noqa: BLE001
            keep_handle, why = None, str(exc)
        else:
            why = "Shopify returned no handle"
        if not keep_handle:
            out["stopped"].append({"model": merge["model"], "sku": keep["sku"], "step": "read kept handle", "why": why})
            continue
        for fold in merge["fold"]:
            step = "read handle"
            try:
                old_handle = await _handle_of(db, fold["shopify_product_id"])
                if not old_handle:
                    raise RuntimeError("Shopify returned no handle")
                step = "take down"
                twin = db["catalog_products"].find_one({"id": fold["twin_id"]})
                down = await shopify_push.push_product_delist(db, twin)
                if not (down.ok and down.mode == shopify_push.MODE_LIVE):
                    raise RuntimeError(down.error or down.reason or "not taken down")
                step = "redirect"
                err = await _redirect(db, old_handle, keep_handle)
                if err:
                    raise RuntimeError(err)
                step = "release in IMS"
                filters = (
                    ("catalog_products", {"id": fold["twin_id"]}),
                    ("catalog_variants", {"sku": fold["sku"]}),
                    ("products", {"product_id": fold["product_id"]}),
                )
                before = {"filters": filters, "docs": {c: _snapshot(db, c, f) for c, f in filters}}
                twin = db["catalog_products"].find_one({"id": fold["twin_id"]}) or {}
                ecom = dict(twin.get("ecom") or {})
                former = {k: ecom.pop(k) for k in list(pm.LISTING_FIELDS) + list(pm._VARIANT_IDS) if k in ecom}
                former.update({"handle": old_handle, "redirected_to": keep_handle, "merged_at": datetime.now(timezone.utc)})
                ecom["former_listing"] = former
                db["catalog_products"].update_one({"id": fold["twin_id"]}, {"$set": {"ecom": ecom}})
                db["catalog_variants"].update_one(
                    {"sku": fold["sku"]}, {"$unset": {f: "" for f in _ENGINE_ROW_FIELDS}}
                )
                step = "link to the model"
                pm.link_to_family(db, fold["product_id"], keep["product_id"], actor=actor)
                _record_fold(db, run_id, fold, keep, before)
                out["merged"].append({"model": merge["model"], "sku": fold["sku"], "into": keep["sku"]})
            except Exception as exc:  # noqa: BLE001 -- stop this item, report, go on
                out["stopped"].append({"model": merge["model"], "sku": fold["sku"], "step": step, "why": str(exc)})
    return out


def _latest_run(db, run_id: Optional[str]) -> Optional[str]:
    if run_id is not None:
        return run_id
    last = db[BACKUP].find_one({"reverted_at": {"$exists": False}}, sort=[("at", -1)])
    return last["run_id"] if last else None


def _records(db, run_id: str) -> List[Dict[str, Any]]:
    return [b for b in db[BACKUP].find({"run_id": run_id, "reverted_at": {"$exists": False}}) if b.get("fold")]


def added_variants(db, run_id: str) -> List[Dict[str, Any]]:
    """The folded colours the KEPT listing already sells as its own variant
    (a push after the apply added them). A revert puts the colour back on its
    old (drafted) listing in IMS, so such a variant would be left on the kept
    listing with nothing in IMS managing it."""
    out = []
    for rec in _records(db, run_id):
        fold = rec["fold"]
        row = db["catalog_variants"].find_one({"sku": fold["sku"]}) or {}
        if row.get("parent_product_id") == fold["keep_twin_id"] and row.get("shopify_variant_id"):
            out.append({**fold, "shopify_variant_id": row["shopify_variant_id"],
                        "shopify_inventory_item_id": row.get("shopify_inventory_item_id")})
    return out


def changed_since_apply(db, run_id: str, *, added_skus=()) -> List[str]:
    """Every field --apply wrote that holds something else now ("sku:
    collection.field") -- a person's (or another door's) later edit a revert
    would wipe. A folded colour the kept listing now sells (``added_skus``)
    has its row's listing fields moved by that push: those are not counted."""
    changed = []
    for rec in _records(db, run_id):
        sku = rec["fold"]["sku"]
        for doc in rec["docs"]:
            now = _flat(_snapshot(db, doc["collection"], doc["filter"]))
            for path, vals in doc["fields"].items():
                if sku in added_skus and doc["collection"] == "catalog_variants" and path in _ROW_LISTING_FIELDS:
                    continue
                if now.get(path, _MISSING) != vals["after"]:
                    changed.append(f"{sku}: {doc['collection']}.{path}")
    return changed


def _put_back(db, collection: str, flt: Dict[str, Any], values: Dict[str, Any]) -> None:
    sets = {p: v for p, v in values.items() if v != _MISSING}
    unsets = {p: "" for p, v in values.items() if v == _MISSING}
    update = {}
    if sets:
        update["$set"] = sets
    if unsets:
        update["$unset"] = unsets
    if update:
        db[collection].update_one(flt, update)


async def _take_down_variant(db, added: Dict[str, Any]) -> Optional[str]:
    """Take ONE added variant off sale on the kept listing (stop selling at 0
    + quantity 0 at every mapped shop) -- never deleted. The error, or None."""
    from api.services import shopify_push
    from api.services.shopify_push.inventory import _set_variant_tracking, stock_write_ticket
    from api.services.stores_util import physical_stores

    tracked = await _set_variant_tracking(db, added["keep_shopify_product_id"], [added["shopify_variant_id"]], "DENY")
    if tracked.get("errors"):
        return "; ".join(str(e) for e in tracked["errors"])
    mapped = shopify_push.mapped_store_locations(physical_stores(db))
    out = await shopify_push.push_skus_stock(
        db, [added["sku"]], quantities={added["sku"]: {sid: 0 for sid in mapped}},
        source="merge_revert", product_id=added["keep_twin_id"], policy="DENY", tracked=None,
        ticket=stock_write_ticket(db),
    )
    return out.get("error") if out.get("errors") else None


async def revert(
    db, run_id: Optional[str] = None, *, take_down_added: bool = False, leave_added: bool = False
) -> Dict[str, Any]:
    """Puts back ONLY the fields --apply wrote (the drafted listing stays
    Draft on Shopify, the redirect stays); every later edit -- the kept
    item's price, name, photos, tags, stock record, the folded item's price
    -- is left exactly as it is (review of #1233).

    REFUSES, listing why, when:
      * a field --apply wrote holds something else now (a later edit the
        revert would wipe) -- nothing is changed;
      * the kept listing already sells a folded colour as its own variant --
        unless --take-down-added (that variant is taken off sale first: stop
        at 0, quantity 0, never deleted; needs a LIVE push) or
        --leave-added-unmanaged (a person accepts it). The colour's row is
        then put back on its own listing too."""
    run_id = _latest_run(db, run_id)
    if run_id is None:
        return {"run_id": None, "restored": 0}
    added = added_variants(db, run_id)
    if added and not (take_down_added or leave_added):
        return {"run_id": run_id, "restored": 0, "refused": True, "added": added}
    changed = changed_since_apply(db, run_id, added_skus={a["sku"] for a in added})
    if changed:
        return {"run_id": run_id, "restored": 0, "refused": True, "added": added, "changed": changed,
                "why": "these fields were changed after the apply -- nothing was reverted: " + ", ".join(changed)}
    taken = []
    if added and take_down_added:
        from api.services import shopify_push

        live, reason = shopify_push._live_or_reason(db)
        if not live:
            return {"run_id": run_id, "restored": 0, "refused": True, "added": added,
                    "why": f"the Shopify push is not LIVE ({reason}) -- nothing was changed"}
        for a in added:
            err = await _take_down_variant(db, a)
            if err:
                return {"run_id": run_id, "restored": 0, "refused": True, "added": added, "taken_down": taken,
                        "why": f"{a['sku']}: the take-down failed ({err}) -- nothing was restored"}
            taken.append(a["sku"])
    added_skus = {a["sku"] for a in added}
    restored = 0
    for rec in _records(db, run_id):
        for doc in rec["docs"]:
            values = {p: v["before"] for p, v in doc["fields"].items()}
            if doc["collection"] == "catalog_variants" and rec["fold"]["sku"] in added_skus:
                values.update(rec.get("row_before") or {})
            _put_back(db, doc["collection"], doc["filter"], values)
            restored += len(values)
    db[BACKUP].update_many({"run_id": run_id}, {"$set": {"reverted_at": datetime.now(timezone.utc)}})
    return {"run_id": run_id, "restored": restored, "taken_down": taken,
            "left_unmanaged": [a["sku"] for a in added] if added and not take_down_added else []}


def print_report(report: Dict[str, Any], mode: str) -> None:
    print(f"[{mode}] models with two or more website listings: {len(report['merges']) + len(report['left'])}")
    for m in report["merges"]:
        k = m["keep"]
        print(f"  {m['model']}: KEEP {k['sku']} ({k['shopify_product_id']}, handle {k['handle']})")
        for f in m["fold"]:
            print(
                f"      fold {f['sku']} ({f['shopify_product_id']}): take down to Draft, redirect "
                f"/products/{f['handle']} -> /products/{k['handle']} (handles re-read from Shopify on "
                f"--apply), add its colour to the kept listing"
            )
    for left in report["left"]:
        print(f"  LEFT ALONE {left['model']}: {left['reason']}")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Fold duplicate model listings into one. Dry-run by default.")
    parser.add_argument("--mongo-uri", default=None)
    parser.add_argument("--db", default=os.getenv("MONGO_DATABASE", "ims_2_0"))
    parser.add_argument("--apply", action="store_true", help="Take down, redirect, link (needs a LIVE push).")
    parser.add_argument("--revert", action="store_true", help="Undo an applied run in IMS.")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--take-down-added", action="store_true",
                        help="With --revert: take the folded colours the kept listing already sells off sale first.")
    parser.add_argument("--leave-added-unmanaged", action="store_true",
                        help="With --revert: restore anyway, leaving those variants on the kept listing unmanaged.")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from backfill_reorder_quantity_minus1 import resolve_mongo_uri  # one resolver
    from pymongo import MongoClient

    uri = resolve_mongo_uri(args.mongo_uri)
    if not uri:
        raise SystemExit("No Mongo connection: pass --mongo-uri or use `railway run`.")
    db = MongoClient(uri, serverSelectionTimeoutMS=10000)[args.db]
    if args.revert:
        out = asyncio.run(revert(db, args.run_id, take_down_added=args.take_down_added,
                                 leave_added=args.leave_added_unmanaged))
        if out.get("refused"):
            print("[REVERT] REFUSED -- nothing was changed.")
            for a in out.get("added") or []:
                print(f"    the kept listing already sells {a['sku']}: variant {a['shopify_variant_id']} "
                      f"on {a['keep_shopify_product_id']}")
            for c in out.get("changed") or []:
                print(f"    changed after the apply (a revert would wipe it): {c}")
            print("    " + (out.get("why") or "re-run with --take-down-added (recommended: off sale, never deleted) "
                                              "or --leave-added-unmanaged"))
        else:
            print(f"[REVERT] {out}")
        return 0
    report = plan(db)
    mode = "APPLY" if args.apply else "DRY-RUN"
    print_report(report, mode)
    if args.apply:
        print(f"[APPLY] {asyncio.run(apply(db, report))}")
    else:
        print("[DRY-RUN] nothing written, nothing sent to Shopify; re-run with --apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
