"""A return's price is the accounts roles' to WRITE, not only to read
(owner rulings 2026-10-12: R2 recording supplier money is accounts-only, R3
managers see items, quantities and reasons only on returns and RMAs).

Before: the Create Return form asked a store manager for a "Unit Price" and
showed him a "Total Return Value"; the API required the price from him
(ReturnItemCreate.unit_price, RMALineCreate.unit_cost); credit_issued copied
that figure into the supplier credit with no way for the accountant to set it
(credit_note_amount 29997 from a manager's Rs 9,999 x 3), and an RMA's
expected credit (Rs 8,888) came from the manager's unit cost, so the
accountant's Rs 100 credit left a variance only a write-off could close.

Now:
  * a manager raises a vendor return / RMA WITHOUT prices (a price from him is
    a 403, nothing written);
  * the accounts roles price the lines -- at create, or on the pricing door
    (PUT /vendor-returns/{id}/prices, PUT /vendor-rma/{id}/prices), which the
    managers are refused;
  * the credit (credit_issued), an RMA credit note and the GST debit note are
    refused (422 price_required) while the lines are unpriced, and the figure
    they record is the accountant's;
  * a settled return, an RMA with a credit recorded, or an RTV whose debit note
    was issued keeps its prices (409).

Handlers run for real against mongomock through FastAPI. No emoji (cp1252).
"""
import os
import sys
from types import SimpleNamespace

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import mongomock  # noqa: E402
import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.routers import rtv_debit_notes as dn_router  # noqa: E402
from api.routers import vendor_returns as vr_router  # noqa: E402
from api.routers import vendor_rma as rma_router  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from api.services import rbac_policy  # noqa: E402
from api.services.rbac_policy._core import ACCOUNTS as ACCOUNTS_ROW  # noqa: E402

DHN, PUN = "BV-DHN-01", "WO-PUN-01"
MANAGERS = ("STORE_MANAGER", "AREA_MANAGER")
ACCOUNTS = ("ACCOUNTANT", "ADMIN", "SUPERADMIN")


def _user(role, shop=DHN):
    return {"user_id": f"u-{role.lower()}-{shop}", "username": role.lower(),
            "roles": [role], "store_ids": [shop], "active_store_id": shop}


@pytest.fixture
def world(monkeypatch):
    db = mongomock.MongoClient()["return_prices"]
    db["entities"].insert_one(
        {"entity_id": "E1", "code": "BV", "legal_name": "Better Vision Pvt Ltd",
         "gstin": "20ABCDE1234F1Z5", "state_code": "20",
         "gstins": [{"gstin": "20ABCDE1234F1Z5", "state_code": "20"}]})
    db["stores"].insert_one({"store_id": DHN, "entity_id": "E1", "name": "BV Dhanbad",
                             "state_code": "20"})
    db["vendors"].insert_one({"vendor_id": "V1", "name": "GKB Optical",
                              "gstin": "20ZZZZZ9999Z1Z5", "state_code": "20"})
    db["products"].insert_one({"product_id": "P1", "name": "Zeiss Lens", "gst_rate": 5.0,
                               "hsn": "9001"})
    for mod in (vr_router, rma_router, dn_router):
        monkeypatch.setattr(mod, "_get_db", lambda: db)
    app = FastAPI()
    app.include_router(vr_router.router, prefix="/api/v1/vendor-returns")
    app.include_router(rma_router.router, prefix="/api/v1/vendor-rma")
    app.include_router(dn_router.router, prefix="/api/v1/rtv-debit-notes")
    who = {"user": _user("STORE_MANAGER")}

    async def _current():
        return dict(who["user"])

    app.dependency_overrides[get_current_user] = _current
    client = TestClient(app)

    def call(user, method, path, **kw):
        who["user"] = user
        return client.request(method, "/api/v1" + path, **kw)

    return SimpleNamespace(db=db, call=call)


_LINE = {"product_id": "P1", "product_name": "Zeiss Lens", "quantity": 3,
         "reason": "defective"}
_RETURN = {"vendor_id": "V1", "vendor_name": "GKB Optical", "store_id": DHN,
           "return_type": "credit_note", "items": [_LINE]}


def _create_return(w, user, **line):
    body = dict(_RETURN, items=[dict(_LINE, **line)])
    return w.call(user, "POST", "/vendor-returns/", json=body)


def _to_received(w, rid, user):
    for status in ("approved", "shipped", "received_by_vendor"):
        r = w.call(user, "PATCH", f"/vendor-returns/{rid}/status", json={"status": status})
        assert r.status_code == 200, r.text


def _stored_return(w, rid):
    return w.db["vendor_returns"].find_one({"return_id": rid})


# ============================================================================
# Vendor returns
# ============================================================================


@pytest.mark.parametrize("role", MANAGERS)
def test_a_manager_sending_a_price_is_refused_and_nothing_is_written(world, role):
    r = _create_return(world, _user(role), unit_price=9999.0)
    assert r.status_code == 403, r.text
    assert world.db["vendor_returns"].count_documents({}) == 0


@pytest.mark.parametrize("role", MANAGERS)
def test_a_manager_raises_the_goods_unpriced(world, role):
    r = _create_return(world, _user(role))
    assert r.status_code == 201, r.text
    stored = _stored_return(world, r.json()["return_id"])
    assert stored["items"][0]["unit_price"] is None
    assert stored["total_value"] is None


@pytest.mark.parametrize("role", ACCOUNTS)
def test_an_accounts_role_may_price_as_it_raises(world, role):
    r = _create_return(world, _user(role), unit_price=500.0)
    assert r.status_code == 201, r.text
    assert _stored_return(world, r.json()["return_id"])["total_value"] == 1500.0


def test_the_credit_is_refused_until_the_lines_are_priced_then_is_the_accountants(world):
    mgr, acct = _user("STORE_MANAGER"), _user("ACCOUNTANT")
    rid = _create_return(world, mgr).json()["return_id"]
    _to_received(world, rid, mgr)

    r = world.call(acct, "PATCH", f"/vendor-returns/{rid}/status", json={"status": "credit_issued"})
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["error"] == "price_required"
    stored = _stored_return(world, rid)
    assert stored["status"] == "received_by_vendor" and stored["credit_note_amount"] is None

    # The manager cannot price it: the door is the accounts roles'.
    r = world.call(mgr, "PUT", f"/vendor-returns/{rid}/prices", json={"unit_prices": [9999.0]})
    assert r.status_code == 403, r.text
    assert _stored_return(world, rid)["total_value"] is None

    r = world.call(acct, "PUT", f"/vendor-returns/{rid}/prices", json={"unit_prices": [100.0]})
    assert r.status_code == 200, r.text
    assert r.json()["return"]["total_value"] == 300.0
    stored = _stored_return(world, rid)
    assert stored["priced_by"] == acct["user_id"] and stored["created_by"] == mgr["user_id"]

    r = world.call(acct, "PATCH", f"/vendor-returns/{rid}/status", json={"status": "credit_issued"})
    assert r.status_code == 200, r.text
    assert _stored_return(world, rid)["credit_note_amount"] == 300.0

    # A settled return keeps the prices it was settled at.
    r = world.call(acct, "PUT", f"/vendor-returns/{rid}/prices", json={"unit_prices": [1.0]})
    assert r.status_code == 409, r.text
    assert _stored_return(world, rid)["credit_note_amount"] == 300.0


def test_the_manager_never_reads_the_price_the_accountant_set(world):
    mgr, acct = _user("STORE_MANAGER"), _user("ACCOUNTANT")
    rid = _create_return(world, mgr).json()["return_id"]
    world.call(acct, "PUT", f"/vendor-returns/{rid}/prices", json={"unit_prices": [100.0]})
    body = world.call(mgr, "GET", f"/vendor-returns/{rid}").json()
    assert body["items"] == [{k: _LINE[k] for k in ("product_id", "product_name", "quantity", "reason")}]
    assert "total_value" not in body and "priced_by" not in body


def test_one_price_per_line(world):
    acct = _user("ACCOUNTANT")
    rid = _create_return(world, _user("STORE_MANAGER")).json()["return_id"]
    r = world.call(acct, "PUT", f"/vendor-returns/{rid}/prices", json={"unit_prices": [1.0, 2.0]})
    assert r.status_code == 422, r.text
    r = world.call(acct, "PUT", f"/vendor-returns/{rid}/prices", json={"unit_prices": [-1.0]})
    assert r.status_code == 422, r.text
    assert _stored_return(world, rid)["total_value"] is None


def test_another_shops_return_is_not_found_to_a_shop_accountant(world):
    rid = _create_return(world, _user("STORE_MANAGER")).json()["return_id"]
    r = world.call(_user("ACCOUNTANT", PUN), "PUT", f"/vendor-returns/{rid}/prices",
                   json={"unit_prices": [100.0]})
    assert r.status_code == 404, r.text


def test_the_debit_note_waits_for_the_price_and_then_fixes_it(world):
    mgr, acct = _user("STORE_MANAGER"), _user("ACCOUNTANT")
    rid = _create_return(world, mgr).json()["return_id"]
    issue = {"source_type": "vendor_return", "rtv_id": rid}

    r = world.call(acct, "POST", "/rtv-debit-notes/issue", json=issue)
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["error"] == "price_required"
    assert world.db["debit_notes"].count_documents({}) == 0
    assert world.db["counters"].count_documents({}) == 0  # no serial consumed

    world.call(acct, "PUT", f"/vendor-returns/{rid}/prices", json={"unit_prices": [100.0]})
    r = world.call(acct, "POST", "/rtv-debit-notes/issue", json=issue)
    assert r.status_code == 201, r.text
    assert r.json()["debit_note"]["totals"]["taxable_paise"] == 30000

    # The note was issued at these prices: they stay.
    r = world.call(acct, "PUT", f"/vendor-returns/{rid}/prices", json={"unit_prices": [1.0]})
    assert r.status_code == 409, r.text
    assert _stored_return(world, rid)["total_value"] == 300.0


# ============================================================================
# Vendor RMAs
# ============================================================================

_RMA = {"vendor_id": "V1", "vendor_name": "GKB Optical", "store_id": DHN,
        "lines": [{"product_id": "P1", "product_name": "Zeiss Lens", "quantity": 2,
                   "reason": "DEFECTIVE"}]}


def _raise_rma(w, user, **line):
    body = dict(_RMA, lines=[dict(_RMA["lines"][0], **line)])
    return w.call(user, "POST", "/vendor-rma/", json=body)


def _dispatched_rma(w, user):
    rid = _raise_rma(w, user).json()["rma_id"]
    for step, body in (("authorize", {"vendor_rma_number": "Z-1"}),
                       ("dispatch", {"carrier": "DTDC", "awb": "A1"})):
        r = w.call(user, "POST", f"/vendor-rma/{rid}/{step}", json=body)
        assert r.status_code == 200, r.text
    return rid


def _stored_rma(w, rid):
    return w.db["vendor_rmas"].find_one({"rma_id": rid})


@pytest.mark.parametrize("role", MANAGERS)
def test_a_manager_raising_an_rma_with_a_price_is_refused(world, role):
    r = _raise_rma(world, _user(role), unit_cost=4444.0)
    assert r.status_code == 403, r.text
    assert world.db["vendor_rmas"].count_documents({}) == 0


def test_an_rma_credit_waits_for_the_accountants_price(world):
    mgr, acct = _user("STORE_MANAGER"), _user("ACCOUNTANT")
    rid = _dispatched_rma(world, mgr)
    stored = _stored_rma(world, rid)
    assert stored["expected_credit_paise"] is None
    assert stored["lines"][0]["unit_cost_paise"] is None

    credit = {"credit_note_number": "CN-1", "received_amount": 100.0}
    r = world.call(acct, "POST", f"/vendor-rma/{rid}/credit-note", json=credit)
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["error"] == "price_required"
    assert _stored_rma(world, rid)["credit_notes"] == []

    r = world.call(mgr, "PUT", f"/vendor-rma/{rid}/prices", json={"unit_costs": [4444.0]})
    assert r.status_code == 403, r.text
    assert _stored_rma(world, rid)["expected_credit_paise"] is None

    r = world.call(acct, "PUT", f"/vendor-rma/{rid}/prices", json={"unit_costs": [50.0]})
    assert r.status_code == 200, r.text
    assert r.json()["expected_credit_paise"] == 10000

    r = world.call(acct, "POST", f"/vendor-rma/{rid}/credit-note", json=credit)
    assert r.status_code == 200, r.text
    stored = _stored_rma(world, rid)
    assert (stored["expected_credit_paise"], stored["variance_paise"]) == (10000, 0)
    # A manager closes the reconciled RMA: no write-off needed.
    r = world.call(mgr, "POST", f"/vendor-rma/{rid}/close", json={})
    assert r.status_code == 200, r.text

    # Credited and closed: the price the credit reconciled against stays.
    r = world.call(acct, "PUT", f"/vendor-rma/{rid}/prices", json={"unit_costs": [1.0]})
    assert r.status_code == 409, r.text


def test_an_unpriced_rma_is_refused_before_the_approval_tier_reads_its_expectation(world):
    # A large credit on an unpriced RMA: the answer is "price it first", not
    # an approval demanded against an expected credit nobody has set.
    rid = _dispatched_rma(world, _user("STORE_MANAGER"))
    r = world.call(_user("ACCOUNTANT"), "POST", f"/vendor-rma/{rid}/credit-note",
                   json={"credit_note_number": "CN-BIG", "received_amount": 60000.0})
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["error"] == "price_required"
    assert world.db["approval_requests"].count_documents({}) == 0


def test_an_rma_debit_note_waits_for_the_price(world):
    acct = _user("ACCOUNTANT")
    rid = _raise_rma(world, _user("STORE_MANAGER")).json()["rma_id"]
    issue = {"source_type": "vendor_rma", "rtv_id": rid}
    r = world.call(acct, "POST", "/rtv-debit-notes/issue", json=issue)
    assert r.status_code == 422, r.text
    assert world.db["debit_notes"].count_documents({}) == 0

    world.call(acct, "PUT", f"/vendor-rma/{rid}/prices", json={"unit_costs": [50.0]})
    r = world.call(acct, "POST", "/rtv-debit-notes/issue", json=issue)
    assert r.status_code == 201, r.text
    r = world.call(acct, "PUT", f"/vendor-rma/{rid}/prices", json={"unit_costs": [1.0]})
    assert r.status_code == 409, r.text
    assert _stored_rma(world, rid)["expected_credit_paise"] == 10000


# ============================================================================
# Round 4 P5 (audit F63): another shop's RMA, debit note or return id is the
# 404 a missing id gets -- never a 403 'No access to store X' that confirms
# the record exists and names its shop -- on every by-id read and write.
# ============================================================================


def test_another_shops_rma_debit_note_or_return_answers_like_a_missing_id(world):
    acct = _user("ACCOUNTANT")
    rid = _create_return(world, _user("STORE_MANAGER")).json()["return_id"]
    world.call(acct, "PUT", f"/vendor-returns/{rid}/prices", json={"unit_prices": [100.0]})
    r = world.call(acct, "POST", "/rtv-debit-notes/issue",
                   json={"source_type": "vendor_return", "rtv_id": rid})
    assert r.status_code == 201, r.text
    dn_id = r.json()["debit_note"]["debit_note_id"]
    rma = _raise_rma(world, _user("STORE_MANAGER")).json()["rma_id"]
    # A second priced Dhanbad return with no debit note yet (the issue door).
    other = {**_stored_return(world, rid), "return_id": "VR-UNISSUED"}
    other.pop("_id")
    world.db["vendor_returns"].insert_one(other)

    pune = _user("ACCOUNTANT", PUN)
    doors = (
        ("GET", "/vendor-rma/{}", None),
        ("POST", "/vendor-rma/{}/authorize", {"vendor_rma_number": "Z-1"}),
        ("POST", "/vendor-rma/{}/dispatch", {"carrier": "DTDC", "awb": "A1"}),
        ("PUT", "/vendor-rma/{}/prices", {"unit_costs": [1.0]}),
        ("POST", "/vendor-rma/{}/credit-note", {"credit_note_number": "CN", "received_amount": 1.0}),
        ("POST", "/vendor-rma/{}/reject", {"reason": "no"}),
        ("POST", "/vendor-rma/{}/close", {}),
    )
    for method, path, body in doors:
        seen = world.call(pune, method, path.format(rma), json=body)
        missing = world.call(pune, method, path.format("RMA-NOPE"), json=body)
        assert (seen.status_code, seen.json()) == (missing.status_code, missing.json())             == (404, {"detail": "RMA not found"}), (path, seen.text)
    for path in ("/rtv-debit-notes/{}", "/rtv-debit-notes/{}/print", "/rtv-debit-notes/{}/tally"):
        seen = world.call(pune, "GET", path.format(dn_id))
        missing = world.call(pune, "GET", path.format("DN-NOPE"))
        assert (seen.status_code, seen.json()) == (missing.status_code, missing.json())             == (404, {"detail": "Debit note not found"}), (path, seen.text)
    for rtv_id in ("VR-UNISSUED", "VR-NOPE"):
        r = world.call(pune, "POST", "/rtv-debit-notes/issue",
                       json={"source_type": "vendor_return", "rtv_id": rtv_id})
        assert (r.status_code, r.json()) == (404, {"detail": "source RTV not found"}), r.text
    # Nothing was written for the other shop.
    assert _stored_rma(world, rma)["status"] == "DRAFT"
    assert world.db["debit_notes"].count_documents({}) == 1


# ============================================================================
# The pricing doors' policy rows are the accounts roles, as their code gates
# ============================================================================


@pytest.mark.parametrize("path", ("/api/v1/vendor-returns/{return_id}/prices",
                                  "/api/v1/vendor-rma/{rma_id}/prices"))
def test_the_pricing_doors_are_the_accounts_rows(path):
    row = next(r for r in rbac_policy.POLICY if (r["method"], r["path"]) == ("PUT", path))
    assert row["allowed"] is ACCOUNTS_ROW
    assert row.get("store_scoped") is True
