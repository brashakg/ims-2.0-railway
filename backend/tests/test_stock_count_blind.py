"""
IMS 2.0 -- THE COUNT IS BLIND UNTIL IT IS SUBMITTED
===================================================
Owner ruling 2026-08-25: a BLIND count is THE day-end. If the counter can see
"books say 14", the number written down is 14 and the count proves nothing.

The rule is about WHEN, not whether:

  * An OPEN (in_progress) session's RESPONSES carry NO expected quantities --
    not on GET /stock-count/{id} (no `system_quantities`, no
    `system_unit_fingerprints`, no `system_quantity` on any sheet line), not
    on the /start response, not on a row in the GET /stock-count list, and
    not echoed back by a recording scan (no system_count / variance /
    variance_percent).
  * The moment the session is COMPLETED, expected vs counted flows again --
    that comparison is the whole value of counting.
  * WITHHELD, NOT DESTROYED: the stored document keeps its opening snapshot;
    only the response body is stripped. Deleting a screen column is not a
    fix while the number still ships in the payload (this repo has shipped
    two response-body leaks of exactly that shape).

Every test here asserts on the SERVER RESPONSE BODY, per the house rule: a
DOM-only test passes while the number is still being shipped.

Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest \
     backend/tests/test_stock_count_blind.py -q

No emoji (Windows cp1252).
"""

from __future__ import annotations

import os
import sys
import uuid
from typing import Any, Dict

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import inventory as inv_mod  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402

STORE = "ST-BLIND"

# The fields an OPEN session's responses must never carry.
WITHHELD_DOC_FIELDS = ("system_quantities", "system_unit_fingerprints")
WITHHELD_SCAN_FIELDS = ("system_count", "variance", "variance_percent")


# ============================================================================
# Harness: the REAL inventory router over a REAL Mongo (mongomock fallback),
# same shape as test_stock_count_lifecycle.py. Nothing under test is stubbed.
# ============================================================================


@pytest.fixture(scope="module")
def mongo_db():
    from pymongo import MongoClient

    uri = (
        os.getenv("MONGODB_URL")
        or os.getenv("MONGODB_URI")
        or "mongodb://localhost:27017"
    )
    db_name = f"ims_test_blind_{uuid.uuid4().hex[:8]}"
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=2000)
        client.server_info()
    except Exception:
        try:
            import mongomock
        except ImportError:
            pytest.skip("no Mongo and no mongomock available")
            return None
        client = mongomock.MongoClient()
    db = client[db_name]
    try:
        yield db
    finally:
        try:
            client.drop_database(db_name)
        except Exception:
            pass
        client.close()


class _DBProxy:
    def __init__(self, db):
        self._db = db
        self.is_connected = True

    def get_collection(self, name):
        return self._db[name]

    def __getattr__(self, name):
        return self._db[name]


@pytest.fixture
def client(mongo_db, monkeypatch):
    from database.repositories.product_repository import (
        ProductRepository,
        StockRepository,
    )

    proxy = _DBProxy(mongo_db)
    monkeypatch.setattr(inv_mod, "_get_db", lambda: proxy)
    monkeypatch.setattr(
        inv_mod,
        "get_stock_repository",
        lambda: StockRepository(mongo_db["stock_units"]),
    )
    monkeypatch.setattr(
        inv_mod,
        "get_product_repository",
        lambda: ProductRepository(mongo_db["products"]),
    )

    app = FastAPI()
    app.include_router(inv_mod.router, prefix="/inventory")

    async def _user():
        # ADMIN on purpose: blindness is decided by SESSION STATUS, not by
        # role. If even an admin-counter got the figures, the same person
        # counting and reviewing would defeat the control.
        return {
            "user_id": "u-blind",
            "username": "counter",
            "roles": ["ADMIN"],
            "store_ids": [STORE],
            "active_store_id": STORE,
        }

    app.dependency_overrides[get_current_user] = _user
    return TestClient(app)


def _seed_product(mongo_db, category: str, qty: int = 7) -> tuple:
    pid = f"PRD-{uuid.uuid4().hex[:8]}"
    mongo_db["products"].insert_one(
        {
            "_id": pid,
            "product_id": pid,
            "sku": f"SKU-{pid[-6:]}",
            "brand": "Ray-Ban",
            "model": "RB3025",
            "category": category,
            "mrp": 5000.0,
            "cost_price": 2000.0,
            "is_active": True,
        }
    )
    barcodes = []
    for i in range(qty):
        bc = f"BC-{uuid.uuid4().hex[:10]}"
        mongo_db["stock_units"].insert_one(
            {
                "stock_id": f"STK-{uuid.uuid4().hex[:8]}",
                "product_id": pid,
                "store_id": STORE,
                "barcode": bc,
                "status": "AVAILABLE",
                "quantity": 1,
                "location_code": "DEFAULT",
                "created_at": f"2026-01-0{(i % 8) + 1}T00:00:00",
            }
        )
        barcodes.append(bc)
    return pid, barcodes


def _open_session(client, mongo_db, qty: int = 7):
    """One product with `qty` units, in a category of its own, and an OPEN
    session scoped to it. Returns (count_id, pid, barcodes)."""
    category = f"CAT-{uuid.uuid4().hex[:8]}"
    pid, barcodes = _seed_product(mongo_db, category, qty)
    r = client.post("/inventory/stock-count/start", json={"category": category})
    assert r.status_code == 200, r.text
    return r.json()["count_id"], pid, barcodes


# ============================================================================
# 1. THE KEY TEST: an open session's GET response body is blind
# ============================================================================


def test_an_open_session_response_carries_no_expected_quantities(
    client, mongo_db
):
    """Deleting the 'Books say' column is not a fix while the number still
    ships in the body, one devtools tab away. Assert on the RESPONSE BODY."""
    count_id, pid, _bc = _open_session(client, mongo_db, qty=7)

    r = client.get(f"/inventory/stock-count/{count_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "in_progress"

    for field in WITHHELD_DOC_FIELDS:
        assert field not in body, (
            f"an OPEN session's response shipped `{field}` -- the counter can "
            "read the expected figures out of devtools and the count proves "
            "nothing"
        )

    lines = body["expected_lines"]
    assert lines, "the sheet must still list what to count"
    for ln in lines:
        assert "system_quantity" not in ln, (
            "a sheet line on an OPEN session carried the expected quantity"
        )
        # The sheet stays USABLE blind: identity + own-answer state survive.
        assert ln["product_id"]
        assert ln["product_name"]
        assert ln["sku"]
        assert ln["counted_quantity"] is None


def test_withheld_not_destroyed_the_stored_snapshot_survives(client, mongo_db):
    """The strip is a response concern. The DB doc keeps the snapshot, or
    completion would have nothing to compare against."""
    count_id, pid, _bc = _open_session(client, mongo_db, qty=7)
    client.get(f"/inventory/stock-count/{count_id}")  # response was stripped

    doc = mongo_db["stock_counts"].find_one({"count_id": count_id})
    assert doc["system_quantities"] == {pid: 7}
    assert pid in (doc.get("system_unit_fingerprints") or {})


# ============================================================================
# 2. The other doors an open session leaks through
# ============================================================================


def test_the_start_response_is_blind(client, mongo_db):
    """POST /stock-count/start answers the very person about to count."""
    category = f"CAT-{uuid.uuid4().hex[:8]}"
    pid, _bc = _seed_product(mongo_db, category, qty=7)

    r = client.post("/inventory/stock-count/start", json={"category": category})
    assert r.status_code == 200, r.text
    body = r.json()
    for field in WITHHELD_DOC_FIELDS:
        assert field not in body, f"/start response shipped `{field}`"
    # ... while the persisted session kept its snapshot.
    doc = mongo_db["stock_counts"].find_one({"count_id": body["count_id"]})
    assert doc["system_quantities"] == {pid: 7}


def test_the_list_endpoint_is_blind_for_open_sessions(client, mongo_db):
    count_id, _pid, _bc = _open_session(client, mongo_db, qty=7)

    r = client.get("/inventory/stock-count", params={"store_id": STORE})
    assert r.status_code == 200, r.text
    rows = [c for c in r.json()["counts"] if c["count_id"] == count_id]
    assert rows, "the open session must still be listed"
    for field in WITHHELD_DOC_FIELDS:
        assert field not in rows[0], (
            f"the audit LIST shipped `{field}` for an open session"
        )


def test_a_recording_scan_echoes_no_variance(client, mongo_db):
    """The scan door used to answer every scan with system_count + variance
    -- a live 'you matched' readout while the count was still open."""
    count_id, pid, barcodes = _open_session(client, mongo_db, qty=7)

    r = client.post(
        "/inventory/stock-count-scan",
        json={"barcode": barcodes[0], "physical_count": 3, "count_id": count_id},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["recorded"] is True
    assert body["items_counted"] == 1
    for field in WITHHELD_SCAN_FIELDS:
        assert field not in body, (
            f"a recording scan on an open session echoed `{field}`"
        )
    # The answer still landed on the session.
    doc = mongo_db["stock_counts"].find_one({"count_id": count_id})
    assert doc["items"][0]["counted_quantity"] == 3


# ============================================================================
# 3. WHEN, not whether: submission opens the books
# ============================================================================


def test_a_completed_session_reveals_expected_vs_counted(client, mongo_db):
    """The comparison is the whole value of counting -- it must flow the
    moment the session leaves in_progress. Guards against over-withholding
    (a 'fix' that strips every status would pass test 1 and fail this)."""
    count_id, pid, _bc = _open_session(client, mongo_db, qty=7)
    r = client.post(
        f"/inventory/stock-count/{count_id}/items",
        json={"product_id": pid, "counted_quantity": 5},
    )
    assert r.status_code == 200, r.text
    r = client.post(f"/inventory/stock-count/{count_id}/complete", json={})
    assert r.status_code == 200, r.text

    r = client.get(f"/inventory/stock-count/{count_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "completed"
    assert body["system_quantities"] == {pid: 7}
    line = next(ln for ln in body["expected_lines"] if ln["product_id"] == pid)
    assert line["system_quantity"] == 7
    assert line["counted_quantity"] == 5
    vrow = next(v for v in body["variances"] if v["product_id"] == pid)
    assert vrow["variance"] == -2


def test_the_list_shows_a_completed_sessions_figures(client, mongo_db):
    count_id, pid, _bc = _open_session(client, mongo_db, qty=7)
    client.post(
        f"/inventory/stock-count/{count_id}/items",
        json={"product_id": pid, "counted_quantity": 5},
    )
    client.post(f"/inventory/stock-count/{count_id}/complete", json={})

    r = client.get("/inventory/stock-count", params={"store_id": STORE})
    row = next(c for c in r.json()["counts"] if c["count_id"] == count_id)
    assert row["status"] == "completed"
    assert row["variance_percentage"] is not None
    assert row["variances"], "the reviewer's list keeps the comparison"


# ============================================================================
# 4. The deliberate boundary: a SESSIONLESS scan is a live stock lookup
# ============================================================================


def test_a_sessionless_scan_still_answers_with_the_live_count(
    client, mongo_db
):
    """Without a count_id nothing is recorded; the endpoint is a lookup tool
    showing the same on-hand figure as the inventory dashboard. Documented
    here so a future 'tidy-up' does not silently widen or narrow the rule."""
    category = f"CAT-{uuid.uuid4().hex[:8]}"
    _pid, barcodes = _seed_product(mongo_db, category, qty=4)

    r = client.post(
        "/inventory/stock-count-scan",
        json={"barcode": barcodes[0], "physical_count": 4},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["recorded"] is False
    assert body["system_count"] == 4
    assert body["variance"] == 0
