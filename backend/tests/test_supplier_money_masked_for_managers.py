"""Supplier money is the accounts roles' alone -- on the surfaces #1161 leaves
open to store / area managers.

Owner ruling 2026-10-01 (verbatim): "supplier balances should not be shown to
anyone apart from admin superadmin and accountant". Supplier money = what we
owe / have paid / are owed by a supplier. Here: the RTV GST debit note's
amounts (and its print + Tally export), a vendor return's credit, a vendor
RMA's expected / received credit and variance, a supplier's month-to-date
billing on its scorecard, a bill's total and paid state on the PO timeline,
and the amount on a Return-to-Vendor ('rtv') approval.

ONE rule (services/payables_mask -> cost_mask.can_see_cost(user, "payables")):
SUPERADMIN / ADMIN / ACCOUNTANT see it unchanged; everyone else -- the store
and area managers, and the counter roles where a route admits one -- gets the
same response with the money keys ABSENT (checked at every depth), never a
zero. The writes themselves are unchanged: a manager's write still lands.

Every handler runs for real against an in-memory Mongo double; no network.
No emoji (Windows cp1252).
"""

from __future__ import annotations

import asyncio
import copy
import operator
import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-supplier-money")
os.environ.setdefault("ENVIRONMENT", "test")

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.services import payables_mask as pm  # noqa: E402
from api.services.cost_mask import can_see_cost  # noqa: E402


MANAGERS = ("STORE_MANAGER", "AREA_MANAGER")
ACCOUNTS = ("ACCOUNTANT", "ADMIN", "SUPERADMIN")
# Counter roles a HEAD read still admits (AUTHENTICATED rows); #1161 narrows
# some of those reads, and these handler-level checks hold either way.
COUNTER = ("SALES_STAFF", "WORKSHOP_STAFF")
ALL_ROLES = (
    "SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER", "ACCOUNTANT",
    "CATALOG_MANAGER", "OPTOMETRIST", "SALES_CASHIER", "SALES_STAFF",
    "CASHIER", "WORKSHOP_STAFF", "DESIGN_MANAGER", "INVESTOR",
)

DEBIT_NOTE_MONEY = {
    "rate_paise", "taxable_paise", "cgst_paise", "sgst_paise", "igst_paise",
    "tax_paise", "line_total_paise", "grand_total_paise", "totals", "totals_rupees",
}
RETURN_CREDIT = {"total_value", "credit_note_amount", "credit_note_number"}
RMA_CREDIT = {
    "expected_credit_paise", "expected_credit_rupees", "received_credit_paise",
    "received_credit_rupees", "variance_paise", "variance_rupees",
    "written_off_paise", "line_expected_paise", "received_paise",
}


def _run(coro):
    return asyncio.run(coro)


def _user(role: str, uid: str = "") -> Dict[str, Any]:
    return {
        "user_id": uid or f"u-{role.lower()}",
        "roles": [role],
        "store_ids": ["S1"],
        "active_store_id": "S1",
    }


def _keys(node: Any) -> set:
    """Every dict key at any depth."""
    if isinstance(node, dict):
        out = set(node)
        for v in node.values():
            out |= _keys(v)
        return out
    if isinstance(node, list):
        out = set()
        for v in node:
            out |= _keys(v)
        return out
    return set()


def _strings(node: Any) -> List[str]:
    if isinstance(node, dict):
        return [s for v in node.values() for s in _strings(v)]
    if isinstance(node, list):
        return [s for v in node for s in _strings(v)]
    return [node] if isinstance(node, str) else []


# ============================================================================
# In-memory Mongo double (only what these handlers + engines use)
# ============================================================================

_CMP = {"$gt": operator.gt, "$gte": operator.ge, "$lt": operator.lt, "$lte": operator.le}


def _resolve(doc: Dict[str, Any], key: str) -> Any:
    if "." not in key:
        return doc.get(key)
    head, rest = key.split(".", 1)
    val = doc.get(head)
    if isinstance(val, list):
        return [_resolve(el, rest) if isinstance(el, dict) else None for el in val]
    if isinstance(val, dict):
        return _resolve(val, rest)
    return None


def _match_one(actual: Any, cond: Any) -> bool:
    if isinstance(cond, dict) and cond and all(str(k).startswith("$") for k in cond):
        vals = actual if isinstance(actual, list) else [actual]
        for op, exp in cond.items():
            if op == "$in":
                if not any(v in exp for v in vals):
                    return False
            elif op == "$ne":
                if any(v == exp for v in vals):
                    return False
            elif op == "$exists":
                if bool(exp) != (actual is not None and actual != []):
                    return False
            else:
                try:
                    if not any(v is not None and _CMP[op](v, exp) for v in vals):
                        return False
                except TypeError:
                    return False
        return True
    if isinstance(actual, list) and not isinstance(cond, list):
        return cond in actual
    return actual == cond


def _matches(doc: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for k, v in (query or {}).items():
        if k == "$or":
            if not any(_matches(doc, sub) for sub in v):
                return False
        elif not _match_one(_resolve(doc, k), v):
            return False
    return True


def _apply(doc: Dict[str, Any], update: Dict[str, Any], inserting: bool) -> None:
    for op, fields in update.items():
        if op == "$set" or (op == "$setOnInsert" and inserting):
            doc.update(copy.deepcopy(fields))
        elif op == "$inc":
            for k, v in fields.items():
                doc[k] = (doc.get(k) or 0) + v
        elif op == "$push":
            for k, v in fields.items():
                doc.setdefault(k, []).append(copy.deepcopy(v))
        elif op == "$unset":
            for k in fields:
                doc.pop(k, None)


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, field, direction=-1):
        if isinstance(field, list):
            field, direction = field[0]
        self._docs.sort(
            key=lambda d: (d.get(field) is None, str(d.get(field) or "")),
            reverse=(direction == -1),
        )
        return self

    def skip(self, n):
        self._docs = self._docs[int(n):]
        return self

    def limit(self, n):
        self._docs = self._docs[: int(n)] if n else self._docs
        return self

    def __iter__(self):
        return iter(self._docs)


class _Coll:
    def __init__(self):
        self.docs: List[Dict[str, Any]] = []

    @staticmethod
    def _out(doc, projection=None):
        out = copy.deepcopy(doc)
        if projection and projection.get("_id") == 0:
            out.pop("_id", None)
        return out

    def insert_one(self, doc):
        doc.setdefault("_id", f"oid-{len(self.docs)}")
        self.docs.append(copy.deepcopy(doc))
        return type("R", (), {"inserted_id": doc["_id"]})()

    def find_one(self, query=None, projection=None, **_kw):
        for d in self.docs:
            if _matches(d, query or {}):
                return self._out(d, projection)
        return None

    def find(self, query=None, projection=None, **_kw):
        return _Cursor(self._out(d, projection) for d in self.docs if _matches(d, query or {}))

    def count_documents(self, query=None):
        return sum(1 for d in self.docs if _matches(d, query or {}))

    def _upsert(self, query, update):
        new = {k: v for k, v in query.items() if not isinstance(v, dict)}
        _apply(new, update, inserting=True)
        self.insert_one(new)
        return self.docs[-1]

    def update_one(self, query, update, upsert=False):
        for d in self.docs:
            if _matches(d, query):
                _apply(d, update, inserting=False)
                return type("R", (), {"matched_count": 1, "modified_count": 1})()
        if upsert:
            self._upsert(query, update)
        return type("R", (), {"matched_count": 0, "modified_count": 0})()

    def find_one_and_update(self, query, update, upsert=False, **_kw):
        for d in self.docs:
            if _matches(d, query):
                _apply(d, update, inserting=False)
                return self._out(d)
        if upsert:
            return self._out(self._upsert(query, update))
        return None

    def create_index(self, *_a, **_k):
        return "idx"


class FakeDB:
    def __init__(self):
        self.collections: Dict[str, _Coll] = {}
        self.touched: List[str] = []

    def get_collection(self, name: str) -> _Coll:
        self.touched.append(name)
        return self.collections.setdefault(name, _Coll())

    def __getitem__(self, name: str) -> _Coll:
        return self.get_collection(name)


# ============================================================================
# 0. The one rule
# ============================================================================


@pytest.mark.parametrize("role", ALL_ROLES)
def test_one_rule_is_the_cost_mask_payables_context(role):
    user = _user(role)
    assert pm.can_see_payables(user) is can_see_cost(user, "payables")
    assert pm.can_see_payables(user) is (role in ACCOUNTS)


def test_no_caller_sees_nothing():
    assert pm.can_see_payables(None) is False
    assert pm.strip_vendor_return_credit({"total_value": 5.0}, None) == {}


def test_strips_never_mutate_what_they_were_given():
    note = {"debit_note_number": "DN/1", "totals": {"grand_total_paise": 105},
            "lines": [{"qty": 1, "rate_paise": 100, "line_total_paise": 105}]}
    before = copy.deepcopy(note)
    out = pm.strip_debit_note_money(note, _user("STORE_MANAGER"))
    assert note == before
    assert out == {"debit_note_number": "DN/1", "lines": [{"qty": 1}]}
    assert pm.strip_debit_note_money(note, _user("ACCOUNTANT")) is note


def test_route_decorator_refuses_a_sync_handler():
    with pytest.raises(TypeError):
        pm.masks_supplier_money(pm.strip_debit_note_money)(lambda current_user: {})


def test_route_decorator_strips_results_and_error_details():
    @pm.masks_supplier_money(pm.strip_debit_note_money)
    async def handler(note_id: str, current_user: dict):
        if note_id == "boom":
            raise HTTPException(status_code=409, detail={"error": "x", "totals": {"t": 1}})
        return {"debit_note_id": note_id, "totals": {"grand_total_paise": 9}}

    # The caller is found whether passed by keyword or by position.
    assert _run(handler("DN-1", current_user=_user("STORE_MANAGER"))) == {"debit_note_id": "DN-1"}
    assert _run(handler("DN-1", _user("AREA_MANAGER"))) == {"debit_note_id": "DN-1"}
    assert _run(handler("DN-1", _user("ACCOUNTANT")))["totals"] == {"grand_total_paise": 9}
    with pytest.raises(HTTPException) as ei:
        _run(handler("boom", _user("STORE_MANAGER")))
    assert ei.value.detail == {"error": "x"}


# ============================================================================
# 1. RTV GST debit notes: list / detail / issue response / print / Tally
# ============================================================================


@pytest.fixture
def rtv(monkeypatch):
    import api.routers.rtv_debit_notes as r

    db = FakeDB()
    db.get_collection("entities").insert_one(
        {"entity_id": "E1", "code": "BV", "legal_name": "Better Vision Pvt Ltd",
         "gstin": "20ABCDE1234F1Z5", "state_code": "20"})
    db.get_collection("stores").insert_one(
        {"store_id": "S1", "entity_id": "E1", "name": "BV Ranchi", "state_code": "20"})
    db.get_collection("vendors").insert_one(
        {"vendor_id": "V1", "name": "GKB Optical", "gstin": "20ZZZZZ9999Z1Z5",
         "state_code": "20", "address": "Jamshedpur"})
    db.get_collection("vendor_returns").insert_one({
        "return_id": "VR-1", "store_id": "S1", "vendor_id": "V1", "entity_id": "E1",
        "vendor_name": "GKB Optical", "purchase_invoice_number": "PINV-77",
        "lines": [{"product_id": "P1", "product_name": "Zeiss Lens", "hsn": "9001",
                   "quantity": 2, "rate_paise": 150000, "gst_rate": 5.0}],
    })
    monkeypatch.setattr(r, "_get_db", lambda: db)
    return SimpleNamespace(r=r, db=db)


def _issue(rtv, role):
    r = rtv.r
    body = r.DebitNoteIssue(source_type="vendor_return", rtv_id="VR-1")
    return _run(r.issue_debit_note(body, current_user=_user(role)))


def _note_id(rtv):
    return rtv.db.get_collection("debit_notes").docs[0]["debit_note_id"]


def _dn_reads(rtv, role):
    user = _user(role)
    listed = _run(rtv.r.list_debit_notes(store_id=None, vendor_id=None, skip=0, limit=50,
                                         current_user=user))
    return listed, _run(rtv.r.get_debit_note(_note_id(rtv), current_user=user))


@pytest.mark.parametrize("role", MANAGERS)
def test_rtv_issue_response_has_no_money_for_a_manager(rtv, role):
    res = _issue(rtv, role)
    assert not _keys(res) & DEBIT_NOTE_MONEY, _keys(res) & DEBIT_NOTE_MONEY
    assert res["debit_note"]["debit_note_number"].startswith("DN/BV/")
    assert res["debit_note"]["lines"][0]["qty"] == 2
    # The write itself is untouched: the stored note carries its amounts.
    stored = rtv.db.get_collection("debit_notes").docs[0]
    assert stored["totals"]["grand_total_paise"] == 315000
    # ...and a manager's idempotent re-issue is stripped the same way.
    again = _issue(rtv, role)
    assert again["idempotent"] is True and not _keys(again) & DEBIT_NOTE_MONEY


@pytest.mark.parametrize("role", MANAGERS + COUNTER)
def test_rtv_reads_have_no_money_outside_accounts(rtv, role):
    _issue(rtv, "ACCOUNTANT")
    listed, detail = _dn_reads(rtv, role)
    for body in (listed, detail):
        assert not _keys(body) & DEBIT_NOTE_MONEY, (role, _keys(body) & DEBIT_NOTE_MONEY)
    assert listed["total"] == 1
    assert detail["debit_note_number"].startswith("DN/BV/")
    assert detail["lines"][0]["qty"] == 2  # the item and quantity still read


@pytest.mark.parametrize("role", ACCOUNTS)
def test_rtv_reads_keep_money_for_accounts(rtv, role):
    issued = _issue(rtv, "ACCOUNTANT")
    assert issued["debit_note"]["totals_rupees"]["grand_total"] == 3150.0
    listed, detail = _dn_reads(rtv, role)
    assert detail["totals"]["grand_total_paise"] == 315000
    assert detail["totals_rupees"]["grand_total"] == 3150.0
    assert detail["lines"][0]["line_total_paise"] == 315000
    assert listed["debit_notes"][0]["totals_rupees"]["tax"] == 150.0


@pytest.mark.parametrize("role", MANAGERS + COUNTER)
def test_rtv_print_and_tally_are_403_outside_accounts(rtv, role):
    _issue(rtv, "ACCOUNTANT")
    for handler in (rtv.r.print_debit_note, rtv.r.export_debit_note_tally):
        with pytest.raises(HTTPException) as ei:
            _run(handler(_note_id(rtv), current_user=_user(role)))
        assert ei.value.status_code == 403, (handler.__name__, role)


@pytest.mark.parametrize("role", ACCOUNTS)
def test_rtv_print_and_tally_work_for_accounts(rtv, role):
    _issue(rtv, "ACCOUNTANT")
    html = _run(rtv.r.print_debit_note(_note_id(rtv), current_user=_user(role)))
    assert "3,150.00" in html.body.decode()
    xml = _run(rtv.r.export_debit_note_tally(_note_id(rtv), current_user=_user(role)))
    assert "3150" in xml.body.decode()


def test_rtv_list_strip_holds_through_fastapi(rtv):
    """The list's strip is a route decorator: prove FastAPI still resolves the
    query params + the caller through it, and the money stays out."""
    from api.routers.auth import get_current_user

    _issue(rtv, "ACCOUNTANT")
    app = FastAPI()
    app.include_router(rtv.r.router, prefix="/api/v1/rtv-debit-notes")
    who = {"role": "STORE_MANAGER"}
    app.dependency_overrides[get_current_user] = lambda: _user(who["role"])
    client = TestClient(app)

    r = client.get("/api/v1/rtv-debit-notes?limit=10")
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 1
    assert not _keys(r.json()) & DEBIT_NOTE_MONEY

    who["role"] = "ACCOUNTANT"
    r = client.get("/api/v1/rtv-debit-notes?limit=10")
    assert r.status_code == 200, r.text
    assert r.json()["debit_notes"][0]["totals"]["grand_total_paise"] == 315000


# ============================================================================
# 2. Vendor returns: list / detail / PATCH response (POST carries no money)
# ============================================================================


@pytest.fixture
def vret(monkeypatch):
    import api.routers.vendor_returns as r

    db = FakeDB()
    db.get_collection("vendor_returns").insert_one({
        "return_id": "VR-OPEN", "vendor_id": "V1", "vendor_name": "GKB", "store_id": "S1",
        "items": [{"product_id": "P1", "product_name": "Zeiss Lens", "quantity": 2,
                   "reason": "defective", "unit_price": 1500.0}],
        "return_type": "credit_note", "status": "received_by_vendor",
        "total_value": 3000.0, "credit_note_number": None, "credit_note_amount": None,
        "created_at": "2026-09-30T10:00:00", "status_history": [],
    })
    db.get_collection("vendor_returns").insert_one({
        "return_id": "VR-DONE", "vendor_id": "V1", "vendor_name": "GKB", "store_id": "S1",
        "items": [{"product_id": "P2", "product_name": "Frame", "quantity": 1,
                   "reason": "damaged_in_transit", "unit_price": 800.0}],
        "return_type": "credit_note", "status": "credit_issued",
        "total_value": 800.0, "credit_note_number": "CN-2609", "credit_note_amount": 800.0,
        "created_at": "2026-09-29T10:00:00", "status_history": [],
    })
    monkeypatch.setattr(r, "_get_db", lambda: db)
    return SimpleNamespace(r=r, db=db)


def _ret_reads(vret, role):
    user = _user(role)
    listed = _run(vret.r.list_vendor_returns(store_id=None, vendor_id=None, status=None,
                                             skip=0, limit=50, current_user=user))
    return listed, _run(vret.r.get_vendor_return("VR-DONE", current_user=user))


@pytest.mark.parametrize("role", MANAGERS + COUNTER)
def test_vendor_return_reads_have_no_credit_outside_accounts(vret, role):
    listed, detail = _ret_reads(vret, role)
    for body in (listed, detail):
        assert not _keys(body) & RETURN_CREDIT, (role, _keys(body) & RETURN_CREDIT)
    assert listed["total"] == 2
    assert detail["status"] == "credit_issued"
    assert detail["items"][0]["quantity"] == 1  # the goods still read


@pytest.mark.parametrize("role", ACCOUNTS)
def test_vendor_return_reads_keep_credit_for_accounts(vret, role):
    listed, detail = _ret_reads(vret, role)
    assert detail["credit_note_amount"] == 800.0
    assert detail["credit_note_number"] == "CN-2609"
    assert {r["return_id"]: r["total_value"] for r in listed["returns"]} == {
        "VR-OPEN": 3000.0, "VR-DONE": 800.0}


@pytest.mark.parametrize("role", MANAGERS)
def test_vendor_return_credit_issue_response_has_no_credit_for_a_manager(vret, role):
    body = vret.r.VendorReturnStatusUpdate(status="credit_issued")
    res = _run(vret.r.update_return_status("VR-OPEN", body, current_user=_user(role)))
    assert res["return"]["status"] == "credit_issued"
    assert not _keys(res) & RETURN_CREDIT, _keys(res) & RETURN_CREDIT
    # The credit itself was still issued and stored.
    stored = vret.db.get_collection("vendor_returns").find_one({"return_id": "VR-OPEN"})
    assert stored["credit_note_amount"] == 3000.0 and stored["credit_note_number"]


def test_vendor_return_credit_issue_response_keeps_credit_for_accounts(vret):
    body = vret.r.VendorReturnStatusUpdate(status="credit_issued")
    res = _run(vret.r.update_return_status("VR-OPEN", body,
                                           current_user=_user("ACCOUNTANT")))
    assert res["return"]["credit_note_amount"] == 3000.0
    assert res["return"]["credit_note_number"].startswith("CN-")


@pytest.mark.parametrize("role", MANAGERS)
def test_vendor_return_create_response_carries_no_credit(vret, role):
    r = vret.r
    body = r.VendorReturnCreate(
        vendor_id="V1", vendor_name="GKB", store_id="S1", return_type="credit_note",
        items=[r.ReturnItemCreate(product_id="P9", product_name="Lens", quantity=1,
                                  reason="defective", unit_price=10.0)])
    res = _run(r.create_vendor_return(body, current_user=_user(role)))
    assert set(res) == {"return_id", "message"}


# ============================================================================
# 3. Vendor RMA: every read, every write result, the close refusal
# ============================================================================


@pytest.fixture
def rma(monkeypatch):
    import api.routers.vendor_rma as r

    db = FakeDB()
    monkeypatch.setattr(r, "_get_db", lambda: db)
    return SimpleNamespace(r=r, db=db)


def _rma_to_credit(rma, role):
    """raise -> authorize -> dispatch -> a Rs 1,000 credit on an Rs 3,000 RMA
    (below the maker-checker tier), all as ``role``; returns every response."""
    r, user = rma.r, _user(role)
    raised = _run(r.raise_rma(r.RMACreate(
        vendor_id="V1", vendor_name="Zeiss", store_id="S1",
        lines=[r.RMALineCreate(product_id="P1", product_name="Lens", quantity=2,
                               reason="DEFECTIVE", unit_cost=1500.0)]), current_user=user))
    rid = raised["rma_id"]
    authorized = _run(r.authorize_rma(rid, r.RMAAuthorize(vendor_rma_number="Z-1"),
                                      current_user=user))
    dispatched = _run(r.dispatch_rma(rid, r.RMADispatch(carrier="DTDC", awb="AWB1"),
                                     current_user=user))
    credited = _run(r.record_credit_note(
        rid, r.RMACreditNote(credit_note_number="CN-Z1", received_amount=1000.0),
        current_user=user))
    return rid, [raised, authorized, dispatched, credited]


def _rma_reads(rma, rid, role):
    user = _user(role)
    listed = _run(rma.r.list_rmas(store_id=None, vendor_id=None, status=None, skip=0,
                                  limit=50, current_user=user))
    return listed, _run(rma.r.get_rma(rid, current_user=user))


@pytest.mark.parametrize("role", MANAGERS)
def test_rma_never_shows_a_manager_the_credit(rma, role):
    rid, writes = _rma_to_credit(rma, role)
    for res in writes:
        assert res["ok"] is True
        assert not _keys(res) & RMA_CREDIT, (res, _keys(res) & RMA_CREDIT)

    listed, detail = _rma_reads(rma, rid, role)
    for body in (listed, detail):
        assert not _keys(body) & RMA_CREDIT, _keys(body) & RMA_CREDIT
        # The engine's own history note ("... for 100000 paise") keeps the
        # event and drops the figure.
        assert not [s for s in _strings(body) if " paise" in s]
    assert detail["status"] == "CREDIT_RECEIVED"
    assert detail["credit_notes"][0]["credit_note_number"] == "CN-Z1"
    assert detail["lines"][0]["quantity"] == 2

    # The close refusal names the outstanding variance -- not to a manager.
    with pytest.raises(HTTPException) as ei:
        _run(rma.r.close_rma(rid, rma.r.RMAClose(), current_user=_user(role)))
    assert ei.value.status_code == 409
    assert ei.value.detail == {"error": "variance_outstanding"}

    closed = _run(rma.r.close_rma(rid, rma.r.RMAClose(write_off_variance=True),
                                  current_user=_user(role)))
    assert closed["status"] == "CLOSED" and not _keys(closed) & RMA_CREDIT
    _, detail = _rma_reads(rma, rid, role)
    assert not [s for s in _strings(detail) if " paise" in s]
    assert "closed; variance written off" in [h.get("notes") for h in detail["status_history"]]

    # Every write still landed with its money.
    stored = rma.db.get_collection("vendor_rmas").find_one({"rma_id": rid})
    assert stored["received_credit_paise"] == 100000
    assert stored["written_off_paise"] == 200000


@pytest.mark.parametrize("role", MANAGERS)
def test_rma_reject_result_carries_no_credit(rma, role):
    r = rma.r
    raised = _run(r.raise_rma(r.RMACreate(
        vendor_id="V1", vendor_name="Zeiss", store_id="S1",
        lines=[r.RMALineCreate(product_id="P1", product_name="Lens", quantity=1,
                               reason="WRONG", unit_cost=900.0)]), current_user=_user(role)))
    res = _run(r.reject_rma(raised["rma_id"], r.RMAReject(reason="sent back"),
                            current_user=_user(role)))
    assert res["status"] == "REJECTED" and not _keys(res) & RMA_CREDIT


_ALL_RMA_MONEY = {k: 1 for k in RMA_CREDIT}


class _MoneyEngine:
    """An RMA engine whose every result carries every credit key -- so each
    write route is held to the strip, not just the ones whose results carry
    money today."""

    def _coll(self):
        return object()

    def get(self, rid):
        return {"rma_id": rid, "store_id": "S1"}

    def _result(self, *_a, **_kw):
        return {"ok": True, "rma_id": "R1", "status": "X", **_ALL_RMA_MONEY}

    raise_rma = authorize = dispatch = reject = record_credit_note = close_rma = _result


_RMA_WRITES = {
    "raise": lambda r, u: r.raise_rma(r.RMACreate(
        vendor_id="V1", vendor_name="Z", store_id="S1",
        lines=[r.RMALineCreate(product_id="P1", product_name="L", quantity=1,
                               reason="WRONG", unit_cost=1.0)]), current_user=u),
    "authorize": lambda r, u: r.authorize_rma(
        "R1", r.RMAAuthorize(vendor_rma_number="Z-1"), current_user=u),
    "dispatch": lambda r, u: r.dispatch_rma(
        "R1", r.RMADispatch(carrier="DTDC", awb="A1"), current_user=u),
    "credit-note": lambda r, u: r.record_credit_note(
        "R1", r.RMACreditNote(credit_note_number="CN-1", received_amount=10.0),
        current_user=u),
    "reject": lambda r, u: r.reject_rma("R1", r.RMAReject(), current_user=u),
    "close": lambda r, u: r.close_rma("R1", r.RMAClose(), current_user=u),
}


@pytest.mark.parametrize("write", sorted(_RMA_WRITES))
@pytest.mark.parametrize("role,shown", (("STORE_MANAGER", False), ("AREA_MANAGER", False),
                                        ("ACCOUNTANT", True)))
def test_every_rma_write_result_goes_through_the_strip(rma, monkeypatch, write, role, shown):
    monkeypatch.setattr(rma.r, "_engine", lambda: _MoneyEngine())
    res = _run(_RMA_WRITES[write](rma.r, _user(role)))
    assert res["ok"] is True
    assert (RMA_CREDIT <= set(res)) is shown and bool(set(res) & RMA_CREDIT) is shown


@pytest.mark.parametrize("role", ("ACCOUNTANT", "ADMIN"))
def test_rma_accounts_see_the_credit(rma, role):
    rid, writes = _rma_to_credit(rma, role)
    assert writes[0]["expected_credit_paise"] == 300000
    assert writes[3]["received_credit_paise"] == 100000
    assert writes[3]["variance_paise"] == 200000
    listed, detail = _rma_reads(rma, rid, role)
    assert detail["expected_credit_rupees"] == 3000.0
    assert detail["received_credit_rupees"] == 1000.0
    assert detail["variance_rupees"] == 2000.0
    assert detail["credit_notes"][0]["received_paise"] == 100000
    assert listed["rmas"][0]["variance_paise"] == 200000
    assert any(h.get("notes") == "credit note CN-Z1 for 100000 paise"
               for h in detail["status_history"])
    with pytest.raises(HTTPException) as ei:
        _run(rma.r.close_rma(rid, rma.r.RMAClose(), current_user=_user(role)))
    assert ei.value.detail["variance_paise"] == 200000


# ============================================================================
# 4. Vendor scorecard: mtd_spend (what the supplier billed us this month)
# ============================================================================


class _VendorRepo:
    def find_by_id(self, _vid):
        return {"vendor_id": "V1", "trade_name": "Acme"}


@pytest.fixture
def perf(monkeypatch):
    from api.routers import vendors as v
    from tests.ist_business_day import business_now

    db = FakeDB()
    db.get_collection("vendor_bills").insert_one(
        {"vendor_id": "V1", "total_amount": 1250.0,
         "bill_date": business_now().strftime("%Y-%m-10")})
    monkeypatch.setattr(v, "_get_db", lambda: db)
    monkeypatch.setattr(v, "get_vendor_repository", lambda: _VendorRepo())
    return SimpleNamespace(v=v, db=db)


def _with_grn(db):
    db.get_collection("grns").insert_one(
        {"grn_id": "G1", "vendor_id": "V1", "status": "ACCEPTED",
         "created_at": datetime.now(), "total_received": 4, "total_accepted": 4})


def _perf(perf, role):
    return _run(perf.v.vendor_performance("V1", months=6, current_user=_user(role)))


@pytest.mark.parametrize("role", MANAGERS + COUNTER)
@pytest.mark.parametrize("grn_history", (False, True))
def test_scorecard_has_no_spend_outside_accounts(perf, role, grn_history):
    if grn_history:
        _with_grn(perf.db)
    perf.db.touched.clear()
    body = _perf(perf, role)
    assert "mtd_spend" not in body
    assert body["grns_evaluated"] == (1 if grn_history else 0)
    # Not just hidden: the supplier's bills are not even read.
    assert "vendor_bills" not in perf.db.touched


@pytest.mark.parametrize("role", ACCOUNTS)
@pytest.mark.parametrize("grn_history", (False, True))
def test_scorecard_keeps_spend_for_accounts(perf, role, grn_history):
    if grn_history:
        _with_grn(perf.db)
    assert _perf(perf, role)["mtd_spend"] == 1250.0


# ============================================================================
# 5. PO timeline: a bill's total and paid state
# ============================================================================


class _PORepo:
    def find_by_id(self, pid):
        return {"po_id": "PO1", "po_number": "PO-1", "vendor_id": "V1",
                "delivery_store_id": "S1", "status": "RECEIVED",
                "created_at": "2026-09-01T10:00:00", "created_by": "u9"}


class _GRNRepo:
    def find_many(self, flt, limit=200):
        return [{"po_id": "PO1", "grn_id": "G1", "grn_number": "RCPT-1",
                 "status": "ACCEPTED", "created_at": "2026-09-05T11:00:00",
                 "accepted_at": "2026-09-05T12:00:00", "total_received": 5,
                 "total_accepted": 5}]


@pytest.fixture
def timeline(monkeypatch):
    from api.routers import vendors as v

    db = FakeDB()
    db.get_collection("vendor_bills").insert_one(
        {"doc_type": "PURCHASE_INVOICE", "po_id": "PO1", "grn_id": "G1",
         "bill_id": "B1", "invoice_number": "INV-9", "status": "PARTIAL",
         "total": 5250, "created_at": "2026-09-06T10:00:00"})
    monkeypatch.setattr(v, "get_purchase_order_repository", lambda: _PORepo())
    monkeypatch.setattr(v, "get_grn_repository", lambda: _GRNRepo())
    monkeypatch.setattr(v, "_get_db", lambda: db)
    return v


def _bill_event(body):
    return next(e for e in body["events"] if e["kind"] == "bill_settled")


@pytest.mark.parametrize("role", MANAGERS + COUNTER)
def test_timeline_shows_a_booked_bill_without_money(timeline, role):
    body = _run(timeline.get_po_timeline("PO1", _user(role)))
    (inv,) = body["invoices"]
    assert inv["invoice_number"] == "INV-9" and inv["bill_id"] == "B1"
    assert "total" not in inv
    assert inv["status"] == "BOOKED"
    assert _bill_event(body)["detail"] == "Purchase invoice booked"
    assert not [s for s in _strings(body) if "PARTIAL" in s]
    # The rest of the life of the PO is unchanged.
    assert [e["kind"] for e in body["events"]] == [
        "ordered", "box_received", "on_shelf", "bill_settled"]


@pytest.mark.parametrize("role", ACCOUNTS)
def test_timeline_keeps_bill_money_for_accounts(timeline, role):
    body = _run(timeline.get_po_timeline("PO1", _user(role)))
    (inv,) = body["invoices"]
    assert inv["total"] == 5250 and inv["status"] == "PARTIAL"
    assert _bill_event(body)["detail"] == "Purchase invoice booked (PARTIAL)"


# ============================================================================
# 6. Approvals: an 'rtv' request's amount (inbox / mine / get / consume / bell)
# ============================================================================


@pytest.fixture
def appr(monkeypatch):
    import api.routers.approvals as r
    from api.services.approvals import ApprovalEngine

    db = FakeDB()
    users = db.get_collection("users")
    for role in ("STORE_MANAGER", "AREA_MANAGER"):
        users.insert_one({"user_id": f"u-{role.lower()}", "roles": [role],
                          "store_ids": ["S1"], "is_active": True})
    monkeypatch.setattr(r, "_get_db", lambda: db)
    eng = ApprovalEngine(db=db)
    rtv_req = eng.request(action_type="rtv", requested_by="u-store_manager",
                          requested_by_roles=["STORE_MANAGER"], store_id="S1",
                          amount=1500.0,
                          context={"rma_id": "RMA-1", "credit_amount": 1500.0})
    refund_req = eng.request(action_type="refund", requested_by="u-maker",
                             store_id="S1", amount=999.0, context={"order_id": "O1"})
    return SimpleNamespace(r=r, db=db, rtv_id=rtv_req["request_id"],
                           refund_id=refund_req["request_id"])


def _rows_by_action(body):
    return {row["action_type"]: row for row in body["requests"]}


@pytest.mark.parametrize("role", MANAGERS)
def test_rtv_approval_amount_is_hidden_from_managers(appr, role):
    user = _user(role)
    inbox = _rows_by_action(_run(appr.r.get_inbox(store_id=None, status=None,
                                                   current_user=user)))
    assert "amount" not in inbox["rtv"]
    assert inbox["rtv"]["context"] == {"rma_id": "RMA-1"}
    # Only supplier money goes: a refund's amount is not in the ruling.
    assert inbox["refund"]["amount"] == 999.0

    one = _run(appr.r.get_request(appr.rtv_id, current_user=user))
    assert "amount" not in one and one["context"] == {"rma_id": "RMA-1"}


def test_rtv_approval_amount_is_hidden_from_its_manager_maker(appr):
    mine = _run(appr.r.get_my_requests(current_user=_user("STORE_MANAGER")))
    (row,) = mine["requests"]
    assert row["action_type"] == "rtv" and "amount" not in row


@pytest.mark.parametrize("role", ("ACCOUNTANT", "ADMIN"))
def test_rtv_approval_amount_reads_for_accounts(appr, role):
    user = _user(role)
    inbox = _rows_by_action(_run(appr.r.get_inbox(store_id=None, status=None,
                                                   current_user=user)))
    assert inbox["rtv"]["amount"] == 1500.0
    assert inbox["rtv"]["context"]["credit_amount"] == 1500.0
    assert _run(appr.r.get_request(appr.rtv_id, current_user=user))["amount"] == 1500.0


def _approve_in_place(appr):
    appr.db.get_collection("approval_requests").update_one(
        {"request_id": appr.rtv_id},
        {"$set": {"status": "APPROVED", "approval_token": "tok-1",
                  "expires_at": datetime.now(timezone.utc) + timedelta(minutes=30)}})


@pytest.mark.parametrize("role,shown", (("STORE_MANAGER", False), ("ACCOUNTANT", True)))
def test_rtv_consume_result_follows_the_rule(appr, role, shown):
    _approve_in_place(appr)
    res = _run(appr.r.consume_request(
        appr.rtv_id, appr.r.ConsumeAction(action_type="rtv", approval_token="tok-1"),
        current_user=_user(role)))
    assert res["ok"] is True and res["request"]["status"] == "CONSUMED"
    assert ("amount" in res["request"]) is shown


def test_rtv_bell_never_carries_the_amount(appr):
    bells = appr.db.get_collection("notifications").docs
    rtv_bells = [b for b in bells if b.get("request_id") == appr.rtv_id]
    refund_bells = [b for b in bells if b.get("request_id") == appr.refund_id]
    # The manager-tier bell rings for the area manager (the maker is skipped).
    assert [b["user_id"] for b in rtv_bells] == ["u-area_manager"]
    assert "1500" not in rtv_bells[0]["message"]
    assert rtv_bells[0]["message"].startswith("Return to Vendor for review")
    # A refund bell still names its amount.
    assert any("Rs 999.00" in b["message"] for b in refund_bells)
