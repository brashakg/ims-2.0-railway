"""READ-ONLY census of every distinct product-category spelling in prod Mongo.

Prints value + count per collection/field so we can see exactly which legacy
spellings (lens/lenses/sunglass/sunglasses/...) exist where. No writes.

Run: railway run --service MongoDB -- ".venv\\Scripts\\python.exe" scripts/census_category_values.py
"""

import os
import sys

from pymongo import MongoClient

URI = (
    os.getenv("MONGO_PUBLIC_URL")
    or os.getenv("MONGODB_URI")
    or os.getenv("MONGO_URL")
)
if not URI:
    print("ERROR: no Mongo URI env (MONGO_PUBLIC_URL / MONGODB_URI)")
    sys.exit(1)

client = MongoClient(URI, serverSelectionTimeoutMS=15000)
db = client["ims_2_0"]

SITES = [
    ("products", "category", None),
    ("catalog_products", "category", None),
    ("orders", "items.category", "items"),
    ("orders", "items.item_type", "items"),
    ("product_templates", "category", None),
    ("brand_masters", "categories", "categories"),
    ("hsn_gst_master", "category", None),
    ("catalog_field_options", "category", None),
    ("stock_counts", "category", None),
    ("walkouts", "category", None),
    ("lens_catalog", "category", None),
]

for coll_name, field, unwind in SITES:
    try:
        coll = db[coll_name]
        if coll.estimated_document_count() == 0:
            print(f"{coll_name}.{field}: (collection empty)")
            continue
        pipeline = []
        if unwind:
            pipeline.append({"$unwind": f"${unwind}"})
            leaf = field if "." not in field else field
            pipeline.append({"$group": {"_id": f"${leaf}", "n": {"$sum": 1}}})
        else:
            pipeline.append({"$group": {"_id": f"${field}", "n": {"$sum": 1}}})
        pipeline.append({"$sort": {"n": -1}})
        rows = list(coll.aggregate(pipeline))
        vals = ", ".join(f"{r['_id']!r}x{r['n']}" for r in rows[:25])
        print(f"{coll_name}.{field}: {vals}")
    except Exception as e:  # noqa: BLE001
        print(f"{coll_name}.{field}: ERROR {str(e)[:100]}")

print("CENSUS DONE (read-only)")
