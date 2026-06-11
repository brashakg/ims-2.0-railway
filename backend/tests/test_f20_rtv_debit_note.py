"""
IMS 2.0 - F20 RTV Debit Note (intent-level tests)
==================================================
Exercise the REAL ``rtv_debit_note`` service + the router's RBAC / store-scope /
idempotency against a faithful in-memory fake Mongo (no network, no live mongod).
A hollow shell that skips the FY atomic serial, the paise-exact GST split, the
balanced Tally voucher, the store-IDOR guard, the role gate, or the idempotent
re-issue FAILS here.

Covers the F20 acceptance list:
  - debit-note serial is consecutive per entity+FY + collision-safe (atomic)
  - intra-state -> CGST+SGST, inter-state -> IGST (paise-exact, matches sales)
  - totals = sum(line taxable) + tax
  - builds off an existing RTV / vendor-return (and a vendor_rma)
  - Tally Debit Note voucher XML is balanced (debits == credits)
  - store/entity-scope 403; cashier 403
  - idempotent (re-issue returns the same note, no double-serial)

CI-robust: every accessor monkeypatched + docs seeded; no whole-JSON substring.
"""

from __future__ import annotations

import asyncio
import os
import sys
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-f20")
os.environ.setdefault("ENVIRONMENT", "test")

from api.services import rtv_debit_note as dn  # noqa: E402
from api.services.rtv_debit_note import (  # noqa: E402
    DebitNoteEngine,
    build_debit_note,
    next_debit_note_number,
    render_debit_note_html,
    tally_build_debit_note_xml,
    state_code_of,
    financial_year_label,
)


# ============================================================================
# Faithful in-memory fake Mongo (same shape as test_n4_vendor_rma)
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
                if isinstance(actual, list) and "." in k:
                    if op == "$in":
                        if not any(_v in expected for _v in actual):
                            return False
                        continue
                if not _cmp_op(actual, op, expected):
                    return False
            continue
        if isinstance(actual, list) and "." in k:
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


class _DupKeyError(Exception):
    pass


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
    """Supports unique indexes: a registered unique key rejects a duplicate
    insert (raises) so the engine's idempotent re-issue path is exercised."""

    def __init__(self, database=None):
        self.docs: List[Dict[str, Any]] = []
        self._n = 0
        self.database = database
        self._unique: List[str] = []

    def insert_one(self, doc):
        for key in self._unique:
            val = doc.get(key)
            if val is None:
                continue
            if any(d.get(key) == val for d in self.docs):
                raise _DupKeyError(f"dup:{key}={val}")
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

    def find_one_and_update(self, query, update, return_document=None, upsert=False, **_kw):
        for d in self.docs:
            if _matches(d, query):
                _apply_update(d, update)
                return _project(d, None)
        if upsert:
            new_doc: Dict[str, Any] = {}
            # honour an _id-equality filter for the upsert key
            for k, v in query.items():
                if not (isinstance(v, dict) and any(str(kk).startswith("$") for kk in v)):
                    new_doc[k] = v
            _apply_update(new_doc, update)
            new_doc.setdefault("_id", f"oid-{self._n}")
            self._n += 1
            self.docs.append(new_doc)
            return _project(new_doc, None)
        return None

    def create_index(self, keys, unique=False, **k):
        if unique and isinstance(keys, str):
            self._unique.append(keys)
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
# Fixtures + seed
# ============================================================================


@pytest.fixture
def db():
    d = FakeDB()
    # Seed an entity (prefix) + vendor + a source vendor_return.
    d.get_collection("entities").insert_one(
        {"entity_id": "E1", "code": "BV", "legal_name": "Better Vision Pvt Ltd",
         "gstin": "20ABCDE1234F1Z5", "state_code": "20", "address": "Ranchi"}
    )
    d.get_collection("stores").insert_one(
        {"store_id": "S1", "entity_id": "E1", "name": "BV Ranchi",
         "gstin": "20ABCDE1234F1Z5", "state_code": "20"}
    )
    # Intra-state vendor (state 20 == seller 20) and an inter-state vendor (27).
    d.get_collection("vendors").insert_one(
        {"vendor_id": "V1", "name": "GKB Optical", "gstin": "20ZZZZZ9999Z1Z5",
         "state_code": "20", "address": "Jamshedpur"}
    )
    d.get_collection("vendors").insert_one(
        {"vendor_id": "V2", "name": "Essilor India", "gstin": "27EEEEE8888E1Z5",
         "state_code": "27", "address": "Mumbai"}
    )
    return d


def _seed_return(db, *, return_id="VR-1", store_id="S1", vendor_id="V1",
                 entity_id="E1", lines=None):
    items = lines or [
        {"product_id": "P1", "product_name": "Zeiss Lens", "hsn": "9001",
         "quantity": 2, "rate_paise": 150000, "gst_rate": 5.0},
        {"product_id": "P2", "product_name": "Ray-Ban Sunglass", "hsn": "9004",
         "quantity": 1, "rate_paise": 99999, "gst_rate": 18.0},
    ]
    doc = {
        "return_id": return_id,
        "store_id": store_id,
        "vendor_id": vendor_id,
        "vendor_name": "GKB Optical",
        "entity_id": entity_id,
        "purchase_invoice_number": "PINV-2026-77",
        "purchase_invoice_date": "2026-05-01",
        "lines": items,
    }
    db.get_collection("vendor_returns").insert_one(dict(doc))
    return doc


def _seller():
    return {"entity_id": "E1", "name": "Better Vision Pvt Ltd",
            "gstin": "20ABCDE1234F1Z5", "state_code": "20", "address": "Ranchi"}


# ============================================================================
# 1. FY serial: consecutive per entity+FY + collision-safe (atomic)
# ============================================================================


def test_serial_consecutive_per_entity_fy(db):
    n1 = next_debit_note_number(db, "E1")
    n2 = next_debit_note_number(db, "E1")
    n3 = next_debit_note_number(db, "E1")
    assert n1.endswith("/000001")
    assert n2.endswith("/000002")
    assert n3.endswith("/000003")
    # Format DN/{prefix}/{FY}/{serial}: entity code prefix + FY label.
    parts = n1.split("/")
    assert parts[0] == "DN" and parts[1] == "BV"
    assert parts[2] == financial_year_label()


def test_serial_separate_series_per_entity(db):
    db.get_collection("entities").insert_one({"entity_id": "E2", "code": "WZ"})
    a1 = next_debit_note_number(db, "E1")
    b1 = next_debit_note_number(db, "E2")
    # Distinct entities have independent counters -> both start at 1.
    assert a1.endswith("/000001") and "/BV/" in a1
    assert b1.endswith("/000001") and "/WZ/" in b1


def test_serial_atomic_no_collision(db):
    # The atomic $inc counter means N allocations are N distinct serials.
    serials = {next_debit_note_number(db, "E1") for _ in range(25)}
    assert len(serials) == 25


# ============================================================================
# 2. GST split: intra -> CGST+SGST, inter -> IGST (paise-exact, sales-mirror)
# ============================================================================


def test_intra_state_splits_cgst_sgst(db):
    rtv = _seed_return(db)  # vendor V1 state 20 == seller 20 -> intra
    note = build_debit_note(rtv, db.get_collection("vendors").find_one({"vendor_id": "V1"}),
                            [], "DN/BV/2026-27/000001", seller=_seller())
    assert note["is_inter_state"] is False
    # Line 1: taxable 2*150000=300000 @5% -> tax 15000; cgst=7500, sgst=7500
    l1 = note["lines"][0]
    assert l1["taxable_paise"] == 300000
    assert l1["cgst_paise"] == 7500 and l1["sgst_paise"] == 7500
    assert l1["igst_paise"] == 0
    assert l1["cgst_paise"] + l1["sgst_paise"] == l1["tax_paise"] == 15000


def test_inter_state_splits_igst(db):
    rtv = _seed_return(db, return_id="VR-INT", vendor_id="V2")  # vendor state 27 -> inter
    note = build_debit_note(rtv, db.get_collection("vendors").find_one({"vendor_id": "V2"}),
                            [], "DN/BV/2026-27/000002", seller=_seller())
    assert note["is_inter_state"] is True
    l1 = note["lines"][0]
    assert l1["igst_paise"] == 15000  # full tax as IGST
    assert l1["cgst_paise"] == 0 and l1["sgst_paise"] == 0


def test_odd_paise_residual_on_sgst(db):
    # 99999 @18% = 17999.82 -> round-half-up 18000 paise; cgst=9000, sgst=9000.
    # Use an odd-tax line to prove the residual rule: taxable 33333 @5% = 1666.65
    # -> 1667 paise tax; cgst=833, sgst=834 (residual on SGST), sum == 1667.
    rtv = _seed_return(db, return_id="VR-ODD", lines=[
        {"product_id": "PX", "product_name": "x", "hsn": "9001",
         "quantity": 1, "rate_paise": 33333, "gst_rate": 5.0}])
    note = build_debit_note(rtv, db.get_collection("vendors").find_one({"vendor_id": "V1"}),
                            [], "DN/BV/2026-27/000003", seller=_seller())
    l1 = note["lines"][0]
    assert l1["tax_paise"] == 1667
    assert l1["cgst_paise"] == 833 and l1["sgst_paise"] == 834
    assert l1["cgst_paise"] + l1["sgst_paise"] == l1["tax_paise"]


def test_state_code_of_from_gstin():
    assert state_code_of("27EEEEE8888E1Z5") == "27"
    assert state_code_of("20") == "20"
    assert state_code_of(None, "", "20ABCDE1234F1Z5") == "20"
    assert state_code_of("nonsense") == ""


# ============================================================================
# 3. Totals = sum(line taxable) + tax
# ============================================================================


def test_totals_equal_sum_of_lines(db):
    rtv = _seed_return(db)
    note = build_debit_note(rtv, db.get_collection("vendors").find_one({"vendor_id": "V1"}),
                            [], "DN/BV/2026-27/000001", seller=_seller())
    t = note["totals"]
    assert t["taxable_paise"] == sum(l["taxable_paise"] for l in note["lines"])
    assert t["cgst_paise"] == sum(l["cgst_paise"] for l in note["lines"])
    assert t["sgst_paise"] == sum(l["sgst_paise"] for l in note["lines"])
    assert t["tax_paise"] == t["cgst_paise"] + t["sgst_paise"] + t["igst_paise"]
    assert t["grand_total_paise"] == t["taxable_paise"] + t["tax_paise"]
    # Line 1 taxable 300000 tax 15000 + Line 2 taxable 99999 @18% tax 18000
    assert t["taxable_paise"] == 300000 + 99999
    assert t["grand_total_paise"] == (300000 + 99999) + (15000 + 18000)


# ============================================================================
# 4. Build off an existing RTV / vendor-return + carry original invoice ref
# ============================================================================


def test_builds_off_existing_return(db):
    rtv = _seed_return(db)
    note = build_debit_note(rtv, db.get_collection("vendors").find_one({"vendor_id": "V1"}),
                            [], "DN/BV/2026-27/000001", seller=_seller())
    assert note["rtv_ref"] == {"type": "vendor_return", "id": "VR-1"}
    assert note["original_invoice"]["number"] == "PINV-2026-77"
    assert note["vendor"]["gstin"] == "20ZZZZZ9999Z1Z5"
    assert note["seller"]["gstin"] == "20ABCDE1234F1Z5"
    assert note["store_id"] == "S1"


def test_builds_off_vendor_rma(db):
    rma = {"rma_id": "RMA-9", "store_id": "S1", "vendor_id": "V1", "entity_id": "E1",
           "lines": [{"product_id": "P", "product_name": "Lens", "quantity": 1,
                      "unit_cost_paise": 50000, "gst_rate": 5.0}]}
    note = build_debit_note(rma, db.get_collection("vendors").find_one({"vendor_id": "V1"}),
                            [], "DN/BV/2026-27/000001", seller=_seller())
    assert note["rtv_ref"] == {"type": "vendor_rma", "id": "RMA-9"}
    assert note["lines"][0]["taxable_paise"] == 50000


# ============================================================================
# 5. Tally Debit Note voucher XML is balanced (debits == credits)
# ============================================================================


def test_tally_voucher_balanced_intra(db):
    rtv = _seed_return(db)
    note = build_debit_note(rtv, db.get_collection("vendors").find_one({"vendor_id": "V1"}),
                            [], "DN/BV/2026-27/000001", seller=_seller())
    xml = tally_build_debit_note_xml(note)
    root = ET.fromstring(xml)
    voucher = root.find(".//VOUCHER")
    assert voucher is not None
    assert voucher.get("VCHTYPE") == "Debit Note"
    # Sum signed amounts: debit legs are negative, credit legs positive -> 0.
    total = 0.0
    debits = 0.0
    credits = 0.0
    for leg in voucher.findall("ALLLEDGERENTRIES.LIST"):
        amt = float(leg.find("AMOUNT").text)
        total += amt
        if amt < 0:
            debits += -amt
        else:
            credits += amt
    assert abs(total) < 0.005  # balanced to the paise
    assert abs(debits - credits) < 0.005
    # Vendor party leg is the single debit = grand total.
    assert abs(debits - dn.paise_to_rupees(note["totals"]["grand_total_paise"])) < 0.005


def test_tally_voucher_balanced_inter(db):
    rtv = _seed_return(db, return_id="VR-INT", vendor_id="V2")
    note = build_debit_note(rtv, db.get_collection("vendors").find_one({"vendor_id": "V2"}),
                            [], "DN/BV/2026-27/000002", seller=_seller())
    xml = tally_build_debit_note_xml(note)
    root = ET.fromstring(xml)
    voucher = root.find(".//VOUCHER")
    total = sum(float(leg.find("AMOUNT").text)
                for leg in voucher.findall("ALLLEDGERENTRIES.LIST"))
    assert abs(total) < 0.005
    # Inter-state -> an Input IGST credit leg present, no CGST/SGST.
    ledgers = [leg.find("LEDGERNAME").text for leg in voucher.findall("ALLLEDGERENTRIES.LIST")]
    assert any("IGST" in (l or "") for l in ledgers)
    assert not any("CGST" in (l or "") for l in ledgers)


def test_print_html_renders(db):
    rtv = _seed_return(db)
    note = build_debit_note(rtv, db.get_collection("vendors").find_one({"vendor_id": "V1"}),
                            [], "DN/BV/2026-27/000001", seller=_seller())
    html = render_debit_note_html(note)
    assert "DEBIT NOTE" in html
    assert "DN/BV/2026-27/000001" in html
    assert "Better Vision Pvt Ltd" in html
    assert "GKB Optical" in html


# ============================================================================
# 6. Idempotent issue: re-issue returns the same note, no double-serial
# ============================================================================


def test_issue_then_reissue_idempotent(db):
    _seed_return(db)
    eng = DebitNoteEngine(db=db)
    eng.ensure_indexes()
    rtv = db.get_collection("vendor_returns").find_one({"return_id": "VR-1"})
    vendor = db.get_collection("vendors").find_one({"vendor_id": "V1"})

    first = eng.issue(rtv, vendor, actor="acc", seller=_seller())
    assert first["ok"] is True and first["idempotent"] is False
    serial1 = first["debit_note"]["debit_note_number"]

    second = eng.issue(rtv, vendor, actor="acc", seller=_seller())
    assert second["ok"] is True and second["idempotent"] is True
    assert second["debit_note"]["debit_note_number"] == serial1
    # Exactly one debit note persisted; no second serial burned.
    assert len(db.get_collection("debit_notes").docs) == 1
    # An audit row was written for the issue.
    rows = [r for r in db.get_collection("audit_logs").docs
            if r.get("entity_type") == "DEBIT_NOTE"]
    assert len(rows) == 1


def test_issue_requires_rtv_ref(db):
    eng = DebitNoteEngine(db=db)
    res = eng.issue({"store_id": "S1"}, {"vendor_id": "V1"}, actor="acc")
    assert res["ok"] is False and res["error"] == "rtv_ref_required"


def test_fail_soft_no_db():
    eng = DebitNoteEngine(db=None)
    assert eng.list() == []
    assert eng.get("DN-x") is None
    res = eng.issue({"return_id": "VR-1", "store_id": "S1"}, {"vendor_id": "V1"},
                    actor="acc")
    assert res["ok"] is False and res["error"] == "no_db"


# ============================================================================
# 7. Router: RBAC (cashier 403) + store-scope 403 + idempotent issue
# ============================================================================


def _run(coro):
    return asyncio.run(coro)


def _user(roles, store_ids=("S1",), active="S1", uid="u1"):
    return {
        "user_id": uid,
        "roles": list(roles),
        "store_ids": list(store_ids),
        "active_store_id": active,
    }


@pytest.fixture
def rt(monkeypatch, db):
    import api.routers.rtv_debit_notes as r

    monkeypatch.setattr(r, "_get_db", lambda: db)
    return r


def test_router_issue_and_list(rt, db):
    _seed_return(db)
    from api.routers.rtv_debit_notes import issue_debit_note, list_debit_notes, DebitNoteIssue

    body = DebitNoteIssue(source_type="vendor_return", rtv_id="VR-1")
    res = _run(issue_debit_note(body, current_user=_user(["ACCOUNTANT"])))
    assert res["idempotent"] is False
    dn_doc = res["debit_note"]
    assert dn_doc["debit_note_number"].startswith("DN/BV/")
    # rupee display block alongside paise integers
    assert dn_doc["totals_rupees"]["grand_total"] == dn.paise_to_rupees(
        dn_doc["totals"]["grand_total_paise"])

    listed = _run(list_debit_notes(store_id=None, vendor_id=None, skip=0, limit=50,
                                   current_user=_user(["ACCOUNTANT"])))
    assert listed["total"] == 1


def test_router_reissue_idempotent(rt, db):
    _seed_return(db)
    from api.routers.rtv_debit_notes import issue_debit_note, DebitNoteIssue

    body = DebitNoteIssue(source_type="vendor_return", rtv_id="VR-1")
    first = _run(issue_debit_note(body, current_user=_user(["ADMIN"])))
    second = _run(issue_debit_note(body, current_user=_user(["ADMIN"])))
    assert second["idempotent"] is True
    assert (second["debit_note"]["debit_note_number"]
            == first["debit_note"]["debit_note_number"])
    assert len(db.get_collection("debit_notes").docs) == 1


def test_router_cross_store_issue_403(rt, db):
    from fastapi import HTTPException
    from api.routers.rtv_debit_notes import issue_debit_note, DebitNoteIssue

    _seed_return(db, return_id="VR-S2", store_id="S2")  # not the caller's store
    body = DebitNoteIssue(source_type="vendor_return", rtv_id="VR-S2")
    with pytest.raises(HTTPException) as ei:
        _run(issue_debit_note(body, current_user=_user(["STORE_MANAGER"],
                                                       store_ids=["S1"], active="S1")))
    assert ei.value.status_code == 403


def test_router_issue_unknown_rtv_404(rt, db):
    from fastapi import HTTPException
    from api.routers.rtv_debit_notes import issue_debit_note, DebitNoteIssue

    body = DebitNoteIssue(source_type="vendor_return", rtv_id="VR-NOPE")
    with pytest.raises(HTTPException) as ei:
        _run(issue_debit_note(body, current_user=_user(["ACCOUNTANT"])))
    assert ei.value.status_code == 404


def test_cashier_cannot_issue_403():
    # require_roles is the enforcement primitive used on the issue + tally routes.
    # A cashier hitting it raises 403 (SUPERADMIN/AP roles pass).
    from fastapi import HTTPException
    from api.routers.auth import require_roles
    from api.routers.rtv_debit_notes import _DEBIT_NOTE_ROLES

    dep = require_roles(*_DEBIT_NOTE_ROLES)
    with pytest.raises(HTTPException) as ei:
        _run(dep(current_user=_user(["SALES_CASHIER"])))
    assert ei.value.status_code == 403
    # An accountant passes.
    out = _run(dep(current_user=_user(["ACCOUNTANT"])))
    assert out["roles"] == ["ACCOUNTANT"]


def test_rbac_policy_rows_present():
    # The policy registry must catalogue every F20 route (so the request-time RBAC
    # middleware + the access-matrix test see them) -- issue/tally gated to AP, the
    # reads AUTHENTICATED + store_scoped.
    from api.services.rbac_policy import POLICY

    by_path = {(p["method"], p["path"]): p for p in POLICY
               if "rtv-debit-notes" in p.get("path", "")}
    issue = by_path[("POST", "/api/v1/rtv-debit-notes/issue")]
    assert set(issue["allowed"]) == {"ACCOUNTANT", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"}
    assert issue.get("store_scoped") is True
    tally = by_path[("GET", "/api/v1/rtv-debit-notes/{debit_note_id}/tally")]
    assert "SALES_CASHIER" not in tally["allowed"]
    listing = by_path[("GET", "/api/v1/rtv-debit-notes")]
    assert listing["allowed"] == "AUTHENTICATED" and listing.get("store_scoped") is True
