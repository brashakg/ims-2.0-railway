"""Product imports are retired; historical customer/order imports still work."""

import mongomock
import pytest

from api.routers import techcherry_import as tc


RETIRED = [
    ("POST", "/api/v1/products/bulk-create"),
    ("POST", "/api/v1/catalog/products/import"),
    ("POST", "/api/v1/catalog-import/preview"),
    ("POST", "/api/v1/catalog-import/commit"),
    ("POST", "/api/v1/admin/products/bulk-import"),
    ("GET", "/api/v1/admin/products/bulk-import/{job_id}/file"),
]


@pytest.mark.parametrize("method,path", RETIRED)
def test_product_import_routes_are_not_registered(app, client, auth_headers, method, path):
    assert not any(
        route.path == path and method in getattr(route, "methods", set())
        for route in app.routes
    )
    response = client.request(method, path.replace("{job_id}", "old-job"),
                              headers=auth_headers, json={})
    assert response.status_code in (404, 405)


def test_techcherry_rejects_products_before_opening_database(client, auth_headers, monkeypatch):
    def forbidden():
        pytest.fail("A retired product import must not reach any database")

    monkeypatch.setattr(tc, "_get_db", forbidden)
    response = client.post("/api/v1/admin/techcherry/import", headers=auth_headers,
                           json={"type": "products", "store_id": "S1",
                                 "rows": [{"Prod Name": "Frame", "Barcode": "12345"}]})
    assert response.status_code == 422


@pytest.mark.parametrize("kind,row,collection,key", [
    ("customers", {"Name": "Existing customer", "Mobile": "9876543210"},
     "customers", {"phone": "9876543210"}),
    ("orders", {"InvoiceNo": "OLD-1", "GrandTotal": "1500",
                "items": [{"sph": "-2.00", "cyl": "-1.00", "axis": 90}]},
     "orders", {"order_number": "OLD-1"}),
])
def test_customer_and_order_import_reimport_and_skip_still_work(
        client, auth_headers, monkeypatch, kind, row, collection, key):
    db = mongomock.MongoClient().db
    original_product = {"product_id": "P1", "sku": "OLD-PRODUCT", "source": "techcherry"}
    db.products.insert_one(dict(original_product))
    monkeypatch.setattr(tc, "_get_db", lambda: db)
    body = {"type": kind, "store_id": "S1", "rows": [row]}
    for options, counts in [({}, (1, 0, 0)), ({}, (0, 1, 0)),
                            ({"overwrite": False}, (0, 0, 1))]:
        response = client.post("/api/v1/admin/techcherry/import", headers=auth_headers,
                               json={**body, **options})
        assert response.status_code == 200, response.text
        data = response.json()
        assert (data["inserted"], data["updated"], data["skipped"]) == counts
        assert data["errors"] == []
    assert db[collection].count_documents(key) == 1
    if kind == "orders":
        assert db.orders.find_one(key)["items"] == row["items"]
    assert db.products.find_one({}, {"_id": 0}) == original_product


def test_manual_catalogue_and_operational_upload_routes_remain(app):
    registered = {(method, route.path) for route in app.routes
                  for method in getattr(route, "methods", set())}
    assert {
        ("POST", "/api/v1/products"),
        ("POST", "/api/v1/catalog/products"),
        ("POST", "/api/v1/admin/techcherry/import"),
        ("POST", "/api/v1/inventory/opening-stock/preview"),
        ("POST", "/api/v1/inventory/opening-stock/commit"),
        ("POST", "/api/v1/lens-stock/{lens_line_id}/bulk-import"),
        ("POST", "/api/v1/finance/bank-statement/import"),
    } <= registered
