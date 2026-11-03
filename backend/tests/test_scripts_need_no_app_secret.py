"""A one-off data script must run without the app's login secret.

backfill_average_cost.py died on prod (2026-11-03) with "JWT_SECRET_KEY
environment variable is required": it imported a constant from a router, and
importing api.routers runs auth.py, which demands the secret at import. The
shared rules now live in api.services.receipt_origin. This test runs the
scripts' dry runs in a child process with JWT_SECRET_KEY and ENVIRONMENT
UNSET, and scans every backend/scripts/*.py for a router import.
"""

import ast
import os
import pathlib
import subprocess
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]

_CHILD = r"""
import sys
sys.path.insert(0, ".")
import mongomock
db = mongomock.MongoClient().db
db.grns.insert_many([
    {"grn_id": "G1", "grn_subtype": "NO_PO", "status": "ACCEPTED",
     "accepted_at": "2026-10-20T00:00:00", "store_id": "S1",
     "items": [{"product_id": "P1"}]},
])
db.products.insert_one({"product_id": "P1", "sku": "SKU1", "cost_price": 100.0,
                        "cost_source": "GRN_WALKIN"})
db.vendor_bills.insert_one({"bill_id": "B1", "grn_id": "G1"})

import scripts.backfill_average_cost as bac
bac._db = lambda: db
assert bac.main([]) == 0
bac._walk_in_latest_price(db, db.products.find_one({"product_id": "P1"}), "P1")
bac.legacy_receipts(db)

import scripts.backfill_bill_store_id as bbs
assert bbs.backfill(db)["placed"] == 1

leaked = [m for m in sys.modules if m.startswith("api.routers")]
assert not leaked, leaked
print("OK")
"""


def test_backfill_dry_runs_without_jwt_secret():
    pytest.importorskip("mongomock")
    env = {k: v for k, v in os.environ.items() if k not in ("JWT_SECRET_KEY", "ENVIRONMENT")}
    r = subprocess.run([sys.executable, "-c", _CHILD], cwd=BACKEND, env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and "OK" in r.stdout, r.stdout[-2000:] + r.stderr[-2000:]


def test_no_script_imports_a_router():
    bad = []
    for path in sorted((BACKEND / "scripts").glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            bad += [f"{path.name}:{node.lineno} {n}" for n in names
                    if n == "api.routers" or n.startswith("api.routers.") or n == "api.main"]
    assert not bad, bad
