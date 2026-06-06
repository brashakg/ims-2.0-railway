"""NEW-ORDER-PRODUCTID-STAMP: an order placed by SKU (or _id) must persist the
catalog's CANONICAL product_id on the line -- not the raw client SKU -- so orders
reconcile against the catalog. Live products are seeded in catalog_products and
resolved with product_repo=None via _get_catalog_collection, so that path must be
handled. Virtual skus (custom-/lens-) are left as-is."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")


class _CatalogColl:
    def __init__(self, docs):
        self.docs = docs

    def find_one(self, query, projection=None):
        clauses = query.get("$or", [query])
        for d in self.docs:
            for c in clauses:
                if all(d.get(k) == v for k, v in c.items()):
                    return d
        return None


def _setup(monkeypatch, catalog_docs):
    from tests.test_walkouts import FakeDB
    from api.routers import orders as om
    from api import dependencies as deps
    from database.repositories.order_repository import OrderRepository
    from database.repositories.customer_repository import CustomerRepository
    from database.repositories.audit_repository import AuditRepository

    fake_db = FakeDB()
    order_repo = OrderRepository(fake_db.get_collection("orders"))
    customer_repo = CustomerRepository(fake_db.get_collection("customers"))
    audit_repo = AuditRepository(fake_db.get_collection("audit_logs"))
    monkeypatch.setattr(om, "get_order_repository", lambda: order_repo)
    monkeypatch.setattr(om, "get_customer_repository", lambda: customer_repo)
    monkeypatch.setattr(om, "get_product_repository", lambda: None)  # live catalog path
    monkeypatch.setattr(om, "get_walkin_counter_repository", lambda: None)
    monkeypatch.setattr(deps, "get_audit_repository", lambda: audit_repo)
    monkeypatch.setattr(om, "_get_catalog_collection", lambda: _CatalogColl(catalog_docs))
    customer_repo.create({"customer_id": "cust-test", "name": "Test", "mobile": "9100000099"})
    return fake_db


_FRAME = {
    "id": "prod-fr-canonical", "sku": "SKU-IMPORTED-123", "title": "Imported Frame",
    "category": "FRAME", "gst_rate": 5.0, "hsn_code": "900311",
    "pricing": {"mrp": 5000.0, "offer_price": 5000.0}, "is_active": True,
}


def test_order_create_stamps_canonical_product_id_from_catalog(client, auth_headers, monkeypatch):
    fake_db = _setup(monkeypatch, [_FRAME])
    resp = client.post(
        "/api/v1/orders",
        json={
            "customer_id": "cust-test",
            "items": [{
                "product_id": "SKU-IMPORTED-123", "product_name": "Imported Frame",
                "item_type": "FRAME", "category": "FRAME", "quantity": 1, "unit_price": 5000.0,
            }],
        },
        headers=auth_headers,
    )
    assert resp.status_code in (200, 201), resp.text
    doc = fake_db.get_collection("orders").find_one({"order_id": resp.json()["order_id"]})
    assert doc["items"][0]["product_id"] == "prod-fr-canonical"  # canonical, not the SKU


def test_order_create_virtual_product_ids_unaffected(client, auth_headers, monkeypatch):
    fake_db = _setup(monkeypatch, [])
    resp = client.post(
        "/api/v1/orders",
        json={
            "customer_id": "cust-test",
            "items": [
                {"product_id": "custom-frame-1", "product_name": "Custom", "item_type": "FRAME",
                 "category": "FRAME", "quantity": 1, "unit_price": 3000.0},
                {"product_id": "lens-1.5-zeiss", "product_name": "1.5 ZEISS", "item_type": "LENS",
                 "category": "LENS", "quantity": 2, "unit_price": 4000.0},
            ],
        },
        headers=auth_headers,
    )
    assert resp.status_code in (200, 201), resp.text
    doc = fake_db.get_collection("orders").find_one({"order_id": resp.json()["order_id"]})
    pids = {it["product_id"] for it in doc["items"]}
    assert "custom-frame-1" in pids and "lens-1.5-zeiss" in pids  # virtual ids untouched
