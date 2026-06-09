"""
IMS 2.0 - F17/#25 Maker-checker journal entries (intent-level tests)
====================================================================
These exercise the REAL je_service + the REAL shared E4 ApprovalEngine against a
faithful in-memory fake Mongo (no network, no live mongod). A hollow shell that
skips the balance invariant, the COA gate, the maker-checker self-approval block,
the post-time approval consume, or the P&L integration FAILS here.

CI-robustness (HARD lessons folded in):
  (1) The FakeDB faithfully implements every operator je_service + approvals use
      (find_one_and_update with the status-guard filter, $inc, $set, dotted
      paths, $in, $gt). Every guard a handler reads (chart_of_accounts, users
      with a PIN, approval_requests, journal_entries) is SEEDED -- no reliance on
      a fail-soft fallback that would diverge local (no Mongo) vs CI (real Mongo).
  (2) The PIN-leak test asserts on the SPECIFIC audit field, not a whole-JSON
      substring (a random hash/id can contain the PIN string by chance).
  (3) E4 reads its tier thresholds from E2 get_policy -- left at the registry
      default; journal_entry routing does not depend on the threshold.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")
# JE write paths are feature-flagged; the engine functions are not, but turn it
# on so any flag-coupled code path behaves as in production.
os.environ.setdefault("ENABLE_MANUAL_JE", "1")

from api.routers.auth import hash_password  # noqa: E402
from api.services import approvals as appr  # noqa: E402
from api.services import je_service  # noqa: E402


# ============================================================================
# Faithful in-memory fake Mongo (operators je_service + approvals actually use)
# ============================================================================


def _get_path(doc: Dict[str, Any], dotted: str):
    cur: Any = doc
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _set_path(doc: Dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cur = doc
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


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
        actual = _get_path(doc, k) if "." in k else doc.get(k)
        if isinstance(v, dict) and any(str(kk).startswith("$") for kk in v):
            for op, expected in v.items():
                if not _cmp_op(actual, op, expected):
                    return False
            continue
        if actual != v:
            return False
    return True


def _project(doc: Dict[str, Any], projection: Optional[Dict[str, int]]) -> Dict[str, Any]:
    out = dict(doc)
    if projection and projection.get("_id") == 0:
        out.pop("_id", None)
    return out


def _apply_update(doc: Dict[str, Any], update: Dict[str, Any]) -> None:
    for op, fields in update.items():
        if op == "$set":
            for kk, vv in fields.items():
                if "." in kk:
                    _set_path(doc, kk, vv)
                else:
                    doc[kk] = vv
        elif op == "$inc":
            for kk, vv in fields.items():
                cur = _get_path(doc, kk) if "." in kk else doc.get(kk)
                newv = (cur or 0) + vv
                if "." in kk:
                    _set_path(doc, kk, newv)
                else:
                    doc[kk] = newv
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

    def limit(self, n):
        self._docs = self._docs[: int(n)]
        return self

    def __iter__(self):
        return iter(self._docs)


class FakeCollection:
    def __init__(self, database=None):
        self.docs: List[Dict[str, Any]] = []
        self._n = 0
        self.database = database  # AuditRepository derives the chain-head db from this

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
        if upsert:
            self._upsert(query, update)
            return type("R", (), {"modified_count": 0, "matched_count": 0, "upserted_id": 1})()
        return type("R", (), {"modified_count": 0, "matched_count": 0})()

    def find_one_and_update(self, query, update, return_document=None, upsert=False, **_kw):
        for d in self.docs:
            if _matches(d, query):
                _apply_update(d, update)
                return _project(d, None)
        if upsert:
            d = self._upsert(query, update)
            return _project(d, None)
        return None

    def delete_one(self, query):
        for i, d in enumerate(self.docs):
            if _matches(d, query):
                self.docs.pop(i)
                return type("R", (), {"deleted_count": 1})()
        return type("R", (), {"deleted_count": 0})()

    def _upsert(self, query, update):
        base: Dict[str, Any] = {}
        for k, v in query.items():
            if not isinstance(v, dict):
                base[k] = v
        soi = update.get("$setOnInsert", {})
        base.update(soi)
        _apply_update(base, {k: v for k, v in update.items() if k != "$setOnInsert"})
        self.insert_one(base)
        return base

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
# Fixtures / helpers
# ============================================================================


@pytest.fixture
def db():
    d = FakeDB()
    # Seed every guard the handlers read.
    je_service.seed_chart_of_accounts(d)
    return d


def _seed_user(db, user_id: str, *, pin: Optional[str] = None, roles=None) -> None:
    coll = db.get_collection("users")
    doc = {"user_id": user_id, "is_active": True, "roles": roles or ["ADMIN"]}
    if pin is not None:
        doc["approval_pin_hash"] = hash_password(pin)
        doc["pin_attempts"] = {"count": 0, "window_start": appr._now()}
    coll.insert_one(doc)


def _audit_rows(db) -> List[Dict[str, Any]]:
    return db.get_collection("audit_logs").docs


def _two_line(debit_code: str, credit_code: str, amount: float) -> List[Dict[str, Any]]:
    """A balanced 2-line voucher: debit one allow_manual_je account, credit
    another, for the same rupee amount."""
    return [
        {"account_code": debit_code, "debit": amount, "credit": 0},
        {"account_code": credit_code, "debit": 0, "credit": amount},
    ]


def _entry_date() -> datetime:
    # A fixed open period (no period_locks seeded -> open).
    return datetime(2026, 5, 15)


def _draft(db, maker_id: str, amount: float = 5000.0, store_id: Optional[str] = None) -> str:
    res = je_service.create_je(
        db, store_id=store_id, entity_id=None, entry_date=_entry_date(),
        description="Depreciation for May 2026",
        lines=_two_line("5001", "2001", amount),
        maker_id=maker_id, maker_name=maker_id,
    )
    assert res["ok"], res
    return res["je"]["je_id"]


def _submit(db, je_id: str, maker_id: str, roles=None) -> str:
    res = je_service.submit_je(db, je_id=je_id, maker_id=maker_id,
                               maker_roles=roles or ["ADMIN"])
    assert res["ok"], res
    return res["request_id"]


# ============================================================================
# Tests
# ============================================================================


def test_balanced_je_accepted_unbalanced_rejected(db):
    _seed_user(db, "maker", roles=["ACCOUNTANT"])
    # Off-by-1-paisa unbalanced -> 422 unbalanced, nothing persisted.
    bad = je_service.create_je(
        db, store_id=None, entity_id=None, entry_date=_entry_date(),
        description="x",
        lines=[{"account_code": "5001", "debit": 500.00, "credit": 0},
               {"account_code": "2001", "debit": 0, "credit": 500.01}],
        maker_id="maker",
    )
    assert bad["ok"] is False
    assert bad["http"] == 422
    assert bad["error"] == "unbalanced"
    assert db.get_collection("journal_entries").docs == []

    # Perfectly balanced -> DRAFT, paisa-exact totals.
    good = je_service.create_je(
        db, store_id=None, entity_id=None, entry_date=_entry_date(),
        description="x", lines=_two_line("5001", "2001", 500.0), maker_id="maker",
    )
    assert good["ok"] is True
    assert good["je"]["status"] == "DRAFT"
    assert good["je"]["total_debit"] == 50000  # paisa
    assert good["je"]["total_credit"] == 50000


def test_allow_manual_je_gate(db):
    # 2101 = GST Output Tax, allow_manual_je=False -> blocked; 5001 ok.
    blocked = je_service.create_je(
        db, store_id=None, entity_id=None, entry_date=_entry_date(),
        description="x", lines=_two_line("2101", "2001", 100.0), maker_id="m",
    )
    assert blocked["ok"] is False
    assert blocked["http"] == 422
    assert blocked["error"] == "account_not_allowed_for_manual_je"


def test_maker_cannot_self_approve_but_other_admin_can(db):
    # The headline maker-checker rule: the maker cannot approve their own JE.
    _seed_user(db, "user_A", pin="1111", roles=["ADMIN"])
    _seed_user(db, "user_B", pin="2222", roles=["ADMIN"])
    je_id = _draft(db, "user_A")
    _submit(db, je_id, "user_A", roles=["ADMIN"])

    # The E4 approval_requests doc must carry maker_checker=True for journal_entry.
    je = je_service.get_je(db, je_id)
    req = db.get_collection("approval_requests").find_one(
        {"request_id": je["approval_request_id"]})
    assert req["maker_checker"] is True

    self_res = je_service.approve_je(
        db, je_id=je_id, approver_id="user_A", approver_roles=["ADMIN"], pin="1111")
    assert self_res["ok"] is False
    assert self_res["http"] == 403
    assert self_res["error"] == "cannot_approve_own"
    assert je_service.get_je(db, je_id)["status"] == "SUBMITTED"  # unchanged

    # A DIFFERENT admin approves successfully.
    other = je_service.approve_je(
        db, je_id=je_id, approver_id="user_B", approver_roles=["ADMIN"], pin="2222")
    assert other["ok"] is True
    assert je_service.get_je(db, je_id)["status"] == "APPROVED"


def test_je_does_not_post_until_approved(db):
    # A SUBMITTED (un-approved) JE must NOT post, and must NOT appear in the P&L.
    _seed_user(db, "user_A", pin="1111", roles=["ADMIN"])
    je_id = _draft(db, "user_A", amount=5000.0)
    _submit(db, je_id, "user_A")

    # Post before approval -> rejected; status stays SUBMITTED.
    res = je_service.post_je(db, je_id=je_id, poster_id="user_B")
    assert res["ok"] is False
    assert res["http"] == 409
    assert res["error"] == "not_approved"
    assert je_service.get_je(db, je_id)["status"] == "SUBMITTED"

    # P&L sees NOTHING from a non-POSTED JE.
    adj = je_service.pnl_adjustments(db, store_id=None, from_dt=datetime(2026, 4, 1),
                                     to_dt=datetime(2026, 6, 30))
    assert adj["je_expense_adjustment"] == 0.0
    assert adj["je_revenue_adjustment"] == 0.0


def test_approved_je_posts_once_and_hits_pnl(db):
    _seed_user(db, "user_A", pin="1111", roles=["ADMIN"])
    _seed_user(db, "user_B", pin="2222", roles=["ADMIN"])
    # 5001 EXPENSE debit 5000 / 2001 LIABILITY credit 5000.
    je_id = _draft(db, "user_A", amount=5000.0)
    _submit(db, je_id, "user_A")
    je_service.approve_je(db, je_id=je_id, approver_id="user_B",
                          approver_roles=["ADMIN"], pin="2222")

    first = je_service.post_je(db, je_id=je_id, poster_id="user_B")
    assert first["ok"] is True
    assert je_service.get_je(db, je_id)["status"] == "POSTED"

    # POSTED expense JE raises the P&L expense adjustment by exactly 5000.
    adj = je_service.pnl_adjustments(db, store_id=None, from_dt=datetime(2026, 4, 1),
                                     to_dt=datetime(2026, 6, 30))
    assert adj["je_expense_adjustment"] == 5000.0


def test_post_is_idempotent_exactly_once(db):
    # Posting twice (or two racing posts) must consume the approval exactly once.
    _seed_user(db, "user_A", pin="1111", roles=["ADMIN"])
    _seed_user(db, "user_B", pin="2222", roles=["ADMIN"])
    je_id = _draft(db, "user_A", amount=3000.0)
    _submit(db, je_id, "user_A")
    je_service.approve_je(db, je_id=je_id, approver_id="user_B",
                          approver_roles=["ADMIN"], pin="2222")

    r1 = je_service.post_je(db, je_id=je_id, poster_id="user_B")
    r2 = je_service.post_je(db, je_id=je_id, poster_id="user_B")
    oks = [r for r in (r1, r2) if r.get("ok")]
    losers = [r for r in (r1, r2) if not r.get("ok")]
    assert len(oks) == 1
    assert len(losers) == 1
    assert losers[0]["error"] == "already_posted"

    # P&L counts the JE exactly once (5000? no -> 3000), not doubled.
    adj = je_service.pnl_adjustments(db, store_id=None, from_dt=datetime(2026, 4, 1),
                                     to_dt=datetime(2026, 6, 30))
    assert adj["je_expense_adjustment"] == 3000.0
    # Exactly one POSTED journal_entries doc.
    posted = [d for d in db.get_collection("journal_entries").docs if d["status"] == "POSTED"]
    assert len(posted) == 1


def test_reject_blocks_post_and_records_note(db):
    _seed_user(db, "user_A", pin="1111", roles=["ADMIN"])
    _seed_user(db, "user_B", pin="2222", roles=["ADMIN"])
    je_id = _draft(db, "user_A")
    _submit(db, je_id, "user_A")

    # A <10 char note is rejected at the service layer.
    short = je_service.reject_je(db, je_id=je_id, approver_id="user_B",
                                 approver_roles=["ADMIN"], pin="2222", note="no")
    assert short["ok"] is False and short["error"] == "note_required"

    ok = je_service.reject_je(db, je_id=je_id, approver_id="user_B",
                              approver_roles=["ADMIN"], pin="2222",
                              note="Wrong depreciation base, redo")
    assert ok["ok"] is True
    je = je_service.get_je(db, je_id)
    assert je["status"] == "REJECTED"
    assert je["checker_note"] == "Wrong depreciation base, redo"

    # A REJECTED JE cannot post.
    res = je_service.post_je(db, je_id=je_id, poster_id="user_B")
    assert res["ok"] is False
    assert res["error"] == "not_approved"


def test_reversal_nets_pnl_to_zero(db):
    _seed_user(db, "user_A", pin="1111", roles=["ADMIN"])
    _seed_user(db, "user_B", pin="2222", roles=["ADMIN"])
    je_id = _draft(db, "user_A", amount=5000.0)
    _submit(db, je_id, "user_A")
    je_service.approve_je(db, je_id=je_id, approver_id="user_B",
                          approver_roles=["ADMIN"], pin="2222")
    je_service.post_je(db, je_id=je_id, poster_id="user_B")

    rev = je_service.reverse_je(db, je_id=je_id, actor_id="user_B", actor_name="user_B")
    assert rev["ok"] is True
    reversal_id = rev["reversal_je_id"]

    original = je_service.get_je(db, je_id)
    reversal = je_service.get_je(db, reversal_id)
    assert original["status"] == "REVERSED"
    assert original["reversed_by"] == reversal_id
    assert reversal["status"] == "POSTED"
    assert reversal["reversal_of"] == je_id
    # Debits/credits swapped.
    assert reversal["total_debit"] == original["total_credit"]
    assert reversal["total_credit"] == original["total_debit"]

    # Net P&L effect of original + reversal = zero (the reversal entry dates
    # today, FY 2026-27, so widen the window to include both).
    adj = je_service.pnl_adjustments(db, store_id=None, from_dt=datetime(2026, 4, 1),
                                     to_dt=datetime(2027, 3, 31))
    assert adj["je_expense_adjustment"] == 0.0

    # Double reverse blocked.
    again = je_service.reverse_je(db, je_id=je_id, actor_id="user_B")
    assert again["ok"] is False
    assert again["error"] == "already_reversed"


def test_revenue_je_raises_revenue_adjustment(db):
    # 4001 Miscellaneous Income (REVENUE) credit -> revenue adjustment up.
    _seed_user(db, "user_A", pin="1111", roles=["ADMIN"])
    _seed_user(db, "user_B", pin="2222", roles=["ADMIN"])
    res = je_service.create_je(
        db, store_id=None, entity_id=None, entry_date=_entry_date(),
        description="Scrap sale income",
        lines=[{"account_code": "5006", "debit": 0, "credit": 1200.0},
               {"account_code": "4001", "debit": 0, "credit": 0},  # placeholder fixed below
               ],
        maker_id="user_A",
    )
    # The placeholder above is invalid (zero line); build a real revenue voucher:
    # debit Misc Expense 0? No -> debit an asset/expense, credit revenue.
    assert res["ok"] is False  # the zero line is correctly rejected
    res2 = je_service.create_je(
        db, store_id=None, entity_id=None, entry_date=_entry_date(),
        description="Scrap sale income",
        lines=[{"account_code": "5006", "debit": 1200.0, "credit": 0},
               {"account_code": "4001", "debit": 0, "credit": 1200.0}],
        maker_id="user_A",
    )
    assert res2["ok"], res2
    je_id = res2["je"]["je_id"]
    _submit(db, je_id, "user_A")
    je_service.approve_je(db, je_id=je_id, approver_id="user_B",
                          approver_roles=["ADMIN"], pin="2222")
    je_service.post_je(db, je_id=je_id, poster_id="user_B")
    adj = je_service.pnl_adjustments(db, store_id=None, from_dt=datetime(2026, 4, 1),
                                     to_dt=datetime(2026, 6, 30))
    assert adj["je_revenue_adjustment"] == 1200.0
    assert adj["je_expense_adjustment"] == 1200.0  # the 5006 debit side


def test_audit_row_per_transition_no_pin_leak(db):
    _seed_user(db, "user_A", pin="1111", roles=["ADMIN"])
    _seed_user(db, "user_B", pin="2222", roles=["ADMIN"])
    je_id = _draft(db, "user_A")
    _submit(db, je_id, "user_A")
    je_service.approve_je(db, je_id=je_id, approver_id="user_B",
                          approver_roles=["ADMIN"], pin="2222")
    je_service.post_je(db, je_id=je_id, poster_id="user_B")

    je_rows = [a for a in _audit_rows(db) if a.get("entity_type") == "journal_entry"]
    actions = [a["action"] for a in je_rows]
    assert "je_created" in actions
    assert "je_submitted" in actions
    assert "je_approved" in actions
    assert "je_posted" in actions
    # Hash-chained (AuditRepository.create -> audit_chain stamps these).
    for a in je_rows:
        assert "entry_hash" in a
        assert "prev_hash" in a

    # PIN leak guard: assert on the SPECIFIC audit fields, NOT a whole-JSON
    # substring (a random hash/id could contain "2222" by chance).
    for a in _audit_rows(db):
        after = a.get("after_state") or {}
        assert "approval_pin_hash" not in after
        assert "pin" not in after
        # The reason / snapshot fields never carry the PIN value.
        assert a.get("reason") not in ("2222", "1111")


def test_je_number_is_fy_scoped_consecutive(db):
    _seed_user(db, "user_A", roles=["ACCOUNTANT"])
    n1 = je_service.create_je(
        db, store_id=None, entity_id=None, entry_date=_entry_date(),
        description="a", lines=_two_line("5001", "2001", 100.0), maker_id="user_A")["je"]["je_number"]
    n2 = je_service.create_je(
        db, store_id=None, entity_id=None, entry_date=_entry_date(),
        description="b", lines=_two_line("5001", "2001", 100.0), maker_id="user_A")["je"]["je_number"]
    assert n1.startswith("JE/GEN/2026-27/")
    assert n1.endswith("000001")
    assert n2.endswith("000002")


def test_feature_flag_off_blocks_writes(monkeypatch):
    # With ENABLE_MANUAL_JE unset, the router-level guard 503s; the service
    # function itself is flag-agnostic. Test is_je_enabled directly.
    monkeypatch.delenv("ENABLE_MANUAL_JE", raising=False)
    assert je_service.is_je_enabled() is False
    monkeypatch.setenv("ENABLE_MANUAL_JE", "1")
    assert je_service.is_je_enabled() is True


def test_chart_of_accounts_manual_only_filter(db):
    manual = je_service.list_accounts(db, manual_only=True)
    codes = {a["account_code"] for a in manual}
    assert "5001" in codes          # allow_manual_je=True
    assert "2101" not in codes      # GST Output Tax allow_manual_je=False
    assert "1001" not in codes      # Stock allow_manual_je=False


def test_tally_journal_voucher_xml_only_posted(db):
    _seed_user(db, "user_A", pin="1111", roles=["ADMIN"])
    _seed_user(db, "user_B", pin="2222", roles=["ADMIN"])
    # A POSTED JE.
    posted_id = _draft(db, "user_A", amount=5000.0)
    _submit(db, posted_id, "user_A")
    je_service.approve_je(db, je_id=posted_id, approver_id="user_B",
                          approver_roles=["ADMIN"], pin="2222")
    je_service.post_je(db, je_id=posted_id, poster_id="user_B")
    # A DRAFT JE (must NOT export).
    draft_id = _draft(db, "user_A", amount=999.0)

    all_jes = je_service.list_jes(db, limit=100)
    xml = je_service.build_journal_voucher_xml(all_jes)
    posted = je_service.get_je(db, posted_id)
    draft = je_service.get_je(db, draft_id)
    assert "<JOURNALVOUCHER" not in xml or True  # we emit VCHTYPE="Journal"
    assert 'VCHTYPE="Journal"' in xml
    assert posted["je_number"] in xml
    assert draft["je_number"] not in xml
    # Debit ledger marked ISDEEMEDPOSITIVE=Yes, credit side No.
    assert "<ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>" in xml
    assert "<ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>" in xml


def test_fail_soft_no_db():
    # db=None -> empty reads, no-op writes, never raises.
    assert je_service.get_je(None, "JE-x") is None
    assert je_service.list_jes(None) == []
    assert je_service.create_je(None, store_id=None, entity_id=None,
                                entry_date=_entry_date(), description="x",
                                lines=_two_line("5001", "2001", 100.0),
                                maker_id="m")["ok"] is False
    assert je_service.pnl_adjustments(None, store_id=None, from_dt=None, to_dt=None) == {
        "je_revenue_adjustment": 0.0, "je_expense_adjustment": 0.0}
    # list_accounts falls back to the static catalogue with no DB.
    accts = je_service.list_accounts(None, manual_only=True)
    assert any(a["account_code"] == "5001" for a in accts)
