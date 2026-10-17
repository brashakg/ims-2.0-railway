"""A vendor return has ONE per-object store guard (vendor_returns._return_in_reach).

The detail (GET /vendor-returns/{id}) and the status change (PATCH
/vendor-returns/{id}/status) on the same return answer alike: a return outside
the caller's shops -- another shop's, or a legacy one stamped with no shop --
is "not found", exactly like an id that does not exist. Before this, the
detail said 404 while the status change said 403 "No access to store
BV-DHN-01" (confirming the id and naming its shop), and it APPROVED a no-shop
return the detail hid.
"""
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import mongomock  # noqa: E402
import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.routers import vendor_returns as vr  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402

DHN, PUN = "BV-DHN-01", "WO-PUN-01"
MGR_PUN = {"user_id": "m-pun", "roles": ["STORE_MANAGER"], "store_ids": [PUN],
           "active_store_id": PUN}
ADMIN = {"user_id": "adm", "roles": ["ADMIN"], "store_ids": [], "active_store_id": None}


def _ret(rid, store):
    return {"return_id": rid, "vendor_id": "V1", "store_id": store, "status": "created",
            "return_type": "replacement",
            "items": [{"product_id": "P", "quantity": 1, "reason": "defective"}],
            "status_history": []}


@pytest.fixture
def call(monkeypatch):
    db = mongomock.MongoClient()["vr_guard"]
    db["vendor_returns"].insert_many([
        _ret("VR-DHN", DHN), _ret("VR-NOSHOP", None), _ret("VR-PUN", PUN),
    ])
    monkeypatch.setattr(vr, "_get_db", lambda: db)
    app = FastAPI()
    app.include_router(vr.router, prefix="/vendor-returns")
    tc = TestClient(app)

    def do(method, path, user, **kw):
        async def _u():
            return dict(user)
        app.dependency_overrides[get_current_user] = _u
        r = tc.request(method, path, **kw)
        return r.status_code, r.json()
    return do, db


@pytest.mark.parametrize("rid", ["VR-DHN", "VR-NOSHOP"])
def test_a_return_outside_the_shop_answers_like_a_missing_id(call, rid):
    do, db = call
    missing_get = do("GET", "/vendor-returns/VR-NOPE", MGR_PUN)
    missing_patch = do("PATCH", "/vendor-returns/VR-NOPE/status", MGR_PUN,
                       json={"status": "approved"})
    assert missing_get == (404, {"detail": "Return not found"})
    assert missing_patch == missing_get

    assert do("GET", f"/vendor-returns/{rid}", MGR_PUN) == missing_get
    # The status change on the same return says the same -- not a 403 naming
    # the shop, and not a 200 that writes to a return the detail hides.
    assert do("PATCH", f"/vendor-returns/{rid}/status", MGR_PUN,
              json={"status": "approved"}) == missing_get
    assert db["vendor_returns"].find_one({"return_id": rid})["status"] == "created"


def test_the_manager_still_moves_his_own_shops_return(call):
    do, db = call
    code, body = do("PATCH", "/vendor-returns/VR-PUN/status", MGR_PUN,
                    json={"status": "approved"})
    assert code == 200, body
    assert db["vendor_returns"].find_one({"return_id": "VR-PUN"})["status"] == "approved"


def test_an_admin_reaches_every_return(call):
    do, db = call
    for rid in ("VR-DHN", "VR-NOSHOP"):
        assert do("GET", f"/vendor-returns/{rid}", ADMIN)[0] == 200
        code, body = do("PATCH", f"/vendor-returns/{rid}/status", ADMIN,
                        json={"status": "approved"})
        assert code == 200, body
