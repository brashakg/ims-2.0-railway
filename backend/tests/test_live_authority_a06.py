"""An existing signed token loses removed roles/shops on its next request."""

from types import SimpleNamespace

import mongomock
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from api import dependencies
from api.middleware.rbac_enforcement import rbac_enforcement_middleware
from api.routers import auth


@pytest.fixture(autouse=True)
def _isolate_db():
    # All requests use this module's in-memory repository, not app startup.
    yield


@pytest.fixture
def world(monkeypatch):
    collection = mongomock.MongoClient().db.users
    collection.insert_one({
        "user_id": "a06-user", "username": "a06-user", "is_active": True,
        "roles": ["ADMIN"], "store_ids": ["S1", "S2"],
    })
    repo = SimpleNamespace(collection=collection)
    repo.find_by_id = lambda uid: collection.find_one({"user_id": uid})
    monkeypatch.setattr(dependencies, "get_user_repository", lambda: repo)
    monkeypatch.setattr(auth._token_blacklist, "is_revoked", lambda token: False)
    auth.invalidate_user_status("a06-user")
    app = FastAPI()
    app.middleware("http")(rbac_enforcement_middleware)
    from api.main import block_investor_writes
    app.middleware("http")(block_investor_writes)

    @app.get("/api/v1/reports/inventory/valuation")
    async def middleware_only(user=Depends(auth.get_current_user)):
        return {"cost": 123, "roles": user["roles"]}

    @app.get("/route-guard")
    async def route_guard(user=Depends(auth.require_roles("ADMIN"))):
        return {"roles": user["roles"]}

    @app.get("/api/v1/auth/me")
    async def claims(user=Depends(auth.get_current_user)):
        return user

    @app.post("/api/v1/customers")
    async def authenticated_write(user=Depends(auth.get_current_user)):
        return {"written": True}

    @app.get("/shop/{store_id}")
    async def shop(store_id: str, user=Depends(auth.get_current_user)):
        dependencies.validate_store_access(store_id, user)
        return {"store_id": store_id}

    token = auth.create_access_token({
        "user_id": "a06-user", "username": "a06-user", "roles": ["ADMIN"],
        "store_ids": ["S1", "S2"], "active_store_id": "S2", "sid": "same-session",
    })
    yield TestClient(app), collection, {"Authorization": f"Bearer {token}"}
    auth.invalidate_user_status("a06-user")


@pytest.mark.parametrize("path", ["/route-guard", "/api/v1/reports/inventory/valuation"])
def test_same_token_loses_removed_admin_at_both_gates(world, path):
    client, users, headers = world
    assert client.get(path, headers=headers).status_code == 200
    users.update_one({"user_id": "a06-user"}, {"$set": {"roles": ["SALES_STAFF"]}})
    assert client.get(path, headers=headers).status_code == 403


def test_removed_shop_is_denied_and_active_shop_is_reassigned(world):
    client, users, headers = world
    assert client.get("/shop/S2", headers=headers).status_code == 200
    users.update_one({"user_id": "a06-user"}, {"$set": {
        "roles": ["STORE_MANAGER"], "store_ids": ["S1"],
    }})
    assert client.get("/shop/S2", headers=headers).status_code == 403
    assert client.get("/shop/S1", headers=headers).status_code == 200
    claims = client.get("/api/v1/auth/me", headers=headers).json()
    assert claims["active_store_id"] == "S1"
    assert claims["store_ids"] == ["S1"]
    assert claims["sid"] == "same-session"


def test_removing_all_roles_does_not_bypass_middleware(world):
    client, users, headers = world
    users.update_one({"user_id": "a06-user"}, {"$set": {"roles": []}})
    assert client.get("/api/v1/reports/inventory/valuation", headers=headers).status_code == 403
    reply = client.get("/api/v1/auth/me", headers=headers)
    assert reply.status_code == 200
    assert reply.json()["roles"] == []


@pytest.mark.parametrize("shops", [[], None])
def test_removing_all_shops_clears_old_active_shop(world, shops):
    client, users, headers = world
    users.update_one({"user_id": "a06-user"}, {"$set": {
        "roles": ["STORE_MANAGER"], "store_ids": shops,
    }})
    assert client.get("/shop/S2", headers=headers).status_code == 403
    claims = client.get("/api/v1/auth/me", headers=headers).json()
    assert claims["active_store_id"] is None
    assert claims["store_ids"] == []


def test_authority_reads_are_shared_within_one_request_not_between_requests(world, monkeypatch):
    client, users, headers = world
    reads = []
    original = users.find_one

    def observe(*args, **kwargs):
        if len(args) > 1 and "roles" in args[1]:
            reads.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(users, "find_one", observe)
    for expected in (1, 2):
        assert client.get("/api/v1/auth/me", headers=headers).status_code == 200
        assert len(reads) == expected


def test_missing_registration_or_outage_preserves_existing_fail_soft_policy(world, monkeypatch):
    client, users, headers = world
    users.delete_many({})
    assert client.get("/route-guard", headers=headers).status_code == 200

    def unavailable(*args, **kwargs):
        raise RuntimeError("test database unavailable")

    monkeypatch.setattr(users, "find_one", unavailable)
    assert client.get("/route-guard", headers=headers).status_code == 200


def test_deactivated_account_does_not_bypass_cached_allow(world):
    client, users, headers = world
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 200
    users.update_one({"user_id": "a06-user"}, {"$set": {"is_active": False}})
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401


def test_live_role_alias_uses_existing_normalizer(world):
    client, users, headers = world
    users.update_one({"user_id": "a06-user"}, {"$set": {"roles": ["SALES_CASHIER"]}})
    result = client.get("/api/v1/auth/me", headers=headers)
    assert result.status_code == 200
    assert result.json()["roles"] == ["SALES_STAFF"]


def test_new_investor_role_blocks_write_with_existing_admin_token(world):
    client, users, headers = world
    assert client.post("/api/v1/customers", headers=headers).status_code == 200
    users.update_one({"user_id": "a06-user"}, {"$set": {"roles": ["INVESTOR"]}})
    blocked = client.post("/api/v1/customers", headers=headers)
    assert blocked.status_code == 403
    assert blocked.json()["code"] == "investor_read_only"


def test_removed_investor_role_does_not_block_current_admin(world):
    client, users, _ = world
    token = auth.create_access_token({
        "user_id": "a06-user", "roles": ["INVESTOR"], "store_ids": ["S1"],
    })
    headers = {"Authorization": f"Bearer {token}"}
    users.update_one({"user_id": "a06-user"}, {"$set": {"roles": ["INVESTOR"]}})
    assert client.post("/api/v1/customers", headers=headers).status_code == 403
    users.update_one({"user_id": "a06-user"}, {"$set": {"roles": ["ADMIN"]}})
    assert client.post("/api/v1/customers", headers=headers).status_code == 200
