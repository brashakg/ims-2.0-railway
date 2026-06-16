"""
IMS 2.0 - N7 CL/lens purchase-order generator tests (intent-level)
==================================================================
Exercises the REAL cl_po_generator service + the cl_po router against a
faithful in-memory fake Mongo (no network, no live mongod). A hollow shell
that keeps qty<=0 lines, forgets to merge duplicate power cells, drops the
power dict off a PO line, writes on dry_run, auto-SENDs a PO, lets a
non-manager draft, or leaks one store's needs to another FAILS here.

Maps to the N7 acceptance intents:
  * power-cell lines    -- every PO line carries power {sph, cyl, add} + qty
                           and a human description ("Acuvue Oasys SPH -2.00
                           CYL -0.75") so a supplier gets an exact power grid
  * merge/clamp/drop    -- duplicate cells merge (qty summed), garbage / <=0
                           qty drops, "+2.00" and 2.0 snap to one cell
  * vendor grouping     -- one group per preferred vendor; unresolved (or
                           resolver-raising) items group under vendor_id=None
                           (PO drafts vendor-less; FE disables send)
  * dry_run safety      -- dry_run=True is the DEFAULT and writes NOTHING
  * DRAFT only          -- non-dry-run creates status=DRAFT POs (never SENT)
                           via the existing PO repository + one audit row
  * role gate 403       -- the manager-ladder gate rejects SALES_STAFF
  * store-scope 403     -- a BV-1 manager cannot draft for BV-2 (REAL
                           validate_store_access)
  * DB-absent fail-soft -- no DB -> empty draft / "nothing was written"
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any, Dict, List, Optional

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from api.routers import cl_po  # noqa: E402
from api.routers import item_events as item_events_router  # noqa: E402
from api.routers import lens_stock as lens_stock_router  # noqa: E402
from api.routers.auth import require_roles  # noqa: E402
from api.services import cl_po_generator as gen  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


# ============================================================================
# Faithful in-memory fake Mongo (only the operators N7's readers use)
# ============================================================================


def _cmp_op(actual: Any, op: str, expected: Any) -> bool:
    if actual is None and op in ("$gt", "$gte", "$lt", "$lte"):
        return False
    try:
        if op == "$gt":
            return actual > expected
        if op == "$gte":
            return actual >= expected
        if op == "$lt":
            return actual < expected
        if op == "$lte":
            return actual <= expected
        if op == "$ne":
            return actual != expected
        if op == "$in":
            return actual in expected
        if op == "$nin":
            return actual not in expected
    except TypeError:
        return False
    return False


def _matches(doc: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for k, v in query.items():
        if k == "$or":
            if not any(_matches(doc, sub) for sub in v):
                return False
            continue
        actual = doc.get(k)
        if isinstance(v, dict) and any(str(kk).startswith("$") for kk in v):
            for op, expected in v.items():
                if not _cmp_op(actual, op, expected):
                    return False
            continue
        if actual != v:
            return False
    return True


class FakeCursor:
    def __init__(self, docs: List[Dict[str, Any]]):
        self._docs = docs

    def __iter__(self):
        return iter(self._docs)


class FakeCollection:
    def __init__(self):
        self.docs: List[Dict[str, Any]] = []

    def insert_one(self, doc):
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc.get("_id")})()

    def find_one(self, query, projection=None):
        for d in self.docs:
            if _matches(d, query):
                return dict(d)
        return None

    def find(self, query=None, projection=None):
        return FakeCursor([dict(d) for d in self.docs if _matches(d, query or {})])


class FakeDB:
    def __init__(self):
        self._collections: Dict[str, FakeCollection] = {}
        self.is_connected = True

    def get_collection(self, name: str) -> FakeCollection:
        if name not in self._collections:
            self._collections[name] = FakeCollection()
        return self._collections[name]

    def __getitem__(self, name: str) -> FakeCollection:
        return self.get_collection(name)


class FakePORepo:
    """Records create() calls -- a write on dry_run fails the safety test."""

    def __init__(self):
        self.created: List[Dict[str, Any]] = []

    def create(self, doc):
        self.created.append(dict(doc))
        return doc


class FakeVendorRepo:
    def __init__(self, vendors: Optional[Dict[str, Dict[str, Any]]] = None):
        self.vendors = vendors or {}

    def find_by_id(self, vendor_id):
        return self.vendors.get(vendor_id)


class FakeAuditRepo:
    def __init__(self):
        self.rows: List[Dict[str, Any]] = []

    def create(self, row):
        self.rows.append(dict(row))
        return row


# ============================================================================
# Fixtures + helpers
# ============================================================================


def _manager(uid="M1", store="BV-1"):
    return {"user_id": uid, "full_name": "Manager One", "roles": ["STORE_MANAGER"],
            "store_ids": [store], "active_store_id": store}


def _staff(uid="S1", store="BV-1"):
    return {"user_id": uid, "full_name": "Sales Staff", "roles": ["SALES_STAFF"],
            "store_ids": [store], "active_store_id": store}


@pytest.fixture()
def db() -> FakeDB:
    return FakeDB()


@pytest.fixture()
def env(db, monkeypatch):
    """Wire ONE FakeDB through every data path the router reads + recording
    fake repos. Returns (db, po_repo, vendor_repo, audit_repo)."""
    po_repo = FakePORepo()
    vendor_repo = FakeVendorRepo({"V-JJ": {"vendor_id": "V-JJ", "trade_name": "J&J Vision"}})
    audit_repo = FakeAuditRepo()
    monkeypatch.setattr(cl_po, "_get_db", lambda: db)
    monkeypatch.setattr(lens_stock_router, "_get_db", lambda: db)
    monkeypatch.setattr(item_events_router, "_get_db", lambda: db)
    monkeypatch.setattr(cl_po, "get_purchase_order_repository", lambda: po_repo)
    monkeypatch.setattr(cl_po, "get_vendor_repository", lambda: vendor_repo)
    monkeypatch.setattr(cl_po, "get_audit_repository", lambda: audit_repo)
    # S5: generate_po_number now allocates an ATOMIC per-store/FY serial via the
    # shared counters collection. Patch it to a deterministic in-memory atomic
    # counter so the generator yields DISTINCT serials without a live Mongo
    # (the FakeDB has no find_one_and_update; otherwise it fail-softs to a
    # second-grained timestamp that collides within one call).
    from api.routers import vendors as _vendors

    _seqs: Dict[str, int] = {}

    class _AtomicCounters:
        def find_one_and_update(self, q, u, upsert=False, return_document=None):
            k = q.get("_id")
            _seqs[k] = _seqs.get(k, 0) + int(u.get("$inc", {}).get("seq", 1))
            return {"_id": k, "seq": _seqs[k]}

    monkeypatch.setattr(_vendors, "_counters_collection", lambda: _AtomicCounters())
    return db, po_repo, vendor_repo, audit_repo


def _seed_gap_cells(db: FakeDB):
    """Two lens lines below reorder at BV-1: LL-OASYS (vendor V-JJ) and
    LL-NOVEND (no preferred vendor). Plus one healthy cell that must NOT
    appear and one BV-2 cell that must NOT leak."""
    db.get_collection("lens_catalog").docs.extend([
        {"lens_line_id": "LL-OASYS", "brand": "Acuvue", "series": "Oasys",
         "preferred_vendor_id": "V-JJ"},
        {"lens_line_id": "LL-NOVEND", "brand": "Bausch", "series": "SofLens"},
    ])
    db.get_collection("lens_stock_lines").docs.extend([
        # gap = 5 - (2 - 1) = 4
        {"line_stock_id": "C1", "lens_line_id": "LL-OASYS", "store_id": "BV-1",
         "sph": -2.0, "cyl": -0.75, "add": None,
         "on_hand": 2, "reserved": 1, "reorder_point": 5},
        # gap = 3 - 0 = 3
        {"line_stock_id": "C2", "lens_line_id": "LL-NOVEND", "store_id": "BV-1",
         "sph": 1.5, "cyl": 0.0, "add": None,
         "on_hand": 0, "reserved": 0, "reorder_point": 3},
        # healthy -- available(10) >= reorder(5): excluded
        {"line_stock_id": "C3", "lens_line_id": "LL-OASYS", "store_id": "BV-1",
         "sph": -3.0, "cyl": 0.0, "add": None,
         "on_hand": 10, "reserved": 0, "reorder_point": 5},
        # other store -- must never leak into a BV-1 draft
        {"line_stock_id": "C4", "lens_line_id": "LL-OASYS", "store_id": "BV-2",
         "sph": -2.0, "cyl": -0.75, "add": None,
         "on_hand": 0, "reserved": 0, "reorder_point": 5},
    ])


# ============================================================================
# Pure service: build_po_lines -- drop / clamp / merge / describe
# ============================================================================


def test_build_po_lines_drops_zero_negative_and_garbage_qty():
    needs = [
        {"lens_line_id": "LL-A", "sph": -2.0, "qty": 0},
        {"lens_line_id": "LL-A", "sph": -2.0, "qty": -3},
        {"lens_line_id": "LL-A", "sph": -2.0, "qty": "garbage"},
        {"lens_line_id": "LL-A", "sph": -2.0, "qty": None},
    ]
    assert gen.build_po_lines(needs) == []
    assert gen.build_po_lines([]) == []
    assert gen.build_po_lines(None) == []


def test_build_po_lines_coerces_qty_to_int():
    lines = gen.build_po_lines([
        {"lens_line_id": "LL-A", "sph": -2.0, "qty": "4"},
        {"lens_line_id": "LL-B", "sph": 1.0, "qty": 2.7},
    ])
    assert [(ln["product_id"], ln["quantity"]) for ln in lines] == [("LL-A", 4), ("LL-B", 2)]
    assert all(isinstance(ln["quantity"], int) for ln in lines)


def test_build_po_lines_merges_duplicate_cells_summing_qty():
    # "+2.00" (string), 2.0 (float) and 2 (int) are the SAME cell.
    lines = gen.build_po_lines([
        {"lens_line_id": "LL-A", "sph": "+2.00", "cyl": -0.75, "qty": 3},
        {"lens_line_id": "LL-A", "sph": 2.0, "cyl": -0.75, "qty": 2},
        {"lens_line_id": "LL-A", "sph": 2, "cyl": -0.75, "qty": 1},
    ])
    assert len(lines) == 1
    assert lines[0]["quantity"] == 6
    assert lines[0]["power"] == {"sph": 2.0, "cyl": -0.75, "add": None}


def test_build_po_lines_distinct_cells_stay_separate():
    lines = gen.build_po_lines([
        {"lens_line_id": "LL-A", "sph": -2.0, "cyl": -0.75, "qty": 1},
        {"lens_line_id": "LL-A", "sph": -2.0, "cyl": -1.25, "qty": 1},  # other cyl
        {"lens_line_id": "LL-B", "sph": -2.0, "cyl": -0.75, "qty": 1},  # other item
    ])
    assert len(lines) == 3


def test_build_po_lines_human_description():
    lines = gen.build_po_lines([
        {"lens_line_id": "LL-A", "description": "Acuvue Oasys",
         "sph": -2.0, "cyl": -0.75, "qty": 1},
        {"lens_line_id": "LL-B", "description": "Zeiss Progressive",
         "sph": 1.5, "add": 2.0, "qty": 1},
        {"lens_line_id": "LL-C", "description": "Bausch SofLens",
         "sph": -1.0, "cyl": 0.0, "qty": 1},  # zero cyl omitted from text
    ])
    by_id = {ln["lens_line_id"]: ln for ln in lines}
    assert by_id["LL-A"]["description"] == "Acuvue Oasys SPH -2.00 CYL -0.75"
    assert by_id["LL-B"]["description"] == "Zeiss Progressive SPH +1.50 ADD +2.00"
    assert by_id["LL-C"]["description"] == "Bausch SofLens SPH -1.00"
    # but the power DICT still carries the zero cyl (it is data, not prose)
    assert by_id["LL-C"]["power"] == {"sph": -1.0, "cyl": 0.0, "add": None}


def test_build_po_lines_negative_unit_price_clamped():
    lines = gen.build_po_lines([
        {"lens_line_id": "LL-A", "sph": -2.0, "qty": 1, "unit_price": -50},
    ])
    assert lines[0]["unit_price"] == 0.0


# ============================================================================
# Pure service: group_needs_by_vendor
# ============================================================================


def test_group_needs_by_vendor_groups_and_none_bucket():
    needs = [
        {"lens_line_id": "LL-A", "sph": -2.0, "qty": 2},
        {"lens_line_id": "LL-B", "sph": 1.0, "qty": 1},
        {"lens_line_id": "LL-C", "sph": 0.5, "qty": 3},
    ]
    resolver = lambda n: {"LL-A": "V-1", "LL-B": "V-1"}.get(n["lens_line_id"])  # noqa: E731
    groups = gen.group_needs_by_vendor(needs, resolver)
    assert set(groups.keys()) == {"V-1", None}
    assert len(groups["V-1"]) == 2
    assert len(groups[None]) == 1
    assert groups[None][0]["product_id"] == "LL-C"


def test_group_needs_by_vendor_resolver_exception_is_fail_soft():
    def _boom(_need):
        raise RuntimeError("vendor lookup exploded")

    groups = gen.group_needs_by_vendor(
        [{"lens_line_id": "LL-A", "sph": -2.0, "qty": 1}], _boom
    )
    assert set(groups.keys()) == {None}
    assert groups[None][0]["quantity"] == 1


def test_group_needs_by_vendor_omits_groups_that_fully_drop():
    needs = [
        {"lens_line_id": "LL-A", "sph": -2.0, "qty": 0},   # drops -> group gone
        {"lens_line_id": "LL-B", "sph": 1.0, "qty": 2},
    ]
    resolver = lambda n: "V-EMPTY" if n["lens_line_id"] == "LL-A" else "V-OK"  # noqa: E731
    groups = gen.group_needs_by_vendor(needs, resolver)
    assert set(groups.keys()) == {"V-OK"}
    assert gen.group_needs_by_vendor([], lambda n: None) == {}


# ============================================================================
# Router: role gate + store scope + validation
# ============================================================================


def test_role_gate_rejects_sales_staff_allows_manager():
    assert set(cl_po._PO_ROLES) == {"STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"}
    dep = require_roles(*cl_po._PO_ROLES)
    with pytest.raises(HTTPException) as exc:
        _run(dep(current_user=_staff()))
    assert exc.value.status_code == 403
    assert _run(dep(current_user=_manager()))["user_id"] == "M1"


def test_rbac_policy_row_matches_inline_gate():
    from api.services.rbac_policy import policy_for

    row = policy_for("POST", "/api/v1/cl-po/generate")
    assert row is not None, "POST /api/v1/cl-po/generate missing from RBAC POLICY"
    assert set(row["allowed"]) == set(cl_po._PO_ROLES)
    assert row.get("store_scoped") is True


def test_cross_store_manager_gets_403(env):
    body = cl_po.CLPOGenerateRequest(store_id="BV-2", source="gap-planner")
    with pytest.raises(HTTPException) as exc:
        _run(cl_po.generate_cl_po(body, current_user=_manager(store="BV-1")))
    assert exc.value.status_code == 403


def test_invalid_source_and_bad_grid_422(env):
    with pytest.raises(HTTPException) as exc:
        _run(cl_po.generate_cl_po(
            cl_po.CLPOGenerateRequest(store_id="BV-1", source="ouija-board"),
            current_user=_manager(),
        ))
    assert exc.value.status_code == 422
    # replenishment without a valid grid is a 422, not a silent empty draft
    for bad_grid in (None, "BOGUS"):
        with pytest.raises(HTTPException) as exc:
            _run(cl_po.generate_cl_po(
                cl_po.CLPOGenerateRequest(store_id="BV-1", source="replenishment",
                                          grid=bad_grid),
                current_user=_manager(),
            ))
        assert exc.value.status_code == 422


# ============================================================================
# Router: dry_run safety -- the DEFAULT writes NOTHING
# ============================================================================


def test_dry_run_is_default_and_writes_nothing(env):
    db, po_repo, _, audit_repo = env
    _seed_gap_cells(db)
    body = cl_po.CLPOGenerateRequest(store_id="BV-1", source="gap-planner")
    assert body.dry_run is True  # safe default

    res = _run(cl_po.generate_cl_po(body, current_user=_manager()))

    assert res["dry_run"] is True
    assert res["pos_created"] == 0 and res["created_pos"] == []
    assert res["total_lines"] == 2  # the two below-reorder BV-1 cells only
    # NOTHING was written anywhere.
    assert po_repo.created == []
    assert audit_repo.rows == []
    # the draft is vendor-grouped: V-JJ + the vendor-less (None) group
    assert {g["vendor_id"] for g in res["groups"]} == {"V-JJ", None}


def test_dry_run_draft_never_leaks_other_store(env):
    db, _, _, _ = env
    _seed_gap_cells(db)
    res = _run(cl_po.generate_cl_po(
        cl_po.CLPOGenerateRequest(store_id="BV-1", source="gap-planner"),
        current_user=_manager(),
    ))
    all_lines = [ln for g in res["groups"] for ln in g["lines"]]
    # BV-2's C4 cell (same line + power as C1) would inflate C1's qty to 9 if
    # the store filter leaked; the real gap for BV-1 C1 is 4.
    oasys = [ln for ln in all_lines if ln["lens_line_id"] == "LL-OASYS"]
    assert len(oasys) == 1 and oasys[0]["quantity"] == 4


# ============================================================================
# Router: non-dry-run creates DRAFT POs with power lines + one audit row
# ============================================================================


def test_non_dry_run_creates_one_draft_po_per_vendor_group(env):
    db, po_repo, _, audit_repo = env
    _seed_gap_cells(db)
    res = _run(cl_po.generate_cl_po(
        cl_po.CLPOGenerateRequest(store_id="BV-1", source="gap-planner", dry_run=False),
        current_user=_manager(),
    ))

    assert res["pos_created"] == 2
    assert len(po_repo.created) == 2
    by_vendor = {p.get("vendor_id"): p for p in po_repo.created}
    assert set(by_vendor.keys()) == {"V-JJ", None}

    # DRAFT only -- a generator that auto-sends is a money-path bug.
    assert all(p["status"] == "DRAFT" for p in po_repo.created)
    assert all(p.get("sent_at") is None for p in po_repo.created)

    jj = by_vendor["V-JJ"]
    assert jj["vendor_name"] == "J&J Vision"
    assert jj["delivery_store_id"] == "BV-1"
    assert jj["created_by"] == "M1"
    assert jj["po_number"].startswith("PO/BV-1/")  # S5 per-store/FY serial
    line = jj["items"][0]
    assert line["power"] == {"sph": -2.0, "cyl": -0.75, "add": None}
    assert line["quantity"] == 4
    assert line["description"] == "Acuvue Oasys SPH -2.00 CYL -0.75"

    # vendor-less group drafts with vendor_id=None (FE disables send)
    novend = by_vendor[None]
    assert novend["vendor_name"] is None
    assert novend["items"][0]["power"]["sph"] == 1.5

    # distinct po_numbers within one call
    assert len({p["po_number"] for p in po_repo.created}) == 2

    # exactly ONE audit row, fail-soft pattern, covering both POs
    assert len(audit_repo.rows) == 1
    row = audit_repo.rows[0]
    assert row["action"] == "CL_PO_GENERATED"
    assert row["entity_type"] == "purchase_order"
    assert row["store_id"] == "BV-1" and row["user_id"] == "M1"
    assert row["detail"]["pos_created"] == 2


def test_audit_failure_does_not_undo_creation(env, monkeypatch):
    db, po_repo, _, _ = env
    _seed_gap_cells(db)

    class ExplodingAudit:
        def create(self, row):
            raise RuntimeError("audit store down")

    monkeypatch.setattr(cl_po, "get_audit_repository", lambda: ExplodingAudit())
    res = _run(cl_po.generate_cl_po(
        cl_po.CLPOGenerateRequest(store_id="BV-1", source="gap-planner", dry_run=False),
        current_user=_manager(),
    ))
    assert res["pos_created"] == 2
    assert len(po_repo.created) == 2


# ============================================================================
# Router: replenishment (Base-Bank) source -- power cells from cell_key
# ============================================================================


def test_replenishment_source_drafts_power_lines(env):
    db, _, _, _ = env
    db.get_collection("base_bank_targets").docs.extend([
        {"scope": "STORE", "store_id": "BV-1", "grid": "CL_POWER",
         "cell_key": "-2.00", "base_bank": 5, "product_line_id": "LL-OASYS"},
        {"scope": "STORE", "store_id": "BV-1", "grid": "CL_POWER",
         "cell_key": "+1.00", "base_bank": 2, "product_line_id": "LL-OASYS"},
    ])
    db.get_collection("lens_catalog").docs.append(
        {"lens_line_id": "LL-OASYS", "brand": "Acuvue", "series": "Oasys",
         "preferred_vendor_id": "V-JJ"})
    # 2 sellable units at -2.00 -> required 3; 2 at +1.00 -> required 0 (full)
    db.get_collection("stock_units").docs.extend([
        {"store_id": "BV-1", "product_id": "LL-OASYS", "status": "AVAILABLE", "power": -2.0},
        {"store_id": "BV-1", "product_id": "LL-OASYS", "status": "AVAILABLE", "power": "-2.00"},
        {"store_id": "BV-1", "product_id": "LL-OASYS", "status": "AVAILABLE", "power": 1.0},
        {"store_id": "BV-1", "product_id": "LL-OASYS", "status": "AVAILABLE", "power": "+1.00"},
        # quarantined unit must NOT count as in-hand
        {"store_id": "BV-1", "product_id": "LL-OASYS", "status": "QUARANTINED", "power": -2.0},
    ])

    res = _run(cl_po.generate_cl_po(
        cl_po.CLPOGenerateRequest(store_id="BV-1", source="replenishment",
                                  grid="cl_power"),  # case-insensitive grid
        current_user=_manager(),
    ))

    assert res["grid"] == "CL_POWER"
    assert res["total_lines"] == 1  # the full +1.00 cell drops (required 0)
    assert [g["vendor_id"] for g in res["groups"]] == ["V-JJ"]
    line = res["groups"][0]["lines"][0]
    assert line["power"]["sph"] == -2.0
    assert line["quantity"] == 3
    assert line["description"] == "Acuvue Oasys SPH -2.00"


# ============================================================================
# DB-absent fail-soft
# ============================================================================


def test_db_absent_fail_soft_returns_empty_draft(monkeypatch):
    monkeypatch.setattr(cl_po, "_get_db", lambda: None)
    monkeypatch.setattr(lens_stock_router, "_get_db", lambda: None)
    monkeypatch.setattr(item_events_router, "_get_db", lambda: None)
    monkeypatch.setattr(cl_po, "get_purchase_order_repository", lambda: None)
    monkeypatch.setattr(cl_po, "get_vendor_repository", lambda: None)
    monkeypatch.setattr(cl_po, "get_audit_repository", lambda: None)

    for source, grid in (("gap-planner", None), ("replenishment", "CL_POWER")):
        res = _run(cl_po.generate_cl_po(
            cl_po.CLPOGenerateRequest(store_id="BV-1", source=source, grid=grid),
            current_user=_manager(),
        ))
        assert res["groups"] == [] and res["total_lines"] == 0

    # non-dry-run with no PO storage: says so loudly, writes nothing, no 500
    res = _run(cl_po.generate_cl_po(
        cl_po.CLPOGenerateRequest(store_id="BV-1", source="gap-planner", dry_run=False),
        current_user=_manager(),
    ))
    assert res["pos_created"] == 0
    assert "nothing was written" in res["message"].lower()
