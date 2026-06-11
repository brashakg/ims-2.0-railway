"""
IMS 2.0 - N4 Vendor RMA + credit-note reconciliation (intent-level tests)
=========================================================================
These exercise the REAL VendorRMAEngine + the router's RBAC / store-scope / E4
seam against a faithful in-memory fake Mongo (no network, no live mongod). A
hollow shell that skips the atomic find_one_and_update, the paisa-exact credit
math, the store-IDOR guard, the role gate, or the audit row FAILS here.

Covers the N4 acceptance list:
  - raise RMA + line items (paisa-exact expected credit)
  - authorize requires role + records the vendor RMA authorization number
  - dispatch records courier / AWB
  - credit-note reconcile computes expected-vs-received variance paisa-exact,
    incl. partial credits accumulating
  - atomic state transition: two concurrent transitions -> exactly one wins,
    exactly one transition audit row
  - store-scope 403 cross-store (create + read)
  - a cashier cannot authorize an RMA or record a credit (403)

CI-robust: every accessor is monkeypatched + docs seeded; no whole-JSON
substring assertions.
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-n4")
os.environ.setdefault("ENVIRONMENT", "test")

from api.services import vendor_rma as vr  # noqa: E402
from api.services.vendor_rma import VendorRMAEngine, rupees_to_paise  # noqa: E402


# ============================================================================
# Faithful in-memory fake Mongo (supports the operators the engine uses)
# ============================================================================


def _cmp_op(actual: Any, op: str, expected: Any) -> bool:
    try:
        if op == "$gt":
            return actual is not None and actual > expected
        if op == "$lt":
            return actual is not None and actual < expected
        if op == "$gte":
            return actual is not None and actual >= expected
        if op == "$lte":
            return actual is not None and actual <= expected
        if op == "$ne":
            return actual != expected
        if op == "$in":
            return actual in expected
    except TypeError:
        return False
    return False


def _resolve_path(doc: Dict[str, Any], key: str) -> Any:
    """Resolve a (possibly dotted) field. For an ``array.field`` path where the
    array holds sub-docs, return the LIST of that field across elements -- this
    is what Mongo matches an embedded query against (e.g.
    ``credit_notes.credit_note_number`` -> [cn1, cn2, ...]). A plain key is a
    direct get."""
    if "." not in key:
        return doc.get(key)
    head, rest = key.split(".", 1)
    val = doc.get(head)
    if isinstance(val, list):
        return [(_resolve_path(el, rest) if isinstance(el, dict) else None) for el in val]
    if isinstance(val, dict):
        return _resolve_path(val, rest)
    return None


def _matches(doc: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for k, v in query.items():
        actual = _resolve_path(doc, k)
        if isinstance(v, dict) and any(str(kk).startswith("$") for kk in v):
            for op, expected in v.items():
                # Mongo array semantics: ``arr.field {$ne: x}`` matches when NO
                # element equals x; ``$in`` / equality match when ANY does.
                if isinstance(actual, list) and "." in k:
                    if op == "$ne":
                        if any(_v == expected for _v in actual):
                            return False
                        continue
                    if op == "$in":
                        if not any(_v in expected for _v in actual):
                            return False
                        continue
                    if op == "$exists":
                        present = len(actual) > 0
                        if bool(expected) != present:
                            return False
                        continue
                if not _cmp_op(actual, op, expected):
                    return False
            continue
        if isinstance(actual, list) and "." in k:
            # bare equality against an embedded array path -> ANY element matches
            if v not in actual:
                return False
            continue
        if actual != v:
            return False
    return True


def _project(doc, projection):
    out = dict(doc)
    if projection and projection.get("_id") == 0:
        out.pop("_id", None)
    return out


def _apply_update(doc: Dict[str, Any], update: Dict[str, Any]) -> None:
    for op, fields in update.items():
        if op == "$set":
            for kk, vv in fields.items():
                doc[kk] = vv
        elif op == "$inc":
            for kk, vv in fields.items():
                doc[kk] = (doc.get(kk) or 0) + vv
        elif op == "$push":
            for kk, vv in fields.items():
                doc.setdefault(kk, []).append(vv)
        elif op == "$unset":
            for kk in fields:
                doc.pop(kk, None)


class FakeCursor:
    def __init__(self, docs: List[Dict[str, Any]]):
        self._docs = docs

    def sort(self, field, direction=-1):
        self._docs = sorted(
            self._docs, key=lambda d: (d.get(field) is None, d.get(field)),
            reverse=(direction == -1),
        )
        return self

    def skip(self, n):
        self._docs = self._docs[int(n):]
        return self

    def limit(self, n):
        self._docs = self._docs[: int(n)]
        return self

    def __iter__(self):
        return iter(self._docs)


class FakeCollection:
    def __init__(self, database=None):
        self.docs: List[Dict[str, Any]] = []
        self._n = 0
        self.database = database

    def insert_one(self, doc):
        doc.setdefault("_id", f"oid-{self._n}")
        self._n += 1
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc["_id"]})()

    def find_one(self, query, projection=None):
        for d in self.docs:
            if _matches(d, query):
                return _project(d, projection)
        return None

    def find(self, query=None, projection=None):
        matched = [_project(d, projection) for d in self.docs if _matches(d, query or {})]
        return FakeCursor(matched)

    def update_one(self, query, update, upsert=False):
        for d in self.docs:
            if _matches(d, query):
                _apply_update(d, update)
                return type("R", (), {"modified_count": 1, "matched_count": 1})()
        return type("R", (), {"modified_count": 0, "matched_count": 0})()

    def find_one_and_update(self, query, update, return_document=None, **_kw):
        for d in self.docs:
            if _matches(d, query):
                _apply_update(d, update)
                return _project(d, None)
        return None

    def create_index(self, *a, **k):
        return "idx"


class FakeDB:
    def __init__(self):
        self._collections: Dict[str, FakeCollection] = {}

    def get_collection(self, name: str) -> FakeCollection:
        if name not in self._collections:
            self._collections[name] = FakeCollection(database=self)
        return self._collections[name]

    def __getitem__(self, name: str) -> FakeCollection:
        return self.get_collection(name)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def db():
    return FakeDB()


@pytest.fixture
def engine(db):
    return VendorRMAEngine(db=db)


def _audit_rows(db, entity_type="VENDOR_RMA"):
    return [r for r in db.get_collection("audit_logs").docs
            if r.get("entity_type") == entity_type]


def _seed_rma(engine, *, store_id="S1", expected=False) -> str:
    lines = [{"product_id": "P1", "product_name": "Zeiss Lens",
              "quantity": 2, "reason": "DEFECTIVE", "unit_cost": 1500.0}]
    res = engine.raise_rma(vendor_id="V1", vendor_name="Zeiss", store_id=store_id,
                           lines=lines, created_by="mgr")
    assert res["ok"]
    return res["rma_id"]


# ============================================================================
# 1. Raise RMA + line items (paisa-exact expected)
# ============================================================================


def test_raise_rma_computes_paisa_exact_expected(engine, db):
    lines = [
        {"product_id": "P1", "product_name": "Zeiss Lens", "quantity": 2,
         "reason": "DEFECTIVE", "unit_cost": 1500.55},
        {"product_id": "P2", "product_name": "Frame", "quantity": 1,
         "reason": "WRONG", "unit_cost": 999.99},
    ]
    res = engine.raise_rma(vendor_id="V1", vendor_name="Zeiss", store_id="S1",
                           lines=lines, created_by="mgr")
    assert res["ok"] is True
    # 2*150055 + 1*99999 = 300110 + 99999 = 400109 paise
    assert res["expected_credit_paise"] == 2 * 150055 + 99999 == 400109
    doc = db.get_collection("vendor_rmas").find_one({"rma_id": res["rma_id"]})
    assert doc["status"] == "DRAFT"
    assert doc["variance_paise"] == 400109  # full expected outstanding at draft
    assert doc["lines"][0]["unit_cost_paise"] == 150055
    # audit row written for the raise
    assert any(a["action"] == "rma_raised" for a in _audit_rows(db))


def test_raise_rejects_bad_reason_and_qty(engine):
    bad_reason = engine.raise_rma(vendor_id="V1", vendor_name="Z", store_id="S1",
                                  lines=[{"product_id": "P", "product_name": "x",
                                          "quantity": 1, "reason": "JUNK",
                                          "unit_cost": 10.0}], created_by="m")
    assert bad_reason["ok"] is False and bad_reason["error"] == "bad_reason"
    bad_qty = engine.raise_rma(vendor_id="V1", vendor_name="Z", store_id="S1",
                               lines=[{"product_id": "P", "product_name": "x",
                                       "quantity": 0, "reason": "DEFECTIVE",
                                       "unit_cost": 10.0}], created_by="m")
    assert bad_qty["ok"] is False and bad_qty["error"] == "bad_quantity"


# ============================================================================
# 2. Authorize records the vendor RMA number; 3. Dispatch records courier/AWB
# ============================================================================


def test_authorize_records_vendor_rma_number(engine, db):
    rma_id = _seed_rma(engine)
    blank = engine.authorize(rma_id, vendor_rma_number="  ", actor="mgr")
    assert blank["ok"] is False and blank["error"] == "vendor_rma_number_required"
    ok = engine.authorize(rma_id, vendor_rma_number="ZRMA-2026-001", actor="mgr")
    assert ok["ok"] is True and ok["status"] == "AUTHORIZED"
    doc = db.get_collection("vendor_rmas").find_one({"rma_id": rma_id})
    assert doc["vendor_rma_number"] == "ZRMA-2026-001"
    assert any(a["action"] == "rma_authorized" for a in _audit_rows(db))


def test_dispatch_records_courier_awb(engine, db):
    rma_id = _seed_rma(engine)
    engine.authorize(rma_id, vendor_rma_number="ZRMA-1", actor="mgr")
    no_awb = engine.dispatch(rma_id, carrier="BlueDart", awb="", dispatch_date=None, actor="mgr")
    assert no_awb["ok"] is False and no_awb["error"] == "awb_required"
    ok = engine.dispatch(rma_id, carrier="BlueDart", awb="AWB123456",
                         dispatch_date="2026-06-11", actor="mgr")
    assert ok["ok"] is True and ok["status"] == "DISPATCHED"
    doc = db.get_collection("vendor_rmas").find_one({"rma_id": rma_id})
    assert doc["courier"]["carrier"] == "BlueDart"
    assert doc["courier"]["awb"] == "AWB123456"
    assert doc["courier"]["dispatch_date"] == "2026-06-11"


def test_dispatch_blocked_before_authorize(engine):
    rma_id = _seed_rma(engine)
    res = engine.dispatch(rma_id, carrier="BlueDart", awb="AWB1", dispatch_date=None, actor="mgr")
    assert res["ok"] is False and res["http"] == 409 and res["error"] == "invalid_transition"


# ============================================================================
# 4. Credit-note reconcile: expected-vs-received variance, incl. partial
# ============================================================================


def _dispatched(engine) -> str:
    rma_id = _seed_rma(engine)  # expected = 2 * 150000 = 300000 paise
    engine.authorize(rma_id, vendor_rma_number="ZRMA-1", actor="mgr")
    engine.dispatch(rma_id, carrier="DTDC", awb="AWB1", dispatch_date=None, actor="mgr")
    return rma_id


def test_full_credit_reconciles_to_zero_variance(engine, db):
    rma_id = _dispatched(engine)
    res = engine.record_credit_note(rma_id, credit_note_number="CN-1",
                                    received_amount=3000.00, actor="acc")
    assert res["ok"] is True
    assert res["status"] == "CREDIT_RECEIVED"
    assert res["expected_credit_paise"] == 300000
    assert res["received_credit_paise"] == 300000
    assert res["variance_paise"] == 0
    assert res["fully_reconciled"] is True


def test_partial_credits_accumulate_paisa_exact(engine, db):
    rma_id = _dispatched(engine)  # expected 300000 paise
    r1 = engine.record_credit_note(rma_id, credit_note_number="CN-1",
                                   received_amount=1800.50, actor="acc")
    assert r1["ok"] and r1["received_credit_paise"] == 180050
    assert r1["variance_paise"] == 300000 - 180050 == 119950
    assert r1["fully_reconciled"] is False
    # second partial credit accumulates
    r2 = engine.record_credit_note(rma_id, credit_note_number="CN-2",
                                   received_amount=1199.50, actor="acc")
    assert r2["received_credit_paise"] == 180050 + 119950 == 300000
    assert r2["variance_paise"] == 0
    assert r2["fully_reconciled"] is True
    doc = db.get_collection("vendor_rmas").find_one({"rma_id": rma_id})
    assert len(doc["credit_notes"]) == 2


def test_short_credit_leaves_positive_variance_and_blocks_close(engine, db):
    rma_id = _dispatched(engine)  # expected 300000
    engine.record_credit_note(rma_id, credit_note_number="CN-1",
                              received_amount=2500.00, actor="acc")  # 250000 paise
    doc = db.get_collection("vendor_rmas").find_one({"rma_id": rma_id})
    assert doc["variance_paise"] == 50000  # vendor still owes Rs 500
    blocked = engine.close_rma(rma_id, actor="acc")
    assert blocked["ok"] is False and blocked["http"] == 409
    assert blocked["error"] == "variance_outstanding" and blocked["variance_paise"] == 50000
    # force-close writes off the residual
    forced = engine.close_rma(rma_id, actor="acc", write_off_variance=True)
    assert forced["ok"] is True and forced["written_off_paise"] == 50000
    doc2 = db.get_collection("vendor_rmas").find_one({"rma_id": rma_id})
    assert doc2["status"] == "CLOSED" and doc2["written_off_paise"] == 50000


def test_duplicate_credit_note_number_rejected(engine):
    rma_id = _dispatched(engine)
    engine.record_credit_note(rma_id, credit_note_number="CN-DUP",
                              received_amount=100.0, actor="acc")
    dup = engine.record_credit_note(rma_id, credit_note_number="CN-DUP",
                                    received_amount=100.0, actor="acc")
    assert dup["ok"] is False and dup["http"] == 409 and dup["error"] == "duplicate_credit_note"


def test_full_reconcile_then_close(engine, db):
    rma_id = _dispatched(engine)
    engine.record_credit_note(rma_id, credit_note_number="CN-1",
                              received_amount=3000.00, actor="acc")
    closed = engine.close_rma(rma_id, actor="acc")
    assert closed["ok"] is True and closed["status"] == "CLOSED"
    assert closed["written_off_paise"] == 0


# ============================================================================
# 5. Atomic state transition: two concurrent -> one wins, one audit row
# ============================================================================


def test_concurrent_authorize_one_wins_one_audit(engine, db):
    rma_id = _seed_rma(engine)
    # Both read the DRAFT doc, then both attempt the guarded flip. The atomic
    # find_one_and_update guard (status==DRAFT) lets exactly one through.
    first = engine.authorize(rma_id, vendor_rma_number="A-1", actor="mgrA")
    second = engine.authorize(rma_id, vendor_rma_number="A-2", actor="mgrB")
    oks = [r for r in (first, second) if r.get("ok")]
    losers = [r for r in (first, second) if not r.get("ok")]
    assert len(oks) == 1
    assert len(losers) == 1 and losers[0]["http"] == 409
    # exactly one rma_authorized audit row for this RMA
    auth_rows = [a for a in _audit_rows(db)
                 if a["action"] == "rma_authorized" and a["entity_id"] == rma_id]
    assert len(auth_rows) == 1
    doc = db.get_collection("vendor_rmas").find_one({"rma_id": rma_id})
    assert doc["vendor_rma_number"] == "A-1"  # the winner's number


def test_concurrent_dispatch_one_wins(engine, db):
    rma_id = _seed_rma(engine)
    engine.authorize(rma_id, vendor_rma_number="A-1", actor="mgr")
    d1 = engine.dispatch(rma_id, carrier="C1", awb="W1", dispatch_date=None, actor="mgr")
    d2 = engine.dispatch(rma_id, carrier="C2", awb="W2", dispatch_date=None, actor="mgr")
    oks = [r for r in (d1, d2) if r.get("ok")]
    losers = [r for r in (d1, d2) if not r.get("ok")]
    assert len(oks) == 1 and len(losers) == 1
    assert losers[0]["http"] == 409


# ============================================================================
# 6. Audit row per transition
# ============================================================================


def test_audit_row_per_transition(engine, db):
    rma_id = _dispatched(engine)
    engine.record_credit_note(rma_id, credit_note_number="CN-1",
                              received_amount=3000.0, actor="acc")
    engine.close_rma(rma_id, actor="acc")
    actions = [a["action"] for a in _audit_rows(db) if a["entity_id"] == rma_id]
    for expected in ("rma_raised", "rma_authorized", "rma_dispatched",
                     "rma_credit_recorded", "rma_closed"):
        assert expected in actions, expected


# ============================================================================
# 7. Fail-soft (no DB)
# ============================================================================


def test_fail_soft_no_db():
    eng = VendorRMAEngine(db=None)
    assert eng.list() == []
    assert eng.get("RMA-x") is None
    assert eng.raise_rma(vendor_id="V", vendor_name="Z", store_id="S1",
                         lines=[{"product_id": "P", "product_name": "x",
                                 "quantity": 1, "reason": "DEFECTIVE",
                                 "unit_cost": 1.0}], created_by="m") == {
        "ok": False, "error": "no_db"}
    assert eng.authorize("RMA-x", vendor_rma_number="A", actor="m")["ok"] is False


def test_rupees_to_paise_rounding():
    assert rupees_to_paise("123.456") == 12346   # half-up
    assert rupees_to_paise("123.454") == 12345
    assert rupees_to_paise(0) == 0
    assert rupees_to_paise(-5) == 0
    assert rupees_to_paise("not-a-number") == 0


# ============================================================================
# ROUTER-level: RBAC + store-scope + E4 seam (drive the async handlers directly)
# ============================================================================


def _run(coro):
    # Python 3.12+/3.14 removed the implicit current-loop; asyncio.run spins a
    # fresh loop per call (each handler invocation is independent here).
    return asyncio.run(coro)


def _patch_router_db(monkeypatch, db):
    """Point the router's _get_db at the fake DB so the engine + approvals run
    against it."""
    import api.routers.vendor_rma as rt

    monkeypatch.setattr(rt, "_get_db", lambda: db)
    return rt


def _user(roles, store_ids=("S1",), active="S1", uid="u1"):
    return {
        "user_id": uid,
        "roles": list(roles),
        "store_ids": list(store_ids),
        "active_store_id": active,
    }


@pytest.fixture
def rt(monkeypatch, db):
    return _patch_router_db(monkeypatch, db)


def test_router_raise_and_list(rt, db):
    from api.routers.vendor_rma import raise_rma, list_rmas, RMACreate, RMALineCreate

    body = RMACreate(
        vendor_id="V1", vendor_name="Zeiss", store_id="S1",
        lines=[RMALineCreate(product_id="P1", product_name="Lens", quantity=2,
                             reason="DEFECTIVE", unit_cost=1500.0)],
    )
    res = _run(raise_rma(body, current_user=_user(["STORE_MANAGER"])))
    assert res["ok"] is True
    # Pass the query params explicitly: calling the handler directly bypasses
    # FastAPI's Query-default resolution, so the raw Query objects must be
    # overridden with concrete values.
    listed = _run(list_rmas(store_id=None, vendor_id=None, status=None, skip=0,
                            limit=50, current_user=_user(["STORE_MANAGER"])))
    assert listed["total"] == 1
    # rupee display field present alongside the paise integer
    assert listed["rmas"][0]["expected_credit_rupees"] == 3000.0
    assert listed["rmas"][0]["expected_credit_paise"] == 300000


def test_router_cross_store_create_403(rt, db):
    from fastapi import HTTPException

    from api.routers.vendor_rma import raise_rma, RMACreate, RMALineCreate

    body = RMACreate(
        vendor_id="V1", vendor_name="Zeiss", store_id="S2",  # not the caller's store
        lines=[RMALineCreate(product_id="P1", product_name="Lens", quantity=1,
                             reason="DEFECTIVE", unit_cost=100.0)],
    )
    with pytest.raises(HTTPException) as ei:
        _run(raise_rma(body, current_user=_user(["STORE_MANAGER"], store_ids=["S1"], active="S1")))
    assert ei.value.status_code == 403


def test_router_cross_store_read_403(rt, engine):
    from fastapi import HTTPException

    from api.routers.vendor_rma import get_rma

    rma_id = _seed_rma(engine, store_id="S2")
    with pytest.raises(HTTPException) as ei:
        _run(get_rma(rma_id, current_user=_user(["STORE_MANAGER"], store_ids=["S1"], active="S1")))
    assert ei.value.status_code == 403


def test_router_admin_reads_cross_store(rt, engine):
    from api.routers.vendor_rma import get_rma

    rma_id = _seed_rma(engine, store_id="S2")
    doc = _run(get_rma(rma_id, current_user=_user(["ADMIN"], store_ids=[], active=None)))
    assert doc["rma_id"] == rma_id


def test_cashier_cannot_authorize_or_credit():
    """A cashier (and every junior role) is NOT in _VENDOR_RMA_ROLES, so the
    require_roles gate 403s before the handler body runs."""
    from api.routers.vendor_rma import _VENDOR_RMA_ROLES

    for junior in ("SALES_CASHIER", "SALES_STAFF", "CASHIER", "WORKSHOP_STAFF",
                   "OPTOMETRIST", "CATALOG_MANAGER"):
        assert junior not in _VENDOR_RMA_ROLES
    for ap in ("ADMIN", "AREA_MANAGER", "STORE_MANAGER", "ACCOUNTANT"):
        assert ap in _VENDOR_RMA_ROLES


def test_cashier_403_via_require_roles_dependency():
    """Exercise the actual require_roles dependency the write endpoints carry: a
    cashier token is 403'd; a STORE_MANAGER passes."""
    from fastapi import HTTPException

    from api.routers.auth import require_roles
    from api.routers.vendor_rma import _VENDOR_RMA_ROLES

    dep = require_roles(*_VENDOR_RMA_ROLES)
    # The dependency closes over an inner async _dep(current_user=...).
    inner = dep
    with pytest.raises(HTTPException) as ei:
        _run(inner(current_user=_user(["SALES_CASHIER"])))
    assert ei.value.status_code == 403
    passed = _run(inner(current_user=_user(["STORE_MANAGER"])))
    assert passed["user_id"] == "u1"


# ============================================================================
# E4 maker-checker seam: a large credit needs a consumed approval token
# ============================================================================


def test_large_credit_requires_approval_token(rt, db, monkeypatch):
    """A credit over the E2 threshold (default Rs 50,000) without an approval
    token -> 403 approval_required."""
    from fastapi import HTTPException

    from api.routers.vendor_rma import record_credit_note, RMACreditNote
    from api.services.vendor_rma import VendorRMAEngine

    eng = VendorRMAEngine(db=db)
    rma_id = eng.raise_rma(
        vendor_id="V1", vendor_name="Luxottica", store_id="S1",
        lines=[{"product_id": "P1", "product_name": "Frame", "quantity": 100,
                "reason": "WARRANTY", "unit_cost": 1000.0}],  # expected Rs 100,000
        created_by="mgr")["rma_id"]
    eng.authorize(rma_id, vendor_rma_number="LRMA-1", actor="mgr")
    eng.dispatch(rma_id, carrier="X", awb="W", dispatch_date=None, actor="mgr")

    body = RMACreditNote(credit_note_number="CN-BIG", received_amount=80000.0)  # > Rs 50k
    with pytest.raises(HTTPException) as ei:
        _run(record_credit_note(rma_id, body, current_user=_user(["ACCOUNTANT"])))
    assert ei.value.status_code == 403
    detail = ei.value.detail
    assert isinstance(detail, dict) and detail.get("error") == "approval_required"


def test_large_credit_proceeds_with_valid_approval(rt, db):
    """With a consumed E4 approval (action_type 'rtv'), the large credit records."""
    from api.routers.auth import hash_password
    from api.routers.vendor_rma import record_credit_note, RMACreditNote
    from api.services.approvals import ApprovalEngine
    from api.services.vendor_rma import VendorRMAEngine

    # Seed an approver with a PIN so the E4 approve() can mint a token. Rs 80,000
    # resolves to the SUPER tier (>= Rs 10,000), so the approver is a SUPERADMIN.
    users = db.get_collection("users")
    users.insert_one({"user_id": "sa", "roles": ["SUPERADMIN"],
                      "approval_pin_hash": hash_password("1234"),
                      "pin_attempts": {"count": 0, "window_start": vr._now()}})

    eng = VendorRMAEngine(db=db)
    rma_id = eng.raise_rma(
        vendor_id="V1", vendor_name="Luxottica", store_id="S1",
        lines=[{"product_id": "P1", "product_name": "Frame", "quantity": 100,
                "reason": "WARRANTY", "unit_cost": 1000.0}], created_by="mgr")["rma_id"]
    eng.authorize(rma_id, vendor_rma_number="LRMA-1", actor="mgr")
    eng.dispatch(rma_id, carrier="X", awb="W", dispatch_date=None, actor="mgr")

    # P1-2: the rtv approval is minted BOUND to this RMA + store (context.rma_id +
    # store_id). The router supplies approval_request_id at consume time.
    appr = ApprovalEngine(db=db)
    req = appr.request(action_type="rtv", requested_by="acc", amount=80000.0,
                       store_id="S1", context={"rma_id": rma_id})
    assert req["required_tier"] == "super"
    approved = appr.approve(req["request_id"], approver_user_id="sa",
                            approver_roles=["SUPERADMIN"], pin="1234")
    assert approved["ok"] is True
    token = approved["approval_token"]

    body = RMACreditNote(credit_note_number="CN-BIG", received_amount=80000.0,
                         approval_token=token, approval_request_id=req["request_id"])
    res = _run(record_credit_note(rma_id, body, current_user=_user(["ACCOUNTANT"], uid="acc")))
    assert res["ok"] is True
    assert res["received_credit_paise"] == 8_000_000  # Rs 80,000 in paise


# ============================================================================
# P1-1: atomic credit accumulation -- no lost update; dup-CN race -> one only
# ============================================================================


def test_concurrent_distinct_credit_notes_sum_exact_no_lost_update(engine, db, monkeypatch):
    """Two concurrent DISTINCT credit notes against one dispatched RMA. Both READ
    the same stale pre-credit doc (the real lost-update race), then each commits
    via the atomic $inc. The received total MUST be the exact SUM of both notes --
    the old read-then-absolute-$set would have clobbered the first with the
    second (lost update)."""
    rma_id = _dispatched(engine)  # expected 300000 paise

    # Force BOTH calls to read the identical stale snapshot (status DISPATCHED,
    # received 0) so a non-atomic absolute-$set engine would compute both totals
    # off the same base and clobber. The atomic $inc is immune.
    stale = db.get_collection("vendor_rmas").find_one({"rma_id": rma_id})
    real_get = engine.get
    calls = {"n": 0}

    def _staleable_get(rid):
        # First two reads (one per concurrent credit) see the stale snapshot; any
        # later read (variance recompute / audit) sees the live doc.
        if rid == rma_id and calls["n"] < 2:
            calls["n"] += 1
            return dict(stale)
        return real_get(rid)

    monkeypatch.setattr(engine, "get", _staleable_get)

    r1 = engine.record_credit_note(rma_id, credit_note_number="CN-A",
                                   received_amount=1000.00, actor="acc")  # 100000
    r2 = engine.record_credit_note(rma_id, credit_note_number="CN-B",
                                   received_amount=500.00, actor="acc")   # 50000
    assert r1["ok"] and r2["ok"]
    doc = db.get_collection("vendor_rmas").find_one({"rma_id": rma_id})
    # Exact sum -- no lost update.
    assert doc["received_credit_paise"] == 100000 + 50000 == 150000
    assert doc["variance_paise"] == 300000 - 150000 == 150000
    assert len(doc["credit_notes"]) == 2


def test_concurrent_duplicate_credit_note_records_exactly_once(engine, db, monkeypatch):
    """Two concurrent records of the SAME credit-note number. Both read the doc
    before either commits (no dup present yet at read time). The in-filter
    $ne-on-credit_notes guard lets exactly ONE commit; the loser gets a 409
    duplicate_credit_note -- no double-credit."""
    rma_id = _dispatched(engine)  # expected 300000

    stale = db.get_collection("vendor_rmas").find_one({"rma_id": rma_id})
    real_get = engine.get
    calls = {"n": 0}

    def _staleable_get(rid):
        if rid == rma_id and calls["n"] < 2:
            calls["n"] += 1
            return dict(stale)
        return real_get(rid)

    monkeypatch.setattr(engine, "get", _staleable_get)

    r1 = engine.record_credit_note(rma_id, credit_note_number="CN-DUP",
                                   received_amount=1000.00, actor="acc")
    r2 = engine.record_credit_note(rma_id, credit_note_number="CN-DUP",
                                   received_amount=1000.00, actor="acc")
    oks = [r for r in (r1, r2) if r.get("ok")]
    losers = [r for r in (r1, r2) if not r.get("ok")]
    assert len(oks) == 1
    assert len(losers) == 1
    assert losers[0]["http"] == 409 and losers[0]["error"] == "duplicate_credit_note"
    doc = db.get_collection("vendor_rmas").find_one({"rma_id": rma_id})
    # Exactly one note recorded; received reflects a SINGLE credit (no double).
    assert len(doc["credit_notes"]) == 1
    assert doc["received_credit_paise"] == 100000


# ============================================================================
# P1-2: an rtv token bound to store-A/RMA-A is rejected on store-B or RMA-B
# ============================================================================


def _seed_super_approver(db):
    from api.routers.auth import hash_password

    db.get_collection("users").insert_one({
        "user_id": "sa", "roles": ["SUPERADMIN"],
        "approval_pin_hash": hash_password("1234"),
        "pin_attempts": {"count": 0, "window_start": vr._now()},
    })


def _mint_rtv_token(db, *, store_id, rma_id, amount=80000.0):
    """Mint an APPROVED rtv approval bound (context.rma_id + store_id) to a
    specific store + RMA. Returns (request_id, token)."""
    from api.services.approvals import ApprovalEngine

    appr = ApprovalEngine(db=db)
    req = appr.request(action_type="rtv", requested_by="acc", amount=amount,
                       store_id=store_id, context={"rma_id": rma_id})
    approved = appr.approve(req["request_id"], approver_user_id="sa",
                            approver_roles=["SUPERADMIN"], pin="1234")
    assert approved["ok"] is True
    return req["request_id"], approved["approval_token"]


def _big_dispatched_rma(eng, *, store_id, vendor_rma_number):
    """A DISPATCHED RMA with a Rs-100,000 expected credit (so an Rs-80,000 credit
    note crosses the Rs-50,000 maker-checker tier)."""
    rma_id = eng.raise_rma(
        vendor_id="V1", vendor_name="Luxottica", store_id=store_id,
        lines=[{"product_id": "P1", "product_name": "Frame", "quantity": 100,
                "reason": "WARRANTY", "unit_cost": 1000.0}], created_by="mgr")["rma_id"]
    eng.authorize(rma_id, vendor_rma_number=vendor_rma_number, actor="mgr")
    eng.dispatch(rma_id, carrier="X", awb="W", dispatch_date=None, actor="mgr")
    return rma_id


def test_rtv_token_rejected_on_different_rma(rt, db):
    """An rtv token minted bound to RMA-A is REJECTED (403 approval_invalid) when
    consumed against a DIFFERENT RMA-B in the SAME store -- the consume re-checks
    context.rma_id == the RMA being credited and rolls back on mismatch."""
    from fastapi import HTTPException

    from api.routers.vendor_rma import record_credit_note, RMACreditNote

    _seed_super_approver(db)
    eng = VendorRMAEngine(db=db)
    rma_a = _big_dispatched_rma(eng, store_id="S1", vendor_rma_number="A-1")
    rma_b = _big_dispatched_rma(eng, store_id="S1", vendor_rma_number="B-1")

    # Token bound to RMA-A...
    req_id, token = _mint_rtv_token(db, store_id="S1", rma_id=rma_a)

    # ...replayed against RMA-B -> rejected.
    body = RMACreditNote(credit_note_number="CN-X", received_amount=80000.0,
                         approval_token=token, approval_request_id=req_id)
    with pytest.raises(HTTPException) as ei:
        _run(record_credit_note(rma_b, body, current_user=_user(["ACCOUNTANT"], uid="acc")))
    assert ei.value.status_code == 403
    assert ei.value.detail.get("error") == "approval_invalid"
    # The token was rolled back (not burned) by the mismatch, so it still works
    # against its OWN RMA-A.
    body_ok = RMACreditNote(credit_note_number="CN-A", received_amount=80000.0,
                            approval_token=token, approval_request_id=req_id)
    res = _run(record_credit_note(rma_a, body_ok,
                                  current_user=_user(["ACCOUNTANT"], uid="acc")))
    assert res["ok"] is True


def test_rtv_token_rejected_on_different_store(rt, db):
    """An rtv token minted for store-A is REJECTED when consumed against an RMA in
    store-B -- the consume re-checks store_id == the RMA's store. ADMIN caller so
    the cross-store RMA read itself is allowed; the BLOCK is the approval binding,
    not the store-IDOR guard."""
    from fastapi import HTTPException

    from api.routers.vendor_rma import record_credit_note, RMACreditNote

    _seed_super_approver(db)
    eng = VendorRMAEngine(db=db)
    rma_b = _big_dispatched_rma(eng, store_id="S2", vendor_rma_number="B-1")

    # Token minted for store S1 but carrying RMA-B's id (so only the STORE differs).
    req_id, token = _mint_rtv_token(db, store_id="S1", rma_id=rma_b)

    body = RMACreditNote(credit_note_number="CN-X", received_amount=80000.0,
                         approval_token=token, approval_request_id=req_id)
    with pytest.raises(HTTPException) as ei:
        _run(record_credit_note(rma_b, body,
                                current_user=_user(["ADMIN"], store_ids=[], active=None,
                                                   uid="acc")))
    assert ei.value.status_code == 403
    assert ei.value.detail.get("error") == "approval_invalid"


def test_rtv_floating_token_without_request_id_rejected(rt, db):
    """P1-2: a bare floating token (no approval_request_id) is rejected with
    approval_required -- a concrete request id is mandatory so the binding can be
    re-validated against the named request."""
    from fastapi import HTTPException

    from api.routers.vendor_rma import record_credit_note, RMACreditNote

    eng = VendorRMAEngine(db=db)
    rma_id = _big_dispatched_rma(eng, store_id="S1", vendor_rma_number="A-1")

    body = RMACreditNote(credit_note_number="CN-X", received_amount=80000.0,
                         approval_token="some-floating-token")  # no request id
    with pytest.raises(HTTPException) as ei:
        _run(record_credit_note(rma_id, body, current_user=_user(["ACCOUNTANT"], uid="acc")))
    assert ei.value.status_code == 403
    assert ei.value.detail.get("error") == "approval_required"
