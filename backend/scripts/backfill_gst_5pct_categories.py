"""BUG-085 backfill: correct stale GST on 5%-category products.

GST 2.0 (eff. 22-Sep-2025) moved frames / optical & contact lenses / corrective
readers to 5%. Products created earlier (or imported) can still carry a stale
`gst_rate` (e.g. 18) and a wrong `hsn_code` (e.g. 9004) on the catalog MASTER.

Live POS billing is item_type-authoritative (orders.py resolves the rate from the
gst_rates table, not the product's stored rate), so invoices are already correct;
this only fixes the catalog DISPLAY + keeps the master consistent with the table.

For every product whose category maps to a 5.0% rate in gst_rates.GST_CATEGORY_TABLE
but whose stored gst_rate != 5.0 (or hsn_code disagrees with the table), set
gst_rate = 5.0 and hsn_code = the canonical table HSN. Idempotent; DRY-RUN by
default. Run on prod with:  railway run .venv\\Scripts\\python.exe backend/scripts/backfill_gst_5pct_categories.py --apply
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services.gst_rates import GST_CATEGORY_TABLE, _normalize_category  # noqa: E402

APPLY = "--apply" in sys.argv

# Categories that should be 5% per the canonical table, with their canonical HSN.
FIVE_PCT = {
    cat: hsn for cat, (hsn, rate) in GST_CATEGORY_TABLE.items() if rate == 5.0
}


def main():
    uri = (
        os.environ.get("MONGODB_URI")
        or os.environ.get("MONGODB_URL")
        or os.environ.get("MONGO_URL")
        or os.environ.get("DATABASE_URL")
        or ""
    )
    if not uri:
        print("No MONGODB_URI set — aborting (run via `railway run`).")
        return
    from pymongo import MongoClient

    client = MongoClient(uri)
    try:
        db = client.get_default_database()
        if db is None:
            raise ValueError("no default db in URI")
    except Exception:
        db = client["ims_2_0"]
    coll = db.get_collection("products")

    scanned = fixed = 0
    examples = []
    for p in coll.find({}, {"product_id": 1, "name": 1, "category": 1, "gst_rate": 1, "hsn_code": 1}):
        cat_norm = _normalize_category(p.get("category"))
        want_hsn = FIVE_PCT.get(cat_norm)
        if want_hsn is None:
            continue  # not a 5% category
        scanned += 1
        cur_rate = p.get("gst_rate")
        cur_hsn = str(p.get("hsn_code") or "")
        # RATE-ONLY policy (owner decision): always correct gst_rate to 5%, but
        # PRESERVE an HSN that is already a valid 5%-category code (9001 lenses/
        # contacts, 9003 frames) -- only write the canonical HSN when the current
        # one is missing or not a 9001/9003 code. Avoids reclassifying e.g. a
        # contact lens (900130) onto the spectacle-lens canonical (900150).
        hsn_ok = bool(cur_hsn) and cur_hsn[:4] in ("9001", "9003")
        needs_rate = cur_rate != 5.0
        needs_hsn = not hsn_ok
        if not (needs_rate or needs_hsn):
            continue
        fixed += 1
        new_hsn = cur_hsn if hsn_ok else want_hsn
        if len(examples) < 12:
            examples.append(
                f"  {p.get('product_id')} [{p.get('category')}] {p.get('name','')[:30]} "
                f": gst {cur_rate}->5.0  hsn {cur_hsn or '-'}->{new_hsn}"
            )
        if APPLY:
            set_fields = {"gst_rate": 5.0}
            if needs_hsn:
                set_fields["hsn_code"] = want_hsn
            coll.update_one({"_id": p["_id"]}, {"$set": set_fields})

    print(f"5%-category products scanned: {scanned}")
    print(f"{'FIXED' if APPLY else 'WOULD FIX (dry-run)'}: {fixed}")
    for e in examples:
        print(e)
    if not APPLY and fixed:
        print("\nRe-run with --apply to write the corrections.")


if __name__ == "__main__":
    main()
