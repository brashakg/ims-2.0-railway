"""
IMS 2.0 -- THE PHYSICAL STOCK COUNT MUST ACTUALLY COUNT
=======================================================
Lifecycle audit finding S2 / seam 2. Before this suite the counting screen
started a session and completed it with nothing in between, so completion ran
over an empty list and told the counter "Variance: 0%". Every count the
business had ever run reported a perfect shelf.

What is proved here, against the REAL inventory router over a REAL Mongo
(CI service container; an in-memory mongomock engine on a dev box without one)
-- nothing under test is stubbed:

  * COMPLETING AN EMPTY COUNT IS REFUSED (400). A session with no recorded
    lines is not a count, and must never report a variance at all.
  * A COUNTED QUANTITY IS PERSISTED by the scan door the count sheet uses --
    the SET and the COUNT are both asserted on the stored document.
  * A REAL DISCREPANCY IS REPORTED IN UNITS AND IN RUPEES, per line and in
    total, valued at the product's cost.
  * A UNIT SOLD MID-COUNT IS NEVER WRITTEN OFF: the write-off is conditional
    on the unit still being AVAILABLE at the instant of the write, and the
    shortfall is reported honestly instead of destroying a live sale.
  * A STORE MANAGER CANNOT WRITE OFF (owner ruling 2026-08-25 #8: write-offs
    are ADMIN / SUPERADMIN only, at every value).
  * THE SAME COUNT CANNOT BE CORRECTED TWICE -- no double write-off.
  * SHRINKAGE ROWS CARRY A RUPEE VALUE.

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_stock_count_lifecycle.py -q

No emoji (Windows cp1252).
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime
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

STORE = "ST-COUNT"


# ============================================================================
# Engine: the real Mongo CI runs against; mongomock only as a dev-box fallback
# ============================================================================


@pytest.fixture(scope="module")
def mongo_db():
    from pymongo import MongoClient

    uri = (
        os.getenv("MONGODB_URL")
        or os.getenv("MONGODB_URI")
        or "mongodb://localhost:27017"
    )
    db_name = f"ims_test_count_{uuid.uuid4().hex[:8]}"
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


def _client(mongo_db, monkeypatch, roles):
    """A TestClient over the REAL inventory router bound to the given engine."""
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
        return {
            "user_id": "u-count",
            "username": "counter",
            "roles": list(roles),
            "store_ids": [STORE],
            "active_store_id": STORE,
        }

    app.dependency_overrides[get_current_user] = _user
    return TestClient(app)


@pytest.fixture
def admin_client(mongo_db, monkeypatch):
    return _client(mongo_db, monkeypatch, ["ADMIN"])


@pytest.fixture
def manager_client(mongo_db, monkeypatch):
    return _client(mongo_db, monkeypatch, ["STORE_MANAGER"])


# ============================================================================
# Seed helpers -- shapes copied from the live collections
# ============================================================================


def _seed_product(mongo_db, cost: float = 2000.0, **over: Any) -> str:
    pid = over.pop("product_id", f"PRD-{uuid.uuid4().hex[:8]}")
    doc: Dict[str, Any] = {
        "_id": pid,
        "product_id": pid,
        "sku": f"SKU-{pid[-6:]}",
        "brand": "Ray-Ban",
        "model": "RB3025",
        "category": "SUNGLASS",
        "mrp": 5000.0,
        "cost_price": cost,
        "is_active": True,
    }
    doc.update(over)
    mongo_db["products"].insert_one(doc)
    return pid


def _seed_units(mongo_db, product_id: str, n: int, store_id: str = STORE):
    """n serialized AVAILABLE units, returning their barcodes."""
    barcodes = []
    for i in range(n):
        bc = f"BC-{uuid.uuid4().hex[:10]}"
        mongo_db["stock_units"].insert_one(
            {
                "stock_id": f"STK-{uuid.uuid4().hex[:8]}",
                "product_id": product_id,
                "store_id": store_id,
                "barcode": bc,
                "status": "AVAILABLE",
                "quantity": 1,
                "location_code": "DEFAULT",
                "created_at": f"2026-01-0{i + 1}T00:00:00",
            }
        )
        barcodes.append(bc)
    return barcodes


def _start(client) -> str:
    r = client.post("/inventory/stock-count/start", json={})
    assert r.status_code == 200, r.text
    return r.json()["count_id"]


# ============================================================================
# 1. THE LIE: completing a count where nothing was recorded
# ============================================================================


def test_completing_a_count_with_nothing_recorded_is_refused(admin_client):
    """The count sheet used to answer "Variance: 0%" for a shelf nobody read."""
    count_id = _start(admin_client)

    r = admin_client.post(f"/inventory/stock-count/{count_id}/complete", json={})

    assert r.status_code == 400, (
        "an empty count must be REFUSED, not reported as a perfect result; "
        f"got {r.status_code} {r.text}"
    )
    body = r.json()
    assert "counted" in body["detail"].lower()
    # and it must NOT report a variance of any kind
    assert "variance_percentage" not in body


def test_an_empty_count_stays_open_after_the_refusal(admin_client, mongo_db):
    """A refused completion must not have half-closed the session."""
    count_id = _start(admin_client)
    admin_client.post(f"/inventory/stock-count/{count_id}/complete", json={})

    doc = mongo_db["stock_counts"].find_one({"count_id": count_id})
    assert doc["status"] == "in_progress"
    assert doc.get("completed_at") is None
    assert doc.get("variances") == []


# ============================================================================
# 2. THE MISSING STEP: a counted quantity is actually written onto the session
# ============================================================================


def test_a_scan_records_the_counted_quantity_onto_the_session(admin_client, mongo_db):
    """The count sheet door. Assert the SET and the COUNT on the stored doc."""
    pid = _seed_product(mongo_db)
    barcodes = _seed_units(mongo_db, pid, 5)
    count_id = _start(admin_client)

    r = admin_client.post(
        "/inventory/stock-count-scan",
        json={"barcode": barcodes[0], "physical_count": 3, "count_id": count_id},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["recorded"] is True
    assert body["items_counted"] == 1
    # BLIND COUNT (owner ruling 2026-08-25): a scan recording onto an open
    # session must not echo the expected figure or a live variance.
    assert "system_count" not in body
    assert "variance" not in body
    assert "variance_percent" not in body

    doc = mongo_db["stock_counts"].find_one({"count_id": count_id})
    assert doc["items_counted"] == 1, "the session's line count must move"
    assert len(doc["items"]) == 1
    line = doc["items"][0]
    assert line["product_id"] == pid
    assert line["counted_quantity"] == 3, "the SET value must be what was counted"
    assert line["counted_by"] == "u-count"


def test_recounting_a_product_replaces_its_line_and_does_not_add_one(
    admin_client, mongo_db
):
    """A recount corrects the first pass; it must never be added to it."""
    pid = _seed_product(mongo_db)
    barcodes = _seed_units(mongo_db, pid, 4)
    count_id = _start(admin_client)

    admin_client.post(
        "/inventory/stock-count-scan",
        json={"barcode": barcodes[0], "physical_count": 2, "count_id": count_id},
    )
    r = admin_client.post(
        "/inventory/stock-count-scan",
        json={"barcode": barcodes[1], "physical_count": 4, "count_id": count_id},
    )
    assert r.json()["items_counted"] == 1

    doc = mongo_db["stock_counts"].find_one({"count_id": count_id})
    assert len(doc["items"]) == 1
    assert doc["items"][0]["counted_quantity"] == 4
    assert doc["items_counted"] == 1


def test_a_scan_into_a_completed_session_is_refused(admin_client, mongo_db):
    pid = _seed_product(mongo_db)
    barcodes = _seed_units(mongo_db, pid, 2)
    count_id = _start(admin_client)
    admin_client.post(
        "/inventory/stock-count-scan",
        json={"barcode": barcodes[0], "physical_count": 2, "count_id": count_id},
    )
    assert (
        admin_client.post(
            f"/inventory/stock-count/{count_id}/complete", json={}
        ).status_code
        == 200
    )

    r = admin_client.post(
        "/inventory/stock-count-scan",
        json={"barcode": barcodes[0], "physical_count": 9, "count_id": count_id},
    )
    assert r.status_code == 400
    assert "not in progress" in r.json()["detail"].lower()


def test_a_recorded_count_can_then_be_completed(admin_client, mongo_db):
    """End to end: start -> record -> complete now works, where before the
    only reachable path was start -> complete over an empty list."""
    pid = _seed_product(mongo_db)
    barcodes = _seed_units(mongo_db, pid, 3)
    count_id = _start(admin_client)
    admin_client.post(
        "/inventory/stock-count-scan",
        json={"barcode": barcodes[0], "physical_count": 3, "count_id": count_id},
    )

    r = admin_client.post(f"/inventory/stock-count/{count_id}/complete", json={})
    assert r.status_code == 200, r.text
    assert r.json()["items_counted"] == 1


# ============================================================================
# 3. THE VARIANCE IS REAL, AND IT IS IN RUPEES
# ============================================================================


def test_a_real_discrepancy_is_reported_in_units_and_in_rupees(admin_client, mongo_db):
    """Two frames short at Rs 2,000 cost is "2 short, Rs 4,000" -- per line
    and in total. "Variance: 0%" over an empty list was the whole defect."""
    pid = _seed_product(mongo_db, cost=2000.0)
    barcodes = _seed_units(mongo_db, pid, 5)
    count_id = _start(admin_client)

    admin_client.post(
        "/inventory/stock-count-scan",
        json={"barcode": barcodes[0], "physical_count": 3, "count_id": count_id},
    )
    r = admin_client.post(f"/inventory/stock-count/{count_id}/complete", json={})
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["shrinkage_units"] == 2, "two frames are missing"
    assert body["shrinkage_value"] == 4000.0, "and they are worth Rs 4,000 at cost"
    assert body["overage_units"] == 0
    assert body["overage_value"] == 0.0

    line = body["variances"][0]
    assert line["system_quantity"] == 5
    assert line["physical_quantity"] == 3
    assert line["variance"] == -2
    assert line["unit_cost"] == 2000.0
    assert line["variance_value"] == -4000.0

    doc = mongo_db["stock_counts"].find_one({"count_id": count_id})
    assert doc["shrinkage_value"] == 4000.0, "and it is PERSISTED, not just returned"


def test_a_line_with_no_cost_price_is_flagged_not_valued_at_zero(
    admin_client, mongo_db
):
    """Rs 0 must never be allowed to read as "nothing was lost"."""
    pid = _seed_product(mongo_db, cost=0)
    mongo_db["products"].update_one(
        {"_id": pid}, {"$unset": {"cost_price": "", "landed_cost": ""}}
    )
    barcodes = _seed_units(mongo_db, pid, 4)
    count_id = _start(admin_client)

    admin_client.post(
        "/inventory/stock-count-scan",
        json={"barcode": barcodes[0], "physical_count": 1, "count_id": count_id},
    )
    body = admin_client.post(
        f"/inventory/stock-count/{count_id}/complete", json={}
    ).json()

    assert body["shrinkage_units"] == 3
    assert body["shrinkage_value"] == 0.0
    assert (
        body["lines_without_cost"] == 1
    ), "a rupee total built from products with no cost must say so"


def test_an_overage_is_reported_separately_and_valued(admin_client, mongo_db):
    pid = _seed_product(mongo_db, cost=1500.0)
    barcodes = _seed_units(mongo_db, pid, 2)
    count_id = _start(admin_client)

    admin_client.post(
        "/inventory/stock-count-scan",
        json={"barcode": barcodes[0], "physical_count": 5, "count_id": count_id},
    )
    body = admin_client.post(
        f"/inventory/stock-count/{count_id}/complete", json={}
    ).json()

    assert body["overage_units"] == 3
    assert body["overage_value"] == 4500.0
    assert body["shrinkage_units"] == 0


# ============================================================================
# 4. THE CORRECTION STEP -- who may run it, and what it must never touch
# ============================================================================


def _counted_short(client, mongo_db, *, on_hand: int, counted: int, cost=2000.0):
    """A completed count that found `on_hand - counted` units missing."""
    pid = _seed_product(mongo_db, cost=cost)
    barcodes = _seed_units(mongo_db, pid, on_hand)
    count_id = _start(client)
    client.post(
        "/inventory/stock-count-scan",
        json={"barcode": barcodes[0], "physical_count": counted, "count_id": count_id},
    )
    assert (
        client.post(f"/inventory/stock-count/{count_id}/complete", json={}).status_code
        == 200
    )
    return pid, count_id, barcodes


def test_a_store_manager_cannot_write_off_missing_stock(
    admin_client, manager_client, mongo_db
):
    """Owner ruling 2026-08-25 (#8): ADMIN / SUPERADMIN only, at every value."""
    pid, count_id, _ = _counted_short(admin_client, mongo_db, on_hand=5, counted=3)

    r = manager_client.post(f"/inventory/stock-count/{count_id}/reconcile", json={})

    assert (
        r.status_code == 403
    ), f"a store manager must not be able to destroy stock; got {r.status_code}"
    assert (
        mongo_db["stock_units"].count_documents(
            {"product_id": pid, "status": "AVAILABLE"}
        )
        == 5
    ), "and nothing may have been voided by the refused call"
    assert mongo_db["stock_shrinkage"].count_documents({"count_id": count_id}) == 0


def test_a_unit_sold_during_the_count_is_never_written_off(admin_client, mongo_db):
    """The write-off may only take units that are still AVAILABLE at the
    instant of the write. A frame sold mid-count used to be overwritten from
    SOLD to VOID -- the sale destroyed and the shortfall still "corrected"."""
    pid, count_id, barcodes = _counted_short(
        admin_client, mongo_db, on_hand=5, counted=3
    )

    # The shop sells two frames between the count and the write-off.
    for bc in barcodes[:2]:
        mongo_db["stock_units"].update_one(
            {"barcode": bc}, {"$set": {"status": "SOLD", "sold_at": "2026-08-25"}}
        )

    r = admin_client.post(f"/inventory/stock-count/{count_id}/reconcile", json={})
    assert r.status_code == 200, r.text
    body = r.json()

    sold = list(mongo_db["stock_units"].find({"barcode": {"$in": barcodes[:2]}}))
    assert [u["status"] for u in sold] == [
        "SOLD",
        "SOLD",
    ], "a sold unit must never be voided from under the sale"
    assert (
        mongo_db["stock_units"].count_documents({"product_id": pid, "status": "VOID"})
        == 2
    )
    assert body["units_voided"] == 2
    assert body["units_not_voided"] == 0


def test_when_the_shelf_moved_too_far_the_shortfall_is_reported_not_forced(
    admin_client, mongo_db
):
    """If there are fewer AVAILABLE units left than the count said were
    missing, the write-off takes what it can and SAYS what it could not."""
    pid, count_id, barcodes = _counted_short(
        admin_client, mongo_db, on_hand=5, counted=1
    )  # 4 missing

    # Everything but one unit leaves the shelf before the manager gets to it.
    for bc in barcodes[:4]:
        mongo_db["stock_units"].update_one(
            {"barcode": bc}, {"$set": {"status": "SOLD"}}
        )

    body = admin_client.post(
        f"/inventory/stock-count/{count_id}/reconcile", json={}
    ).json()

    assert body["units_voided"] == 1
    assert body["units_not_voided"] == 3, "and the gap is reported, not forced"
    row = mongo_db["stock_shrinkage"].find_one({"count_id": count_id})
    assert row["shrinkage_quantity"] == 4
    assert row["units_voided"] == 1
    assert row["units_not_voided"] == 3


def test_the_same_count_cannot_be_written_off_twice(admin_client, mongo_db):
    pid, count_id, _ = _counted_short(admin_client, mongo_db, on_hand=5, counted=3)

    first = admin_client.post(f"/inventory/stock-count/{count_id}/reconcile", json={})
    assert first.status_code == 200, first.text
    assert first.json()["units_voided"] == 2

    second = admin_client.post(f"/inventory/stock-count/{count_id}/reconcile", json={})
    assert second.status_code in (400, 409), second.text

    assert (
        mongo_db["stock_units"].count_documents({"product_id": pid, "status": "VOID"})
        == 2
    ), "a second click must never double the write-off"
    assert mongo_db["stock_shrinkage"].count_documents({"count_id": count_id}) == 1


def test_a_completed_count_cannot_be_completed_again(admin_client, mongo_db):
    _pid, count_id, _ = _counted_short(admin_client, mongo_db, on_hand=5, counted=3)
    r = admin_client.post(f"/inventory/stock-count/{count_id}/complete", json={})
    assert r.status_code == 400
    assert "not in progress" in r.json()["detail"].lower()


# ============================================================================
# 5. THE SHRINKAGE RECORD CARRIES A RUPEE VALUE
# ============================================================================


def test_the_shrinkage_record_carries_a_rupee_value(admin_client, mongo_db):
    """It used to carry units only -- and be read by nothing, anywhere."""
    _pid, count_id, _ = _counted_short(
        admin_client, mongo_db, on_hand=5, counted=3, cost=2500.0
    )

    body = admin_client.post(
        f"/inventory/stock-count/{count_id}/reconcile", json={"notes": "back store"}
    ).json()
    assert body["shrinkage_value_written_off"] == 5000.0

    row = mongo_db["stock_shrinkage"].find_one({"count_id": count_id})
    assert row["unit_cost"] == 2500.0
    assert row["shrinkage_value"] == 5000.0
    assert row["notes"] == "back store"

    # ...and it is readable from the session a manager opens.
    doc = mongo_db["stock_counts"].find_one({"count_id": count_id})
    assert doc["status"] == "reconciled"
    assert doc["shrinkage_value_written_off"] == 5000.0
    assert doc["units_voided"] == 2


# ============================================================================
# 4b. THE RACE ITSELF -- a sale landing INSIDE the write-off's window
# ============================================================================


class _SaleLandsMidWriteOff:
    """The REAL stock_units collection, except one sale lands in the window
    between the write-off reading its candidates and writing them.

    Nothing about the subject is faked -- only the timing of a POS sale, which
    is otherwise impossible to schedule from a test. POS claims a unit with an
    atomic find_one_and_update on status=="AVAILABLE", so this is exactly the
    interleaving that happens on a busy counter.
    """

    def __init__(self, coll, barcode):
        self._c = coll
        self._bc = barcode
        self.sale_landed = False

    def find(self, *args, **kwargs):
        docs = list(self._c.find(*args, **kwargs))
        if not self.sale_landed:
            self._c.update_one(
                {"barcode": self._bc},
                {"$set": {"status": "SOLD", "sold_at": "2026-08-25T12:00:00"}},
            )
            self.sale_landed = True
        return docs

    def __getattr__(self, name):
        return getattr(self._c, name)


def test_a_sale_landing_mid_write_off_survives_the_write_off(
    admin_client, mongo_db, monkeypatch
):
    """The candidate list is already stale by the time it is written. The
    write must re-check AVAILABLE, lose the race, and leave the sale alone."""
    pid, count_id, barcodes = _counted_short(
        admin_client, mongo_db, on_hand=4, counted=2
    )  # 2 missing; the oldest two are the candidates

    racing = _SaleLandsMidWriteOff(mongo_db["stock_units"], barcodes[0])
    real_get = _DBProxy(mongo_db).get_collection

    class _Proxy:
        is_connected = True

        def get_collection(self, name):
            return racing if name == "stock_units" else real_get(name)

        def __getattr__(self, name):
            return real_get(name)

    monkeypatch.setattr(inv_mod, "_get_db", lambda: _Proxy())

    body = admin_client.post(
        f"/inventory/stock-count/{count_id}/reconcile", json={}
    ).json()

    assert racing.sale_landed, "the harness must actually have landed the sale"
    sold = mongo_db["stock_units"].find_one({"barcode": barcodes[0]})
    assert (
        sold["status"] == "SOLD"
    ), "the frame the customer just bought was written off from under the sale"
    assert "voided_at" not in sold

    assert body["units_voided"] == 1, "only the unit still on the shelf may go"
    assert body["units_not_voided"] == 1, "and the gap must be reported"
    assert (
        mongo_db["stock_units"].count_documents({"product_id": pid, "status": "VOID"})
        == 1
    )


class _OtherManagerClicksFirst:
    """The REAL stock_counts collection, except a SECOND write-off claims the
    count between this caller's status read and its writes.

    Only the timing is arranged; both callers run the real handler. This is a
    double-click on the write-off button, or two managers on two terminals --
    the check-then-act window that let one shortfall be written off twice.
    """

    def __init__(self, coll):
        self._c = coll
        self.fired = False

    def find_one(self, flt, *args, **kwargs):
        doc = self._c.find_one(flt, *args, **kwargs)
        if not self.fired and doc and doc.get("status") == "completed":
            self.fired = True
            self._c.update_one(
                {"count_id": doc["count_id"], "status": "completed"},
                {"$set": {"status": "reconciling"}},
            )
        return doc

    def __getattr__(self, name):
        return getattr(self._c, name)


def test_a_second_write_off_landing_mid_flight_takes_no_stock(
    admin_client, mongo_db, monkeypatch
):
    """The status this caller read is already stale. It must claim the count
    atomically and stand down, not write off the same shortfall again."""
    pid, count_id, _ = _counted_short(admin_client, mongo_db, on_hand=5, counted=3)

    racing = _OtherManagerClicksFirst(mongo_db["stock_counts"])
    real_get = _DBProxy(mongo_db).get_collection

    class _Proxy:
        is_connected = True

        def get_collection(self, name):
            return racing if name == "stock_counts" else real_get(name)

        def __getattr__(self, name):
            return real_get(name)

    monkeypatch.setattr(inv_mod, "_get_db", lambda: _Proxy())

    r = admin_client.post(f"/inventory/stock-count/{count_id}/reconcile", json={})

    assert racing.fired, "the harness must actually have raced this caller"
    assert (
        r.status_code == 409
    ), f"a write-off already in flight must be stood down; got {r.status_code}"
    assert (
        mongo_db["stock_units"].count_documents({"product_id": pid, "status": "VOID"})
        == 0
    ), "the losing caller destroyed stock the winner is already destroying"
    assert mongo_db["stock_shrinkage"].count_documents({"count_id": count_id}) == 0


# ============================================================================
# 6. A FRAME SOLD WHILE THE COUNT IS OPEN IS NOT MISSING
# ============================================================================
# The session snapshots on-hand when it OPENS. A frame sold at the till before
# the counter reaches that shelf leaves the shelf one short of the snapshot,
# so an honest count read as a shortage and the write-off then VOIDED a real,
# sellable frame -- and under the owner's block-the-oversell ruling that
# destroyed frame goes on to refuse a genuine sale.
#
# The fix is clock-free on purpose (comparing the till's local-naive
# `sold_at` against the count's UTC ISO string is the BUG-104 mixed-clock
# trap): at completion the snapshot is compared against LIVE on-hand, and any
# line whose on-hand moved is flagged and left out of the shortage.


def _sell_one_at_the_till(mongo_db, product_id: str, store_id: str = STORE):
    """Claim one unit exactly the way POS does: an atomic find_one_and_update
    on status == AVAILABLE, stamping the till's local-naive `sold_at`."""
    return mongo_db["stock_units"].find_one_and_update(
        {"product_id": product_id, "store_id": store_id, "status": "AVAILABLE"},
        {"$set": {"status": "SOLD", "sold_at": datetime.now()}},
    )


def test_a_frame_sold_while_the_count_is_open_is_not_reported_as_missing(
    admin_client, mongo_db
):
    """5 on the books, one sold at the till while the session is open, the
    counter honestly counts the 4 on the shelf. Nothing is missing."""
    pid = _seed_product(mongo_db, cost=1450.0)
    barcodes = _seed_units(mongo_db, pid, 5)
    count_id = _start(admin_client)

    assert _sell_one_at_the_till(mongo_db, pid) is not None

    admin_client.post(
        "/inventory/stock-count-scan",
        json={"barcode": barcodes[4], "physical_count": 4, "count_id": count_id},
    )
    body = admin_client.post(
        f"/inventory/stock-count/{count_id}/complete", json={}
    ).json()

    assert body["shrinkage_units"] == 0, (
        "a frame sold during the count is not a missing frame; "
        f"got {body['shrinkage_units']} short worth Rs {body['shrinkage_value']}"
    )
    assert body["shrinkage_value"] == 0.0
    assert body["lines_moved_during_count"] == 1
    line = body["variances"][0]
    assert line["moved_during_count"] is True
    assert line["system_quantity"] == 5, "what the books said when the count opened"
    assert line["system_quantity_now"] == 4, "what the books say now"


def test_the_write_off_never_destroys_a_frame_that_moved_during_the_count(
    admin_client, mongo_db
):
    """The end of the same day: an ADMIN presses write-off anyway. The frame
    the shop still owns must survive -- 4 available before, 4 after."""
    pid = _seed_product(mongo_db, cost=1450.0)
    barcodes = _seed_units(mongo_db, pid, 5)
    count_id = _start(admin_client)
    _sell_one_at_the_till(mongo_db, pid)
    admin_client.post(
        "/inventory/stock-count-scan",
        json={"barcode": barcodes[4], "physical_count": 4, "count_id": count_id},
    )
    admin_client.post(f"/inventory/stock-count/{count_id}/complete", json={})

    r = admin_client.post(f"/inventory/stock-count/{count_id}/reconcile", json={})
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["units_voided"] == 0, "there was nothing missing to write off"
    assert body["lines_skipped_moved"] == 1
    assert (
        mongo_db["stock_units"].count_documents(
            {"product_id": pid, "store_id": STORE, "status": "AVAILABLE"}
        )
        == 4
    ), "a good frame was destroyed and can no longer be sold"
    assert mongo_db["stock_shrinkage"].count_documents({"count_id": count_id}) == 0


def test_a_frame_sold_after_its_shelf_was_counted_is_still_not_missing(
    admin_client, mongo_db
):
    """The mirror case the opening snapshot already handled: the counter sees
    all 5, then one sells before Complete. Still nothing missing."""
    pid = _seed_product(mongo_db, cost=1450.0)
    barcodes = _seed_units(mongo_db, pid, 5)
    count_id = _start(admin_client)
    admin_client.post(
        "/inventory/stock-count-scan",
        json={"barcode": barcodes[0], "physical_count": 5, "count_id": count_id},
    )
    _sell_one_at_the_till(mongo_db, pid)

    body = admin_client.post(
        f"/inventory/stock-count/{count_id}/complete", json={}
    ).json()

    assert body["shrinkage_units"] == 0
    assert body["overage_units"] == 0, "and it must not read as a surplus either"


def test_a_genuine_shortage_on_a_shelf_that_never_moved_is_still_written_off(
    admin_client, mongo_db
):
    """The discriminator: suppressing moved lines must not suppress real
    shrinkage on a shelf where nothing was sold."""
    pid, count_id, _ = _counted_short(admin_client, mongo_db, on_hand=5, counted=3)
    body = admin_client.post(
        f"/inventory/stock-count/{count_id}/reconcile", json={}
    ).json()

    assert body["units_voided"] == 2
    assert body["lines_skipped_moved"] == 0
    assert (
        mongo_db["stock_units"].count_documents(
            {"product_id": pid, "status": "AVAILABLE"}
        )
        == 3
    )


# ============================================================================
# 7. HOW MUCH OF THE SHELF WAS ACTUALLY WALKED
# ============================================================================
# The completion guard was only "at least one line". The session has known the
# full expected set since it opened and never compared it, so counting 1
# product out of 400 reported "everything matched" and a stat tile reading
# Rs 0 missing for a shelf nobody walked. A counter gets interrupted; this is
# the lie that will actually happen on a shop floor.
#
# These sessions are scoped to their OWN category so the expected set is
# exactly what the test seeded (the module shares one engine, and a count with
# no category snapshots every product in the store).


def _start_in_own_category(client, mongo_db, *, products: int, units: int = 2):
    """N products in a category of their own, and a session scoped to it."""
    category = f"CAT-{uuid.uuid4().hex[:8]}"
    pids, barcodes = [], {}
    for _ in range(products):
        pid = _seed_product(mongo_db, cost=1000.0, category=category)
        pids.append(pid)
        barcodes[pid] = _seed_units(mongo_db, pid, units)
    r = client.post("/inventory/stock-count/start", json={"category": category})
    assert r.status_code == 200, r.text
    return r.json()["count_id"], pids, barcodes, category


def test_counting_one_product_out_of_four_is_not_everything_matched(
    admin_client, mongo_db
):
    count_id, pids, barcodes, _ = _start_in_own_category(
        admin_client, mongo_db, products=4
    )

    admin_client.post(
        "/inventory/stock-count-scan",
        json={
            "barcode": barcodes[pids[0]][0],
            "physical_count": 2,
            "count_id": count_id,
        },
    )
    body = admin_client.post(
        f"/inventory/stock-count/{count_id}/complete", json={}
    ).json()

    assert body["products_expected"] == 4
    assert body["products_counted"] == 1
    assert body["products_missed"] == 3
    assert body["coverage_percentage"] == 25.0
    assert body["full_count"] is False, (
        "1 of 4 products counted must never report as a full count -- "
        "that is the 'everything matched' lie for a shelf nobody walked"
    )

    doc = mongo_db["stock_counts"].find_one({"count_id": count_id})
    assert doc["coverage_percentage"] == 25.0, "and it is PERSISTED"
    assert doc["full_count"] is False
    assert set(doc["products_not_counted"]) == set(pids[1:])


def test_a_count_that_walked_every_expected_product_reports_a_full_count(
    admin_client, mongo_db
):
    """The discriminator: coverage must still read 100% when it really is."""
    count_id, pids, barcodes, _ = _start_in_own_category(
        admin_client, mongo_db, products=2
    )
    for pid in pids:
        admin_client.post(
            "/inventory/stock-count-scan",
            json={
                "barcode": barcodes[pid][0],
                "physical_count": 2,
                "count_id": count_id,
            },
        )

    body = admin_client.post(
        f"/inventory/stock-count/{count_id}/complete", json={}
    ).json()

    assert body["products_expected"] == 2
    assert body["products_counted"] == 2
    assert body["products_missed"] == 0
    assert body["coverage_percentage"] == 100.0
    assert body["full_count"] is True


def test_counting_something_the_session_never_expected_does_not_inflate_coverage(
    admin_client, mongo_db
):
    """Coverage is how much of the EXPECTED set was walked. A line for a
    product the session never expected is an overage, not coverage."""
    count_id, _pids, _bc, category = _start_in_own_category(
        admin_client, mongo_db, products=2
    )
    stranger = _seed_product(mongo_db, cost=1000.0, category=category)  # none on hand

    r = admin_client.post(
        f"/inventory/stock-count/{count_id}/items",
        json={"product_id": stranger, "counted_quantity": 1},
    )
    assert r.status_code == 200, r.text

    body = admin_client.post(
        f"/inventory/stock-count/{count_id}/complete", json={}
    ).json()

    assert body["products_expected"] == 2
    assert body["products_counted"] == 0, "no expected product was walked"
    assert body["coverage_percentage"] == 0.0
    assert body["full_count"] is False


# ============================================================================
# 8. THE COUNT SHEET: A STYLE THAT HAS WALKED ENTIRELY CAN STILL BE RECORDED
# ============================================================================
# The only wired door was the barcode scanner. If the last unit of a style has
# gone, so has its label -- which is exactly the case a count exists to find.
# The session must therefore hand the screen the LIST of what it expects, so
# every expected line has a quantity box whether or not a unit survives.


def test_the_session_hands_the_screen_the_lines_it_expects_to_find(
    admin_client, mongo_db
):
    count_id, pids, _bc, _cat = _start_in_own_category(
        admin_client, mongo_db, products=2, units=2
    )

    r = admin_client.get(f"/inventory/stock-count/{count_id}")
    assert r.status_code == 200, r.text
    lines = r.json()["expected_lines"]

    assert {ln["product_id"] for ln in lines} == set(pids), (
        "the count sheet must list every product the session expects, "
        "not only the ones whose barcode still exists on a shelf"
    )
    assert len(lines) == 2
    for ln in lines:
        # BLIND COUNT: an OPEN session's sheet lists WHAT to count, never
        # how many the books expect (owner ruling 2026-08-25).
        assert "system_quantity" not in ln
        assert ln["counted_quantity"] is None, "nothing counted yet"
        assert ln["product_name"], "a line with no name cannot be counted"
        assert ln["sku"]


def test_a_style_with_nothing_left_on_the_shelf_can_be_counted_as_zero(
    admin_client, mongo_db
):
    """Both frames of this style have walked. There is no label to scan, so
    the counter types 0 against the sheet line -- and the loss is found."""
    count_id, pids, _bc, _cat = _start_in_own_category(
        admin_client, mongo_db, products=1, units=2
    )
    pid = pids[0]

    r = admin_client.post(
        f"/inventory/stock-count/{count_id}/items",
        json={"product_id": pid, "counted_quantity": 0},
    )
    assert r.status_code == 200, r.text

    sheet = admin_client.get(f"/inventory/stock-count/{count_id}").json()
    assert sheet["expected_lines"][0]["counted_quantity"] == 0, (
        "a counted zero must read back as zero, never as 'not counted yet'"
    )

    body = admin_client.post(
        f"/inventory/stock-count/{count_id}/complete", json={}
    ).json()
    assert body["shrinkage_units"] == 2
    assert body["shrinkage_value"] == 2000.0
    assert body["full_count"] is True

    admin_client.post(f"/inventory/stock-count/{count_id}/reconcile", json={})
    assert (
        mongo_db["stock_units"].count_documents(
            {"product_id": pid, "status": "AVAILABLE"}
        )
        == 0
    ), "the two frames that walked are off the books"


# ============================================================================
# 9. WRITING OFF A COUNT MUST NOT ERASE IT FROM THE ACCOUNTABILITY REPORT
# ============================================================================
# /inventory/accountability/shrinkage queried status == "completed" only, and
# the write-off flips the count to "reconciled" -- so the one report that
# names who was responsible for a shelf lost the count the moment the loss was
# confirmed. Nothing ever reached "reconciled" before this branch, so nobody
# had seen it happen.


def test_a_written_off_count_still_names_who_was_responsible(admin_client, mongo_db):
    mongo_db["stock_accountability"].delete_many({"store_id": STORE})
    mongo_db["stock_accountability"].insert_one(
        {
            "store_id": STORE,
            "category": "ALL",
            "staff_id": "u-ravi",
            "staff_name": "Ravi",
        }
    )
    _pid, count_id, _bc = _counted_short(admin_client, mongo_db, on_hand=5, counted=3)

    before = admin_client.get("/inventory/accountability/shrinkage").json()["rows"]
    audit_numbers = {r["audit_number"] for r in before}
    doc = mongo_db["stock_counts"].find_one({"count_id": count_id})
    assert doc["audit_number"] in audit_numbers
    assert [r for r in before if r["audit_number"] == doc["audit_number"]][0][
        "custodian_name"
    ] == "Ravi"

    assert (
        admin_client.post(
            f"/inventory/stock-count/{count_id}/reconcile", json={}
        ).status_code
        == 200
    )

    after = admin_client.get("/inventory/accountability/shrinkage").json()["rows"]
    row = [r for r in after if r["audit_number"] == doc["audit_number"]]
    assert row, (
        "writing off the loss erased the count from the only report that says "
        "who was responsible for that shelf"
    )
    assert row[0]["custodian_name"] == "Ravi"


# ============================================================================
# 10. "reconciling" MUST NOT BE A DEAD END
# ============================================================================
# The write-off voids the units FIRST and writes the shrinkage audit rows
# after. If that insert fails the endpoint 500s (correctly -- a lost audit
# trail for stock just destroyed is worth shouting about), but the count is
# then parked in "reconciling" forever: reconcile 400s ("only completed
# counts"), complete 400s ("not in progress"), and no route resets it.
#
# Re-running the write-off is NOT the answer -- the units are already gone, so
# a retry would void a second set. The stuck count is finished from what the
# write-off ACTUALLY did: the voided units still carry
# void_reason="cycle-count-reconcile:{count_id}".


class _ShrinkageAuditIsDown:
    """The real stock_shrinkage collection, except insert_many fails once."""

    def __init__(self, coll):
        self._c = coll
        self.blown = False

    def insert_many(self, *a, **kw):
        if not self.blown:
            self.blown = True
            raise RuntimeError("audit store unavailable")
        return self._c.insert_many(*a, **kw)

    def __getattr__(self, name):
        return getattr(self._c, name)


def _stick_a_count(admin_client, mongo_db, monkeypatch):
    """A count whose write-off destroyed the stock and lost the audit write."""
    pid, count_id, barcodes = _counted_short(
        admin_client, mongo_db, on_hand=5, counted=3
    )
    broken = _ShrinkageAuditIsDown(mongo_db["stock_shrinkage"])
    real_get = _DBProxy(mongo_db).get_collection

    class _Proxy:
        is_connected = True

        def get_collection(self, name):
            return broken if name == "stock_shrinkage" else real_get(name)

        def __getattr__(self, name):
            return real_get(name)

    monkeypatch.setattr(inv_mod, "_get_db", lambda: _Proxy())
    r = admin_client.post(f"/inventory/stock-count/{count_id}/reconcile", json={})
    assert r.status_code == 500, f"a lost audit trail must fail loud; got {r.text}"
    assert broken.blown
    monkeypatch.setattr(inv_mod, "_get_db", lambda: _DBProxy(mongo_db))
    return pid, count_id, barcodes


def test_a_write_off_that_lost_its_audit_write_leaves_the_count_stuck(
    admin_client, mongo_db, monkeypatch
):
    """The precondition, proved rather than assumed."""
    pid, count_id, _bc = _stick_a_count(admin_client, mongo_db, monkeypatch)

    doc = mongo_db["stock_counts"].find_one({"count_id": count_id})
    assert doc["status"] == "reconciling"
    assert (
        mongo_db["stock_units"].count_documents({"product_id": pid, "status": "VOID"})
        == 2
    ), "the stock is already destroyed -- a retry must never void a second set"
    assert mongo_db["stock_shrinkage"].count_documents({"count_id": count_id}) == 0
    # and every other door is shut
    assert (
        admin_client.post(
            f"/inventory/stock-count/{count_id}/reconcile", json={}
        ).status_code
        == 400
    )
    assert (
        admin_client.post(
            f"/inventory/stock-count/{count_id}/complete", json={}
        ).status_code
        == 400
    )


def test_an_admin_can_finish_a_stuck_write_off_without_destroying_more_stock(
    admin_client, mongo_db, monkeypatch
):
    pid, count_id, _bc = _stick_a_count(admin_client, mongo_db, monkeypatch)

    r = admin_client.post(
        f"/inventory/stock-count/{count_id}/reconcile/finish",
        json={"notes": "audit store came back"},
    )
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["units_voided"] == 2, "what the write-off actually took"
    assert body["shrinkage_lines"] == 1
    assert (
        mongo_db["stock_units"].count_documents({"product_id": pid, "status": "VOID"})
        == 2
    ), "finishing the stuck count must never destroy a second set of units"
    assert (
        mongo_db["stock_units"].count_documents(
            {"product_id": pid, "status": "AVAILABLE"}
        )
        == 3
    )

    doc = mongo_db["stock_counts"].find_one({"count_id": count_id})
    assert doc["status"] == "reconciled"
    row = mongo_db["stock_shrinkage"].find_one({"count_id": count_id})
    assert row["units_voided"] == 2
    assert row["shrinkage_value"] == 4000.0, "rebuilt from what was really taken"
    assert row["recovered"] is True


def test_finishing_a_stuck_count_twice_writes_one_audit_trail(
    admin_client, mongo_db, monkeypatch
):
    _pid, count_id, _bc = _stick_a_count(admin_client, mongo_db, monkeypatch)
    admin_client.post(f"/inventory/stock-count/{count_id}/reconcile/finish", json={})

    again = admin_client.post(
        f"/inventory/stock-count/{count_id}/reconcile/finish", json={}
    )
    assert again.status_code == 400
    assert mongo_db["stock_shrinkage"].count_documents({"count_id": count_id}) == 1


def test_a_store_manager_cannot_finish_a_stuck_write_off(
    admin_client, manager_client, mongo_db, monkeypatch
):
    """Owner ruling 2026-08-25 (#8) covers every door onto a write-off."""
    _pid, count_id, _bc = _stick_a_count(admin_client, mongo_db, monkeypatch)

    r = manager_client.post(
        f"/inventory/stock-count/{count_id}/reconcile/finish", json={}
    )
    assert r.status_code == 403
    assert (
        mongo_db["stock_counts"].find_one({"count_id": count_id})["status"]
        == "reconciling"
    )


class _ShrinkageAuditDiesPartWay:
    """insert_many is ORDERED: it can land the first row and then fail. The
    stuck count is then part-audited, and finishing it must not write a
    second row for the product that already has one."""

    def __init__(self, coll):
        self._c = coll
        self.blown = False

    def insert_many(self, docs, *a, **kw):
        docs = list(docs)
        if not self.blown:
            self.blown = True
            if docs:
                self._c.insert_one(docs[0])
            raise RuntimeError("audit store died after the first row")
        return self._c.insert_many(docs, *a, **kw)

    def __getattr__(self, name):
        return getattr(self._c, name)


def test_finishing_a_part_audited_count_does_not_double_the_audit_trail(
    admin_client, mongo_db, monkeypatch
):
    """Two shrinkage lines, the first row written before the audit store
    died. Finishing writes the missing row only."""
    count_id, pids, barcodes, _cat = _start_in_own_category(
        admin_client, mongo_db, products=2, units=3
    )
    for pid in pids:
        admin_client.post(
            "/inventory/stock-count-scan",
            json={
                "barcode": barcodes[pid][0],
                "physical_count": 1,
                "count_id": count_id,
            },
        )
    assert (
        admin_client.post(
            f"/inventory/stock-count/{count_id}/complete", json={}
        ).status_code
        == 200
    )

    broken = _ShrinkageAuditDiesPartWay(mongo_db["stock_shrinkage"])
    real_get = _DBProxy(mongo_db).get_collection

    class _Proxy:
        is_connected = True

        def get_collection(self, name):
            return broken if name == "stock_shrinkage" else real_get(name)

        def __getattr__(self, name):
            return real_get(name)

    monkeypatch.setattr(inv_mod, "_get_db", lambda: _Proxy())
    assert (
        admin_client.post(
            f"/inventory/stock-count/{count_id}/reconcile", json={}
        ).status_code
        == 500
    )
    monkeypatch.setattr(inv_mod, "_get_db", lambda: _DBProxy(mongo_db))
    assert broken.blown
    assert mongo_db["stock_shrinkage"].count_documents({"count_id": count_id}) == 1

    body = admin_client.post(
        f"/inventory/stock-count/{count_id}/reconcile/finish", json={}
    ).json()

    assert body["audit_rows_written"] == 1, "only the row that was lost"
    rows = list(mongo_db["stock_shrinkage"].find({"count_id": count_id}))
    assert len(rows) == 2, (
        "the product that already had an audit row was given a second one -- "
        "the loss now reads as twice what was taken"
    )
    assert {r["product_id"] for r in rows} == set(pids)
    assert sum(r["units_voided"] for r in rows) == 4


# ============================================================================
# 11. AN OVERRIDE MAY NEVER WRITE OFF MORE THAN THE COUNT FOUND
# ============================================================================
# The write-off accepts per-product overrides. Nothing bounded them by what
# was counted, so an override of 0 on a count that found NOTHING missing voided
# the whole shelf -- with no record of who set the number.


def test_an_override_cannot_write_off_more_than_the_count_found(
    admin_client, mongo_db
):
    pid, count_id, _bc = _counted_short(admin_client, mongo_db, on_hand=5, counted=5)

    r = admin_client.post(
        f"/inventory/stock-count/{count_id}/reconcile",
        json={"overrides": [{"product_id": pid, "accepted_quantity": 0}]},
    )

    assert r.status_code == 400, (
        "an override below the counted quantity destroys stock the count "
        f"never said was missing; got {r.status_code} {r.text}"
    )
    assert (
        mongo_db["stock_units"].count_documents(
            {"product_id": pid, "status": "AVAILABLE"}
        )
        == 5
    ), "the whole shelf was voided by a number nobody counted"
    assert mongo_db["stock_shrinkage"].count_documents({"count_id": count_id}) == 0
    assert (
        mongo_db["stock_counts"].find_one({"count_id": count_id})["status"]
        == "completed"
    ), "a refused override must not park the count in reconciling"


def test_an_override_that_accepts_more_than_was_counted_is_stamped_on_the_record(
    admin_client, mongo_db
):
    """Overriding UPWARDS is the legitimate case (a recount found more), it
    writes off LESS, and the record has to say a human changed the number."""
    pid, count_id, _bc = _counted_short(
        admin_client, mongo_db, on_hand=5, counted=2, cost=1000.0
    )

    body = admin_client.post(
        f"/inventory/stock-count/{count_id}/reconcile",
        json={"overrides": [{"product_id": pid, "accepted_quantity": 4}]},
    ).json()

    assert body["units_voided"] == 1, "3 were counted short, 1 is accepted as missing"
    row = mongo_db["stock_shrinkage"].find_one({"count_id": count_id})
    assert row["accepted_quantity"] == 4
    assert row["counted_quantity"] == 2
    assert row["override_applied"] is True
    assert row["overridden_by"] == "u-count", "who changed the number"


def test_a_write_off_with_no_override_is_not_stamped_as_overridden(
    admin_client, mongo_db
):
    """The discriminator: the stamp must mean something."""
    _pid, count_id, _bc = _counted_short(admin_client, mongo_db, on_hand=5, counted=3)
    admin_client.post(f"/inventory/stock-count/{count_id}/reconcile", json={})

    row = mongo_db["stock_shrinkage"].find_one({"count_id": count_id})
    assert row["override_applied"] is False
    assert row.get("overridden_by") is None


# ============================================================================
# 9. MOVEMENT THAT CANCELS OUT IS STILL MOVEMENT
# ============================================================================
# "Did this line move while the session was open?" used to be answered by
# comparing two TOTALS. One frame sells at the till at 10:15 and one is
# received into the stockroom at 11:00: the totals are identical, the line
# reads as untouched, the counter's honest shelf count of 2 is banked as
# shrinkage, and the write-off destroys a frame the shop still owns and could
# still sell. The session now records WHICH units were on hand when it opened,
# and a different SET of units is movement whatever the totals say.


def _receive_one_into_the_stockroom(mongo_db, product_id: str) -> str:
    """A GRN landing mid-count: one more unit of the same style, in the back
    room, where the counter walking the shelf will never see it."""
    return _seed_units(mongo_db, product_id, 1)[0]


def _available_unit_ids(mongo_db, product_id: str, store_id: str = STORE) -> set:
    """The SET of units on the shelf right now -- not how many, WHICH."""
    return {
        u["stock_id"]
        for u in mongo_db["stock_units"].find(
            {"product_id": product_id, "store_id": store_id, "status": "AVAILABLE"}
        )
    }


def _start_in_category(client, category: str) -> str:
    r = client.post("/inventory/stock-count/start", json={"category": category})
    assert r.status_code == 200, r.text
    return r.json()["count_id"]


def test_a_sale_and_a_delivery_that_cancel_out_are_not_a_missing_frame(
    admin_client, mongo_db
):
    """3 on the books. One sells at 10:15, one is received at 11:00, and at
    12:00 the counter walks the shelf and honestly writes 2. The books say 3
    both times -- and 2 frames on the shelf plus 1 in the stockroom is still
    3 sellable frames. Nothing is missing."""
    pid = _seed_product(mongo_db, cost=2000.0)
    barcodes = _seed_units(mongo_db, pid, 3)
    count_id = _start(admin_client)

    assert _sell_one_at_the_till(mongo_db, pid) is not None  # 10:15
    _receive_one_into_the_stockroom(mongo_db, pid)  # 11:00

    admin_client.post(  # 12:00 -- the shelf, honestly
        "/inventory/stock-count-scan",
        json={"barcode": barcodes[2], "physical_count": 2, "count_id": count_id},
    )
    body = admin_client.post(
        f"/inventory/stock-count/{count_id}/complete", json={}
    ).json()
    line = body["variances"][0]

    assert line["system_quantity"] == 3
    assert line["system_quantity_now"] == 3, (
        "the two TOTALS are identical -- that is the whole trap; if this is "
        "not 3 the case being tested has not been set up"
    )
    assert line["units_changed_during_count"] is True, (
        "a different set of frames is on hand than when the session opened"
    )
    assert line["moved_during_count"] is True, (
        "equal-and-opposite movement cancelled out and the line read as "
        "untouched, so an honest shelf count was banked as shrinkage"
    )
    assert body["shrinkage_units"] == 0, (
        f"a real frame was reported missing: {body['shrinkage_units']} unit(s) "
        f"worth Rs {body['shrinkage_value']}"
    )
    assert body["shrinkage_value"] == 0.0
    assert body["lines_moved_during_count"] == 1


def test_the_write_off_spares_the_shelf_when_movement_cancelled_out(
    admin_client, mongo_db
):
    """The end of that same day: an ADMIN presses write-off anyway. Every
    frame the shop still owns must still be there -- the SAME frames, not
    merely the same number of them."""
    pid = _seed_product(mongo_db, cost=2000.0)
    barcodes = _seed_units(mongo_db, pid, 3)
    count_id = _start(admin_client)
    _sell_one_at_the_till(mongo_db, pid)
    _receive_one_into_the_stockroom(mongo_db, pid)
    admin_client.post(
        "/inventory/stock-count-scan",
        json={"barcode": barcodes[2], "physical_count": 2, "count_id": count_id},
    )
    admin_client.post(f"/inventory/stock-count/{count_id}/complete", json={})

    on_the_shelf_before = _available_unit_ids(mongo_db, pid)
    assert len(on_the_shelf_before) == 3

    r = admin_client.post(f"/inventory/stock-count/{count_id}/reconcile", json={})
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["units_voided"] == 0, "there was nothing missing to write off"
    assert body["lines_skipped_moved"] == 1
    assert _available_unit_ids(mongo_db, pid) == on_the_shelf_before, (
        "a good frame was destroyed and can no longer be sold"
    )
    assert mongo_db["stock_shrinkage"].count_documents({"count_id": count_id}) == 0


def test_a_shelf_whose_every_frame_was_replaced_is_not_an_untouched_shelf(
    admin_client, mongo_db
):
    """The purest form of the bug: 3 frames go out on a transfer and 3 fresh
    ones arrive while the session is open. The total never moved and not one
    of the frames is the same, so this count is stale and must be flagged --
    never settled against the opening snapshot."""
    pid = _seed_product(mongo_db, cost=2000.0)
    barcodes = _seed_units(mongo_db, pid, 3)
    count_id = _start(admin_client)

    before = _available_unit_ids(mongo_db, pid)
    mongo_db["stock_units"].update_many(
        {"product_id": pid, "store_id": STORE, "status": "AVAILABLE"},
        {"$set": {"status": "TRANSFERRED"}},
    )
    _seed_units(mongo_db, pid, 3)
    after = _available_unit_ids(mongo_db, pid)
    assert len(after) == 3 and not (before & after), "set up: every frame replaced"

    admin_client.post(
        "/inventory/stock-count-scan",
        json={"barcode": barcodes[0], "physical_count": 3, "count_id": count_id},
    )
    body = admin_client.post(
        f"/inventory/stock-count/{count_id}/complete", json={}
    ).json()
    line = body["variances"][0]

    assert line["system_quantity"] == 3 and line["system_quantity_now"] == 3
    assert line["variance"] == 0
    assert line["units_changed_during_count"] is True
    assert line["moved_during_count"] is True, (
        "every frame on this shelf changed hands during the count; the "
        "matching totals are a coincidence, not a clean line"
    )


def test_a_shelf_nobody_touched_is_still_not_flagged_as_moved(
    admin_client, mongo_db
):
    """The discriminator: flagging on the SET must not flag every line, or a
    genuine shortage would never be correctable again."""
    pid, count_id, _ = _counted_short(admin_client, mongo_db, on_hand=5, counted=3)

    doc = mongo_db["stock_counts"].find_one({"count_id": count_id})
    line = doc["variances"][0]
    assert line["units_changed_during_count"] is False
    assert line["moved_during_count"] is False

    body = admin_client.post(
        f"/inventory/stock-count/{count_id}/reconcile", json={}
    ).json()
    assert body["units_voided"] == 2, "a real shortage is still written off"
    assert body["lines_skipped_moved"] == 0
    assert len(_available_unit_ids(mongo_db, pid)) == 3


def test_the_opening_snapshot_is_a_set_of_units_not_just_a_total(
    admin_client, mongo_db
):
    """The mechanism itself, without reference to its internals: an untouched
    shelf fingerprints the same twice, and swapping one frame for another
    changes the fingerprint while leaving the total alone."""
    category = f"CAT-{uuid.uuid4().hex[:8]}"
    pid = _seed_product(mongo_db, cost=2000.0, category=category)
    _seed_units(mongo_db, pid, 3)

    def _snapshot(count_id):
        return mongo_db["stock_counts"].find_one({"count_id": count_id})

    first = _snapshot(_start_in_category(admin_client, category))
    second = _snapshot(_start_in_category(admin_client, category))
    assert first["system_quantities"] == {pid: 3}
    assert pid in (first.get("system_unit_fingerprints") or {}), (
        "the session recorded how many units were on hand but not WHICH ones, "
        "so movement that cancels out will read as an untouched shelf"
    )
    assert (
        first["system_unit_fingerprints"][pid]
        == second["system_unit_fingerprints"][pid]
    ), "nothing moved between these two sessions, so the picture must match"

    _sell_one_at_the_till(mongo_db, pid)
    _receive_one_into_the_stockroom(mongo_db, pid)
    third = _snapshot(_start_in_category(admin_client, category))

    assert third["system_quantities"][pid] == 3, "the TOTAL is unchanged"
    assert (
        third["system_unit_fingerprints"][pid]
        != first["system_unit_fingerprints"][pid]
    ), "but they are not the same three frames, and the snapshot must say so"


# ============================================================================
# 10. WHICH PRODUCTS A COUNT IS EXPECTED TO WALK
# ============================================================================
# The same snapshot, used by the blind day-end count to know how much of the
# shelf was walked. Tested here because it reads the real stock ledger.


def test_the_scope_snapshot_lists_the_products_with_stock_at_this_store(
    mongo_db,
):
    from api.routers.inventory import _scoped_product_ids

    category = f"CAT-{uuid.uuid4().hex[:8]}"
    kept = _seed_product(mongo_db, category=category)
    _seed_units(mongo_db, kept, 2)
    also_kept = _seed_product(mongo_db, category=category)
    _seed_units(mongo_db, also_kept, 1)
    other_store = _seed_product(mongo_db, category=category)
    _seed_units(mongo_db, other_store, 3, store_id="ST-ELSEWHERE")
    no_stock = _seed_product(mongo_db, category=category)

    scope = _scoped_product_ids(_DBProxy(mongo_db), STORE, category)

    assert set(scope) == {kept, also_kept}, (
        "the scope is the SET of products this store actually has on hand"
    )
    assert other_store not in scope and no_stock not in scope


def test_a_category_with_no_products_expects_nothing_not_everything(mongo_db):
    """The dangerous fallback: an empty category must not silently widen to
    the whole store."""
    from api.routers.inventory import _scoped_product_ids

    pid = _seed_product(mongo_db, category="REAL-CATEGORY")
    _seed_units(mongo_db, pid, 2)

    assert _scoped_product_ids(_DBProxy(mongo_db), STORE, "NO-SUCH-CATEGORY") == []


def test_a_scope_that_cannot_be_read_is_not_an_empty_shelf(mongo_db):
    """"I could not look" must never be recorded as "there was nothing to
    look at" -- an empty scope reads as a completed full count."""
    from api.routers.inventory import _scoped_product_ids

    assert _scoped_product_ids(None, STORE) is None
    assert _scoped_product_ids(_DBProxy(mongo_db), "") is None


# ============================================================================
# 11. ONE DEFINITION OF "ON HAND" INSIDE ONE VERDICT
# ============================================================================
# The count's verdict has two halves and they used to read the shelf two
# different ways:
#   * COVERAGE  (which products the session is expected to walk) listed
#     ["AVAILABLE", "RESERVED"];
#   * VARIANCE  (what the system thinks is on hand per line) used the canonical
#     allowlist, which also tolerates the legacy lowercase "available" /
#     "IN_STOCK" shapes and a unit with no `status` field at all.
# A unit in one of those legacy shapes was therefore INVISIBLE to coverage and
# VISIBLE to the variance: its product never entered the expected SET, so
# skipping it cost the counter nothing and a half-walked shelf locked as a
# clean day-end. The owner ruled (2026-08-25) that the blind count IS the
# day-end, so that is the exact lie this must not permit.
#
# These probe BOTH implementations over the SAME unit and require them to
# agree -- a differential check, so re-splitting the definition fails here
# whichever copy is edited.

# Every shape a physically-present unit is stored in, live or legacy. The CASE
# variants are round-2 finding MF1: `canonical_state` upper-cases before it
# maps, so by this codebase's own rule `reserved` IS a RESERVED unit -- but the
# coverage clause hand-appended the literal "RESERVED" onto an allowlist that
# carried both cases of every other word, so a lowercase `reserved` unit stayed
# out of the expected set and bought a clean day-end over a half-walked shelf.
_ON_HAND_SHAPES = {
    "AVAILABLE": {"status": "AVAILABLE"},
    "RESERVED (held for an order, still on this shelf)": {"status": "RESERVED"},
    "reserved (MF1: legacy lowercase)": {"status": "reserved"},
    "Reserved (title case)": {"status": "Reserved"},
    "IN_STOCK (legacy)": {"status": "IN_STOCK"},
    "in_stock (legacy lowercase)": {"status": "in_stock"},
    "available (legacy lowercase)": {"status": "available"},
    "Available (title case)": {"status": "Available"},
    "padded ' available ' import": {"status": " available "},
    "no status field at all": {},
    "status stored as null": {"status": None},
}

# ...and shapes that are NOT on this shelf, which both halves must also agree
# on -- in their case variants too, or the same split reappears inverted and a
# phantom product is added to every count sheet.
_GONE_SHAPES = {
    "SOLD": {"status": "SOLD"},
    "sold (legacy lowercase)": {"status": "sold"},
    "TRANSFERRED": {"status": "TRANSFERRED"},
    "transferred (legacy lowercase)": {"status": "transferred"},
    "VOID": {"status": "VOID"},
    "QUARANTINED": {"status": "QUARANTINED"},
    "unknown junk status": {"status": "FOO"},
}


def _seed_unit_shaped(mongo_db, product_id: str, shape: Dict[str, Any],
                      store_id: str = STORE):
    """ONE unit stored exactly as `shape` says -- including, deliberately, with
    no `status` key at all."""
    # Same bare-assignment barcode shape the AVAILABLE seeder above uses --
    # already reasoned about in the BUG-104 allow-list, so do not re-inline it.
    bc = f"BC-{uuid.uuid4().hex[:10]}"
    doc: Dict[str, Any] = {
        "stock_id": f"STK-{uuid.uuid4().hex[:8]}",
        "product_id": product_id,
        "store_id": store_id,
        "barcode": bc,
        "quantity": 1,
        "location_code": "DEFAULT",
    }
    doc.update(shape)
    mongo_db["stock_units"].insert_one(doc)


def _both_halves(mongo_db, monkeypatch, store, category, pid):
    """(is this product in the coverage SET, what does the variance expect) --
    read from the two REAL helpers the lock actually calls, not from stubs."""
    from api.routers import blind_stock_take as bst
    from api.routers.inventory import _scoped_product_ids

    monkeypatch.setattr(bst, "_get_db", lambda: _DBProxy(mongo_db))
    scope = _scoped_product_ids(_DBProxy(mongo_db), store, category) or []
    expected = bst._on_hand_resolver(store, [pid])
    return pid in scope, int(expected.get(pid, 0) or 0)


@pytest.mark.parametrize("label", sorted(_ON_HAND_SHAPES))
def test_coverage_and_variance_agree_that_this_unit_is_on_hand(
    mongo_db, monkeypatch, label
):
    store = f"ST-SHAPE-{uuid.uuid4().hex[:6]}"
    category = f"CAT-{uuid.uuid4().hex[:8]}"
    pid = _seed_product(mongo_db, category=category)
    _seed_unit_shaped(mongo_db, pid, _ON_HAND_SHAPES[label], store_id=store)

    in_scope, expected = _both_halves(mongo_db, monkeypatch, store, category, pid)

    assert expected == 1, f"the variance cannot see a unit stored as {label}"
    assert in_scope, (
        f"a unit stored as {label} counts against the variance but never "
        "enters the expected SET, so skipping this product costs the counter "
        "nothing and a partial count locks as a clean day-end"
    )
    assert in_scope == (expected > 0), "the two halves must answer as one"


@pytest.mark.parametrize("label", sorted(_GONE_SHAPES))
def test_coverage_and_variance_agree_that_this_unit_is_gone(
    mongo_db, monkeypatch, label
):
    """The other direction: a unit that has left the shelf must be expected by
    neither half, or every count opens with phantom products to walk."""
    store = f"ST-GONE-{uuid.uuid4().hex[:6]}"
    category = f"CAT-{uuid.uuid4().hex[:8]}"
    pid = _seed_product(mongo_db, category=category)
    _seed_unit_shaped(mongo_db, pid, _GONE_SHAPES[label], store_id=store)

    in_scope, expected = _both_halves(mongo_db, monkeypatch, store, category, pid)

    assert expected == 0, f"a {label} unit is not on hand"
    assert not in_scope, f"a {label} unit must not be expected on the shelf"


@pytest.fixture
def blind_client(mongo_db, monkeypatch):
    """A TestClient over the REAL blind-count router (open / submit / lock),
    with only the DB handle and the store-access check redirected."""
    from api.routers import blind_stock_take as bst

    proxy = _DBProxy(mongo_db)
    monkeypatch.setattr(bst, "_get_db", lambda: proxy)
    monkeypatch.setattr(
        bst, "validate_store_access", lambda sid, u: sid or u.get("active_store_id")
    )

    app = FastAPI()
    app.include_router(bst.router, prefix="/blind")

    async def _user():
        return {
            "user_id": "u-blind",
            "username": "manager",
            "roles": ["ADMIN"],
            "store_ids": [STORE],
            "active_store_id": STORE,
        }

    app.dependency_overrides[get_current_user] = _user
    return TestClient(app)


@pytest.mark.parametrize("label", sorted(_ON_HAND_SHAPES))
def test_a_skipped_product_is_never_free_whatever_shape_its_units_are_in(
    mongo_db, blind_client, label
):
    """End to end over the real /blind/open -> /submit -> /lock routes: two
    products on hand, only one counted. The uncounted one must show up as a
    hole in the day-end no matter which storage shape its unit is in."""
    store = f"ST-BLIND-{uuid.uuid4().hex[:6]}"
    counted = _seed_product(mongo_db)
    _seed_units(mongo_db, counted, 1, store_id=store)
    skipped = _seed_product(mongo_db)
    _seed_unit_shaped(mongo_db, skipped, _ON_HAND_SHAPES[label], store_id=store)

    sid = blind_client.post("/blind/open", json={"store_id": store}).json()["session_id"]
    r = blind_client.post(
        f"/blind/{sid}/submit",
        json={"counts": [{"product_id": counted, "counted_qty": 1}]},
    )
    assert r.status_code == 200, r.text
    locked = blind_client.post(f"/blind/{sid}/lock")
    assert locked.status_code == 200, locked.text
    s = locked.json()["summary"]

    assert s["matched"] == 1, "the one line that WAS walked agrees -- the trap"
    assert s["products_expected"] == 2, (
        f"a unit stored as {label} is on this shelf, so the day-end must "
        "expect the counter to walk it"
    )
    assert skipped in s["products_not_counted"]
    assert s["coverage_percentage"] == 50.0
    assert s["full_count"] is False
    assert s["within_tolerance"] is False, (
        "half the shelf was never looked at; this is not a clean day-end"
    )


def test_a_real_shortage_is_still_short_when_the_whole_shelf_was_walked(
    mongo_db, blind_client
):
    """The discriminator. Coverage must not become the only thing the day-end
    looks at, and a full count must still be allowed to read clean."""
    store = f"ST-BLIND-{uuid.uuid4().hex[:6]}"
    short = _seed_product(mongo_db, cost=2000.0)
    _seed_units(mongo_db, short, 5, store_id=store)
    fine = _seed_product(mongo_db, cost=1000.0)
    _seed_unit_shaped(mongo_db, fine, {"status": "IN_STOCK"}, store_id=store)

    sid = blind_client.post("/blind/open", json={"store_id": store}).json()["session_id"]
    blind_client.post(
        f"/blind/{sid}/submit",
        json={
            "counts": [
                {"product_id": short, "counted_qty": 3},
                {"product_id": fine, "counted_qty": 1},
            ]
        },
    )
    s = blind_client.post(f"/blind/{sid}/lock").json()["summary"]

    assert s["full_count"] is True and s["coverage_percentage"] == 100.0
    assert s["short"] == 1, "two frames are genuinely missing"
    assert s["net_variance_units"] == -2
    assert s["net_variance_value_paise"] == -400000, "2 x Rs 2000, in paise"
    assert s["within_tolerance"] is False


def test_a_unit_held_for_an_order_is_counted_not_reported_as_an_overage(
    mongo_db, blind_client
):
    """A RESERVED unit is committed to somebody's order but it is still
    standing in this shop, so the counter walking the shelf finds it. If the
    coverage half expects it and the variance half does not, an honest count
    of it reads as stock that appeared from nowhere."""
    store = f"ST-BLIND-{uuid.uuid4().hex[:6]}"
    pid = _seed_product(mongo_db, cost=2000.0)
    _seed_unit_shaped(mongo_db, pid, {"status": "RESERVED"}, store_id=store)

    sid = blind_client.post("/blind/open", json={"store_id": store}).json()["session_id"]
    blind_client.post(
        f"/blind/{sid}/submit",
        json={"counts": [{"product_id": pid, "counted_qty": 1}]},
    )
    s = blind_client.post(f"/blind/{sid}/lock").json()["summary"]

    assert s["products_expected"] == 1, "the reserved unit is on this shelf"
    assert s["over"] == 0, (
        "the counter found the reserved frame the books say is here; that is "
        "not an overage"
    )
    assert s["matched"] == 1
    assert s["full_count"] is True and s["within_tolerance"] is True


def test_a_reserved_unit_is_on_the_shelf_to_count_but_not_on_the_shelf_to_sell(
    mongo_db, monkeypatch
):
    """The ONE deliberate difference between the two questions, pinned from
    both sides. A reserved frame is standing in this shop, so the COUNT must
    expect the counter to find it -- and it is committed to somebody else's
    order, so nothing may offer it as sellable stock. Collapsing the two into
    one answer breaks whichever half it is collapsed towards."""
    from api.routers.inventory import _on_hand_by_product

    store = f"ST-RSVD-{uuid.uuid4().hex[:6]}"
    category = f"CAT-{uuid.uuid4().hex[:8]}"
    pid = _seed_product(mongo_db, category=category)
    _seed_unit_shaped(mongo_db, pid, {"status": "RESERVED"}, store_id=store)

    in_scope, count_expects = _both_halves(mongo_db, monkeypatch, store, category, pid)
    assert in_scope and count_expects == 1, "the counter will find this frame"

    sellable = _on_hand_by_product(_DBProxy(mongo_db), [pid], store)
    assert sellable.get(pid, 0) == 0, (
        "a frame held for somebody's order was offered as sellable stock"
    )


# ============================================================================
# 12. ROUND 3: an unreadable scope, an unclassifiable status, a stable print
# ============================================================================


def test_an_unreadable_opening_scope_is_never_a_clean_full_count(
    mongo_db, monkeypatch
):
    """Round-3 coverage twin, cycle side (mirrors the blind count's M4): a
    session whose opening snapshot could NOT be taken must complete as
    coverage UNKNOWN -- never as `coverage 100%, full_count true` (which is
    what an empty {} snapshot reads as)."""
    from database.repositories.product_repository import StockRepository

    client = _client(mongo_db, monkeypatch, ["ADMIN"])
    pid = _seed_product(mongo_db)
    barcodes = _seed_units(mongo_db, pid, 2)

    # The stock collection could not be read when the session opened.
    monkeypatch.setattr(inv_mod, "get_stock_repository", lambda: None)
    count_id = _start(client)
    doc = mongo_db["stock_counts"].find_one({"count_id": count_id})
    assert doc["system_quantities"] is None, (
        '"I could not read the shelf" must never be recorded as "there was '
        'nothing on the shelf" -- {} completes as a clean full count'
    )

    # The shelf is readable again by the time the counter scans and completes.
    monkeypatch.setattr(
        inv_mod,
        "get_stock_repository",
        lambda: StockRepository(mongo_db["stock_units"]),
    )
    r = client.post(
        "/inventory/stock-count-scan",
        json={"barcode": barcodes[0], "physical_count": 2, "count_id": count_id},
    )
    assert r.status_code == 200, r.text
    r = client.post(f"/inventory/stock-count/{count_id}/complete")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["products_expected"] is None
    assert body["coverage_percentage"] is None
    assert body["full_count"] is False, (
        "an unreadable scope certified itself as a fully-walked shelf"
    )


def test_a_category_start_whose_lookup_fails_does_not_snapshot_the_whole_store(
    mongo_db, monkeypatch
):
    """The old fallback: when the category -> product_ids lookup failed, the
    opening snapshot silently widened to the WHOLE STORE -- a category count
    was then judged (and its counter blamed) against shelves it never asked
    to walk. A failed lookup must record an unreadable scope instead."""
    client = _client(mongo_db, monkeypatch, ["ADMIN"])
    pid = _seed_product(mongo_db)
    _seed_units(mongo_db, pid, 3)

    monkeypatch.setattr(
        inv_mod, "_category_product_ids", lambda db, c, **kw: None
    )
    r = client.post("/inventory/stock-count/start", json={"category": "SUNGLASS"})
    assert r.status_code == 200, r.text
    doc = mongo_db["stock_counts"].find_one({"count_id": r.json()["count_id"]})
    assert doc["system_quantities"] is None, (
        "a failed category lookup snapshotted the whole store"
    )


def test_the_category_resolver_reports_failure_as_none_not_as_nothing(mongo_db):
    """`_category_product_ids` is the ONE category resolver both count doors
    share: a failed read is None (unanswerable), an empty category is [] (a
    real answer), and neither may ever mean the other."""
    from api.routers.inventory import _category_product_ids

    class _Exploding:
        def get_collection(self, name):
            class _C:
                def find(self, *a, **k):
                    raise RuntimeError("products collection is down")

            return _C()

    assert _category_product_ids(_Exploding(), "SUNGLASS") is None
    assert _category_product_ids(None, "SUNGLASS") is None
    assert _category_product_ids(_DBProxy(mongo_db), "NO-SUCH-CAT") == []


def test_a_shelf_holding_an_unclassifiable_status_is_never_a_full_count(
    admin_client, mongo_db
):
    """Round-3 residual 4, cycle side: a unit whose status token
    canonicalises to NOTHING (a migration writing "ON HAND") is invisible to
    the expected set AND to every reader -- so twelve such units nobody
    walked used to certify as a clean, fully-covered day-end. While any such
    token exists at the store, the count must refuse to call itself full."""
    pid = _seed_product(mongo_db)
    _seed_units(mongo_db, pid, 2)
    ghost = _seed_product(mongo_db)
    try:
        for _ in range(3):
            # Reuse the shaped seeder (its barcode line is the one the BUG-104
            # guard has already reasoned about -- do not re-inline it).
            _seed_unit_shaped(
                mongo_db, ghost, {"status": "ON HAND"}, store_id=STORE
            )
        count_id = _start(admin_client)
        # Walk EVERY product the session expects, honestly -- so plain
        # coverage is a full 100% and ONLY the tripwire can deny full_count
        # (the module-shared store holds other tests' products too; skipping
        # them would fail coverage anyway and hand this test its answer).
        doc = mongo_db["stock_counts"].find_one({"count_id": count_id})
        for p, q in (doc["system_quantities"] or {}).items():
            r = admin_client.post(
                f"/inventory/stock-count/{count_id}/items",
                json={"product_id": p, "counted_quantity": int(q)},
            )
            assert r.status_code == 200, r.text
        r = admin_client.post(f"/inventory/stock-count/{count_id}/complete")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["products_missed"] == 0, (
            "test precondition: every expected product was walked"
        )
        assert "ON HAND" in body["unknown_status_tokens"]
        assert body["full_count"] is False, (
            "three unclassifiable units nobody walked certified as a clean "
            "full count"
        )
    finally:
        # module-scoped engine: never leak the poison token into later tests
        mongo_db["stock_units"].delete_many({"status": "ON HAND"})


def test_the_blind_day_end_is_never_clean_over_an_unclassifiable_status(
    mongo_db, blind_client
):
    """The same tripwire on the blind path, end to end over the real
    /blind/open -> /submit -> /lock routes."""
    store = f"ST-GHOST-{uuid.uuid4().hex[:6]}"
    pid = _seed_product(mongo_db)
    _seed_units(mongo_db, pid, 2, store_id=store)
    ghost = _seed_product(mongo_db)
    _seed_unit_shaped(mongo_db, ghost, {"status": "ON HAND"}, store_id=store)

    sid = blind_client.post("/blind/open", json={"store_id": store}).json()[
        "session_id"
    ]
    r = blind_client.post(
        f"/blind/{sid}/submit",
        json={"counts": [{"product_id": pid, "counted_qty": 2}]},
    )
    assert r.status_code == 200, r.text
    s = blind_client.post(f"/blind/{sid}/lock").json()["summary"]
    assert s["unknown_status_tokens"] == ["ON HAND"]
    assert s["within_tolerance"] is False, (
        "a store holding units the status vocabulary cannot classify locked "
        "as a clean day-end"
    )
    assert s["full_count"] is False


def test_the_unit_fingerprint_ignores_the_order_mongo_returns_units():
    """The docstring's load-bearing claim ("order-independent") pinned: real
    MongoDB's $addToSet ordering is unspecified (mongomock's happens to be
    stable), so two reads of the SAME shelf must fingerprint identically
    whatever order the units come back in -- otherwise every untouched line
    would flag as moved and no shortage could ever be written off."""
    from api.routers.inventory import _unit_fingerprint

    assert _unit_fingerprint(["u-b", "u-a", "u-c"]) == _unit_fingerprint(
        ["u-c", "u-b", "u-a"]
    )
    assert _unit_fingerprint(["u-a", "u-b"]) != _unit_fingerprint(["u-a", "u-z"])
    assert _unit_fingerprint([]) == _unit_fingerprint(None)
