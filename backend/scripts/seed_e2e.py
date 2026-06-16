"""
IMS 2.0 - E2E test seed (deterministic, idempotent)
====================================================
Seeds the minimum fixed data the Playwright E2E suite asserts against:

  - Stores  : the bootstrap stores from database.seed_data (BV-BOK-01 etc.),
              which carry a GSTIN so GST tax invoices generate. Users too
              (admin / admin123, SUPERADMIN, geo-exempt).
  - Products: a 5% FRAME priced at exactly Rs 999 (the canonical GST-inclusive
              case) and an 18% SUNGLASS at Rs 1180, both with real product_ids.
  - Stock   : one AVAILABLE serialized unit per product at BV-BOK-01 so the
              sale links real on-hand (orders.create only checks existence,
              but seeding stock keeps reports honest).

Idempotent: re-running upserts the products/stock and only inserts stores/users
into empty collections (matching the live seed endpoint's behavior), then force
re-seeds users so the bcrypt hash is correct.

No emojis / non-ASCII in output (Windows cp1252 safe).

Run (after the backend can reach Mongo):
    python backend/scripts/seed_e2e.py
or with an explicit URL:
    MONGODB_URL=mongodb://localhost:27017/ims_test python backend/scripts/seed_e2e.py
"""
import os
import sys

# Make `database` / `api` importable whether run from repo root or backend/.
_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(_HERE)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


# Canonical E2E catalog (must match e2e/fixtures/constants.ts SEED).
E2E_PRODUCTS = [
    {
        "_id": "e2e-frame-999",
        "product_id": "e2e-frame-999",
        "name": "E2E Test Frame 999",
        "sku": "E2E-FR-999",
        "category": "FRAMES",
        "brand": "E2E Optics",
        "model": "Frame999",
        "mrp": 999,
        "offer_price": 999,
        "hsn_code": "9003",
        "gst_rate": 5,
        "frame_type": "Full Rim",
        "is_active": True,
    },
    {
        "_id": "e2e-sunglass-1180",
        "product_id": "e2e-sunglass-1180",
        "name": "E2E Test Sunglass 1180",
        "sku": "E2E-SG-1180",
        "category": "SUNGLASSES",
        "brand": "E2E Optics",
        "model": "Sun1180",
        "mrp": 1180,
        "offer_price": 1180,
        "hsn_code": "9004",
        "gst_rate": 18,
        "lens_type": "Non-Polarized",
        "is_active": True,
    },
]

# AVAILABLE serialized units per product at the primary store.
#
# Why a POOL and not a single unit: the suite COMPLETES a frame sale several
# times (gst-invoice x2, pos-gst-inclusive x2) and each completed sale flips one
# stock_unit AVAILABLE -> SOLD via orders._mark_units_sold. Since the BUG-097
# oversell guard (orders._assert_serialized_stock_available) now correctly 409s
# a sale once AVAILABLE hits 0, a single seeded unit was consumed by the first
# sale and every later sale 409'd ("0 available ... Cannot oversell") -> the POS
# success screen never rendered and the suite went red. retries=2 multiplies the
# consumption further. A generous pool (well above suite_sales x (1+retries))
# keeps every run green without weakening the prod guard, and stays trivially
# small in a fresh CI Mongo. No spec asserts an exact on-hand count, so a larger
# pool changes no assertion.
_E2E_STOCK_PER_PRODUCT = 50
_E2E_STOCK_SPECS = [
    {"prefix": "frame", "product_id": "e2e-frame-999", "barcode": "E2E999", "cost_price": 400},
    {"prefix": "sunglass", "product_id": "e2e-sunglass-1180", "barcode": "E2E118", "cost_price": 500},
]


def _build_e2e_stock():
    units = []
    for spec in _E2E_STOCK_SPECS:
        for i in range(1, _E2E_STOCK_PER_PRODUCT + 1):
            sid = "e2e-stock-%s-%03d" % (spec["prefix"], i)
            units.append(
                {
                    "_id": sid,
                    "stock_id": sid,
                    "product_id": spec["product_id"],
                    "store_id": "BV-BOK-01",
                    "barcode": "%s%06d" % (spec["barcode"], i),
                    "status": "AVAILABLE",
                    "quantity": 1,
                    "cost_price": spec["cost_price"],
                }
            )
    return units


E2E_STOCK = _build_e2e_stock()


def _now_iso():
    from datetime import datetime

    return datetime.utcnow().isoformat()


def seed(db) -> dict:
    """Seed against a connected DB wrapper (db.get_collection(name))."""
    from database.seed_data import get_all_seed_data

    results = {}

    # 1) Stores + users (insert-into-empty; force users for correct hashes).
    bootstrap = get_all_seed_data()
    for coll_name, documents in bootstrap.items():
        collection = db.get_collection(coll_name)
        if coll_name == "users":
            # Force re-seed users so password_hash matches admin123 deterministically.
            collection.delete_many({})
            if documents:
                collection.insert_many(documents)
            results[coll_name] = "RESEEDED (%d)" % len(documents)
            continue
        existing = collection.count_documents({})
        if existing > 0:
            results[coll_name] = "SKIPPED (%d existing)" % existing
            continue
        if documents:
            collection.insert_many(documents)
            results[coll_name] = "SEEDED (%d)" % len(documents)
        else:
            results[coll_name] = "EMPTY"

    # 2) E2E products (upsert so re-runs converge on the fixed prices).
    products = db.get_collection("products")
    for doc in E2E_PRODUCTS:
        doc = dict(doc)
        doc.setdefault("created_at", _now_iso())
        products.replace_one({"_id": doc["_id"]}, doc, upsert=True)
    results["products_e2e"] = "UPSERTED (%d)" % len(E2E_PRODUCTS)

    # 3) E2E stock units (upsert + reset to AVAILABLE so prior runs that sold
    #    a unit don't leave it SOLD).
    stock = db.get_collection("stock_units")
    for doc in E2E_STOCK:
        doc = dict(doc)
        doc.setdefault("created_at", _now_iso())
        # Reset status to AVAILABLE on every seed (idempotent across runs).
        stock.replace_one({"_id": doc["_id"]}, doc, upsert=True)
    results["stock_units_e2e"] = "UPSERTED (%d)" % len(E2E_STOCK)

    return results


def _connect():
    """Connect to MongoDB EXACTLY the way api/main.py does.

    main.py reads MONGODB_URL / MONGO_URL and builds the config with
    database="ims_2_0" HARD-CODED (the URI path is ignored). We must mirror
    that so the seed writes to the same database the running app reads,
    regardless of any '/ims_test' suffix in MONGODB_URL.
    """
    from database.connection import init_db, get_db, DatabaseConfig

    mongo_url = os.getenv("MONGODB_URL") or os.getenv("MONGO_URL")
    if mongo_url:
        config = DatabaseConfig.from_uri(mongo_url, database="ims_2_0")
    else:
        config = DatabaseConfig.from_env()

    if not init_db(config):
        return None
    return get_db()


def main() -> int:
    # Ensure a test-ish environment so rate-limit/seed gates relax if relevant.
    os.environ.setdefault("ENVIRONMENT", "test")
    os.environ.setdefault("JWT_SECRET_KEY", "ci-test-secret-key")

    db = _connect()
    if db is None or not getattr(db, "is_connected", False):
        print("[SEED_E2E] ERROR: database not connected. Set MONGODB_URL / MONGO_URL.")
        return 1

    results = seed(db)
    print("[SEED_E2E] done:")
    for k, v in results.items():
        print("  - %s: %s" % (k, v))
    # Sanity: confirm the canonical frame is queryable.
    frame = db.get_collection("products").find_one({"product_id": "e2e-frame-999"})
    if not frame or frame.get("offer_price") != 999:
        print("[SEED_E2E] ERROR: e2e-frame-999 missing or wrong price after seed.")
        return 1
    print("[SEED_E2E] verified e2e-frame-999 @ Rs %s" % frame.get("offer_price"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
