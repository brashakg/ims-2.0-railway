"""Recording supplier money is the accounts roles' alone, and a return's unit
price is supplier money too.

Owner rulings 2026-10-12 (they extend 2026-10-01: "supplier balances should
not be shown to anyone apart from admin superadmin and accountant"):

R2  Recording supplier money is ADMIN / SUPERADMIN / ACCOUNTANT only: an RMA's
    credit note (POST /vendor-rma/{id}/credit-note), an RMA close that writes
    off a variance (POST /vendor-rma/{id}/close, write_off_variance), a vendor
    return's credit_issued transition (PATCH /vendor-returns/{id}/status) and
    the RTV GST debit note's issue (POST /rtv-debit-notes/issue). The managers
    keep the goods side: raise, authorize, dispatch, ship, receive, reject or
    cancel before the goods leave, 'replaced' on a replacement return, close
    without a write-off.
    Forgoing a credit is recording it as nil, so it is the accounts roles' too:
    rejecting a DISPATCHED RMA (its whole expected credit) and closing a
    credit-note return as 'replaced'. A manager doing either would leave
    nothing for the accountant to record the supplier's credit against.
    Two-person control on a large RMA credit (E4 'rtv' approval): the accounts
    roles both record the credit and approve 'rtv' (R1), so the approval spent
    must come from someone other than the recorder and other than whoever
    raised the request -- else one accountant raises, approves and spends it.
R3  On vendor returns and RMAs a manager (and any non-accounts role) reads the
    items, quantities and reasons only -- no unit price, no line value, no
    rate -- on every read and every write response. RTV debit-note lines were
    already stripped; held here too.

Each R2 gate is held on its own, so undoing any one of them fails a test: the
route's own require_roles dependency (whole-money routes), the inline
require_payables in the handler body (a direct call skips Depends), and the
rbac_policy row. The HTTP answer through FastAPI is checked on top.

The roles that may record money are read from the code (cost_mask.AP_ROLES,
rbac_policy ACCOUNTS); the ruling's own list is spelled once below so a drift
in either fails here. Handlers run for real against the in-memory Mongo double
of test_supplier_money_masked_for_managers. No network. No emoji (cp1252).
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from types import SimpleNamespace
from typing import Any, Dict

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-supplier-money-recorded")
os.environ.setdefault("ENVIRONMENT", "test")

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.services import payables_mask as pm  # noqa: E402
from api.services import rbac_policy as rbac  # noqa: E402
from api.services.cost_mask import AP_ROLES  # noqa: E402
from tests.test_supplier_money_masked_for_managers import (  # noqa: E402
    FakeDB,
    _keys,
)


# The ruling's list: who records (and reads) supplier money.
RECORDERS = ("ACCOUNTANT", "ADMIN", "SUPERADMIN")
MANAGERS = ("STORE_MANAGER", "AREA_MANAGER")
ALL_ROLES = (
    "SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER", "ACCOUNTANT",
    "CATALOG_MANAGER", "OPTOMETRIST", "SALES_CASHIER", "SALES_STAFF",
    "CASHIER", "WORKSHOP_STAFF", "DESIGN_MANAGER",
)
# Non-accounts readers of returns: the managers, plus the Vendor Returns
# screen's WORKSHOP_STAFF (direct calls: the read gates are #1161's business).
NON_ACCOUNTS = MANAGERS + ("WORKSHOP_STAFF",)

# R3: a unit price, a line value or a rate, by name...
RETURN_PRICE = {
    "unit_price", "unit_cost", "unit_cost_paise", "unit_cost_rupees",
    "rate", "rate_paise", "line_expected_paise", "line_total",
    "line_total_paise", "line_value", "taxable_paise",
}
# ...and by shape, so a money field added later under another name is caught
# too. (gst_rate is a tax percentage, not a price: not matched.)
_MONEYISH = re.compile(r"price|cost|paise|rupees|amount|value|^rate$|^rate_")


def _moneyish(body: Any) -> set:
    return {k for k in _keys(body) if isinstance(k, str) and _MONEYISH.search(k)}


def _run(coro):
    return asyncio.run(coro)


def _user(role: str) -> Dict[str, Any]:
    return {
        "user_id": f"u-{role.lower()}",
        "roles": [role],
        "store_ids": ["S1"],
        "active_store_id": "S1",
    }


def _status(coro) -> int:
    with pytest.raises(HTTPException) as ei:
        _run(coro)
    return ei.value.status_code


def _route_gate(router, method: str, path: str):
    """The route's own require_roles dependency (auth.require_roles' _dep)."""
    for route in router.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", ()):
            for dep in route.dependant.dependencies:
                if getattr(dep.call, "__name__", "") == "_dep":
                    return dep.call
            raise AssertionError(f"{method} {path} carries no require_roles gate")
    raise AssertionError(f"no {method} {path}")


def _gate_admits(gate, role: str) -> bool:
    try:
        _run(gate(current_user=_user(role)))
        return True
    except HTTPException as exc:
        assert exc.status_code == 403
        return False


# ============================================================================
# Fixtures: each router against the in-memory Mongo double
# ============================================================================


@pytest.fixture
def rma(monkeypatch):
    import api.routers.vendor_rma as r

    db = FakeDB()
    monkeypatch.setattr(r, "_get_db", lambda: db)
    return SimpleNamespace(r=r, db=db)


@pytest.fixture
def vret(monkeypatch):
    import api.routers.vendor_returns as r

    db = FakeDB()
    coll = db.get_collection("vendor_returns")
    for rid, status, kind in (
        ("VR-NEW", "created", "credit_note"), ("VR-OK", "approved", "credit_note"),
        ("VR-SHIP", "shipped", "credit_note"),
        ("VR-RCVD", "received_by_vendor", "credit_note"),
        ("VR-RCVD2", "received_by_vendor", "credit_note"),
        ("VR-SWAP", "received_by_vendor", "replacement"),
    ):
        coll.insert_one({
            "return_id": rid, "vendor_id": "V1", "vendor_name": "GKB", "store_id": "S1",
            "items": [{"product_id": "P1", "product_name": "Zeiss Lens", "quantity": 2,
                       "reason": "defective", "unit_price": 1500.0}],
            "return_type": kind, "status": status, "total_value": 3000.0,
            "credit_note_number": None, "credit_note_amount": None,
            "created_at": "2026-10-10T10:00:00", "status_history": [],
        })
    # A legacy-shaped return: its lines carry the rupee / rate forms the RTV
    # debit-note engine also reads (rtv_debit_note._norm_line).
    coll.insert_one({
        "return_id": "VR-LEGACY", "vendor_id": "V1", "vendor_name": "GKB", "store_id": "S1",
        "items": [{"product_id": "P2", "product_name": "Frame", "quantity": 1,
                   "reason": "wrong_item", "unit_cost": 800.0, "rate": 800.0,
                   "rate_paise": 80000}],
        "return_type": "replacement", "status": "created",
        "created_at": "2026-10-09T10:00:00", "status_history": [],
    })
    monkeypatch.setattr(r, "_get_db", lambda: db)
    return SimpleNamespace(r=r, db=db)


@pytest.fixture
def rtv(monkeypatch):
    import api.routers.rtv_debit_notes as r

    db = FakeDB()
    db.get_collection("entities").insert_one(
        {"entity_id": "E1", "code": "BV", "legal_name": "Better Vision Pvt Ltd",
         "gstin": "20ABCDE1234F1Z5", "state_code": "20",
         "gstins": [{"gstin": "20ABCDE1234F1Z5", "state_code": "20"}]})
    db.get_collection("stores").insert_one(
        {"store_id": "S1", "entity_id": "E1", "name": "BV Ranchi", "state_code": "20"})
    db.get_collection("vendors").insert_one(
        {"vendor_id": "V1", "name": "GKB Optical", "gstin": "20ZZZZZ9999Z1Z5",
         "state_code": "20", "address": "Jamshedpur"})
    original_return = {
        "return_id": "VR-1", "store_id": "S1", "vendor_id": "V1", "entity_id": "E1",
        "vendor_name": "GKB Optical", "purchase_invoice_number": "PINV-77",
        "lines": [{"product_id": "P1", "product_name": "Zeiss Lens", "hsn": "9001",
                   "quantity": 2, "rate_paise": 150000, "gst_rate": 5.0}],
    }
    from tests.test_f20_rtv_debit_note import _seed_original_bill
    _seed_original_bill(db, original_return)
    db.get_collection("vendor_returns").insert_one(original_return)
    monkeypatch.setattr(r, "_get_db", lambda: db)
    return SimpleNamespace(r=r, db=db)


def _raise(rma, role="STORE_MANAGER", unit_cost=1500.0):
    """``role`` raises the RMA -- the goods, no price (owner rulings 2026-10-12
    R2 + R3: a return's price is the accounts roles' to write) -- and an
    ACCOUNTANT prices its line at ``unit_cost`` (None leaves it unpriced).
    Returns the raise response."""
    r = rma.r
    raised = _run(r.raise_rma(r.RMACreate(
        vendor_id="V1", vendor_name="Zeiss", store_id="S1",
        lines=[r.RMALineCreate(product_id="P1", product_name="Lens", quantity=2,
                               reason="DEFECTIVE")]),
        current_user=_user(role)))
    if unit_cost is not None:
        _run(r.price_rma(raised["rma_id"], r.RMAPrices(unit_costs=[unit_cost]),
                         current_user=_user("ACCOUNTANT")))
    return raised


def _dispatched(rma, role="STORE_MANAGER"):
    """A manager raises, authorizes and dispatches an Rs 3,000 RMA (the goods
    side); returns its id and every write response."""
    r = rma.r
    raised = _raise(rma, role)
    rid = raised["rma_id"]
    authorized = _run(r.authorize_rma(rid, r.RMAAuthorize(vendor_rma_number="Z-1"),
                                      current_user=_user(role)))
    dispatched = _run(r.dispatch_rma(rid, r.RMADispatch(carrier="DTDC", awb="AWB1"),
                                     current_user=_user(role)))
    return rid, [raised, authorized, dispatched]


def _credit(rma, rid, role="ACCOUNTANT", amount=1000.0, number="CN-Z1"):
    r = rma.r
    return _run(r.record_credit_note(
        rid, r.RMACreditNote(credit_note_number=number, received_amount=amount),
        current_user=_user(role)))


def _stored_rma(rma, rid):
    return rma.db.get_collection("vendor_rmas").find_one({"rma_id": rid})


def _stored_return(vret, rid):
    return vret.db.get_collection("vendor_returns").find_one({"return_id": rid})


# ============================================================================
# R2.1  The rule itself: the ruling's list is the code's
# ============================================================================


def test_the_recorders_are_the_one_payables_rule():
    for role in ALL_ROLES:
        assert pm.can_see_payables(_user(role)) is (role in RECORDERS), role
    assert set(AP_ROLES) | {"SUPERADMIN"} == set(RECORDERS)


# ============================================================================
# R2.2  Whole-money routes: the route's own gate is AP_ROLES
# ============================================================================

WHOLE_MONEY_ROUTES = (
    ("vendor_rma", "POST", "/{rma_id}/credit-note"),
    ("rtv_debit_notes", "POST", "/issue"),
)


@pytest.mark.parametrize("module,method,path", WHOLE_MONEY_ROUTES)
def test_whole_money_route_gate_admits_the_accounts_roles_alone(module, method, path):
    import importlib

    router = importlib.import_module(f"api.routers.{module}").router
    gate = _route_gate(router, method, path)
    admitted = {role for role in ALL_ROLES if _gate_admits(gate, role)}
    assert admitted == set(RECORDERS), (module, path, admitted)


# ============================================================================
# R2.3  The rbac_policy rows equal the gates
# ============================================================================

POLICY_PATHS = {
    "credit-note": ("POST", "/api/v1/vendor-rma/RMA-1/credit-note"),
    "issue": ("POST", "/api/v1/rtv-debit-notes/issue"),
}
# Goods-side routes whose handler narrows the money case inline: their row
# stays with the purchase roles (the managers still use them).
GOODS_SIDE_PATHS = {
    "return-status": ("PATCH", "/api/v1/vendor-returns/VR-1/status"),
    "rma-close": ("POST", "/api/v1/vendor-rma/RMA-1/close"),
    "rma-reject": ("POST", "/api/v1/vendor-rma/RMA-1/reject"),
}


@pytest.mark.parametrize("name", sorted(POLICY_PATHS))
def test_policy_row_is_accounts_for_a_whole_money_route(name):
    method, path = POLICY_PATHS[name]
    row = rbac.policy_for(method, path)
    assert row is not None, path
    assert sorted(row["allowed"]) == sorted(AP_ROLES), (name, row["allowed"])
    assert row.get("store_scoped") is True
    for role in ALL_ROLES:
        assert rbac.check_access(method, path, [role]) is (role in RECORDERS), (name, role)


@pytest.mark.parametrize("name", sorted(GOODS_SIDE_PATHS))
def test_policy_row_keeps_the_goods_side_for_managers(name):
    method, path = GOODS_SIDE_PATHS[name]
    for role in MANAGERS + RECORDERS:
        assert rbac.check_access(method, path, [role]) is True, (name, role)


# ============================================================================
# R2.4  RMA credit note: a manager is refused (and nothing is written)
# ============================================================================


@pytest.mark.parametrize("role", MANAGERS)
def test_rma_credit_note_refused_to_a_manager_and_nothing_written(rma, role):
    rid, _ = _dispatched(rma)
    assert _status(rma.r.record_credit_note(
        rid, rma.r.RMACreditNote(credit_note_number="CN-M", received_amount=1000.0),
        current_user=_user(role))) == 403
    stored = _stored_rma(rma, rid)
    assert stored["status"] == "DISPATCHED"
    assert stored["received_credit_paise"] == 0 and stored["credit_notes"] == []


@pytest.mark.parametrize("role", RECORDERS)
def test_rma_credit_note_recorded_by_accounts(rma, role):
    rid, _ = _dispatched(rma)
    res = _credit(rma, rid, role)
    assert res["ok"] is True and res["received_credit_paise"] == 100000
    assert _stored_rma(rma, rid)["received_credit_paise"] == 100000


# ============================================================================
# R2.5  RMA close: a write-off is the accounts roles'; a plain close is not
# ============================================================================


@pytest.mark.parametrize("role", MANAGERS)
def test_rma_write_off_refused_to_a_manager_and_nothing_written(rma, role):
    rid, _ = _dispatched(rma)
    _credit(rma, rid)  # Rs 1,000 of Rs 3,000: Rs 2,000 outstanding
    assert _status(rma.r.close_rma(rid, rma.r.RMAClose(write_off_variance=True),
                                   current_user=_user(role))) == 403
    stored = _stored_rma(rma, rid)
    assert stored["status"] == "CREDIT_RECEIVED" and "written_off_paise" not in stored


@pytest.mark.parametrize("role", RECORDERS)
def test_rma_write_off_by_accounts(rma, role):
    rid, _ = _dispatched(rma)
    _credit(rma, rid)
    res = _run(rma.r.close_rma(rid, rma.r.RMAClose(write_off_variance=True),
                               current_user=_user(role)))
    assert res["status"] == "CLOSED" and res["written_off_paise"] == 200000
    assert _stored_rma(rma, rid)["written_off_paise"] == 200000


@pytest.mark.parametrize("role", MANAGERS)
def test_rma_plain_close_stays_with_managers(rma, role):
    rid, _ = _dispatched(rma)
    _credit(rma, rid, amount=3000.0)  # fully reconciled by accounts
    res = _run(rma.r.close_rma(rid, rma.r.RMAClose(), current_user=_user(role)))
    assert res["ok"] is True and res["status"] == "CLOSED"
    # An outstanding variance still refuses a plain close (409, as before).
    rid2, _ = _dispatched(rma)
    _credit(rma, rid2, number="CN-Z2")
    assert _status(rma.r.close_rma(rid2, rma.r.RMAClose(), current_user=_user(role))) == 409


@pytest.mark.parametrize("role", MANAGERS)
def test_rma_goods_side_stays_with_managers(rma, role):
    rid, writes = _dispatched(rma, role)
    assert [w["ok"] for w in writes] == [True, True, True]
    assert _stored_rma(rma, rid)["status"] == "DISPATCHED"
    # A reject before the goods leave -- from DRAFT and from AUTHORIZED.
    draft = _raise(rma, role)["rma_id"]
    authorized = _raise(rma, role)["rma_id"]
    _run(rma.r.authorize_rma(authorized, rma.r.RMAAuthorize(vendor_rma_number="Z-2"),
                             current_user=_user(role)))
    for other in (draft, authorized):
        res = _run(rma.r.reject_rma(other, rma.r.RMAReject(reason="sent back"),
                                    current_user=_user(role)))
        assert res["ok"] is True and res["status"] == "REJECTED"
        stored = _stored_rma(rma, other)
        assert stored["status"] == "REJECTED"
        assert stored["status_history"][-1]["notes"] == "sent back"
        assert stored["status_history"][-1]["by"] == _user(role)["user_id"]


# ============================================================================
# R2.6  Vendor return: credit_issued is the accounts roles'; the rest is not
# ============================================================================


def _patch(vret, rid, status, role):
    return vret.r.update_return_status(
        rid, vret.r.VendorReturnStatusUpdate(status=status), current_user=_user(role))


@pytest.mark.parametrize("role", MANAGERS)
def test_credit_issued_refused_to_a_manager_and_nothing_written(vret, role):
    assert _status(_patch(vret, "VR-RCVD", "credit_issued", role)) == 403
    stored = _stored_return(vret, "VR-RCVD")
    assert stored["status"] == "received_by_vendor"
    assert stored["credit_note_number"] is None and stored["credit_note_amount"] is None


@pytest.mark.parametrize("role", RECORDERS)
def test_credit_issued_by_accounts(vret, role):
    # The old status-only shortcut cannot fabricate a supplier's note. The
    # canonical API's actual-note success/retry is covered separately.
    assert _status(_patch(vret, "VR-RCVD", "credit_issued", role)) == 422
    stored = _stored_return(vret, "VR-RCVD")
    assert stored["credit_note_amount"] is None
    assert stored["credit_note_number"] is None


@pytest.mark.parametrize("role", MANAGERS)
@pytest.mark.parametrize("rid,status", (
    ("VR-NEW", "approved"), ("VR-NEW", "cancelled"), ("VR-OK", "shipped"),
    ("VR-SHIP", "received_by_vendor"), ("VR-SWAP", "replaced"),
))
def test_return_goods_transitions_stay_with_managers(vret, role, rid, status):
    res = _run(_patch(vret, rid, status, role))
    assert res["return"]["status"] == status
    assert _stored_return(vret, rid)["status"] == status


@pytest.mark.parametrize("role", MANAGERS)
def test_return_create_stays_with_managers(vret, role):
    r = vret.r
    res = _run(r.create_vendor_return(r.VendorReturnCreate(
        vendor_id="V1", vendor_name="GKB", store_id="S1", return_type="credit_note",
        items=[r.ReturnItemCreate(product_id="P9", product_name="Lens", quantity=1,
                                  reason="defective")]),
        current_user=_user(role)))
    assert set(res) == {"return_id", "message"}


# ============================================================================
# R2.7  RTV debit note: the issue is the accounts roles'
# ============================================================================


def _issue(rtv, role):
    return rtv.r.issue_debit_note(
        rtv.r.DebitNoteIssue(source_type="vendor_return", rtv_id="VR-1"),
        current_user=_user(role))


@pytest.mark.parametrize("role", MANAGERS)
def test_debit_note_issue_refused_to_a_manager_and_nothing_written(rtv, role):
    rtv.db.touched.clear()
    assert _status(_issue(rtv, role)) == 403
    # Refused before anything is read or written: no note, no serial consumed
    # (services/rtv_debit_note keeps its FY serial in "counters").
    assert rtv.db.touched == []
    assert rtv.db.get_collection("debit_notes").docs == []
    assert rtv.db.get_collection("counters").docs == []


@pytest.mark.parametrize("role", RECORDERS)
def test_debit_note_issued_by_accounts(rtv, role):
    res = _run(_issue(rtv, role))
    assert res["debit_note"]["totals"]["grand_total_paise"] == 315000
    assert len(rtv.db.get_collection("debit_notes").docs) == 1


# ============================================================================
# R2.8  Through FastAPI: Depends + the handler together
# ============================================================================


@pytest.fixture
def http(rma, vret, rtv):
    from api.routers.auth import get_current_user

    app = FastAPI()
    app.include_router(rma.r.router, prefix="/api/v1/vendor-rma")
    app.include_router(vret.r.router, prefix="/api/v1/vendor-returns")
    app.include_router(rtv.r.router, prefix="/api/v1/rtv-debit-notes")
    who = {"role": "STORE_MANAGER"}
    app.dependency_overrides[get_current_user] = lambda: _user(who["role"])
    return SimpleNamespace(client=TestClient(app), who=who, rma=rma, vret=vret, rtv=rtv)


def test_http_every_money_write_is_403_for_a_manager_and_lands_for_accounts(http):
    rid, _ = _dispatched(http.rma)
    calls = (
        ("post", f"/api/v1/vendor-rma/{rid}/credit-note",
         {"credit_note_number": "CN-H", "received_amount": 1000.0}),
        ("post", f"/api/v1/vendor-rma/{rid}/close", {"write_off_variance": True}),
        ("patch", "/api/v1/vendor-returns/VR-RCVD/status", {"status": "credit_issued"}),
        ("post", "/api/v1/rtv-debit-notes/issue",
         {"source_type": "vendor_return", "rtv_id": "VR-1"}),
    )
    for role in MANAGERS:
        http.who["role"] = role
        for verb, url, body in calls:
            r = getattr(http.client, verb)(url, json=body)
            assert r.status_code == 403, (role, url, r.text)
    assert _stored_rma(http.rma, rid)["received_credit_paise"] == 0
    assert http.rtv.db.get_collection("debit_notes").docs == []

    http.who["role"] = "ACCOUNTANT"
    for verb, url, body in calls:
        r = getattr(http.client, verb)(url, json=body)
        expected = (422,) if url.endswith("/status") else (200, 201)
        assert r.status_code in expected, (url, r.text)
    stored = _stored_rma(http.rma, rid)
    assert stored["status"] == "CLOSED" and stored["written_off_paise"] == 200000
    assert _stored_return(http.vret, "VR-RCVD")["credit_note_amount"] is None
    assert len(http.rtv.db.get_collection("debit_notes").docs) == 1


def test_http_goods_side_still_open_to_a_manager(http):
    http.who["role"] = "STORE_MANAGER"
    r = http.client.patch("/api/v1/vendor-returns/VR-OK/status", json={"status": "shipped"})
    assert r.status_code == 200, r.text
    r = http.client.post("/api/v1/vendor-rma", json={
        "vendor_id": "V1", "vendor_name": "Zeiss", "store_id": "S1",
        "lines": [{"product_id": "P1", "product_name": "Lens", "quantity": 1,
                   "reason": "WRONG"}]})
    assert r.status_code == 201, r.text
    rid = r.json()["rma_id"]
    for step, body in (("authorize", {"vendor_rma_number": "Z-9"}),
                       ("dispatch", {"carrier": "DTDC", "awb": "A9"})):
        r = http.client.post(f"/api/v1/vendor-rma/{rid}/{step}", json=body)
        assert r.status_code == 200, (step, r.text)
    # 'replaced' on a replacement return is the goods coming back.
    r = http.client.patch("/api/v1/vendor-returns/VR-SWAP/status", json={"status": "replaced"})
    assert r.status_code == 200, r.text


# ============================================================================
# R2.9  Forgoing a credit is recording it as nil: the accounts roles'
#       (a DISPATCHED RMA's reject; a credit-note return closed as 'replaced')
# ============================================================================


@pytest.mark.parametrize("role", MANAGERS)
def test_rma_reject_after_dispatch_refused_to_a_manager_and_nothing_written(rma, role):
    rid, _ = _dispatched(rma)
    assert _status(rma.r.reject_rma(rid, rma.r.RMAReject(reason="vendor refused"),
                                    current_user=_user(role))) == 403
    stored = _stored_rma(rma, rid)
    assert stored["status"] == "DISPATCHED"
    assert stored["status_history"][-1]["status"] == "DISPATCHED"
    # The supplier's credit is still there for accounts to record.
    assert _credit(rma, rid)["received_credit_paise"] == 100000


@pytest.mark.parametrize("role", RECORDERS)
def test_rma_reject_after_dispatch_by_accounts(rma, role):
    rid, _ = _dispatched(rma)
    res = _run(rma.r.reject_rma(rid, rma.r.RMAReject(reason="vendor refused"),
                                current_user=_user(role)))
    assert res["ok"] is True and res["status"] == "REJECTED"
    stored = _stored_rma(rma, rid)
    assert stored["status"] == "REJECTED"
    assert stored["status_history"][-1]["by"] == _user(role)["user_id"]


@pytest.mark.parametrize("role", MANAGERS)
def test_rma_reject_is_pinned_to_the_state_it_read(rma, monkeypatch, role):
    """The goods are dispatched between the reject's read and its write: the
    reject loses the race (409) -- it never lands on the dispatched RMA."""
    from api.services.vendor_rma import VendorRMAEngine

    rid, _ = _dispatched(rma)
    stale = {"reads": 1}

    class _ReadBeforeDispatch(VendorRMAEngine):
        def get(self, rma_id):
            doc = super().get(rma_id)
            if doc is not None and stale["reads"]:
                stale["reads"] -= 1
                doc = {**doc, "status": "AUTHORIZED"}
            return doc

    monkeypatch.setattr(rma.r, "_engine", lambda: _ReadBeforeDispatch(db=rma.db))
    with pytest.raises(HTTPException) as ei:
        _run(rma.r.reject_rma(rid, rma.r.RMAReject(), current_user=_user(role)))
    assert ei.value.status_code == 409
    assert _stored_rma(rma, rid)["status"] == "DISPATCHED"


@pytest.mark.parametrize("role", MANAGERS)
def test_rma_reject_of_a_closed_state_is_still_409_for_a_manager(rma, role):
    rid, _ = _dispatched(rma)
    _credit(rma, rid)
    assert _status(rma.r.reject_rma(rid, rma.r.RMAReject(), current_user=_user(role))) == 409
    assert _stored_rma(rma, rid)["status"] == "CREDIT_RECEIVED"


@pytest.mark.parametrize("role", MANAGERS)
def test_replaced_on_a_credit_return_refused_to_a_manager_and_nothing_written(vret, role):
    assert _status(_patch(vret, "VR-RCVD2", "replaced", role)) == 403
    stored = _stored_return(vret, "VR-RCVD2")
    assert stored["status"] == "received_by_vendor" and stored["status_history"] == []
    # The credit is still there for accounts to record.
    assert _status(_patch(vret, "VR-RCVD2", "credit_issued", "ACCOUNTANT")) == 422
    assert _stored_return(vret, "VR-RCVD2")["credit_note_amount"] is None
    assert _stored_return(vret, "VR-RCVD2")["status"] == "received_by_vendor"


@pytest.mark.parametrize("role", MANAGERS)
def test_replaced_on_an_untyped_return_counts_as_a_credit_return(vret, role):
    """Fail closed: a return with no return_type is treated as owed a credit."""
    vret.db.get_collection("vendor_returns").insert_one({
        "return_id": "VR-OLD", "vendor_id": "V1", "vendor_name": "GKB", "store_id": "S1",
        "items": [{"product_id": "P1", "product_name": "Lens", "quantity": 1,
                   "reason": "defective", "unit_price": 700.0}],
        "status": "received_by_vendor", "total_value": 700.0, "status_history": [],
    })
    assert _status(_patch(vret, "VR-OLD", "replaced", role)) == 403
    assert _stored_return(vret, "VR-OLD")["status"] == "received_by_vendor"


@pytest.mark.parametrize("role", RECORDERS)
def test_replaced_on_a_credit_return_by_accounts(vret, role):
    res = _run(_patch(vret, "VR-RCVD2", "replaced", role))
    assert res["return"]["status"] == "replaced"
    assert _stored_return(vret, "VR-RCVD2")["status"] == "replaced"


def test_http_forgoing_a_credit_is_403_for_a_manager_and_lands_for_accounts(http):
    rid, _ = _dispatched(http.rma)
    calls = (
        ("post", f"/api/v1/vendor-rma/{rid}/reject", {"reason": "vendor refused"}),
        ("patch", "/api/v1/vendor-returns/VR-RCVD2/status", {"status": "replaced"}),
    )
    for role in MANAGERS:
        http.who["role"] = role
        for verb, url, body in calls:
            r = getattr(http.client, verb)(url, json=body)
            assert r.status_code == 403, (role, url, r.text)
    assert _stored_rma(http.rma, rid)["status"] == "DISPATCHED"
    assert _stored_return(http.vret, "VR-RCVD2")["status"] == "received_by_vendor"

    http.who["role"] = "ACCOUNTANT"
    for verb, url, body in calls:
        r = getattr(http.client, verb)(url, json=body)
        assert r.status_code == 200, (url, r.text)
    assert _stored_rma(http.rma, rid)["status"] == "REJECTED"
    assert _stored_return(http.vret, "VR-RCVD2")["status"] == "replaced"


# ============================================================================
# R2.10  Two-person control on a large RMA credit (E4 'rtv' approval)
# ============================================================================
# The accounts roles record the credit (R2) and approve 'rtv' (R1), so the
# approval spent on a credit above the Rs 50,000 tier must come from someone
# other than the recorder and other than whoever raised the request. Real
# VendorRMAEngine + ApprovalEngine + both routers on one mongomock database.

_PIN = "4321"
_BIG_CREDIT = 60000.0  # over the Rs 50,000 rtv.credit.approval_above default
_SECOND_ACCOUNTANT = {"user_id": "u-accountant-2", "roles": ["ACCOUNTANT"],
                      "store_ids": ["S1"], "active_store_id": "S1"}


@pytest.fixture
def two(monkeypatch):
    import mongomock

    import api.routers.approvals as appr_router
    import api.routers.vendor_rma as r
    from api.routers.auth import hash_password
    from api.services.approvals import ApprovalEngine

    db = mongomock.MongoClient().db
    pin_hash = hash_password(_PIN)
    for user in [_user(role) for role in RECORDERS + MANAGERS] + [_SECOND_ACCOUNTANT]:
        db["users"].insert_one({**user, "is_active": True, "approval_pin_hash": pin_hash})
    monkeypatch.setattr(r, "_get_db", lambda: db)
    monkeypatch.setattr(appr_router, "_get_db", lambda: db)
    rma_ns = SimpleNamespace(r=r, db=db)
    # A manager raises, authorizes and dispatches 100 frames; the accountant
    # prices them at Rs 1,000 each (Rs 1,00,000 expected).
    raised = _run(r.raise_rma(r.RMACreate(
        vendor_id="V1", vendor_name="Luxottica", store_id="S1",
        lines=[r.RMALineCreate(product_id="P1", product_name="Frame", quantity=100,
                               reason="WARRANTY")]),
        current_user=_user("STORE_MANAGER")))
    rid = raised["rma_id"]
    _run(r.price_rma(rid, r.RMAPrices(unit_costs=[1000.0]),
                     current_user=_SECOND_ACCOUNTANT))
    _run(r.authorize_rma(rid, r.RMAAuthorize(vendor_rma_number="L-1"),
                         current_user=_user("STORE_MANAGER")))
    _run(r.dispatch_rma(rid, r.RMADispatch(carrier="X", awb="W"),
                        current_user=_user("STORE_MANAGER")))
    return SimpleNamespace(r=r, db=db, rma=rma_ns, rid=rid, appr_router=appr_router,
                           eng=ApprovalEngine(db=db))


def _rtv_request(two, requested_by):
    res = two.eng.request(action_type="rtv", requested_by=requested_by,
                          amount=_BIG_CREDIT, store_id="S1",
                          context={"rma_id": two.rid})
    assert res["ok"] is True, res
    return res["request_id"]


def _mark_approved(two, request_id, reviewed_by):
    """APPROVED by ``reviewed_by`` as approve() leaves it -- written directly,
    so the check under test is the credit-note route's, whatever the approval
    engine's own maker-checker says about who may approve."""
    token = f"APT-{request_id}"
    two.db["approval_requests"].update_one(
        {"request_id": request_id},
        {"$set": {"status": "APPROVED", "reviewed_by": reviewed_by,
                  "approval_token": token}})
    return token


def _record_big(two, user, request_id, token, number="CN-BIG"):
    return two.r.record_credit_note(
        two.rid, two.r.RMACreditNote(credit_note_number=number, received_amount=_BIG_CREDIT,
                                     approval_token=token, approval_request_id=request_id),
        current_user=user)


def _approval(two, request_id):
    return two.db["approval_requests"].find_one({"request_id": request_id})


def _nothing_recorded(two):
    stored = two.db["vendor_rmas"].find_one({"rma_id": two.rid})
    return stored["status"] == "DISPATCHED" and stored["received_credit_paise"] == 0 \
        and stored["credit_notes"] == []


@pytest.mark.parametrize("raised_by", ("u-store_manager", "self"))
@pytest.mark.parametrize("role", RECORDERS)
def test_large_credit_approved_by_its_recorder_is_refused_and_not_spent(two, role, raised_by):
    recorder = _user(role)
    req = _rtv_request(two, recorder["user_id"] if raised_by == "self" else raised_by)
    token = _mark_approved(two, req, recorder["user_id"])
    with pytest.raises(HTTPException) as ei:
        _run(_record_big(two, recorder, req, token))
    assert ei.value.status_code == 403
    assert ei.value.detail["error"] == "cannot_approve_own"
    assert _nothing_recorded(two)
    doc = _approval(two, req)
    assert doc["status"] == "APPROVED" and doc["consumed"] is False  # not spent


@pytest.mark.parametrize("role", RECORDERS)
def test_a_request_its_raiser_approved_is_refused_to_any_recorder(two, role):
    """An accountant raises and approves their own request; a second person
    may not spend it either -- the requester cannot approve their own."""
    req = _rtv_request(two, "u-accountant-2")
    token = _mark_approved(two, req, "u-accountant-2")
    with pytest.raises(HTTPException) as ei:
        _run(_record_big(two, _user(role), req, token))
    assert ei.value.status_code == 403 and ei.value.detail["error"] == "cannot_approve_own"
    assert _nothing_recorded(two)
    assert _approval(two, req)["consumed"] is False


@pytest.mark.parametrize("raised_by", ("u-store_manager", "self"))
@pytest.mark.parametrize("role", RECORDERS)
def test_large_credit_with_a_second_person_lands(two, role, raised_by):
    recorder = _user(role)
    req = _rtv_request(two, recorder["user_id"] if raised_by == "self" else raised_by)
    token = _mark_approved(two, req, "u-accountant-2")
    res = _run(_record_big(two, recorder, req, token))
    assert res["ok"] is True and res["received_credit_paise"] == 6_000_000
    doc = _approval(two, req)
    assert doc["status"] == "CONSUMED" and doc["consumed_by"] == recorder["user_id"]


def test_large_credit_still_needs_an_approved_request(two):
    """A request nobody approved yet is refused as before (approval_invalid),
    and stays unspent."""
    req = _rtv_request(two, "u-store_manager")
    with pytest.raises(HTTPException) as ei:
        _run(_record_big(two, _user("ACCOUNTANT"), req, None))
    assert ei.value.status_code == 403 and ei.value.detail["error"] == "approval_invalid"
    assert _approval(two, req)["status"] == "REQUESTED" and _nothing_recorded(two)


def test_http_one_accountant_cannot_raise_approve_and_spend_their_own_rtv(two):
    """The reported flow, end to end through both routers: an accountant raises
    an 'rtv' request, PIN-approves it and spends it on the credit note. Whether
    or not the approval engine lets them approve their own, the credit is
    refused and nothing is recorded. Then the case no approval-side rule sees:
    a manager raises the request, an accountant approves it and that same
    accountant spends it -- refused; a second accountant may record it. Last,
    a SUPERADMIN's approval lets the first accountant record their own."""
    from api.routers.auth import get_current_user

    app = FastAPI()
    app.include_router(two.r.router, prefix="/api/v1/vendor-rma")
    app.include_router(two.appr_router.router, prefix="/api/v1/approvals")
    who = {"user": _user("ACCOUNTANT")}
    app.dependency_overrides[get_current_user] = lambda: who["user"]
    cli = TestClient(app)
    credit_url = f"/api/v1/vendor-rma/{two.rid}/credit-note"

    def _raise_request():
        r = cli.post("/api/v1/approvals/requests", json={
            "action_type": "rtv", "amount": _BIG_CREDIT, "store_id": "S1",
            "context": {"rma_id": two.rid}})
        assert r.status_code == 200, r.text
        return r.json()["request_id"]

    req = _raise_request()
    approve = cli.post(f"/api/v1/approvals/requests/{req}/approve", json={"pin": _PIN})
    if approve.status_code == 200:
        token = approve.json()["approval_token"]
        r = cli.post(credit_url, json={"credit_note_number": "CN-SELF",
                                       "received_amount": _BIG_CREDIT,
                                       "approval_token": token,
                                       "approval_request_id": req})
        assert r.status_code == 403, r.text
        assert r.json()["detail"]["error"] == "cannot_approve_own"
        assert _approval(two, req)["consumed"] is False
    else:
        assert approve.status_code == 403, approve.text
    assert _nothing_recorded(two)

    # A manager raises it; the accountant approves it (a second person to the
    # request) and then tries to record the credit with it themselves.
    who["user"] = _user("STORE_MANAGER")
    req_m = _raise_request()
    who["user"] = _user("ACCOUNTANT")
    approve = cli.post(f"/api/v1/approvals/requests/{req_m}/approve", json={"pin": _PIN})
    assert approve.status_code == 200, approve.text
    body = {"credit_note_number": "CN-MINE", "received_amount": _BIG_CREDIT,
            "approval_token": approve.json()["approval_token"],
            "approval_request_id": req_m}
    r = cli.post(credit_url, json=body)
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["error"] == "cannot_approve_own"
    assert _approval(two, req_m)["consumed"] is False and _nothing_recorded(two)
    # A second accountant records it with that approval.
    who["user"] = _SECOND_ACCOUNTANT
    r = cli.post(credit_url, json=body)
    assert r.status_code == 200, r.text
    assert r.json()["received_credit_paise"] == 6_000_000
    assert _approval(two, req_m)["consumed_by"] == "u-accountant-2"

    who["user"] = _user("ACCOUNTANT")
    req2 = _raise_request()
    who["user"] = _user("SUPERADMIN")
    approve = cli.post(f"/api/v1/approvals/requests/{req2}/approve", json={"pin": _PIN})
    assert approve.status_code == 200, approve.text
    who["user"] = _user("ACCOUNTANT")
    r = cli.post(credit_url, json={"credit_note_number": "CN-TWO",
                                   "received_amount": _BIG_CREDIT,
                                   "approval_token": approve.json()["approval_token"],
                                   "approval_request_id": req2})
    assert r.status_code == 200, r.text
    # Rs 60,000 by the second accountant plus this Rs 60,000.
    assert r.json()["received_credit_paise"] == 12_000_000


# ============================================================================
# R3.1  The one rule's projections carry the line prices
# ============================================================================
# A vendor return's one projection is cost_mask.mask_vendor_return (on the
# payables rule since 2026-10-12, R3); an RMA's is payables_mask.strip_rma_credit.


@pytest.mark.parametrize("role", NON_ACCOUNTS)
def test_strips_drop_every_return_line_price(role):
    from api.services.cost_mask import mask_vendor_return

    ret = {"return_id": "VR", "items": [{"product_id": "P", "product_name": "L",
                                         "quantity": 2, "reason": "defective",
                                         "unit_price": 10.0, "unit_cost": 9.0,
                                         "rate": 10.0, "rate_paise": 1000}]}
    out = mask_vendor_return(ret, _user(role))
    assert out["items"] == [{"product_id": "P", "product_name": "L",
                             "quantity": 2, "reason": "defective"}]
    rma_doc = {"rma_id": "R", "lines": [{"product_id": "P", "product_name": "L",
                                         "quantity": 2, "reason": "WRONG",
                                         "unit_cost_paise": 1000, "unit_cost": 10.0,
                                         "line_expected_paise": 2000}]}
    out = pm.strip_rma_credit(rma_doc, _user(role))
    assert out["lines"] == [{"product_id": "P", "product_name": "L",
                             "quantity": 2, "reason": "WRONG"}]


@pytest.mark.parametrize("role", RECORDERS)
def test_strips_keep_line_prices_for_accounts(role):
    from api.services.cost_mask import mask_vendor_return

    ret = {"items": [{"unit_price": 10.0}]}
    assert mask_vendor_return(ret, _user(role)) is ret
    doc = {"lines": [{"unit_cost_paise": 1000}]}
    assert pm.strip_rma_credit(doc, _user(role)) is doc


# ============================================================================
# R3.2  Vendor returns: list / detail / write response
# ============================================================================


def _return_reads(vret, role, rid="VR-OK"):
    user = _user(role)
    listed = _run(vret.r.list_vendor_returns(store_id=None, vendor_id=None, status=None,
                                             skip=0, limit=50, current_user=user))
    return listed, _run(vret.r.get_vendor_return(rid, current_user=user))


@pytest.mark.parametrize("role", NON_ACCOUNTS)
def test_return_reads_carry_no_price_outside_accounts(vret, role):
    listed, detail = _return_reads(vret, role)
    legacy = _run(vret.r.get_vendor_return("VR-LEGACY", current_user=_user(role)))
    for body in (listed, detail, legacy):
        assert not _keys(body) & RETURN_PRICE, (role, _keys(body) & RETURN_PRICE)
        assert not _moneyish(body), (role, _moneyish(body))
    assert listed["total"] == 7
    # The goods still read: item, quantity, reason.
    assert detail["items"] == [{"product_id": "P1", "product_name": "Zeiss Lens",
                                "quantity": 2, "reason": "defective"}]


@pytest.mark.parametrize("role", MANAGERS)
def test_return_write_response_carries_no_price_for_a_manager(vret, role):
    res = _run(_patch(vret, "VR-OK", "shipped", role))
    assert res["return"]["items"][0]["quantity"] == 2
    assert not _keys(res) & RETURN_PRICE and not _moneyish(res), _moneyish(res)
    # The stored return keeps its price: only the answer is stripped.
    assert _stored_return(vret, "VR-OK")["items"][0]["unit_price"] == 1500.0


@pytest.mark.parametrize("role", RECORDERS)
def test_return_reads_and_writes_keep_price_for_accounts(vret, role):
    listed, detail = _return_reads(vret, role)
    assert detail["items"][0]["unit_price"] == 1500.0
    assert all(r["items"][0].get("unit_price", r["items"][0].get("unit_cost"))
               for r in listed["returns"])
    res = _run(_patch(vret, "VR-SHIP", "received_by_vendor", role))
    assert res["return"]["items"][0]["unit_price"] == 1500.0


# ============================================================================
# R3.3  RMAs: list / detail / every write response
# ============================================================================


def _rma_reads(rma, rid, role):
    user = _user(role)
    listed = _run(rma.r.list_rmas(store_id=None, vendor_id=None, status=None, skip=0,
                                  limit=50, current_user=user))
    return listed, _run(rma.r.get_rma(rid, current_user=user))


@pytest.mark.parametrize("role", NON_ACCOUNTS)
def test_rma_reads_carry_no_price_outside_accounts(rma, role):
    rid, _ = _dispatched(rma)
    _credit(rma, rid)
    listed, detail = _rma_reads(rma, rid, role)
    for body in (listed, detail):
        assert not _keys(body) & RETURN_PRICE, (role, _keys(body) & RETURN_PRICE)
        assert not _moneyish(body), (role, _moneyish(body))
    assert detail["lines"] == [{"product_id": "P1", "product_name": "Lens",
                                "quantity": 2, "reason": "DEFECTIVE"}]


@pytest.mark.parametrize("role", RECORDERS)
def test_rma_reads_keep_price_for_accounts(rma, role):
    rid, _ = _dispatched(rma)
    listed, detail = _rma_reads(rma, rid, role)
    (line,) = detail["lines"]
    assert line["unit_cost_paise"] == 150000 and line["line_expected_paise"] == 300000
    assert listed["rmas"][0]["lines"][0]["unit_cost_paise"] == 150000


_PRICED_LINES = [{"product_id": "P1", "product_name": "Lens", "quantity": 2,
                  "reason": "WRONG", "unit_cost_paise": 150000, "unit_cost": 1500.0,
                  "line_expected_paise": 300000}]


class _PricedEngine:
    """An RMA engine whose every write result carries priced lines -- so each
    write route is held to the strip, not only the ones whose results carry
    lines today."""

    def _coll(self):
        return object()

    def get(self, rid):
        return {"rma_id": rid, "store_id": "S1", "expected_credit_paise": 0,
                "received_credit_paise": 0}

    def _result(self, *_a, **_kw):
        return {"ok": True, "rma_id": "R1", "status": "X",
                "lines": [dict(ln) for ln in _PRICED_LINES]}

    raise_rma = authorize = dispatch = reject = record_credit_note = close_rma = _result


_RMA_WRITES = {
    "raise": lambda r, u: r.raise_rma(r.RMACreate(
        vendor_id="V1", vendor_name="Z", store_id="S1",
        lines=[r.RMALineCreate(product_id="P1", product_name="L", quantity=1,
                               reason="WRONG")]), current_user=u),
    "authorize": lambda r, u: r.authorize_rma(
        "R1", r.RMAAuthorize(vendor_rma_number="Z-1"), current_user=u),
    "dispatch": lambda r, u: r.dispatch_rma(
        "R1", r.RMADispatch(carrier="DTDC", awb="A1"), current_user=u),
    "reject": lambda r, u: r.reject_rma("R1", r.RMAReject(), current_user=u),
    "close": lambda r, u: r.close_rma("R1", r.RMAClose(), current_user=u),
    "credit-note": lambda r, u: r.record_credit_note(
        "R1", r.RMACreditNote(credit_note_number="CN-1", received_amount=10.0),
        current_user=u),
}
_MANAGER_RMA_WRITES = sorted(set(_RMA_WRITES) - {"credit-note"})


@pytest.mark.parametrize("write", _MANAGER_RMA_WRITES)
@pytest.mark.parametrize("role", MANAGERS)
def test_every_rma_write_result_drops_line_prices_for_a_manager(rma, monkeypatch,
                                                                write, role):
    monkeypatch.setattr(rma.r, "_engine", lambda: _PricedEngine())
    res = _run(_RMA_WRITES[write](rma.r, _user(role)))
    assert res["ok"] is True
    assert res["lines"] == [{"product_id": "P1", "product_name": "Lens",
                             "quantity": 2, "reason": "WRONG"}]


@pytest.mark.parametrize("write", sorted(_RMA_WRITES))
def test_every_rma_write_result_keeps_line_prices_for_accounts(rma, monkeypatch, write):
    monkeypatch.setattr(rma.r, "_engine", lambda: _PricedEngine())
    res = _run(_RMA_WRITES[write](rma.r, _user("ACCOUNTANT")))
    assert res["lines"] == _PRICED_LINES


# ============================================================================
# R3.4  RTV debit-note lines: already stripped -- confirmed
# ============================================================================


@pytest.mark.parametrize("role", NON_ACCOUNTS)
def test_debit_note_lines_carry_no_rate_or_value_outside_accounts(rtv, role):
    _run(_issue(rtv, "ACCOUNTANT"))
    note_id = rtv.db.get_collection("debit_notes").docs[0]["debit_note_id"]
    user = _user(role)
    listed = _run(rtv.r.list_debit_notes(store_id=None, vendor_id=None, skip=0, limit=50,
                                         current_user=user))
    detail = _run(rtv.r.get_debit_note(note_id, current_user=user))
    for body in (listed, detail):
        assert not _keys(body) & RETURN_PRICE, (role, _keys(body) & RETURN_PRICE)
        assert not _moneyish(body), (role, _moneyish(body))
    (line,) = detail["lines"]
    assert line["qty"] == 2
