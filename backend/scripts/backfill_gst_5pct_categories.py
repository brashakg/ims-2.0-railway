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
        needs = (cur_rate != 5.0) or (cur_hsn and cur_hsn[:4] != want_hsn[:4])
        if not needs:
            continue
        fixed += 1
        if len(examples) < 12:
            examples.append(
                f"  {p.get('product_id')} [{p.get('category')}] {p.get('name','')[:30]} "
                f": gst {cur_rate}->5.0  hsn {cur_hsn or '-'}->{want_hsn}"
            )
        if APPLY:
            coll.update_one(
                {"_id": p["_id"]},
                {"$set": {"gst_rate": 5.0, "hsn_code": want_hsn}},
            )

    print(f"5%-category products scanned: {scanned}")
    print(f"{'FIXED' if APPLY else 'WOULD FIX (dry-run)'}: {fixed}")
    for e in examples:
        print(e)
    if not APPLY and fixed:
        print("\nRe-run with --apply to write the corrections.")


if __name__ == "__main__":
    main()
