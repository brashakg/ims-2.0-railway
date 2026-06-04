"""Local-only READ-ONLY: report test data left in prod by recent checks/tests.
No writes/deletes. Run via:
  railway run --service MongoDB <venv-python> scripts/_check_test_data.py
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


def summ(d, keys):
    return {k: d.get(k) for k in keys if k in d}


# This session's live-QA customer + any QA/test-marked records.
test_cust = list(db["customers"].find({
    "$or": [
        {"mobile": {"$in": ["9000000077"]}},
        {"phone": {"$in": ["9000000077"]}},
        {"name": {"$regex": "QA TEST", "$options": "i"}},
        {"name": {"$regex": "^test", "$options": "i"}},
    ]
}))
print("=== TEST-LOOKING CUSTOMERS (%d) ===" % len(test_cust))
cids = []
for c in test_cust:
    cid = c.get("customer_id")
    cids.append(cid)
    print(" -", summ(c, ["customer_id", "name", "mobile", "phone", "created_at"]))

if cids:
    et = db["eye_tests"].count_documents({"customer_id": {"$in": cids}})
    rx = db["prescriptions"].count_documents({"customer_id": {"$in": cids}})
    q = db["eye_test_queue"].count_documents({"customer_id": {"$in": cids}})
    od = db["orders"].count_documents({"customer_id": {"$in": cids}})
    print("=== LINKED RECORDS for those customers ===")
    print("  eye_tests=%d  prescriptions=%d  eye_test_queue=%d  orders=%d" % (et, rx, q, od))

# Also: today's clinical activity (the walkthrough was today).
print("=== eye_tests with mobile 9000000077 (any link field) ===")
n = db["eye_tests"].count_documents({"$or": [{"mobile": "9000000077"}, {"customer_mobile": "9000000077"}]})
print("  count=%d" % n)
print("DONE_CHECK")
