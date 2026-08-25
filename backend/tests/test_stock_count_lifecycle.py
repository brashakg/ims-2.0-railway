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
    assert body["system_count"] == 5
    assert body["variance"] == -2

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
        assert ln["system_quantity"] == 2
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
