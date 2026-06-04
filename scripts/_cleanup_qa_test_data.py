"""Local-only: delete the QA test data created during the live walkthrough
(customer 9000000077 "QA TEST Workflow" + its eye test / prescription / queue).
No order was created (the POS flow was interrupted at the Products step).

  railway run --service MongoDB <venv-python> scripts/_cleanup_qa_test_data.py
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

MOBILE = "9000000077"
cust = db["customers"].find_one({"$or": [{"mobile": MOBILE}, {"phone": MOBILE}]})
if not cust:
    print("No QA test customer found for", MOBILE, "- nothing to clean.")
    sys.exit(0)

cid = cust.get("customer_id")
print("Found QA customer:", cid, "/", cust.get("name"), "/", MOBILE)

# Guard: only ever touch the clearly-marked QA test record.
name = (cust.get("name") or "").upper()
if "QA TEST" not in name and MOBILE != "9000000077":
    print("SAFETY ABORT: customer name does not look like QA test data:", name)
    sys.exit(4)

for coll, filt in [
    ("eye_tests", {"$or": [{"customer_id": cid}, {"customer_mobile": MOBILE}, {"mobile": MOBILE}]}),
    ("eye_test_queue", {"$or": [{"customer_id": cid}, {"mobile": MOBILE}, {"customer_mobile": MOBILE}]}),
    ("prescriptions", {"customer_id": cid}),
    ("clinical_records", {"customer_id": cid}),
]:
    try:
        r = db[coll].delete_many(filt)
        print("  %-16s deleted %d" % (coll, r.deleted_count))
    except Exception as e:  # noqa: BLE001
        print("  %-16s ERR %s" % (coll, e))

r = db["customers"].delete_one({"customer_id": cid})
print("  %-16s deleted %d" % ("customers", r.deleted_count))
print("DONE_CLEANUP")
