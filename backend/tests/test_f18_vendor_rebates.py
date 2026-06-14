"""
IMS 2.0 - F18 Vendor volume-rebate tests (intent-level)
=======================================================
Exercises the REAL rebate_engine service + router against a faithful in-memory
fake Mongo. A hollow shell that mis-computes the paise rebate, lets a period
post twice, increases (instead of reduces) vendor AP, or skips the period lock
would FAIL here.

Maps to the F18 acceptance intents:
  * tier math -- no-clear=0, boundary, highest-wins, monotonicity raise; paise pct/flat/cap
  * period spend -- only accepted PURCHASE_INVOICE, half-open window
  * preview -- no write
  * manual post -- POSTED ledger + credit_note_number + Tally voucher; reduces AP (credit note)
  * double-post -- same (agreement, period) -> 409, exactly one ledger row
  * period-lock -- 423
  * RBAC -- ACCOUNTANT/ADMIN/SUPERADMIN only
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from api.services import rebate_engine as svc  # noqa: E402

# ============================================================================
# Fake Mongo (with unique-index dup on insert for the double-post guard)
# ============================================================================


def _matches(doc, query):
    for k, v in query.items():
        if doc.get(k) != v:
            return False
    return True


class FakeCollection:
    def __init__(self, unique_keys=None):
        self.docs: List[Dict[str, Any]] = []
        self._unique = unique_keys or []  # list of tuples of field names

    def _violates_unique(self, doc):
        for keyset in self._unique:
            for d in self.docs:
                if all(d.get(k) == doc.get(k) for k in keyset):
                    return True
        return False

    def insert_one(self, doc):
        from pymongo.errors import DuplicateKeyError

        if doc.get("_id") and any(d.get("_id") == doc["_id"] for d in self.docs):
            raise DuplicateKeyError(f"dup _id {doc['_id']}")
        if self._violates_unique(doc):
            raise DuplicateKeyError("dup unique key")
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc.get("_id")})()

    def find_one(self, query, projection=None):
        for d in self.docs:
            if _matches(d, query):
                return dict(d)
        return None

    def find(self, query=None, projection=None):
        return [dict(d) for d in self.docs if _matches(d, query or {})]

    def find_one_and_update(self, query, update, return_document=None, **_kw):
        for d in self.docs:
            if _matches(d, query):
                for op, fields in update.items():
                    if op == "$set":
                        d.update(fields)
                return dict(d)
        return None

    def create_index(self, keys, unique=False, **_kw):
        if unique:
            self._unique.append(tuple(k for k, _dir in keys))
        return "idx"


class FakeDB:
    def __init__(self):
        self._c: Dict[str, FakeCollection] = {}
        # the ledger has the (agreement_id, period_start) unique backstop
        self._c["vendor_rebate_ledger"] = FakeCollection(
            unique_keys=[("agreement_id", "period_start")]
        )
        self.is_connected = True

    def get_collection(self, name):
        return self._c.setdefault(name, FakeCollection())

    def __getitem__(self, name):
        return self.get_collection(name)


@pytest.fixture()
def db() -> FakeDB:
    return FakeDB()


def _acct(uid="AC1"):
    return {
        "user_id": uid,
        "roles": ["ACCOUNTANT"],
        "store_ids": [],
        "active_store_id": None,
    }


def _sales(uid="S1"):
    return {"user_id": uid, "roles": ["SALES_STAFF"], "store_ids": ["BV-1"]}


_TIERS = [
    {"min_spend_paise": 0, "rebate_pct": 1.0},
    {"min_spend_paise": 10_000_000, "rebate_pct": 2.5},  # >= Rs 1,00,000 spend
]


def _seed_bills(db, vendor="V-1", n_paise_each=5_000_000, count=3, date="2026-05-10"):
    coll = db.get_collection("vendor_bills")
    for i in range(count):
        coll.insert_one(
            {
                "_id": f"PI-{i}",
                "bill_id": f"PI-{i}",
                "vendor_id": vendor,
                "doc_type": "PURCHASE_INVOICE",
                "status": "OUTSTANDING",
                "bill_date": date,
                "taxable_amount": n_paise_each / 100.0,
            }
        )


def _agreement(db, vendor="V-1", tiers=None):
    return svc.create_agreement(
        db,
        {
            "vendor_id": vendor,
            "name": "Vol rebate",
            "period": "MONTHLY",
            "tiers": tiers or _TIERS,
        },
        actor=_acct(),
    )


# ============================================================================
# Pure tier math
# ============================================================================


def test_resolve_tier_table():
    assert svc.resolve_tier(0, _TIERS)["rebate_pct"] == 1.0
    assert svc.resolve_tier(5_000_000, _TIERS)["rebate_pct"] == 1.0
    assert (
        svc.resolve_tier(10_000_000, _TIERS)["rebate_pct"] == 2.5
    )  # boundary -> higher tier
    assert (
        svc.resolve_tier(99_000_000, _TIERS)["rebate_pct"] == 2.5
    )  # highest cleared wins


def test_resolve_tier_no_clear_returns_none():
    assert svc.resolve_tier(50, [{"min_spend_paise": 100, "rebate_pct": 5.0}]) is None


def test_monotonicity_raises():
    with pytest.raises(svc.RebateConfigError):
        svc.resolve_tier(
            0,
            [
                {"min_spend_paise": 100, "rebate_pct": 1.0},
                {"min_spend_paise": 100, "rebate_pct": 2.0},
            ],
        )  # duplicate
    with pytest.raises(svc.RebateConfigError):
        svc.resolve_tier(0, [{"min_spend_paise": 0}])  # no earn rule


def test_compute_rebate_paise_pct_flat_cap():
    # 2.5% of Rs 2,00,000 (20,000,000 paise) = Rs 5,000 = 500000 paise
    assert (
        svc.compute_rebate_paise(20_000_000, {"min_spend_paise": 0, "rebate_pct": 2.5})
        == 500_000
    )
    # flat
    assert (
        svc.compute_rebate_paise(
            20_000_000, {"min_spend_paise": 0, "rebate_flat_paise": 12345}
        )
        == 12345
    )
    # cap clamps
    assert (
        svc.compute_rebate_paise(
            20_000_000, {"min_spend_paise": 0, "rebate_pct": 2.5, "cap_paise": 100000}
        )
        == 100000
    )


def test_compute_period_spend_window_and_status():
    bills = [
        {
            "vendor_id": "V-1",
            "doc_type": "PURCHASE_INVOICE",
            "bill_date": "2026-05-10",
            "taxable_amount_paise": 100,
        },
        {
            "vendor_id": "V-1",
            "doc_type": "PURCHASE_INVOICE",
            "bill_date": "2026-06-01",
            "taxable_amount_paise": 999,
        },  # == end -> next period
        {
            "vendor_id": "V-1",
            "doc_type": "PURCHASE_INVOICE",
            "status": "VOID",
            "bill_date": "2026-05-11",
            "taxable_amount_paise": 500,
        },
        {
            "vendor_id": "V-2",
            "doc_type": "PURCHASE_INVOICE",
            "bill_date": "2026-05-12",
            "taxable_amount_paise": 700,
        },  # other vendor
    ]
    assert svc.compute_period_spend(bills, "V-1", "2026-05-01", "2026-06-01") == 100


# ============================================================================
# Agreements + preview
# ============================================================================


def test_create_agreement_rejects_bad_tiers(db):
    with pytest.raises(svc.RebateError) as e:
        svc.create_agreement(
            db, {"vendor_id": "V-1", "tiers": [{"min_spend_paise": 0}]}, actor=_acct()
        )
    assert e.value.status == 422


def test_preview_no_write(db):
    _seed_bills(db)  # 3 x Rs 50,000 = Rs 1,50,000 spend -> tier 2.5%
    ag = _agreement(db)
    out = svc.preview(db, ag["agreement_id"], "2026-05-01", "2026-06-01")
    assert out["spend_paise"] == 15_000_000
    assert out["tier"]["rebate_pct"] == 2.5
    assert out["rebate_paise"] == 375_000  # 2.5% of 1,50,000 = 3,750.00
    # preview wrote NOTHING to the ledger
    assert db.get_collection("vendor_rebate_ledger").docs == []


# ============================================================================
# Manual post -- reduces AP, double-post guarded
# ============================================================================


def test_post_writes_ledger_credit_note_and_reduces_ap(db):
    _seed_bills(db)
    ag = _agreement(db)
    out = svc.post(db, ag["agreement_id"], "2026-05-01", "2026-06-01", actor=_acct())
    assert out["status"] == "POSTED" and out["rebate_paise"] == 375_000
    assert out["credit_note_number"].startswith("RCN-")
    # Tally JV: CREDIT the vendor ledger, DEBIT Rebates-Receivable, not dispatched
    entries = out["tally_voucher"]["entries"]
    vendor_leg = [e for e in entries if "credit_paise" in e][0]
    rebate_leg = [e for e in entries if "debit_paise" in e][0]
    assert vendor_leg["credit_paise"] == 375_000 and "V-1" in vendor_leg["ledger"]
    assert rebate_leg["debit_paise"] == 375_000
    assert out["tally_voucher"]["dispatched"] is False
    # AP REDUCTION: a credit note with NO bill_id (build_aging nets it off net_payable)
    cn = db.get_collection("vendor_credit_notes").find_one(
        {"rebate_id": out["rebate_id"]}
    )
    assert cn is not None and cn["bill_id"] is None and cn["vendor_id"] == "V-1"
    assert cn["amount"] == 3750.0  # rupees, reduces what we owe the vendor


def test_double_post_same_period_blocked(db):
    _seed_bills(db)
    ag = _agreement(db)
    svc.post(db, ag["agreement_id"], "2026-05-01", "2026-06-01", actor=_acct())
    with pytest.raises(svc.RebateError) as e:
        svc.post(db, ag["agreement_id"], "2026-05-01", "2026-06-01", actor=_acct())
    assert e.value.status == 409 and e.value.code == "already_posted"
    rows = db.get_collection("vendor_rebate_ledger").find(
        {"agreement_id": ag["agreement_id"]}
    )
    assert len(rows) == 1  # exactly ONE ledger row despite two post attempts


def test_post_period_lock_423(db):
    _seed_bills(db)
    ag = _agreement(db)

    def _locked(_posting_date):
        from fastapi import HTTPException

        raise HTTPException(status_code=423, detail="period locked")

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as e:
        svc.post(
            db,
            ag["agreement_id"],
            "2026-05-01",
            "2026-06-01",
            actor=_acct(),
            period_lock_check=_locked,
        )
    assert e.value.status_code == 423
    assert db.get_collection("vendor_rebate_ledger").docs == []  # nothing posted


def test_post_inactive_agreement_409(db):
    _seed_bills(db)
    ag = _agreement(db)
    svc.update_agreement(db, ag["agreement_id"], {"active": False}, actor=_acct())
    with pytest.raises(svc.RebateError) as e:
        svc.post(db, ag["agreement_id"], "2026-05-01", "2026-06-01", actor=_acct())
    assert e.value.status == 409


def test_no_tier_clears_posts_zero_rebate_no_credit_note(db):
    _seed_bills(
        db, n_paise_each=10000, count=1
    )  # tiny spend -> tier 1.0% (min 0), rebate = 1% of 100 = 1 paise
    ag = _agreement(
        db, tiers=[{"min_spend_paise": 99_999_999, "rebate_pct": 5.0}]
    )  # nothing clears
    out = svc.post(db, ag["agreement_id"], "2026-05-01", "2026-06-01", actor=_acct())
    assert out["rebate_paise"] == 0 and out["status"] == "POSTED"
    # zero rebate -> no AP-reducing credit note written
    assert db.get_collection("vendor_credit_notes").docs == []


def test_engine_db_absent_failsoft():
    with pytest.raises(svc.RebateError) as e:
        svc.create_agreement(None, {"vendor_id": "V-1", "tiers": _TIERS}, actor=_acct())
    assert e.value.status == 503
    assert svc.list_agreements(None) == []
    assert svc.list_ledger(None) == []
    svc.ensure_indexes(None)


# ============================================================================
# ROUTER -- RBAC + double-post + preview-no-write through the HTTP layer
# ============================================================================


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def test_router_create_403_for_sales_staff(db, monkeypatch):
    from fastapi import HTTPException
    from api.routers import vendor_rebates as r

    monkeypatch.setattr(r, "_get_db", lambda: db)
    body = r.AgreementBody(vendor_id="V-1", tiers=_TIERS)
    with pytest.raises(HTTPException) as exc:
        _run(r.create_agreement(body, current_user=_sales()))
    assert exc.value.status_code == 403


def test_router_post_then_double_post_409(db, monkeypatch):
    from fastapi import HTTPException
    from api.routers import vendor_rebates as r

    monkeypatch.setattr(r, "_get_db", lambda: db)
    monkeypatch.setattr(r, "_period_lock_check", lambda d: None)
    _seed_bills(db)
    ag = _run(
        r.create_agreement(
            r.AgreementBody(vendor_id="V-1", tiers=_TIERS), current_user=_acct()
        )
    )
    body = r.PostBody(
        agreement_id=ag["agreement_id"],
        period_start="2026-05-01",
        period_end="2026-06-01",
    )
    first = _run(r.post_rebate(body, current_user=_acct()))
    assert first["status"] == "POSTED"
    with pytest.raises(HTTPException) as exc:
        _run(r.post_rebate(body, current_user=_acct()))
    assert exc.value.status_code == 409
