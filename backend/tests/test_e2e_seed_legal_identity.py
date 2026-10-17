"""The local E2E fixtures satisfy the real statutory invoice identity gate."""

from copy import deepcopy

import mongomock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import dependencies
from api.routers import orders
from api.routers.auth import get_current_user
from api.services import print_identity
from database.repositories.order_repository import OrderRepository
from database.repositories.store_repository import StoreRepository
from database.seed_data import STORES
from scripts.seed_e2e import seed


@pytest.fixture
def seeded(monkeypatch):
    db = mongomock.MongoClient().db
    seed(db)
    monkeypatch.setattr(print_identity, "_db", lambda: db)
    monkeypatch.setattr(orders, "get_order_repository", lambda: OrderRepository(db.orders))
    monkeypatch.setattr(dependencies, "get_store_repository", lambda: StoreRepository(db.stores))
    app = FastAPI()
    app.include_router(orders.router, prefix="/orders")
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "test", "roles": ["SUPERADMIN"]}
    return db, TestClient(app)


def test_reseeding_keeps_every_shops_own_gstin_and_taxpayer(seeded):
    db, _ = seeded
    before = deepcopy(STORES)
    seed(db)
    pans = {shop["gstin"][2:12] for shop in STORES}
    assert db.entities.count_documents({}) == len(pans)
    for expected in STORES:
        identity = print_identity.resolve_issuing_identity(expected["store_id"])
        shop, entity = identity["store"], identity["entity"]
        assert shop["gstin"] == expected["gstin"]
        assert entity["entity_id"] == shop["entity_id"]
        assert entity["pan"] == shop["gstin"][2:12]
        assert print_identity.assert_store_gstin(shop, entity) == expected["gstin"]
    assert STORES == before  # E2E setup does not modify production bootstrap data.


def _order(db, shop):
    db.orders.insert_one({
        "order_id": "E2E-INVOICE", "order_number": "E2E-INVOICE", "store_id": shop,
        "status": "CONFIRMED", "grand_total": 999.0, "amount_paid": 999.0,
        "balance_due": 0.0, "customer_name": "E2E Customer",
        "items": [{"item_id": "frame", "item_type": "FRAME", "product_name": "E2E Frame",
                   "hsn_code": "9003", "quantity": 1, "unit_price": 999.0,
                   "item_total": 999.0, "gst_rate": 5.0, "taxable_value": 951.43,
                   "tax_amount": 47.57}],
    })


@pytest.mark.parametrize("shop", ["BV-BOK-01", "BV-PUN-01"])
def test_seeded_invoice_json_and_pdf_use_the_actual_owned_registration(seeded, shop):
    db, client = seeded
    _order(db, shop)
    response = client.get("/orders/E2E-INVOICE/invoice")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["storeGstin"] == db.stores.find_one({"store_id": shop})["gstin"]
    assert body["grandTotal"] == body["amountPaid"] == 999.0
    assert body["taxTotals"]["tax"] == 47.57
    assert body["taxTotals"]["cgst"] + body["taxTotals"]["sgst"] == pytest.approx(47.57)
    pdf = client.get("/orders/E2E-INVOICE/invoice.pdf")
    assert pdf.status_code == 200, pdf.text
    assert pdf.content.startswith(b"%PDF")
    assert db.orders.find_one({"order_id": "E2E-INVOICE"})["invoice_number"] == body["invoiceNumber"]


def test_fixture_does_not_bypass_the_guard_when_a_shop_is_linked_to_another_taxpayer(seeded):
    db, client = seeded
    _order(db, "BV-BOK-01")
    wrong = db.stores.find_one({"store_id": "BV-PUN-01"})["entity_id"]
    db.stores.update_one({"store_id": "BV-BOK-01"}, {"$set": {"entity_id": wrong}})
    response = client.get("/orders/E2E-INVOICE/invoice")
    assert response.status_code == 400
    assert "legal entity" in response.text
    assert "invoice_number" not in db.orders.find_one({"order_id": "E2E-INVOICE"})
