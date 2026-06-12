"""
IMS 2.0 - N8 owner "survival" cash-flow tests (intent-level)
============================================================
Exercises the REAL survival_cashflow service + the finance router wiring
against a faithful in-memory fake Mongo (no network, no live mongod).

Maps to the N8 acceptance intents:
  * classification      -- essential heads match case/space-insensitively;
                           short statutory keywords (pf/esi/gst) match whole
                           words only; unknown heads default DEFERRABLE
  * AP MUST_PAY         -- overdue (any vendor) / undatable; due-within-7d
                           only when the vendor is critical (flag or E2 list)
  * min-pay math        -- integer paise, rupees converted once at the
                           boundary; min_pay = fixed + must_pay exactly
  * gap semantics       -- survival_gap_paise = max(0, min_pay - income),
                           surplus_paise mirrors the other side
  * reconciliation      -- every input paisa lands in exactly one bucket
  * E2 override         -- finance.survival_essential_heads /
                           finance.survival_critical_vendors flow through
  * dead path revived   -- GET /finance/budget?mode=survival returns the REAL
                           survival view, not the empty no_budget_set skeleton
  * role gates          -- dedicated route 403s STORE_MANAGER / SALES_STAFF
  * fail-soft           -- DB absent -> all-zero view, never a 500

Read-only guarantee: the router path under test never writes to any
collection (asserted via a write-recording fake).
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from api.services import survival_cashflow as svc  # noqa: E402
import api.routers.finance as fin  # noqa: E402


# A fixed mid-month IST "now": June 2026 has 30 days, day 15 -> the income
# pro-ration factor is exactly 2.0 (no float dust in expectations).
FIXED_NOW = datetime(2026, 6, 15, 12, 0, 0)


# ============================================================================
# Faithful in-memory fake Mongo (find + the aggregate stages finance uses)
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


def _eval_expr(doc: Dict[str, Any], expr: Any) -> Any:
    """Evaluate the tiny expression subset finance aggregations use:
    "$field" references and {"$ifNull": [expr, fallback]}."""
    if isinstance(expr, str) and expr.startswith("$"):
        return doc.get(expr[1:])
    if isinstance(expr, dict) and "$ifNull" in expr:
        primary, fallback = expr["$ifNull"]
        v = _eval_expr(doc, primary)
        return _eval_expr(doc, fallback) if v is None else v
    return expr


class FakeCursor:
    def __init__(self, docs: List[Dict[str, Any]]):
        self._docs = docs

    def __iter__(self):
        return iter(self._docs)


class FakeCollection:
    def __init__(self):
        self.docs: List[Dict[str, Any]] = []
        self.write_calls = 0  # read-only guarantee counter

    def insert_one(self, doc):  # only used by test SETUP, via seed()
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": "x"})()

    def find_one(self, query, projection=None):
        for d in self.docs:
            if _matches(d, query):
                return dict(d)
        return None

    def find(self, query=None, projection=None):
        return FakeCursor([dict(d) for d in self.docs if _matches(d, query or {})])

    def aggregate(self, pipeline):
        docs = [dict(d) for d in self.docs]
        for stage in pipeline:
            if "$match" in stage:
                docs = [d for d in docs if _matches(d, stage["$match"])]
            elif "$group" in stage:
                spec = stage["$group"]
                groups: Dict[Any, Dict[str, Any]] = {}
                order: List[Any] = []
                for d in docs:
                    key = _eval_expr(d, spec["_id"]) if spec["_id"] is not None else None
                    if key not in groups:
                        groups[key] = {"_id": key}
                        order.append(key)
                    g = groups[key]
                    for field, agg in spec.items():
                        if field == "_id":
                            continue
                        if isinstance(agg, dict) and "$sum" in agg:
                            v = _eval_expr(d, agg["$sum"])
                            g[field] = g.get(field, 0) + (v or 0)
                docs = [groups[k] for k in order]
        return iter(docs)

    # Any mutation beyond seeding is a violation of N8's read-only guarantee.
    def update_one(self, *a, **k):
        self.write_calls += 1
        raise AssertionError("N8 survival view must never write")

    def find_one_and_update(self, *a, **k):
        self.write_calls += 1
        raise AssertionError("N8 survival view must never write")

    def delete_one(self, *a, **k):
        self.write_calls += 1
        raise AssertionError("N8 survival view must never write")


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


@pytest.fixture()
def routed(db, monkeypatch):
    """Route the finance module at the fake DB with a frozen IST clock and
    policy reads resolving to registry defaults (no live Mongo)."""
    monkeypatch.setattr(fin, "_get_db", lambda: db)
    monkeypatch.setattr(fin, "now_ist_naive", lambda: FIXED_NOW)
    monkeypatch.setattr(fin, "now_ist", lambda: FIXED_NOW)
    monkeypatch.setattr(
        fin.policy_engine,
        "get_policy",
        lambda key, scope=None, default=None: default,
    )
    return db


def _accountant(uid="ACC1"):
    return {"user_id": uid, "full_name": "Books", "roles": ["ACCOUNTANT"],
            "store_ids": [], "active_store_id": None}


def _store_manager(uid="M1", store="BV-1"):
    return {"user_id": uid, "full_name": "Manager", "roles": ["STORE_MANAGER"],
            "store_ids": [store], "active_store_id": store}


def _sales(uid="S1", store="BV-1"):
    return {"user_id": uid, "full_name": "Sales", "roles": ["SALES_STAFF"],
            "store_ids": [store], "active_store_id": store}


def _expense(category, amount, store="BV-1", day="2026-06-05", status="APPROVED"):
    return {"category": category, "amount": amount, "expense_date": day,
            "status": status, "store_id": store}


def _bill(bill_id, total, due, vendor_id="V-1", vendor_name="Lens Co",
          critical=False):
    return {"bill_id": bill_id, "bill_number": f"INV-{bill_id}",
            "vendor_id": vendor_id, "vendor_name": vendor_name,
            "bill_date": "2026-05-01", "due_date": due,
            "total_amount": total, "status": "OUTSTANDING",
            "vendor_critical": critical}


def _order(grand_total, store="BV-1", created=datetime(2026, 6, 10, 11, 0)):
    return {"grand_total": grand_total, "payment_status": "PAID",
            "status": "DELIVERED", "created_at": created, "store_id": store}


def _seed_standard(db):
    """Rent 50k (essential) + Marketing 20k (deferrable); overdue 10k bill +
    far-future 5k bill; 60k paid revenue by day 15 (-> 120k projected)."""
    db["expenses"].docs.extend([
        _expense("Rent", 50000.0),
        _expense("Marketing", 20000.0),
    ])
    db["vendor_bills"].docs.extend([
        _bill("B-OVD", 10000.0, "2026-06-10"),   # overdue vs 2026-06-15
        _bill("B-FUT", 5000.0, "2026-07-15"),    # due in 30d, non-critical
    ])
    db["orders"].docs.append(_order(60000.0))


# ============================================================================
# Classification: expense heads (essential vs deferrable)
# ============================================================================


def test_essential_match_is_case_and_space_insensitive():
    heads = svc.ESSENTIAL_DEFAULT_HEADS
    assert svc.classify_expense_head("Store RENT", heads) == svc.ESSENTIAL
    assert svc.classify_expense_head("  Salaries  &  Wages ", heads) == svc.ESSENTIAL
    assert svc.classify_expense_head("electricity bill - June", heads) == svc.ESSENTIAL
    assert svc.classify_expense_head("Insurance Premium", heads) == svc.ESSENTIAL


def test_short_statutory_keywords_match_whole_words_only():
    heads = svc.ESSENTIAL_DEFAULT_HEADS
    assert svc.classify_expense_head("GST payment", heads) == svc.ESSENTIAL
    assert svc.classify_expense_head("PF contribution", heads) == svc.ESSENTIAL
    assert svc.classify_expense_head("ESI remittance", heads) == svc.ESSENTIAL
    # 'esi' is inside 'dESIgn' -- a substring match would mis-file a design
    # retainer as a statutory essential. Whole-word rule prevents that.
    assert svc.classify_expense_head("Design retainer", heads) == svc.DEFERRABLE


def test_unknown_or_empty_head_defaults_deferrable():
    heads = svc.ESSENTIAL_DEFAULT_HEADS
    assert svc.classify_expense_head("Marketing", heads) == svc.DEFERRABLE
    assert svc.classify_expense_head("Travel", heads) == svc.DEFERRABLE
    assert svc.classify_expense_head("", heads) == svc.DEFERRABLE
    assert svc.classify_expense_head(None, heads) == svc.DEFERRABLE


# ============================================================================
# Classification: AP bills (MUST_PAY vs DEFERRABLE)
# ============================================================================


def test_overdue_bill_is_must_pay_regardless_of_vendor():
    bill = {"due_date": "2026-06-14", "vendor_id": "V-9", "vendor_name": "Anyone"}
    assert svc.classify_ap_bill(bill, now=FIXED_NOW) == svc.MUST_PAY
    # No parseable due date: conservative MUST_PAY (mirrors ap_engine's
    # undatable -> 90_plus stance; an undatable bill may be years overdue).
    assert svc.classify_ap_bill({"due_date": None}, now=FIXED_NOW) == svc.MUST_PAY
    assert svc.classify_ap_bill({"due_date": "junk"}, now=FIXED_NOW) == svc.MUST_PAY


def test_due_within_7d_is_must_pay_only_for_critical_vendor():
    due_soon = {"due_date": "2026-06-20", "vendor_id": "V-1", "vendor_name": "Lens Co"}
    # Non-critical: deferrable even though due in 5 days.
    assert svc.classify_ap_bill(due_soon, now=FIXED_NOW) == svc.DEFERRABLE
    # Bill-level flag wins.
    flagged = dict(due_soon, vendor_critical=True)
    assert svc.classify_ap_bill(flagged, now=FIXED_NOW) == svc.MUST_PAY
    # E2 critical list matches vendor_id OR vendor_name, normalized.
    assert (
        svc.classify_ap_bill(due_soon, now=FIXED_NOW, critical_vendors=["v-1"])
        == svc.MUST_PAY
    )
    assert (
        svc.classify_ap_bill(due_soon, now=FIXED_NOW, critical_vendors=["  LENS  co "])
        == svc.MUST_PAY
    )


def test_far_future_bill_is_deferrable_even_for_critical_vendor():
    bill = {"due_date": "2026-07-15", "vendor_id": "V-1", "vendor_critical": True}
    assert svc.classify_ap_bill(bill, now=FIXED_NOW) == svc.DEFERRABLE


# ============================================================================
# Pure view math: paise exactness, gap semantics, reconciliation
# ============================================================================


def test_min_pay_math_is_paise_exact():
    out = svc.build_survival_view(
        expenses=[
            {"head": "Rent", "amount": 12345.67},        # rupees -> 1234567 paise
            {"head": "Marketing", "amount": 99.99},      # deferrable
        ],
        ap_bills=[
            {"bill_id": "B1", "due_date": "2026-06-01", "outstanding": 250.5},
        ],
        projected_income_paise=0,
        now=FIXED_NOW,
    )
    assert out["fixed_costs_paise"] == 1234567
    assert out["must_pay_ap_paise"] == 25050
    assert out["min_pay_total_paise"] == 1234567 + 25050
    assert all(isinstance(out[k], int) for k in (
        "fixed_costs_paise", "deferrable_expenses_paise", "must_pay_ap_paise",
        "deferrable_ap_paise", "min_pay_total_paise", "survival_gap_paise",
        "surplus_paise", "total_outflows_paise", "projected_income_paise",
    ))


def test_survival_gap_clamps_at_zero_with_surplus_mirror():
    base = dict(
        expenses=[{"head": "Rent", "amount_paise": 600000}],
        ap_bills=[],
        now=FIXED_NOW,
    )
    short = svc.build_survival_view(projected_income_paise=400000, **base)
    assert short["survival_gap_paise"] == 200000      # shortfall is positive
    assert short["surplus_paise"] == 0
    ok = svc.build_survival_view(projected_income_paise=900000, **base)
    assert ok["survival_gap_paise"] == 0              # never negative
    assert ok["surplus_paise"] == 300000
    exact = svc.build_survival_view(projected_income_paise=600000, **base)
    assert exact["survival_gap_paise"] == 0 and exact["surplus_paise"] == 0


def test_totals_reconcile_every_paisa_in_exactly_one_bucket():
    out = svc.build_survival_view(
        expenses=[
            {"head": "Rent", "amount": 50000},
            {"head": "Salaries", "amount": 80000},
            {"head": "Marketing", "amount": 20000},
            {"head": "Travel", "amount": 7500.25},
        ],
        ap_bills=[
            {"bill_id": "B1", "due_date": "2026-06-01", "outstanding": 10000},
            {"bill_id": "B2", "due_date": "2026-06-18", "outstanding": 3000,
             "vendor_critical": True},
            {"bill_id": "B3", "due_date": "2026-08-01", "outstanding": 4000},
        ],
        projected_income_paise=1,
        now=FIXED_NOW,
    )
    assert (
        out["fixed_costs_paise"]
        + out["deferrable_expenses_paise"]
        + out["must_pay_ap_paise"]
        + out["deferrable_ap_paise"]
        == out["total_outflows_paise"]
    )
    # And the grand total equals the paise sum of every input row.
    expected_total = (
        5000000 + 8000000 + 2000000 + 750025 + 1000000 + 300000 + 400000
    )
    assert out["total_outflows_paise"] == expected_total
    # Detail rows partition the same way (no row lost, none double-counted).
    assert (
        sum(r["amount_paise"] for r in out["essential_detail"])
        == out["fixed_costs_paise"] + out["must_pay_ap_paise"]
    )
    assert (
        sum(r["amount_paise"] for r in out["deferrable_detail"])
        == out["deferrable_expenses_paise"] + out["deferrable_ap_paise"]
    )


def test_essential_heads_override_replaces_seed_list():
    """An owner-tuned essential list REPLACES the seed (it does not union):
    with only 'software' essential, rent becomes deferrable."""
    out = svc.build_survival_view(
        expenses=[
            {"head": "Software Subscription", "amount": 1000},
            {"head": "Rent", "amount": 2000},
        ],
        ap_bills=[],
        projected_income_paise=0,
        now=FIXED_NOW,
        essential_heads=["software"],
    )
    assert out["fixed_costs_paise"] == 100000
    assert out["deferrable_expenses_paise"] == 200000
    assert out["essential_heads_used"] == ["software"]


# ============================================================================
# Router: dedicated GET /finance/survival-cashflow
# ============================================================================


def test_dedicated_route_end_to_end_numbers(routed):
    _seed_standard(routed)
    out = asyncio.run(fin.get_survival_cashflow(store_id=None, current_user=_accountant()))
    sv = out["survival"]
    assert out["month"] == "2026-06"
    assert sv["fixed_costs_paise"] == 5000000           # Rent 50k
    assert sv["deferrable_expenses_paise"] == 2000000   # Marketing 20k
    assert sv["must_pay_ap_paise"] == 1000000           # overdue 10k bill
    assert sv["deferrable_ap_paise"] == 500000          # far-future 5k bill
    assert sv["min_pay_total_paise"] == 6000000
    # 60k paid by day 15 of a 30-day month -> 120k projected.
    assert sv["projected_income_paise"] == 12000000
    assert sv["survival_gap_paise"] == 0
    assert sv["surplus_paise"] == 6000000
    assert sv["total_outflows_paise"] == 8500000
    # Read-only guarantee: nothing wrote to any collection.
    assert all(c.write_calls == 0 for c in routed._collections.values())


def test_dedicated_route_shows_shortfall_when_income_collapses(routed):
    _seed_standard(routed)
    routed["orders"].docs.clear()  # zero revenue month
    out = asyncio.run(fin.get_survival_cashflow(store_id=None, current_user=_accountant()))
    sv = out["survival"]
    assert sv["projected_income_paise"] == 0
    assert sv["survival_gap_paise"] == sv["min_pay_total_paise"] == 6000000
    assert sv["surplus_paise"] == 0


def test_store_id_filters_expenses_and_income_but_not_org_wide_ap(routed):
    _seed_standard(routed)
    routed["expenses"].docs.append(_expense("Rent", 30000.0, store="BV-2"))
    routed["orders"].docs.append(_order(40000.0, store="BV-2"))
    sv_all = asyncio.run(
        fin.get_survival_cashflow(store_id=None, current_user=_accountant())
    )["survival"]
    sv_bv1 = asyncio.run(
        fin.get_survival_cashflow(store_id="BV-1", current_user=_accountant())
    )["survival"]
    assert sv_all["fixed_costs_paise"] == 8000000       # both stores' rent
    assert sv_bv1["fixed_costs_paise"] == 5000000       # BV-1 only
    assert sv_all["projected_income_paise"] == 20000000  # (60k+40k) doubled
    assert sv_bv1["projected_income_paise"] == 12000000
    # Vendor bills carry no store_id -- AP stays org-wide under the filter.
    assert sv_bv1["must_pay_ap_paise"] == sv_all["must_pay_ap_paise"] == 1000000


def test_e2_policy_override_flows_through_the_route(routed, monkeypatch):
    """finance.survival_essential_heads + finance.survival_critical_vendors
    are READ from policy: overriding them reclassifies without a deploy."""
    policy = {
        "finance.survival_essential_heads": ["software"],
        "finance.survival_critical_vendors": ["acme optical"],
    }
    monkeypatch.setattr(
        fin.policy_engine,
        "get_policy",
        lambda key, scope=None, default=None: policy.get(key, default),
    )
    routed["expenses"].docs.extend([
        _expense("Software Subscription", 1000.0),
        _expense("Rent", 2000.0),
    ])
    routed["vendor_bills"].docs.extend([
        _bill("B-CRIT", 500.0, "2026-06-18", vendor_id="V-A", vendor_name="ACME Optical"),
        _bill("B-PLAIN", 700.0, "2026-06-18", vendor_id="V-B", vendor_name="Other Vendor"),
    ])
    sv = asyncio.run(
        fin.get_survival_cashflow(store_id=None, current_user=_accountant())
    )["survival"]
    assert sv["fixed_costs_paise"] == 100000            # software now essential
    assert sv["deferrable_expenses_paise"] == 200000    # rent now deferrable
    assert sv["must_pay_ap_paise"] == 50000             # ACME due-soon -> must pay
    assert sv["deferrable_ap_paise"] == 70000           # same due date, not critical


def test_dedicated_route_403_for_store_manager_and_sales(routed):
    for user in (_store_manager(), _sales()):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(fin.get_survival_cashflow(store_id=None, current_user=user))
        assert exc.value.status_code == 403


def test_dedicated_route_db_absent_fail_soft(monkeypatch):
    monkeypatch.setattr(fin, "_get_db", lambda: None)
    monkeypatch.setattr(fin, "now_ist_naive", lambda: FIXED_NOW)
    monkeypatch.setattr(
        fin.policy_engine, "get_policy",
        lambda key, scope=None, default=None: default,
    )
    out = asyncio.run(fin.get_survival_cashflow(store_id=None, current_user=_accountant()))
    sv = out["survival"]
    assert sv["min_pay_total_paise"] == 0
    assert sv["survival_gap_paise"] == 0
    assert sv["total_outflows_paise"] == 0
    assert sv["essential_detail"] == [] and sv["deferrable_detail"] == []


# ============================================================================
# The revived dead path: GET /finance/budget?mode=survival
# ============================================================================


def test_budget_mode_survival_returns_real_data_not_dead_skeleton(routed):
    """Regression on FIND-7: the budgets writer never stores `mode`, so
    mode=survival used to ALWAYS return the empty no_budget_set skeleton.
    It must now carry the real survival view."""
    _seed_standard(routed)
    out = asyncio.run(
        fin.get_budget(mode="survival", month=None, year=None, current_user=_accountant())
    )
    assert "no_budget_set" not in out
    sv = out["survival"]
    assert sv["fixed_costs_paise"] == 5000000
    assert sv["must_pay_ap_paise"] == 1000000
    assert sv["min_pay_total_paise"] == 6000000
    assert sv["projected_income_paise"] == 12000000
    # The legacy envelope shape survives (categories skeleton still present).
    assert "categories" in out and out["mode"] == "survival"


def test_budget_mode_full_keeps_legacy_envelope_untouched(routed):
    _seed_standard(routed)
    out = asyncio.run(
        fin.get_budget(mode="full", month=None, year=None, current_user=_accountant())
    )
    assert "survival" not in out
    assert out.get("no_budget_set") is True   # honest empty skeleton, as before


def test_budget_mode_survival_narrows_to_owner_gate(routed):
    """Org-wide AP + projected income is owner material: the survival mode of
    the budget hook 403s a STORE_MANAGER (plain budget stays open to them)."""
    _seed_standard(routed)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            fin.get_budget(
                mode="survival", month=None, year=None, current_user=_store_manager()
            )
        )
    assert exc.value.status_code == 403
    # mode=full for the same manager is unaffected.
    out = asyncio.run(
        fin.get_budget(mode="full", month=None, year=None, current_user=_store_manager())
    )
    assert "survival" not in out
