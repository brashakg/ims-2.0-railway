"""Clean a brand typed inside the model number, and archive the twins it hid.

WHY
---
The duplicate guard is products.identity_key (unique index). Products whose
model_no had the brand typed in front ('CARRERA 3001/S') keyed
'carrera|carrera3001s|...' while the same frame re-entered as '3001/S' keyed
'carrera|3001s|...', so the index never fired and duplicates were saved
(prod 10-24: 7 such products; the Flag Lab 16 twins also break the website
sync with OPTION_CLASH). The identity rule now folds a leading brand /
sub-brand away (product_master.model_without_brand); this script brings the
STORED rows in line with it.

WHAT IT DOES
------------
  (a) Lists every product whose stored identity_key differs from the rule's
      because of a brand / sub-brand typed in front of the model, with the
      cleaned model_no and the new key. Keys that differ for any OTHER reason
      are reported and left alone.
  (b) Finds every duplicate PAIR the new rule exposes: the new key already
      held by another row, or -- an eye-size item -- held by the same frame
      catalogued with no eye size (its key lacks the size, so it never
      collided). For each pair a SURVIVOR is picked by one rule
      (_survivor_score): never an archived row > on Shopify > named by any
      stock / order / purchase / receipt / return / transfer / workshop /
      bill / alias row (_REF_COLLECTIONS) > more photos > has a price >
      newer. The other row is archived only when it is not on Shopify
      (product, twin or a catalog_variants Shopify id), no such row names it
      (an unreadable collection counts as naming it) and it leads no linked
      variants; otherwise the pair is reported for a human. A row matched by
      more than one brand-in-model row (a size-less frame vs sizes 52 and 54)
      is reported too, and nothing in that group is written.
  (c) --apply: archives the losers FIRST (the archive door's own fields:
      is_active False, deleted_at, deleted_by, active_before_archive, on the
      spine and its catalogue twin; never a delete; the product-list cache
      dropped). A loser that holds the key its survivor needs gives it up
      ($unset identity_key): the unique index counts archived rows too, and
      leaving it there would leave the LIVE survivor on the stale key the
      guard and the similar-products lookup miss -- the bug itself. Restore
      (catalog _restore_refusal) refuses while a live row is the same product
      and re-keys a restored row, so a keyless duplicate never comes back.
      Then the survivor gets its model_no
      cleaned, the loser's eye size when it has none, and the rule's key; every
      other brand-in-model row is cleaned and re-keyed. Each changed document
      is backed up first in `fix_brand_in_model_backups` under one run id;
      --revert --run-id <id> puts every backed-up field back.

SKUs never change (owner ruling). Stored names are not touched: the minted
name already strips the brand from the model.

USAGE (dry run is the default; nothing is written without --apply)
    railway run --service MongoDB -- ".venv\\Scripts\\python.exe" backend/scripts/fix_brand_in_model.py
    railway run --service MongoDB -- ".venv\\Scripts\\python.exe" backend/scripts/fix_brand_in_model.py --apply
    railway run --service MongoDB -- ".venv\\Scripts\\python.exe" backend/scripts/fix_brand_in_model.py --revert --run-id <id>

Reads MONGO_PUBLIC_URL, else MONGO_URL; database MONGO_DATABASE (default
ims_2_0). Prints no connection string. No emoji (Windows cp1252).
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.services import product_master as pm  # noqa: E402

BACKUPS = "fix_brand_in_model_backups"
ACTOR = "script:fix_brand_in_model"
ACTIVE_BEFORE_ARCHIVE = "active_before_archive"  # routers/catalog.py's mark
_MISSING = {"__missing__": True}


def _clean_raw(raw: str, folded: str) -> Optional[str]:
    """`raw` from the first character on which it still normalises to the
    folded model, leading separators dropped: 'CARRERA 3001/S' -> '3001/S'.
    The same normaliser as the key, so the cleaned value keys the same."""
    for i in range(1, len(raw)):
        if pm.normalise_identity_component(raw[i:]) == folded:
            return raw[i:].lstrip(" -/_.\t")
    return None


def _ids(doc: Dict[str, Any]) -> List[str]:
    return [str(v) for v in {doc.get("product_id"), doc.get("pim_product_id")} if v]


def _twin(db, spine: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    ids = _ids(spine)
    return db["catalog_products"].find_one({"id": {"$in": ids}}) if ids else None


def _eye_size(doc: Dict[str, Any]) -> Any:
    return (doc.get("attributes") or {}).get("lens_size") or None


def _positive(v: Any) -> bool:
    try:
        return float(v or 0) > 0
    except (TypeError, ValueError):
        return False


# Every collection a row of which names a product (by id or SKU, flat or on
# its lines): a copy any of them names is never archived by a script.
_REF_COLLECTIONS = (
    "stock_units", "orders", "purchase_orders", "grns", "returns", "vendor_returns", "vendor_rmas",
    "stock_transfers", "workshop_jobs", "vendor_bills", "debit_notes", "vendor_debit_notes",
    "opening_stock_batches", "vendor_sku_aliases", "stock_counts", "stock_audit", "stock_shrinkage",
    "serial_numbers", "lens_stock_lines", "display_placements",
)
_LINE_PATHS = ("", "items.", "lines.", "line_items.")


def _refs(db, ids: List[str], sku: Any) -> List[str]:
    """The collections that name this product; one that cannot be read is
    named too (unreadable is never "no reference")."""
    terms: List[Dict[str, Any]] = [{p + "product_id": {"$in": ids}} for p in _LINE_PATHS]
    if sku:
        terms += [{p + "sku": sku} for p in _LINE_PATHS]
    out = []
    for coll in _REF_COLLECTIONS:
        try:
            if db[coll].count_documents({"$or": terms}):
                out.append(f"{coll} rows")
        except Exception as exc:  # noqa: BLE001 - refuse, never guess
            out.append(f"{coll} unreadable ({type(exc).__name__})")
    return out


def _facts(db, doc: Dict[str, Any]) -> Dict[str, Any]:
    """What decides which twin survives, and whether the other may go."""
    twin = _twin(db, doc)
    ids = _ids(doc) + ([str(twin["id"])] if twin and twin.get("id") else [])
    sku = doc.get("sku")
    on_shopify = False
    for d in (doc, twin or {}):
        ecom = d.get("ecom") if isinstance(d.get("ecom"), dict) else {}
        on_shopify = on_shopify or bool(ecom.get("shopify_product_id") or d.get("shopify_product_id"))
    variant_or = [{"parent_product_id": {"$in": ids}}] + ([{"parent_sku": sku}, {"sku": sku}] if sku else [])
    for v in db["catalog_variants"].find({"$or": variant_or}):
        on_shopify = on_shopify or bool(v.get("shopify_variant_id") or v.get("shopify_inventory_item_id"))
    refs = _refs(db, ids, sku)
    photos = max(
        db["product_images"].count_documents({"product_id": {"$in": ids}}),
        len((twin or {}).get("images") or doc.get("images") or []),
    )
    price = any(_positive(d.get(k)) for d in (doc, twin or {}) for k in ("mrp", "offer_price"))
    return {
        "twin": twin, "on_shopify": on_shopify, "refs": refs, "photos": photos, "price": price,
        "created": pm._created_sort_value(doc.get("created_at")),
        "created_at": str(doc.get("created_at") or "unknown")[:10],
        "variants": db["products"].count_documents({"variant_of": {"$in": ids}}),
        "archived": bool(doc.get("deleted_at")),
    }


def _survivor_score(f: Dict[str, Any]) -> tuple:
    """THE survivor rule: never an archived row > on Shopify > named by stock,
    orders, purchases, receipts, returns... > more photos > has a price >
    newer."""
    return (not f["archived"], f["on_shopify"], bool(f["refs"]), f["photos"], f["price"], f["created"])


def _why(f: Dict[str, Any]) -> str:
    bits = ["ARCHIVED"] if f["archived"] else []
    bits.append("on Shopify" if f["on_shopify"] else "not on Shopify")
    bits.append("named by " + ", ".join(f["refs"]) if f["refs"] else "named by no stock, order, purchase or return")
    bits.append(f"{f['photos']} photo(s)")
    bits.append("priced" if f["price"] else "no price")
    bits.append(f"created {f['created_at']}")
    return "; ".join(bits)


def _loser_blockers(f: Dict[str, Any]) -> List[str]:
    out = []
    if f["on_shopify"]:
        out.append("is on Shopify (never archived by a script)")
    out += [f"has {r}" for r in f["refs"]]
    if f["variants"]:
        out.append("leads linked variants")
    if f["archived"]:
        out.append("already archived")
    return out


def _holders(products, new_key: str, mine: set, eye_item: bool) -> List[Dict[str, Any]]:
    """Rows that are already this product: the key held as is, else (an
    eye-size item) the same frame catalogued with no eye size, whose key
    lacks the size and so never collided."""
    rows = [d for d in products.find({"identity_key": new_key}) if d.get("product_id") not in mine]
    if rows or not eye_item or new_key.count("|") < 3:
        return rows
    sizeless = new_key.rsplit("|", 1)[0]
    return [d for d in products.find({"identity_key": sizeless})
            if d.get("product_id") not in mine and not _eye_size(d)]


def plan(db) -> Dict[str, Any]:
    """Read-only: what --apply would do."""
    products = db["products"]
    changes: List[Dict[str, Any]] = []
    other_stale: List[str] = []
    for doc in products.find({}):
        old = doc.get("identity_key")
        try:
            new = pm.identity_key_of(doc)
        except pm.ProductMasterError:
            continue
        if not new or new == old:
            continue
        attrs = pm._identity_attributes(doc)
        brand, model, _, _ = pm.identity_parts(attrs, doc.get("category"))
        folded = pm.model_without_brand(brand, model)
        if folded == pm.normalise_identity_component(model):
            other_stale.append(f"{doc.get('sku')}: stored {old!r}, rule {new!r} (not a brand-in-model row)")
            continue
        field = next((k for k in ("model_no", "model_name")
                      if (doc.get("attributes") or {}).get(k) == model), None)
        changes.append({
            "doc": doc, "old_key": old, "new_key": new, "field": field, "raw": model,
            "cleaned": _clean_raw(str(model), folded) if field else None,
        })

    pairs = []
    mine = {c["doc"].get("product_id") for c in changes}
    for c in changes:
        same = [o for o in changes if o is not c and o["new_key"] == c["new_key"]]
        eye_item = pm._size_attribute_key(c["doc"].get("category")) == "lens_size"
        holders = _holders(products, c["new_key"], mine, eye_item)
        if not same and not holders:
            continue
        c["collides"] = True
        if same or len(holders) != 1:
            pairs.append({"key": c["new_key"], "change": c, "other": None,
                          "verdict": f"REPORT ONLY, a human merges it: {1 + len(same) + len(holders)} rows "
                                     "are this product"})
            continue
        h = holders[0]
        fc, fh = _facts(db, c["doc"]), _facts(db, h)
        keep_change = _survivor_score(fc) >= _survivor_score(fh)
        survivor, loser = (c["doc"], h) if keep_change else (h, c["doc"])
        fs, fl = (fc, fh) if keep_change else (fh, fc)
        blockers = _loser_blockers(fl)
        pairs.append({
            "key": c["new_key"], "change": c, "other": h, "survivor": survivor, "loser": loser,
            "fs": fs, "fl": fl, "keep_change": keep_change, "blockers": blockers,
            "fill_size": None if _eye_size(survivor) else _eye_size(loser),
            "verdict": ("REPORT ONLY, a human merges it: the other copy " + "; ".join(blockers))
            if blockers else "ARCHIVE",
        })
    # One row matched by more than one brand-in-model row (a size-less frame
    # vs sizes 52 and 54): which size it is, is a human's call. Nothing in
    # that group is written.
    seen: Dict[Any, int] = {}
    for e in pairs:
        if e.get("other"):
            seen[e["other"].get("product_id")] = seen.get(e["other"].get("product_id"), 0) + 1
    for e in pairs:
        if e.get("other") and seen[e["other"].get("product_id")] > 1:
            e["blockers"] = [f"{e['other'].get('sku')} matches more than one brand-in-model row"]
            e["verdict"] = "REPORT ONLY, a human merges it: " + e["blockers"][0]
    return {"changes": changes, "pairs": pairs, "other_stale": other_stale}


def report(p: Dict[str, Any]) -> None:
    print(f"\n(a) {len(p['changes'])} product(s) with the brand typed in the model:")
    for c in p["changes"]:
        d = c["doc"]
        tag = "  [DUPLICATE PAIR]" if c.get("collides") else ""
        print(f"  {d.get('sku')}: {c['field'] or 'model'} {c['raw']!r} -> {c['cleaned']!r}; "
              f"key {c['old_key']!r} -> {c['new_key']!r}{tag}")
    print(f"\n(b) {len(p['pairs'])} duplicate pair(s) the new rule exposes:")
    for e in p["pairs"]:
        if not e.get("other"):
            print(f"  key {e['key']!r}: {e['change']['doc'].get('sku')} -> {e['verdict']}")
            continue
        s, lo = e["survivor"], e["loser"]
        print(f"  key {e['key']!r}: {e['change']['doc'].get('sku')} vs {e['other'].get('sku')}")
        print(f"    survivor {s.get('sku')} ({_why(e['fs'])})")
        print(f"    other    {lo.get('sku')} ({_why(e['fl'])})")
        if e["blockers"]:
            print(f"    -> {e['verdict']}")
        else:
            extra = f"; survivor gets lens size {e['fill_size']}" if e["fill_size"] else ""
            print(f"    -> ARCHIVE {lo.get('sku')}, keep {s.get('sku')}{extra}")
    if p["other_stale"]:
        print(f"\n{len(p['other_stale'])} key(s) stale for another reason (left alone):")
        for line in p["other_stale"]:
            print("  " + line)


def _backup(db, run_id: str, coll: str, filt: Dict[str, Any], doc: Dict[str, Any], fields: List[str]) -> None:
    before = {}
    for f in fields:
        cur: Any = doc
        for part in f.split("."):
            cur = cur.get(part, _MISSING) if isinstance(cur, dict) else _MISSING
        before[f] = cur
    seq = db[BACKUPS].count_documents({"run_id": run_id})
    db[BACKUPS].insert_one({"_id": f"{run_id}-{seq:05d}", "run_id": run_id, "collection": coll,
                            "filter": filt, "before": before, "at": datetime.now(timezone.utc).isoformat()})


def _write(db, run_id: str, coll: str, filt: Dict[str, Any], doc: Dict[str, Any],
           sets: Dict[str, Any], unsets: Optional[List[str]] = None) -> None:
    _backup(db, run_id, coll, filt, doc, list(sets) + list(unsets or []))
    update: Dict[str, Any] = {"$set": sets} if sets else {}
    if unsets:
        update["$unset"] = {f: "" for f in unsets}
    db[coll].update_one(filt, update)


def _archive(db, run_id: str, spine: Dict[str, Any], twin: Optional[Dict[str, Any]],
             now: str, free_key: bool) -> None:
    for coll, doc, filt in (("products", spine, {"product_id": spine.get("product_id")}),
                            ("catalog_products", twin, {"id": (twin or {}).get("id")})):
        if not doc:
            continue
        sets = {"is_active": False, "deleted_at": now, "deleted_by": ACTOR,
                ACTIVE_BEFORE_ARCHIVE: doc.get("is_active", True) is not False}
        unsets = ["identity_key"] if coll == "products" and free_key else []
        _write(db, run_id, coll, filt, doc, sets, unsets)


def _fix_row(db, run_id: str, doc: Dict[str, Any], c: Optional[Dict[str, Any]], size: Any) -> str:
    """Clean the model (c, a brand-in-model change) and fill an eye size, then
    write the rule's key. 'written', 'unchanged' (nothing to write) or
    'taken' (another row holds the key: nothing written)."""
    attrs = dict(doc.get("attributes") or {})
    spine_set: Dict[str, Any] = {}
    twin_set: Dict[str, Any] = {}
    twin = _twin(db, doc)
    tattrs = (twin or {}).get("attributes") or {}
    if c and c["field"] and c["cleaned"]:
        f = c["field"]
        attrs[f] = spine_set[f"attributes.{f}"] = c["cleaned"]
        if doc.get("model") == c["raw"]:
            spine_set["model"] = c["cleaned"]
        if tattrs.get(f) == c["raw"]:
            twin_set[f"attributes.{f}"] = c["cleaned"]
    if size:
        attrs["lens_size"] = spine_set["attributes.lens_size"] = size
        if not doc.get("size"):
            spine_set["size"] = size
        if twin and not tattrs.get("lens_size"):
            twin_set["attributes.lens_size"] = size
    key = pm.identity_key_of({**doc, **{k: v for k, v in spine_set.items() if "." not in k}, "attributes": attrs})
    pid = doc.get("product_id")
    if key and key != doc.get("identity_key"):
        if db["products"].count_documents({"identity_key": key, "product_id": {"$ne": pid}}):
            print(f"  [skip] {doc.get('sku')}: another product already holds {key!r}; nothing written")
            return "taken"
        spine_set["identity_key"] = key
    if spine_set:
        _write(db, run_id, "products", {"product_id": pid}, doc, spine_set)
    if twin_set:
        _write(db, run_id, "catalog_products", {"id": twin.get("id")}, twin, twin_set)
    return "written" if spine_set or twin_set else "unchanged"


def apply(db, p: Dict[str, Any], run_id: str) -> Dict[str, int]:
    stats = {"archived": 0, "rekeyed": 0, "skipped": 0}

    def count(outcome: str) -> None:
        if outcome == "written":
            stats["rekeyed"] += 1
        elif outcome == "taken":
            stats["skipped"] += 1

    now = datetime.now(timezone.utc).isoformat()
    done = set()
    # Pairs first, the loser archived BEFORE its survivor takes the key.
    for e in p["pairs"]:
        c = e["change"]
        done.add(c["doc"].get("product_id"))
        if not e.get("other") or e["blockers"]:
            stats["skipped"] += 1
            continue
        survivor, loser = e["survivor"], e["loser"]
        _archive(db, run_id, loser, e["fl"]["twin"], now,
                 free_key=bool(loser.get("identity_key")) and loser.get("identity_key") == e["key"])
        stats["archived"] += 1
        count(_fix_row(db, run_id, survivor, c if e["keep_change"] else None, e["fill_size"]))
    for c in p["changes"]:
        if c["doc"].get("product_id") not in done:
            count(_fix_row(db, run_id, c["doc"], c, None))
    if stats["archived"] or stats["rekeyed"]:
        _invalidate_lists()
    return stats


def _invalidate_lists() -> None:
    """The app's own product-list cache drop (as _archive_product does), so no
    cached page keeps an archived twin. Best-effort: without the app's Redis
    in this shell, cached pages expire on their own TTL (5 minutes)."""
    try:
        from api.services.cache import invalidate_product_lists

        invalidate_product_lists()
        print("Product lists cache cleared.")
    except Exception as exc:  # noqa: BLE001 - the data write already stands
        print(f"Product lists cache not cleared ({type(exc).__name__}); pages refresh within 5 minutes.")


def revert(db, run_id: str) -> int:
    rows = list(db[BACKUPS].find({"run_id": run_id, "reverted_at": {"$exists": False}}).sort("_id", 1))
    # Newest first, so a field written twice ends on its first "before".
    for r in reversed(rows):
        sets = {f: v for f, v in r["before"].items() if v != _MISSING}
        unsets = {f: "" for f, v in r["before"].items() if v == _MISSING}
        update: Dict[str, Any] = {}
        if sets:
            update["$set"] = sets
        if unsets:
            update["$unset"] = unsets
        if update:
            db[r["collection"]].update_one(r["filter"], update)
        db[BACKUPS].update_one({"_id": r["_id"]}, {"$set": {"reverted_at": datetime.now(timezone.utc).isoformat()}})
    return len(rows)


def main(argv: List[str]) -> int:
    uri = os.environ.get("MONGO_PUBLIC_URL") or os.environ.get("MONGO_URL")
    if not uri:
        print("No MONGO_PUBLIC_URL / MONGO_URL in the environment.")
        return 2
    from pymongo import MongoClient

    db = MongoClient(uri, serverSelectionTimeoutMS=20000)[os.environ.get("MONGO_DATABASE", "ims_2_0")]
    if "--revert" in argv:
        if "--run-id" not in argv or argv.index("--run-id") + 1 >= len(argv):
            print("--revert needs --run-id <id>")
            return 2
        run_id = argv[argv.index("--run-id") + 1]
        print(f"Reverted {revert(db, run_id)} backed-up change(s) of run {run_id}.")
        return 0
    p = plan(db)
    report(p)
    unresolved = sum(1 for e in p["pairs"] if not e.get("other") or e["blockers"])
    if "--apply" not in argv:
        print("\nDRY RUN -- nothing was written. Re-run with --apply to write the above.")
        return 1 if unresolved else 0
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S-") + uuid.uuid4().hex[:6]
    stats = apply(db, p, run_id)
    print(f"\nAPPLIED run {run_id}: archived={stats['archived']} rekeyed={stats['rekeyed']} "
          f"skipped={stats['skipped']}. Undo: --revert --run-id {run_id}")
    return 1 if unresolved else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
