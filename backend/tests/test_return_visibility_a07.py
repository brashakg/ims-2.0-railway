"""Return reads respect assigned shops and hide foreign IDs."""

import mongomock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers import returns
from api.routers.auth import get_current_user


@pytest.fixture(autouse=True)
def _isolate_db():
    # This module has its own in-memory collection and never starts the app DB.
    yield


@pytest.fixture
def world(monkeypatch):
    collection = mongomock.MongoClient().db.returns
    collection.insert_many([
        {"return_id": "R1", "store_id": "S1", "return_type": "RETURN", "created_at": "2026-10-10"},
        {"return_id": "R2", "store_id": "S2", "return_type": "RETURN", "created_at": "2026-10-11"},
        {"return_id": "R3", "store_id": "S3", "return_type": "RETURN", "created_at": "2026-10-12"},
        {"return_id": "OLD", "return_type": "RETURN", "created_at": "2026-10-09"},
    ])
    actor = {"user_id": "area", "roles": ["AREA_MANAGER"], "store_ids": ["S1", "S2"], "active_store_id": "S1"}
    app = FastAPI()
    app.include_router(returns.router, prefix="/api/v1/returns")
    app.dependency_overrides[get_current_user] = lambda: actor
    monkeypatch.setattr(returns, "_returns_coll", lambda: collection)
    return TestClient(app), actor


def test_area_manager_default_list_and_count_include_only_assigned_shops(world):
    client, _ = world
    response = client.get("/api/v1/returns")
    assert response.status_code == 200
    assert {r["return_id"] for r in response.json()["returns"]} == {"R1", "R2"}
    assert response.json()["total"] == 2
    page = client.get("/api/v1/returns?skip=1&limit=1&return_type=RETURN").json()
    assert page["total"] == 2
    assert [r["return_id"] for r in page["returns"]] == ["R1"]


@pytest.mark.parametrize("role", ["AREA_MANAGER", "STORE_MANAGER", "SALES_STAFF", "ACCOUNTANT"])
def test_explicit_foreign_shop_cannot_widen_list(world, role):
    client, actor = world
    actor["roles"] = [role]
    assert client.get("/api/v1/returns?store_id=S3").status_code == 403
    allowed = client.get("/api/v1/returns?store_id=S2")
    assert allowed.status_code == 200
    assert [r["return_id"] for r in allowed.json()["returns"]] == ["R2"]


def test_store_role_default_is_active_shop(world):
    client, actor = world
    actor["roles"] = ["STORE_MANAGER"]
    assert [r["return_id"] for r in client.get("/api/v1/returns").json()["returns"]] == ["R1"]


@pytest.mark.parametrize("role", ["AREA_MANAGER", "STORE_MANAGER", "ACCOUNTANT"])
def test_no_shop_reads_empty_and_cannot_name_one(world, role):
    client, actor = world
    actor.update(roles=[role], store_ids=[], active_store_id=None)
    assert client.get("/api/v1/returns").json() == {"returns": [], "total": 0}
    assert client.get("/api/v1/returns?store_id=S1").status_code == 403


@pytest.mark.parametrize("role", ["ADMIN", "SUPERADMIN"])
def test_admin_keeps_all_shops_and_unattributed_records(world, role):
    client, actor = world
    actor["roles"] = [role]
    assert client.get("/api/v1/returns").json()["total"] == 4
    assert client.get("/api/v1/returns?store_id=S3").json()["total"] == 1
    assert client.get("/api/v1/returns/OLD").status_code == 200


@pytest.mark.parametrize("role", ["AREA_MANAGER", "STORE_MANAGER", "SALES_STAFF"])
def test_foreign_missing_and_unattributed_ids_are_indistinguishable(world, role):
    client, actor = world
    actor["roles"] = [role]
    responses = [client.get(f"/api/v1/returns/{rid}") for rid in ("R3", "MISSING", "OLD")]
    assert [r.status_code for r in responses] == [404, 404, 404]
    assert [r.json() for r in responses] == [{"detail": "Return not found"}] * 3
    assert client.get("/api/v1/returns/R1").status_code == 200
