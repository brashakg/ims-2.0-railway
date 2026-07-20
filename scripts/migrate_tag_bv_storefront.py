# Tag the live Better Vision Shopify integrations doc with storefront_id="BV",
# seed the BV storefront registry row, and create the BV-ONLINE-01 ONLINE store
# -- the data-side of WizOpt multi-storefront Phase 0.
#
# ***** DRY-RUN BY DEFAULT (prints the plan, writes NOTHING). *****
# Pass --apply to persist. Run against prod via Railway so creds are injected:
#   railway run --service MongoDB -- .venv\Scripts\python.exe scripts\migrate_tag_bv_storefront.py
#   railway run --service MongoDB -- .venv\Scripts\python.exe scripts\migrate_tag_bv_storefront.py --apply
#
# This is SAFE / BACKWARD-COMPATIBLE: tagging the (currently untagged) Shopify
# doc storefront_id="BV" matches how the keyed resolver already treats it, so BV
# behaves byte-identically before and after. It is IDEMPOTENT -- re-running only
# fills in what is missing.
import os
import sys

from pymongo import MongoClient

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"),
)
from api.services.storefronts import BV_STOREFRONT  # noqa: E402

DEFAULT_STOREFRONT_ID = "BV"
ONLINE_STORE_ID = "BV-ONLINE-01"


def _bv_online_store_seed():
    """A minimal, valid ONLINE store doc for BV-ONLINE-01. entity_id / geo are
    left for the owner to fill in from the Stores admin once created -- the
    migration only guarantees the row exists with the ONLINE store_type."""
    return {
        "store_id": ONLINE_STORE_ID,
        "store_code": ONLINE_STORE_ID,
        "store_name": "Better Vision Online",
        "brand": "BETTER_VISION",
        "store_type": "ONLINE",
        "is_active": True,
        "is_hq": False,
        "enabled_categories": [],
    }


def main():
    apply = "--apply" in sys.argv
    url = os.environ.get("MONGO_PUBLIC_URL") or os.environ.get("MONGODB_URL")
    if not url:
        print("ERROR: set MONGO_PUBLIC_URL or MONGODB_URL (use `railway run`)")
        return 1
    db = MongoClient(url, serverSelectionTimeoutMS=20000)["ims_2_0"]

    plan = []

    # 1) Tag the live Shopify integrations doc storefront_id="BV" (only if untagged).
    integ = db["integrations"].find_one(
        {"type": "shopify", "storefront_id": {"$exists": False}}
    )
    if integ is not None:
        plan.append(
            ("integrations", "tag storefront_id=BV on the untagged shopify doc")
        )
    else:
        print("integrations: shopify doc already tagged (or absent) -- no tag needed.")

    # 2) Seed the BV storefront registry row (only if absent).
    if db["storefronts"].find_one({"storefront_id": DEFAULT_STOREFRONT_ID}) is None:
        plan.append(("storefronts", "insert BV registry row"))
    else:
        print("storefronts: BV row already present -- no insert needed.")

    # 3) Create the BV-ONLINE-01 ONLINE store (only if absent).
    if db["stores"].find_one({"store_id": ONLINE_STORE_ID}) is None:
        plan.append(("stores", f"insert {ONLINE_STORE_ID} (store_type=ONLINE)"))
    else:
        print(f"stores: {ONLINE_STORE_ID} already present -- no insert needed.")

    if not plan:
        print("Nothing to do -- everything already in place.")
        return 0

    print("\nPLANNED CHANGES:")
    for coll, what in plan:
        print(f"  [{coll}] {what}")

    if not apply:
        print("\nDry-run only. Re-run with --apply to persist.")
        return 0

    for coll, _what in plan:
        if coll == "integrations":
            db["integrations"].update_one(
                {"type": "shopify", "storefront_id": {"$exists": False}},
                {"$set": {"storefront_id": DEFAULT_STOREFRONT_ID}},
            )
        elif coll == "storefronts":
            db["storefronts"].update_one(
                {"storefront_id": DEFAULT_STOREFRONT_ID},
                {"$setOnInsert": dict(BV_STOREFRONT)},
                upsert=True,
            )
        elif coll == "stores":
            db["stores"].update_one(
                {"store_id": ONLINE_STORE_ID},
                {"$setOnInsert": _bv_online_store_seed()},
                upsert=True,
            )
    print("\nAPPLIED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
