"""READ-ONLY prod scan (council ruling 2026-07-14, review-editor PR ops step).

The catalog PUT's pricing model defaulted discount_category to 'MASS', so any
drawer 'Save fixes' that touched pricing silently stamped MASS onto an imported
doc. MASS flows to POS discount caps at promote (LUXURY 5% would widen to 15%).
This scan measures the blast radius before the fix rolls out. NO WRITES.

Run: railway run --service MongoDB -- ".venv/Scripts/python.exe" scripts/scan_mass_tier_stamp.py
"""
import os
import sys

from pymongo import MongoClient

url = os.environ.get("MONGO_PUBLIC_URL") or os.environ.get("MONGO_URL")
if not url:
    print("No MONGO_PUBLIC_URL/MONGO_URL in env")
    sys.exit(1)

db = MongoClient(url, serverSelectionTimeoutMS=15000)["ims_2_0"]
col = db["catalog_products"]

total_import = col.count_documents({"source": "bvi_import"})
needs_review = col.count_documents({"source": "bvi_import", "needs_review": True})
mass_all = col.count_documents(
    {"source": "bvi_import", "pricing.discount_category": "MASS"}
)

# Docs whose pricing tier is MASS AND that were edited after import
# (updated_at present and newer than migrated_at) = candidates for the
# accidental stamp. Docs never edited since import cannot have been stamped
# by the PUT bug (the migration itself may or may not set a tier - check both).
edited_mass = list(
    col.find(
        {
            "source": "bvi_import",
            "pricing.discount_category": "MASS",
            "$expr": {"$gt": ["$updated_at", "$migrated_at"]},
        },
        {"_id": 0, "id": 1, "name": 1, "title": 1, "brand": 1,
         "needs_review": 1, "pos_ready": 1, "updated_at": 1, "migrated_at": 1},
    ).limit(50)
)
edited_mass_count = col.count_documents(
    {
        "source": "bvi_import",
        "pricing.discount_category": "MASS",
        "$expr": {"$gt": ["$updated_at", "$migrated_at"]},
    }
)

# Of the stamped+edited docs, how many were already PROMOTED (spine impact).
promoted_stamped = col.count_documents(
    {
        "source": "bvi_import",
        "pricing.discount_category": "MASS",
        "pos_ready": True,
        "$expr": {"$gt": ["$updated_at", "$migrated_at"]},
    }
)

# Baseline: what does the migration itself stamp? Tier distribution of
# never-edited imports tells us the import-time default.
pipeline = [
    {"$match": {"source": "bvi_import"}},
    {"$group": {"_id": "$pricing.discount_category", "n": {"$sum": 1}}},
    {"$sort": {"n": -1}},
]
dist = list(col.aggregate(pipeline))

print(f"bvi_import docs total:            {total_import}")
print(f"  still needs_review:             {needs_review}")
print(f"  tier=MASS (any):                {mass_all}")
print(f"  tier=MASS and edited-post-import: {edited_mass_count}")
print(f"  of those, already promoted:     {promoted_stamped}")
print("tier distribution (all imports):")
for row in dist:
    print(f"  {row['_id']!r}: {row['n']}")
print("sample of edited MASS docs (max 50):")
for d in edited_mass:
    print(f"  {d.get('id')} | {d.get('brand')} | {(d.get('name') or d.get('title') or '')[:60]}"
          f" | promoted={d.get('pos_ready')} | edited={d.get('updated_at')}")
