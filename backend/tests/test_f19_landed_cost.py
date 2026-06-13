"""
IMS 2.0 - F19 Dynamic landed-cost purchase matrix tests (intent-level)
======================================================================
Exercises the REAL landed_cost allocation engine + the purchase_invoices
landed-cost routes against a faithful in-memory fake Mongo (no network, no
live mongod). A hollow shell that mis-apportions a paisa, lets an allocated
bill be re-costed, writes during a preview, skips the period lock, or lets a
sales role capture freight FAILS here.

Maps to the F19 acceptance intents:
  * paise-exact allocation -- BY_VALUE / BY_QTY proportional shares whose sum
    EXACTLY equals the component total, with the whole residual handed to the
    largest-base line (awkward splits like 100001 over 3 equal lines included)
  * fail-loud inputs       -- BY_WEIGHT with a missing weight, an unknown
    method, or a negative component amount raises (never silently mis-costs)
  * preview is pure        -- the dry-run endpoint writes NOTHING
  * one-way allocation     -- set-components 409s after allocation; two
    allocations -> exactly ONE winner (guarded find_one_and_update)
  * persistence            -- per-line landed_* fields land on the bill lines;
    the product roll-in writes landed_cost/landed_cost_paise, NEVER cost_price
  * period lock            -- a locked posting month 423s capture + allocation
  * RBAC                   -- ACCOUNTANT/ADMIN only (rows + dependency 403)
  * DB-absent              -- 503, never a silent no-op on a money path

NO emoji in this file (Windows cp1252).
"""

from __future__ import annotations

import asyncio
import copy
import os
import sys
from typing import Any, Dict, List

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from fastapi import HTTPException  # noqa: E402

from api.services import landed_cost as lc  # noqa: E402
from api.routers import purchase_invoices as r  # noqa: E402


# ============================================================================
# Faithful in-memory fake Mongo (only the operators F19 uses)
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


def _apply_update(doc: Dict[str, Any], update: Dict[str, Any]) -> None:
    for op, fields in update.items():
        if op == "$set":
            for kk, vv in fields.items():
                doc[kk] = vv
        elif op == "$inc":
            for kk, vv in fields.items():
                doc[kk] = (doc.get(kk) or 0) + vv


class FakeCursor:
    def __init__(self, docs: List[Dict[str, Any]]):
        self._docs = docs

    def __iter__(self):
        return iter(self._docs)


class FakeCollection:
    def __init__(self):
        self.docs: List[Dict[str, Any]] = []
        self._n = 0

    def insert_one(self, doc):
        doc.setdefault("_id", f"oid-{self._n}")
        self._n += 1
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc["_id"]})()

    def find_one(self, query, projection=None):
        for d in self.docs:
            if _matches(d, query):
                return {k: v for k, v in d.items() if k != "_id"}
        return None

    def find(self, query=None, projection=None):
        return FakeCursor([dict(d) for d in self.docs if _matches(d, query or {})])

    def find_one_and_update(self, query, update, return_document=None, upsert=False, **_kw):
        for d in self.docs:
            if _matches(d, query):
                _apply_update(d, update)
                return dict(d)
        return None

    def update_one(self, query, update, upsert=False):
        for d in self.docs:
            if _matches(d, query):
                _apply_update(d, update)
                return type("R", (), {"matched_count": 1, "modified_count": 1})()
        if upsert:
            doc = {k: v for k, v in query.items() if not str(k).startswith("$")}
            _apply_update(doc, update)
            self.insert_one(doc)
        return type("R", (), {"matched_count": 0, "modified_count": 0})()

    def create_index(self, *a, **k):
        return "idx"


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


# ============================================================================
# Fixtures + helpers
# ============================================================================


@pytest.fixture()
def db() -> FakeDB:
    return FakeDB()


def _acct(uid="AC1"):
    return {"user_id": uid, "full_name": "Accountant", "roles": ["ACCOUNTANT"]}


def _sales(uid="S1"):
    return {"user_id": uid, "full_name": "Sales", "roles": ["SALES_STAFF"]}


def _seed_bill(db, **over):
    """A booked PURCHASE_INVOICE row in vendor_bills, shaped like create()
    writes it (taxable in RUPEES on each line)."""
    doc = {
        "bill_id": "PI-1",
        "invoice_id": "PI-1",
        "doc_type": "PURCHASE_INVOICE",
        "vendor_id": "V-1",
        "bill_number": "INV-001",
        "invoice_number": "INV-001",
        "bill_date": "2026-05-15",
        "invoice_date": "2026-05-15",
        "lines": [
            {"product_id": "P-A", "qty": 2, "unit_price": 100.0, "taxable": 200.0},
            {"product_id": "P-B", "qty": 1, "unit_price": 300.0, "taxable": 300.0},
        ],
        "status": "OUTSTANDING",
    }
    doc.update(over)
    db.get_collection("vendor_bills").insert_one(dict(doc))
    return doc["bill_id"]


def _freight(paise=10000):
    return [{"type": "FREIGHT", "label": "Courier", "amount_paise": paise}]


def _set_body(components=None, method="BY_VALUE"):
    comps = components if components is not None else [
        r.LandedCostComponent(type="FREIGHT", label="Courier", amount_paise=10000)
    ]
    return r.LandedCostsSet(components=comps, allocation_method=method)


def _stored(db, bill_id="PI-1"):
    return db.get_collection("vendor_bills").find_one({"bill_id": bill_id})


# ============================================================================
# ENGINE -- proportional shares, residual-to-largest, sum invariant
# ============================================================================


def test_by_value_proportional_allocation():
    lines = [
        {"product_id": "P-A", "qty": 2, "taxable": 200.0},  # 20000 paise
        {"product_id": "P-B", "qty": 1, "taxable": 300.0},  # 30000 paise
    ]
    rows = lc.allocate_landed_costs(lines, _freight(10000), "BY_VALUE")
    assert [row["landed_alloc_paise"] for row in rows] == [4000, 6000]
    # per-unit + landed unit cost: base 10000 + 2000 / base 30000 + 6000.
    assert rows[0]["landed_per_unit_paise"] == 2000
    assert rows[0]["landed_unit_cost_paise"] == 12000
    assert rows[1]["landed_unit_cost_paise"] == 36000
    assert sum(row["landed_alloc_paise"] for row in rows) == 10000


def test_by_qty_proportional_allocation():
    lines = [
        {"product_id": "P-A", "qty": 1, "taxable": 900.0},
        {"product_id": "P-B", "qty": 3, "taxable": 100.0},
    ]
    rows = lc.allocate_landed_costs(lines, _freight(8000), "BY_QTY")
    # qty 1:3 -> 2000 / 6000 regardless of value.
    assert [row["landed_alloc_paise"] for row in rows] == [2000, 6000]


def test_awkward_split_residual_to_largest_base_sum_exact():
    """100001 paise over 3 equal-value lines: floor gives 33333 each (99999);
    the WHOLE 2-paise residual goes to the first largest-base line."""
    lines = [
        {"product_id": f"P-{i}", "qty": 1, "taxable": 100.0} for i in range(3)
    ]
    rows = lc.allocate_landed_costs(lines, _freight(100001), "BY_VALUE")
    allocs = [row["landed_alloc_paise"] for row in rows]
    assert allocs == [33335, 33333, 33333]
    assert sum(allocs) == 100001


def test_sum_invariant_and_per_unit_identity_on_awkward_splits():
    """For a spread of awkward totals over unequal bases: the allocation sums
    EXACTLY to the total, and per line alloc == per_unit * qty + remainder."""
    lines = [
        {"product_id": "P-A", "qty": 3, "taxable": 333.33},
        {"product_id": "P-B", "qty": 7, "taxable": 123.45},
        {"product_id": "P-C", "qty": 2, "taxable": 999.99},
    ]
    for total in (1, 7, 99999, 100001, 123456789):
        rows = lc.allocate_landed_costs(lines, _freight(total), "BY_VALUE")
        assert sum(row["landed_alloc_paise"] for row in rows) == total
        for row, ln in zip(rows, lines):
            assert (
                row["landed_per_unit_paise"] * int(ln["qty"])
                + row["landed_remainder_paise"]
                == row["landed_alloc_paise"]
            )


def test_by_weight_missing_weight_raises():
    lines = [
        {"product_id": "P-A", "qty": 1, "taxable": 100.0, "weight": 2.5},
        {"product_id": "P-B", "qty": 1, "taxable": 100.0},  # no weight
    ]
    with pytest.raises(ValueError):
        lc.allocate_landed_costs(lines, _freight(1000), "BY_WEIGHT")


def test_unknown_method_and_negative_component_raise():
    lines = [{"product_id": "P-A", "qty": 1, "taxable": 100.0}]
    with pytest.raises(ValueError):
        lc.allocate_landed_costs(lines, _freight(1000), "BY_VIBES")
    with pytest.raises(ValueError):
        lc.allocate_landed_costs(lines, _freight(-5), "BY_VALUE")


def test_zero_components_yield_zero_rows_not_error():
    lines = [{"product_id": "P-A", "qty": 2, "taxable": 200.0}]
    rows = lc.allocate_landed_costs(lines, [], "BY_VALUE")
    assert rows[0]["landed_alloc_paise"] == 0
    assert rows[0]["landed_unit_cost_paise"] == 10000  # base cost intact


def test_landed_unit_cost_by_product_aggregates_repeated_sku():
    """A bill CAN repeat a SKU; the per-product landed unit cost averages
    across every line of that product."""
    lines = [
        {"product_id": "P-A", "qty": 1, "taxable": 100.0},
        {"product_id": "P-A", "qty": 1, "taxable": 200.0},
    ]
    rows = lc.allocate_landed_costs(lines, _freight(3000), "BY_VALUE")
    per = lc.landed_unit_cost_by_product(lines, rows)
    # (10000 + 20000 value + 3000 alloc) // 2 units = 16500
    assert per == {"P-A": 16500}


# ============================================================================
# ROUTER -- capture (set/replace), 400s, one-way 409
# ============================================================================


def test_set_components_persists_and_normalises(db, monkeypatch):
    monkeypatch.setattr(r, "_get_db", lambda: db)
    _seed_bill(db)
    body = r.LandedCostsSet(
        components=[
            r.LandedCostComponent(type="freight", label="Courier", amount_paise=7000),
            r.LandedCostComponent(type="Duty", amount_paise=3000),
        ],
        allocation_method="by_value",
    )
    out = asyncio.run(r.set_invoice_landed_costs("PI-1", body, current_user=_acct()))
    assert out["landed_cost_total_paise"] == 10000
    assert out["allocation_method"] == "BY_VALUE"  # validator normalised case
    assert [c["type"] for c in out["landed_cost_components"]] == ["FREIGHT", "DUTY"]
    stored = _stored(db)
    assert stored["landed_cost_total_paise"] == 10000
    assert stored["landed_cost_allocated"] is False
    assert stored["landed_cost_captured_by"] == "AC1"


def test_set_components_empty_is_400(db, monkeypatch):
    monkeypatch.setattr(r, "_get_db", lambda: db)
    _seed_bill(db)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            r.set_invoice_landed_costs(
                "PI-1", r.LandedCostsSet(components=[]), current_user=_acct()
            )
        )
    assert exc.value.status_code == 400
    assert "landed_cost_components" not in (_stored(db) or {})


def test_set_components_409_after_allocation(db, monkeypatch):
    monkeypatch.setattr(r, "_get_db", lambda: db)
    _seed_bill(db)
    asyncio.run(r.set_invoice_landed_costs("PI-1", _set_body(), current_user=_acct()))
    asyncio.run(r.allocate_invoice_landed_costs("PI-1", current_user=_acct()))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            r.set_invoice_landed_costs("PI-1", _set_body(), current_user=_acct())
        )
    assert exc.value.status_code == 409
    # the allocated components were NOT rewritten.
    assert _stored(db)["landed_cost_allocated"] is True


# ============================================================================
# ROUTER -- preview is pure (NO writes)
# ============================================================================


def test_preview_returns_breakdown_and_writes_nothing(db, monkeypatch):
    monkeypatch.setattr(r, "_get_db", lambda: db)
    _seed_bill(
        db,
        landed_cost_components=_freight(10000),
        allocation_method="BY_VALUE",
    )
    before = copy.deepcopy(db.get_collection("vendor_bills").docs)
    out = asyncio.run(r.preview_invoice_landed_costs("PI-1", current_user=_acct()))
    assert out["landed_cost_total_paise"] == 10000
    assert [row["landed_alloc_paise"] for row in out["allocation"]] == [4000, 6000]
    assert out["landed_unit_cost_by_product_paise"] == {"P-A": 12000, "P-B": 36000}
    assert out["landed_cost_allocated"] is False
    # NOTHING was written -- the stored docs are byte-identical.
    assert db.get_collection("vendor_bills").docs == before


def test_preview_400_when_nothing_captured(db, monkeypatch):
    monkeypatch.setattr(r, "_get_db", lambda: db)
    _seed_bill(db)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(r.preview_invoice_landed_costs("PI-1", current_user=_acct()))
    assert exc.value.status_code == 400


def test_preview_400_on_engine_rejection_by_weight(db, monkeypatch):
    """BY_WEIGHT with a missing line weight surfaces the engine's fail-loud
    ValueError as a 400 (never a 500, never a silent zero)."""
    monkeypatch.setattr(r, "_get_db", lambda: db)
    _seed_bill(
        db,
        landed_cost_components=_freight(10000),
        allocation_method="BY_WEIGHT",
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(r.preview_invoice_landed_costs("PI-1", current_user=_acct()))
    assert exc.value.status_code == 400
    assert "weight" in str(exc.value.detail).lower()


# ============================================================================
# ROUTER -- one-way allocation (exactly one winner) + persistence
# ============================================================================


def test_allocate_persists_per_line_fields_and_flag(db, monkeypatch):
    monkeypatch.setattr(r, "_get_db", lambda: db)
    _seed_bill(db)
    asyncio.run(r.set_invoice_landed_costs("PI-1", _set_body(), current_user=_acct()))
    out = asyncio.run(r.allocate_invoice_landed_costs("PI-1", current_user=_acct()))
    assert out["landed_cost_allocated"] is True
    stored = _stored(db)
    assert stored["landed_cost_allocated"] is True
    assert stored["landed_cost_allocated_by"] == "AC1"
    allocs = [ln["landed_alloc_paise"] for ln in stored["lines"]]
    assert allocs == [4000, 6000] and sum(allocs) == 10000
    for ln in stored["lines"]:
        for key in (
            "landed_alloc_paise",
            "landed_per_unit_paise",
            "landed_remainder_paise",
            "landed_unit_cost_paise",
        ):
            assert key in ln
        # the pre-existing line keys survived the merge.
        assert "taxable" in ln and "unit_price" in ln
    assert stored["landed_cost_allocation"][0]["landed_unit_cost_paise"] == 12000


def test_allocate_twice_exactly_one_winner(db, monkeypatch):
    monkeypatch.setattr(r, "_get_db", lambda: db)
    _seed_bill(db)
    asyncio.run(r.set_invoice_landed_costs("PI-1", _set_body(), current_user=_acct()))
    first = asyncio.run(r.allocate_invoice_landed_costs("PI-1", current_user=_acct()))
    assert first["landed_cost_allocated"] is True
    stamp = _stored(db)["landed_cost_allocated_at"]
    with pytest.raises(HTTPException) as exc:
        asyncio.run(r.allocate_invoice_landed_costs("PI-1", current_user=_acct("AC2")))
    assert exc.value.status_code == 409
    after = _stored(db)
    # the winner's allocation is untouched by the loser.
    assert after["landed_cost_allocated_at"] == stamp
    assert after["landed_cost_allocated_by"] == "AC1"


def test_allocate_race_loser_409_via_guarded_write(db, monkeypatch):
    """Simulate the true race: the loser read the bill BEFORE the winner's flip
    (stale doc passes the fast-path), so only the guarded find_one_and_update
    stands between it and a double allocation. It must 409."""
    monkeypatch.setattr(r, "_get_db", lambda: db)
    _seed_bill(db)
    asyncio.run(r.set_invoice_landed_costs("PI-1", _set_body(), current_user=_acct()))
    stale = dict(_stored(db))  # landed_cost_allocated still False here
    asyncio.run(r.allocate_invoice_landed_costs("PI-1", current_user=_acct()))
    monkeypatch.setattr(
        r, "_load_purchase_invoice_or_404", lambda _db, _id: dict(stale)
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(r.allocate_invoice_landed_costs("PI-1", current_user=_acct("AC2")))
    assert exc.value.status_code == 409
    assert _stored(db)["landed_cost_allocated_by"] == "AC1"


def test_allocate_400_when_no_components_captured(db, monkeypatch):
    monkeypatch.setattr(r, "_get_db", lambda: db)
    _seed_bill(db)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(r.allocate_invoice_landed_costs("PI-1", current_user=_acct()))
    assert exc.value.status_code == 400
    assert not _stored(db).get("landed_cost_allocated")


# ============================================================================
# ROUTER -- product roll-in writes landed_cost, NEVER cost_price
# ============================================================================


def test_product_rollin_writes_landed_cost_not_cost_price(db, monkeypatch):
    monkeypatch.setattr(r, "_get_db", lambda: db)
    _seed_bill(db)
    db.get_collection("products").insert_one(
        {"product_id": "P-A", "cost_price": 95.0}
    )
    db.get_collection("products").insert_one(
        {"product_id": "P-B", "cost_price": 290.0}
    )
    asyncio.run(r.set_invoice_landed_costs("PI-1", _set_body(), current_user=_acct()))
    out = asyncio.run(r.allocate_invoice_landed_costs("PI-1", current_user=_acct()))
    assert {p["product_id"]: p["landed_unit_cost_paise"] for p in out["product_rollin"]} == {
        "P-A": 12000,
        "P-B": 36000,
    }
    pa = db.get_collection("products").find_one({"product_id": "P-A"})
    assert pa["landed_cost_paise"] == 12000
    assert pa["landed_cost"] == 120.0
    assert pa["landed_cost_source"] == "LANDED_COST_ALLOCATION"
    # the AVCO cost_price is owned by the booking-time flow -- UNTOUCHED here.
    assert pa["cost_price"] == 95.0
    assert db.get_collection("products").find_one({"product_id": "P-B"})[
        "cost_price"
    ] == 290.0


# ============================================================================
# ROUTER -- period lock blocks capture + allocation (423)
# ============================================================================


def test_period_lock_blocks_set_and_allocate(db, monkeypatch):
    monkeypatch.setattr(r, "_get_db", lambda: db)
    _seed_bill(db)  # invoice_date 2026-05-15
    db.get_collection("period_locks").insert_one({"month": 5, "year": 2026})
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            r.set_invoice_landed_costs("PI-1", _set_body(), current_user=_acct())
        )
    assert exc.value.status_code == 423
    # seed the components directly, then allocation must ALSO be blocked.
    db.get_collection("vendor_bills").update_one(
        {"bill_id": "PI-1"},
        {"$set": {"landed_cost_components": _freight(10000),
                  "allocation_method": "BY_VALUE"}},
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(r.allocate_invoice_landed_costs("PI-1", current_user=_acct()))
    assert exc.value.status_code == 423
    assert not _stored(db).get("landed_cost_allocated")


# ============================================================================
# RBAC -- policy rows + dependency 403
# ============================================================================


def test_rbac_policy_rows_gate_all_three_routes():
    from api.services import rbac_policy

    base = "/api/v1/vendors/purchase-invoices/{invoice_id}"
    expected = {
        ("POST", base + "/landed-costs"),
        ("GET", base + "/landed-costs/preview"),
        ("POST", base + "/allocate-landed-costs"),
    }
    found = {
        (row["method"], row["path"]): row["allowed"]
        for row in rbac_policy.POLICY
        if (row["method"], row["path"]) in expected
    }
    assert set(found) == expected
    for allowed in found.values():
        assert allowed == ["ACCOUNTANT", "ADMIN"]


def test_require_roles_dependency_403_for_sales_staff():
    dep = r.require_roles(*r._AP_ROLES)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(dep(current_user=_sales()))
    assert exc.value.status_code == 403
    # ACCOUNTANT passes the same gate.
    assert asyncio.run(dep(current_user=_acct()))["user_id"] == "AC1"


# ============================================================================
# DB-absent -- 503, never a silent no-op on a money path
# ============================================================================


def test_db_absent_is_503_on_all_three_routes(monkeypatch):
    monkeypatch.setattr(r, "_get_db", lambda: None)
    for call in (
        lambda: asyncio.run(
            r.set_invoice_landed_costs("PI-1", _set_body(), current_user=_acct())
        ),
        lambda: asyncio.run(
            r.preview_invoice_landed_costs("PI-1", current_user=_acct())
        ),
        lambda: asyncio.run(
            r.allocate_invoice_landed_costs("PI-1", current_user=_acct())
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            call()
        assert exc.value.status_code == 503


# ============================================================================
# Adversarial-pass fixes -- fail-closed period lock + pinned capture version
# ============================================================================


def test_missing_posting_date_fails_closed_422(db, monkeypatch):
    """A bill with NO posting date cannot have its period-lock checked, so the
    landed-cost mutations must 422 (fail loudly), not silently bypass the lock."""
    monkeypatch.setattr(r, "_get_db", lambda: db)
    _seed_bill(db, bill_date=None, invoice_date=None)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(r.set_invoice_landed_costs("PI-1", _set_body(), current_user=_acct()))
    assert exc.value.status_code == 422
    _seed_bill(db, bill_id="PI-2", invoice_id="PI-2", bill_date="", invoice_date="")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(r.allocate_invoice_landed_costs("PI-2", current_user=_acct()))
    assert exc.value.status_code == 422


def test_allocate_409_when_capture_changed_after_read(db, monkeypatch):
    """The allocate guard pins the components+method it computed from: if an
    interleaved set-components lands between allocate's read and its write,
    the guarded filter misses -> 409 -- a persisted allocation can never
    disagree with the stored capture it claims to derive from."""
    monkeypatch.setattr(r, "_get_db", lambda: db)
    _seed_bill(db)
    asyncio.run(r.set_invoice_landed_costs("PI-1", _set_body(), current_user=_acct()))
    stale = _stored(db)
    stale = dict(stale)
    stale["landed_cost_components"] = [
        {"type": "FREIGHT", "label": "Courier", "amount_paise": 99999}
    ]  # what allocate THINKS is stored (another writer changed it after this read)
    monkeypatch.setattr(r, "_load_purchase_invoice_or_404", lambda _db, _id: stale)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(r.allocate_invoice_landed_costs("PI-1", current_user=_acct()))
    assert exc.value.status_code == 409
    # the stored bill is untouched -- still unallocated, components intact.
    kept = _stored(db)
    assert not kept.get("landed_cost_allocated")
    assert kept["landed_cost_components"][0]["amount_paise"] == 10000
