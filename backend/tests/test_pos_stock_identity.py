"""POS stock identity: what a sale line takes off the shelf is the server's call.

Codex audit 2026-11-04, each pinned below on the REAL StockRepository /
ProductRepository / OrderRepository over mongomock (the guarded mark_sold and
the atomic claim are the real filters):

  L2-STOCK-F01  an explicit stock_id the caller supplies bypassed the identity
                and zero-stock guards: a fabricated id saved a bill that took
                NO unit off the shelf, and a real unit of ANOTHER product was
                sold to the line (invoice and physical unit disagree). Now the
                named unit must exist, be the line's product (a legacy alias of
                the same product still sells), be AVAILABLE at the bill's shop,
                and be claimed -- else 409 before any bill is written.
  L2-STOCK-F02  naming a stocked product with item_type SERVICE / EYE_TEST /
                LENS skipped stock consumption. Whether a real product takes a
                unit now comes from its product master; a contradicting caller
                type is refused. True service and made-to-order lines still sell.
  L1-F39        an archived (is_active False / deleted_at) product could still
                be sold by ordinary staff. New sales are refused at both billing
                doors; the old bill stays readable.

Every refusal is checked against an independent reconciliation: the shelf
(pieces by status, with their order ids) and the orders collection are exactly
what they were before the request. No emoji (Windows cp1252).
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

mongomock = pytest.importorskip("mongomock")

STORE = "BV-TEST-01"
OTHER_STORE = "BV-TEST-02"
SG = "SG-ID-1"  # stocked sunglasses, ZERO units on the shelf
FR = "FR-ID-1"  # stocked frame, units on the shelf
ACC = "ACC-ID-1"  # cheap accessory
SVC = "SVC-ID-1"  # a true service product
LS = "LS-ID-1"  # a spectacle-lens master


def _token(roles, user="till-user"):
    from api.routers.auth import create_access_token

    return {
        "Authorization": "Bearer "
        + create_access_token(
            {
                "user_id": user,
                "username": user,
                "roles": roles,
                "store_ids": [STORE],
                "active_store_id": STORE,
                "discount_cap": 10.0,
            }
        )
    }


STAFF = ["SALES_STAFF"]
ADMIN = ["SUPERADMIN"]


def _product(pid, name, category, sku, price):
    return {
        "product_id": pid,
        "name": name,
        "sku": sku,
        "brand": "Generic",
        "category": category,
        "mrp": price,
        "offer_price": price,
        "cost_price": round(price * 0.4, 2),
        "pricing": {"mrp": price, "cost_price": round(price * 0.4, 2)},
        "is_active": True,
    }


@pytest.fixture
def shop(monkeypatch):
    from api import dependencies as dm
    from api.routers import orders as om
    from database.repositories.audit_repository import AuditRepository
    from database.repositories.customer_repository import CustomerRepository
    from database.repositories.order_repository import OrderRepository
    from database.repositories.product_repository import (
        ProductRepository,
        StockRepository,
    )

    db = mongomock.MongoClient()["pos_stock_identity"]
    order_repo = OrderRepository(db.get_collection("orders"))
    cust_repo = CustomerRepository(db.get_collection("customers"))
    prod_repo = ProductRepository(db.get_collection("products"))
    stock_repo = StockRepository(db.get_collection("stock_units"))
    audit_repo = AuditRepository(db.get_collection("audit_logs"))

    monkeypatch.setattr(om, "get_stock_repository", lambda: stock_repo)
    monkeypatch.setattr(om, "get_product_repository", lambda: prod_repo)
    monkeypatch.setattr(om, "get_order_repository", lambda: order_repo)
    monkeypatch.setattr(om, "get_customer_repository", lambda: cust_repo)
    monkeypatch.setattr(om, "get_walkin_counter_repository", lambda: None)
    monkeypatch.setattr(dm, "get_audit_repository", lambda: audit_repo)
    from database.repositories.prescription_repository import PrescriptionRepository

    rx_repo = PrescriptionRepository(db.get_collection("prescriptions"))
    rx_repo.create(
        {"prescription_id": "RX-PSI-1", "customer_id": "cust-psi", "prescription_date": "2026-11-01"}
    )
    monkeypatch.setattr(dm, "get_prescription_repository", lambda: rx_repo)

    cust_repo.create(
        {"customer_id": "cust-psi", "name": "Asha", "mobile": "9100000071", "phone": "9100000071"}
    )
    for doc in (
        _product(SG, "Polar Aviator 58", "SUNGLASS", "SG-POLAR-AV-58", 1000.0),
        _product(FR, "Carrera CA 1 Havana 54", "FRAME", "FR-CARRERA-CA1-54", 5000.0),
        _product(ACC, "Lens Cloth", "ACCESSORIES", "ACC-CLOTH-1", 118.0),
        _product(SVC, "Frame Fitting", "SERVICES", "SVC-FITTING-1", 200.0),
        _product(LS, "1.5 SV Lens", "OPTICAL_LENS", "LS-SV-15", 1500.0),
    ):
        prod_repo.create(doc)
    units = db.get_collection("stock_units")
    for sid, pid, store in (
        ("SU-FR-A", FR, STORE),
        ("SU-FR-B", FR, STORE),
        ("SU-FR-OTHER", FR, OTHER_STORE),
        ("SU-ACC-A", ACC, STORE),
        # A legacy unit stamped with the frame's pre-canonical id (its SKU).
        ("SU-FR-LEGACY", "FR-CARRERA-CA1-54", STORE),
    ):
        units.insert_one(
            {
                "stock_id": sid,
                "barcode": "BC-" + sid,
                "product_id": pid,
                "store_id": store,
                "status": "AVAILABLE",
                "quantity": 1,
            }
        )
    # The sunglasses' only unit was sold earlier: zero on the shelf.
    units.insert_one(
        {
            "stock_id": "SU-SG-SOLD",
            "product_id": SG,
            "store_id": STORE,
            "status": "SOLD",
            "order_id": "ORD-EARLIER",
            "quantity": 1,
        }
    )
    return {"db": db, "products": prod_repo}


ITEMS = {
    SG: ("Polar Aviator 58", "SG-POLAR-AV-58", "SUNGLASS", 1000.0),
    FR: ("Carrera CA 1 Havana 54", "FR-CARRERA-CA1-54", "FRAME", 5000.0),
    ACC: ("Lens Cloth", "ACC-CLOTH-1", "ACCESSORY", 118.0),
    SVC: ("Frame Fitting", "SVC-FITTING-1", "SERVICE", 200.0),
    LS: ("1.5 SV Lens", "LS-SV-15", "LENS", 1500.0),
}


def _line(pid, **over):
    name, sku, item_type, price = ITEMS[pid]
    line = {
        "item_type": item_type,
        "product_id": pid,
        "product_name": name,
        "sku": sku,
        "quantity": 1,
        "unit_price": price,
    }
    line.update(over)
    return line


def _sell(client, items, roles=STAFF, key=None):
    headers = _token(roles)
    if key:
        headers["Idempotency-Key"] = key
    return client.post(
        "/api/v1/orders",
        json={"customer_id": "cust-psi", "items": items},
        headers=headers,
    )


def _order_id(resp):
    body = resp.json()
    return body.get("order_id") or (body.get("order") or {}).get("order_id")


def _ledger(shop):
    """Independent reconciliation: every piece's (status, order_id) and the
    set of saved bills. Read straight off the collections, not via the code."""
    db = shop["db"]
    pieces = {
        u["stock_id"]: (u.get("status"), u.get("order_id"))
        for u in db.get_collection("stock_units").find({}, {"_id": 0})
    }
    bills = sorted(o["order_id"] for o in db.get_collection("orders").find({}, {"order_id": 1}))
    return pieces, bills


def _sold_to(shop, order_id):
    return sorted(
        u["stock_id"]
        for u in shop["db"].get_collection("stock_units").find(
            {"status": "SOLD", "order_id": order_id}
        )
    )


def _refused_unchanged(shop, resp, before, code=409):
    assert resp.status_code == code, resp.text
    assert isinstance(resp.json().get("detail"), str), resp.text  # the till's plain message
    assert _ledger(shop) == before, "a refused sale must leave shelf and bills untouched"


# ---------------------------------------------------------------------------
# L2-STOCK-F01 -- the named unit is the line's, is here, and is taken once
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("roles", [STAFF, ADMIN])
def test_fabricated_stock_id_on_zero_stock_product_is_refused(client, shop, roles):
    before = _ledger(shop)
    r = _sell(client, [_line(SG, stock_id="0b9c3c1e-fake-4d7e-9a11-000000000000")], roles)
    _refused_unchanged(shop, r, before)


def test_fabricated_stock_id_cannot_substitute_for_a_unit_on_the_shelf(client, shop):
    before = _ledger(shop)
    r = _sell(client, [_line(FR, stock_id="SU-DOES-NOT-EXIST")])
    _refused_unchanged(shop, r, before)


def test_unit_of_another_product_is_refused(client, shop):
    """The audit's second case: an accessory line naming a frame's unit."""
    before = _ledger(shop)
    r = _sell(client, [_line(ACC, stock_id="SU-FR-A")])
    _refused_unchanged(shop, r, before)
    assert "su-fr-a" in r.text.lower()


def test_unit_at_another_shop_is_refused(client, shop):
    before = _ledger(shop)
    r = _sell(client, [_line(FR, stock_id="SU-FR-OTHER")])
    _refused_unchanged(shop, r, before)


def test_legacy_alias_of_the_same_product_still_sells(client, shop):
    r = _sell(client, [_line(FR, stock_id="SU-FR-LEGACY")])
    assert r.status_code in (200, 201), r.text
    assert _sold_to(shop, _order_id(r)) == ["SU-FR-LEGACY"]


def test_scanned_unit_of_the_line_sells_exactly_that_unit(client, shop):
    r = _sell(client, [_line(FR, stock_id="SU-FR-B")])
    assert r.status_code in (200, 201), r.text
    oid = _order_id(r)
    assert _sold_to(shop, oid) == ["SU-FR-B"]
    pieces, bills = _ledger(shop)
    assert pieces["SU-FR-A"] == ("AVAILABLE", None)
    assert bills == [oid]


def test_retry_of_a_good_scan_takes_nothing_twice(client, shop):
    r1 = _sell(client, [_line(FR, stock_id="SU-FR-B")], key="psi-retry-1")
    assert r1.status_code in (200, 201), r1.text
    after_first = _ledger(shop)
    r2 = _sell(client, [_line(FR, stock_id="SU-FR-B")], key="psi-retry-1")
    assert r2.status_code in (200, 201), r2.text
    assert _order_id(r2) == _order_id(r1)
    assert _ledger(shop) == after_first


def test_retry_of_a_refused_scan_stays_refused(client, shop):
    before = _ledger(shop)
    for _ in range(2):
        r = _sell(client, [_line(ACC, stock_id="SU-FR-A")], key="psi-retry-bad")
        _refused_unchanged(shop, r, before)


def test_claim_that_wrote_but_lost_its_acknowledgement_is_refused_and_given_back(
    client, shop, monkeypatch
):
    """mark_sold writes the unit SOLD, then the reply is lost (raises). The line
    did not provably take its unit, so the sale is refused -- and the unit this
    unsaved bill took is put back, not stranded SOLD to a bill that never was."""
    from database.repositories.product_repository import StockRepository

    real = StockRepository.mark_sold

    def wrote_then_lost(self, stock_id, order_id):
        real(self, stock_id, order_id)
        raise TimeoutError("ack lost")

    monkeypatch.setattr(StockRepository, "mark_sold", wrote_then_lost)
    before = _ledger(shop)
    r = _sell(client, [_line(FR, stock_id="SU-FR-B")])
    _refused_unchanged(shop, r, before)


def test_claim_refused_while_unit_still_reads_available_is_refused(client, shop, monkeypatch):
    """The guarded claim took nothing but the re-read finds no reason: the line
    still did not take its unit, so no bill may be written for it."""
    from database.repositories.product_repository import StockRepository

    monkeypatch.setattr(StockRepository, "mark_sold", lambda self, s, o: False)
    before = _ledger(shop)
    r = _sell(client, [_line(FR, stock_id="SU-FR-B")])
    _refused_unchanged(shop, r, before)


def _draft(client, shop):
    r = _sell(client, [_line(FR)])
    assert r.status_code in (200, 201), r.text
    return _order_id(r)


def test_add_item_door_refuses_a_unit_of_another_product(client, shop):
    oid = _draft(client, shop)
    before = _ledger(shop)
    r = client.post(
        f"/api/v1/orders/{oid}/items", json=_line(ACC, stock_id="SU-FR-B"), headers=_token(STAFF)
    )
    assert r.status_code == 409, r.text
    assert _ledger(shop) == before
    assert len(shop["db"].get_collection("orders").find_one({"order_id": oid})["items"]) == 1


def test_add_item_door_refuses_a_fabricated_unit(client, shop):
    oid = _draft(client, shop)
    before = _ledger(shop)
    r = client.post(
        f"/api/v1/orders/{oid}/items", json=_line(SG, stock_id="SU-NOPE"), headers=_token(STAFF)
    )
    assert r.status_code == 409, r.text
    assert _ledger(shop) == before


def test_add_item_door_sells_the_lines_own_scanned_unit(client, shop):
    oid = _draft(client, shop)
    r = client.post(
        f"/api/v1/orders/{oid}/items", json=_line(ACC, stock_id="SU-ACC-A"), headers=_token(STAFF)
    )
    assert r.status_code in (200, 201), r.text
    assert "SU-ACC-A" in _sold_to(shop, oid)


# ---------------------------------------------------------------------------
# L2-STOCK-F02 -- the product master, not the request, says what takes stock
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spoof", ["SERVICE", "service", "EYE_TEST", "CONSULTATION", "LENS"])
@pytest.mark.parametrize("roles", [STAFF, ADMIN])
def test_stocked_product_relabelled_to_skip_stock_is_refused(client, shop, spoof, roles):
    # An Rx is attached, so a LENS label clears the Rx gate and only the
    # stock rule can stop it.
    before = _ledger(shop)
    r = _sell(client, [_line(SG, item_type=spoof, prescription_id="RX-PSI-1")], roles)
    _refused_unchanged(shop, r, before, code=400)


def test_lens_master_relabelled_service_is_refused(client, shop):
    before = _ledger(shop)
    r = _sell(client, [_line(LS, item_type="SERVICE", prescription_id="RX-PSI-1")])
    _refused_unchanged(shop, r, before, code=400)


def test_add_item_door_refuses_a_relabelled_stocked_product(client, shop):
    oid = _draft(client, shop)
    before = _ledger(shop)
    r = client.post(
        f"/api/v1/orders/{oid}/items", json=_line(SG, item_type="SERVICE"), headers=_token(STAFF)
    )
    assert r.status_code == 400, r.text
    assert _ledger(shop) == before


@pytest.mark.parametrize("item_type", ["SERVICE", "EYE_TEST"])
def test_true_service_product_still_sells_and_takes_no_stock(client, shop, item_type):
    before, _ = _ledger(shop)
    r = _sell(client, [_line(SVC, item_type=item_type)])
    assert r.status_code in (200, 201), r.text
    pieces, bills = _ledger(shop)
    assert pieces == before and bills == [_order_id(r)]


def test_made_to_order_custom_line_still_sells(client, shop):
    r = _sell(
        client,
        [
            {
                "item_type": "SERVICE",
                "product_id": "custom-repair-1",
                "product_name": "Nose pad repair",
                "quantity": 1,
                "unit_price": 150.0,
            }
        ],
    )
    assert r.status_code in (200, 201), r.text


def test_stocked_product_with_its_own_type_still_takes_a_unit(client, shop):
    r = _sell(client, [_line(FR)])
    assert r.status_code in (200, 201), r.text
    assert r.json()["grand_total"] == 5000.0  # price untouched by the type rule
    assert len(_sold_to(shop, _order_id(r))) == 1


# ---------------------------------------------------------------------------
# L1-F39 -- an archived product is off sale
# ---------------------------------------------------------------------------


def _archive(shop, pid):
    shop["db"].get_collection("products").update_one(
        {"product_id": pid},
        {"$set": {"is_active": False, "deleted_at": "2026-11-04T10:00:00+00:00"}},
    )


@pytest.mark.parametrize("ref", [FR, "FR-CARRERA-CA1-54"])  # by id and by SKU alias
@pytest.mark.parametrize("roles", [STAFF, ADMIN])
def test_archived_product_cannot_be_sold(client, shop, ref, roles):
    _archive(shop, FR)
    before = _ledger(shop)
    r = _sell(client, [_line(FR, product_id=ref)], roles)
    _refused_unchanged(shop, r, before, code=400)


def test_inactive_product_cannot_be_sold(client, shop):
    shop["db"].get_collection("products").update_one(
        {"product_id": FR}, {"$set": {"is_active": False}}
    )
    before = _ledger(shop)
    r = _sell(client, [_line(FR)])
    _refused_unchanged(shop, r, before, code=400)


def test_archived_product_cannot_be_added_to_an_open_bill(client, shop):
    """Archived while a till had the bill open: the next line is refused."""
    oid = _draft(client, shop)
    _archive(shop, ACC)
    before = _ledger(shop)
    r = client.post(
        f"/api/v1/orders/{oid}/items", json=_line(ACC), headers=_token(STAFF)
    )
    assert r.status_code == 400, r.text
    assert _ledger(shop) == before


def test_archiving_leaves_the_old_bill_readable_and_unchanged(client, shop):
    r = _sell(client, [_line(FR)])
    assert r.status_code in (200, 201), r.text
    oid = _order_id(r)
    saved = shop["db"].get_collection("orders").find_one({"order_id": oid}, {"_id": 0})
    _archive(shop, FR)
    got = client.get(f"/api/v1/orders/{oid}", headers=_token(STAFF))
    assert got.status_code == 200, got.text
    assert shop["db"].get_collection("orders").find_one({"order_id": oid}, {"_id": 0}) == saved


def test_active_control_sells_as_ordinary_staff(client, shop):
    r = _sell(client, [_line(ACC)])
    assert r.status_code in (200, 201), r.text
    assert _sold_to(shop, _order_id(r)) == ["SU-ACC-A"]
