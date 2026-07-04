# Migrate loyalty_settings.category_multipliers keys from legacy plural /
# alias spellings (SUNGLASSES, RX_LENSES, ...) onto the canonical
# product-master vocabulary (SUNGLASS, OPTICAL_LENS, ...).
#
# Dry-run by default (prints before/after, writes nothing). Pass --apply to
# persist. Run against prod via Railway so creds are injected, e.g.:
#   railway run --service MongoDB -- .venv\Scripts\python.exe scripts\migrate_loyalty_category_keys.py
#   railway run --service MongoDB -- .venv\Scripts\python.exe scripts\migrate_loyalty_category_keys.py --apply
import os
import sys

from pymongo import MongoClient

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))
from api.services.product_master import resolve_category  # noqa: E402

SINGLETON_ID = "loyalty_settings"


def canonicalise(mults):
    out = {}
    for key, value in (mults or {}).items():
        canon = resolve_category(key) or str(key).strip().upper().replace(" ", "_")
        try:
            out[canon] = float(value)
        except (TypeError, ValueError):
            continue
    return out


def main():
    apply = "--apply" in sys.argv
    url = os.environ.get("MONGO_PUBLIC_URL") or os.environ.get("MONGODB_URL")
    if not url:
        print("ERROR: set MONGO_PUBLIC_URL or MONGODB_URL (use `railway run`)")
        return 1
    db = MongoClient(url, serverSelectionTimeoutMS=20000)["ims_2_0"]
    doc = db["loyalty_settings"].find_one({"_id": SINGLETON_ID})
    if not doc:
        print("No loyalty_settings doc found -- nothing to migrate (defaults are already canonical in code).")
        return 0

    before = doc.get("category_multipliers")
    if not isinstance(before, dict) or not before:
        print("loyalty_settings doc has no category_multipliers -- nothing to migrate.")
        return 0

    after = canonicalise(before)
    print("BEFORE:", dict(before))
    print("AFTER :", after)
    if after == before:
        print("Already canonical -- no write needed.")
        return 0

    if not apply:
        print("Dry-run only. Re-run with --apply to persist.")
        return 0

    db["loyalty_settings"].update_one(
        {"_id": SINGLETON_ID},
        {"$set": {"category_multipliers": after}},
    )
    print("APPLIED: category_multipliers keys are now canonical.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
