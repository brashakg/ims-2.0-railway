"""Local-only: delete ALL prod test-data customers + their linked records.
USER-AUTHORIZED 2026-06-04 ("Delete all 5 + their linked records"). Guarded so a
bad match can't sweep real data.

  railway run --service MongoDB <venv-python> scripts/_cleanup_all_test_data.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
from database.connection import DatabaseConnection, DatabaseConfig  # noqa: E402

uri = os.environ.get("MONGO_PUBLIC_URL") or os.environ.get("MONGODB_URL") or os.environ.get("MONGO_URL")
if not uri:
    print("NO_MONGO_URL_IN_ENV"); sys.exit(2)
conn = DatabaseConnection()
conn.configure(DatabaseConfig.from_uri(uri, "ims_2_0"))
if not conn.connect():
    print("CONNECT_FAILED"); sys.exit(3)
db = conn.db

TEST_MOBILES = ["9999999999", "9998887771", "9000000029", "9000000077"]
QUERY = {
    "$or": [
        {"mobile": {"$in": TEST_MOBILES}},
        {"phone": {"$in": TEST_MOBILES}},
        {"customer_id": "test"},
        {"name": {"$regex": "QA TEST", "$options": "i"}},
        {"name": {"$regex": "^TEST[0-9]*$", "$options": "i"}},
        {"name": {"$regex": "^Test Customer", "$options": "i"}},
    ]
}
custs = list(db["customers"].find(QUERY))
print("Matched %d test customers:" % len(custs))
for c in custs:
    print("   -", c.get("customer_id"), "/", c.get("name"), "/", c.get("mobile") or c.get("phone"))

# Safety: the read-only check found exactly 5. Refuse if a match would sweep
# more than a small bound (protects real customers from a too-greedy regex).
if len(custs) == 0:
    print("Nothing to delete."); sys.exit(0)
if len(custs) > 8:
    print("SAFETY ABORT: matched %d (>8) -- refusing to delete." % len(custs)); sys.exit(4)

cids = [c.get("customer_id") for c in custs if c.get("customer_id")]
for coll in ["orders", "eye_tests", "prescriptions", "eye_test_queue", "clinical_records"]:
    try:
        r = db[coll].delete_many({"customer_id": {"$in": cids}})
        print("  %-16s deleted %d" % (coll, r.deleted_count))
    except Exception as e:  # noqa: BLE001
        print("  %-16s ERR %s" % (coll, e))

r = db["customers"].delete_many({"customer_id": {"$in": cids}})
print("  %-16s deleted %d" % ("customers", r.deleted_count))
print("DONE_CLEANUP_ALL")
