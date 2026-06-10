"""
IMS 2.0 - E5 Tender routing + reconciliation tests (intent-level)
=================================================================
Exercises the REAL tender_routing (pure) + tender_reconciliation (DB) services
against a faithful in-memory fake Mongo (no network, no live mongod). A hollow
shell that silently folds UNKNOWN into CASH, mis-routes a voucher to a bank
ledger, skips the atomic lock guard, or rewrites a POS capture field FAILS here.

CI-robustness: every DB accessor the code under test touches is satisfied by the
fake (orders / tender_ledger_map / payment_reconciliations / stores / audit) and
every query/guard reads SEEDED docs -- there is NO fail-soft divergence between
local (no Mongo) and CI (real Mongo).

Maps to the packet's acceptance tests 1-10:
  1  instruments stop booking as Cash (JV legs hit the bank ledgers, 0 on Cash)
  2  unknown tender -> Suspense, surfaced under UNKNOWN (not folded into CASH)
  3  by-mode net sums to the order paid total (paise-exact)
  4  refund contras the SAME ledger (negative leg)
  5  non-cash-in -> liability/receivable ledgers, never a bank ledger
  6  Tally voucher balances (and an unbalanced set raises)
  7  E2-layered map: a store override beats global for that store + source shown
  8  POS capture unchanged (capture fields untouched; stamp is additive)
  9  lock is atomic + immutable (two concurrent locks -> exactly one wins)
  10 STORE_CREDIT deferred (in the default map; no live capture row uses it)
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

from api.services import tender_reconciliation as trec  # noqa: E402
from api.services.tender_routing import (  # noqa: E402
    IMS_DEFAULT_LEDGERS,
    CASH_IN_TENDERS,
    assert_voucher_balanced,
    build_tender_jv_legs,
    canonicalize_tender,
    resolve_ledger,
    split_payments_by_mode,
)


# ============================================================================
# Faithful in-memory fake Mongo (only the operators E5 uses)
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


def _set_path(doc: Dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cur = doc
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def _apply_update(doc: Dict[str, Any], update: Dict[str, Any]) -> None:
    for op, fields in update.items():
        if op in ("$set", "$setOnInsert"):
            for kk, vv in fields.items():
                if "." in kk:
                    _set_path(doc, kk, vv)
                else:
                    doc[kk] = vv


def _project(doc: Dict[str, Any], projection: Optional[Dict[str, int]]) -> Dict[str, Any]:
    out = dict(doc)
    if projection and projection.get("_id") == 0:
        out.pop("_id", None)
    return out


class FakeCursor:
    def __init__(self, docs: List[Dict[str, Any]]):
        self._docs = docs

    def sort(self, field, direction=-1):
        self._docs = sorted(
            self._docs,
            key=lambda d: (d.get(field) is None, d.get(field)),
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
        self.database = database

    def insert_one(self, doc):
        doc.setdefault("_id", f"oid-{self._n}")
        self._n += 1
        if any(d.get("_id") == doc["_id"] for d in self.docs):
            # Faithful to real Mongo: _id is always unique-indexed.
            from pymongo.errors import DuplicateKeyError

            raise DuplicateKeyError(f"E11000 duplicate key error: _id {doc['_id']}")
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
                _apply_update(d, {k: v for k, v in update.items() if k != "$setOnInsert"})
                return type("R", (), {"modified_count": 1, "matched_count": 1})()
        if upsert:
            self._upsert(query, update)
            return type("R", (), {"modified_count": 0, "matched_count": 0, "upserted_id": 1})()
        return type("R", (), {"modified_count": 0, "matched_count": 0})()

    def find_one_and_update(self, query, update, return_document=None, upsert=False, **_kw):
        for d in self.docs:
            if _matches(d, query):
                _apply_update(d, {k: v for k, v in update.items() if k != "$setOnInsert"})
                return _project(d, None)
        if upsert:
            d = self._upsert(query, update)
            return _project(d, None)
        return None

    def _upsert(self, query, update):
        base: Dict[str, Any] = {}
        for k, v in query.items():
            if not isinstance(v, dict):
                base[k] = v
        base.update(update.get("$setOnInsert", {}))
        _apply_update(base, {k: v for k, v in update.items() if k != "$setOnInsert"})
        self.insert_one(base)
        return base

    def create_index(self, *a, **k):
        return "idx"


class FakeDB:
    def __init__(self):
        self._collections: Dict[str, FakeCollection] = {}
        self.is_connected = True

    def get_collection(self, name: str) -> FakeCollection:
        if name not in self._collections:
            self._collections[name] = FakeCollection(database=self)
        return self._collections[name]

    def __getitem__(self, name: str) -> FakeCollection:
        return self.get_collection(name)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture()
def db() -> FakeDB:
    return FakeDB()


@pytest.fixture(autouse=True)
def _isolate_entity_resolver(monkeypatch):
    """The store->entity resolver memoizes via E2's cache + a `stores` lookup.
    Pin it so scope tests are deterministic and never hit the real (absent) DB.
    Tests that need an entity override set it explicitly."""
    monkeypatch.setattr(
        "api.services.policy_engine._resolve_entity_id",
        lambda store_id: None,
    )


@pytest.fixture(autouse=True)
def _capture_audit(monkeypatch):
    """Capture AuditRepository.create calls so the lock/map-write audit is
    observable WITHOUT a live audit chain. We monkeypatch the repo accessor the
    service uses (get_audit_repository) -- the SAME path the real code takes."""
    rows: List[Dict[str, Any]] = []

    class _Repo:
        def create(self, data):
            rows.append(dict(data))
            return {"log_id": f"AUD-{len(rows)}"}

    monkeypatch.setattr(
        "api.dependencies.get_audit_repository",
        lambda: _Repo(),
    )
    return rows


def _pay(method, amount, **extra):
    row = {
        "payment_id": extra.pop("payment_id", "PAY-x"),
        "method": method,
        "amount": amount,
        "reference": extra.pop("reference", None),
        "received_by": extra.pop("received_by", "U1"),
        "received_at": extra.pop("received_at", "2026-06-09T10:00:00"),
        "idempotency_key": extra.pop("idempotency_key", "IK-x"),
    }
    row.update(extra)
    return row


# ============================================================================
# 1 - instruments stop booking as Cash
# ============================================================================


def test_mixed_upi_card_hits_bank_ledgers_zero_on_cash():
    # 60% UPI / 40% CARD on a 1000 order.
    payments = [_pay("UPI", 600.0), _pay("CARD", 400.0)]
    legs = build_tender_jv_legs(payments)
    ledgers = {leg["ledger"]: leg["amount"] for leg in legs}
    assert ledgers.get("Bank A/c - UPI") == 600.0
    assert ledgers.get("Bank A/c - Card EDC") == 400.0
    # Zero on Cash A/c -- the whole point of E5.
    assert "Cash A/c" not in ledgers
    assert all(leg["is_cash_in"] for leg in legs)


# ============================================================================
# 2 - unknown tender -> Suspense, NOT cash
# ============================================================================


def test_blank_method_maps_to_unknown_not_cash():
    assert canonicalize_tender("", None) == "UNKNOWN"
    assert canonicalize_tender(None, None) == "UNKNOWN"
    assert canonicalize_tender("wibble", None) == "UNKNOWN"
    # And it is NOT cash.
    assert canonicalize_tender("", None) != "CASH"


def test_unknown_tender_surfaces_under_unknown_suspense():
    payments = [_pay("", 250.0), _pay("CASH", 750.0)]
    by_mode = split_payments_by_mode(payments)
    assert by_mode["UNKNOWN"]["count"] == 1
    assert by_mode["UNKNOWN"]["net"] == 250.0
    # Cash is exactly the real cash row -- the blank row was NOT folded in.
    assert by_mode["CASH"]["net"] == 750.0
    assert resolve_ledger("UNKNOWN") == "Suspense A/c"


# ============================================================================
# 3 - by-mode net sums to the order paid total (paise-exact)
# ============================================================================


def test_by_mode_net_sums_to_order_total_paise_exact():
    payments = [_pay("CASH", 333.33), _pay("UPI", 333.33), _pay("CARD", 333.34)]
    by_mode = split_payments_by_mode(payments)
    total = round(sum(r["net"] for r in by_mode.values()), 2)
    assert total == 1000.0


# ============================================================================
# 4 - refund contras the SAME ledger (negative leg)
# ============================================================================


def test_card_refund_contras_same_card_ledger():
    payments = [_pay("CARD", 1000.0), _pay("CARD", -300.0)]  # sale then partial refund
    by_mode = split_payments_by_mode(payments)
    assert by_mode["CARD"]["collected"] == 1000.0
    assert by_mode["CARD"]["refunded"] == 300.0
    assert by_mode["CARD"]["net"] == 700.0
    legs = build_tender_jv_legs(payments)
    card_legs = [leg for leg in legs if leg["tender"] == "CARD"]
    assert len(card_legs) == 1  # ONE leg, same ledger -- no separate reversal ledger
    assert card_legs[0]["ledger"] == "Bank A/c - Card EDC"
    assert card_legs[0]["amount"] == 700.0
    # A refund of a CARD payment still resolves to the CARD ledger.
    assert resolve_ledger("CARD", is_refund=True) == "Bank A/c - Card EDC"


# ============================================================================
# 5 - non-cash-in -> liability / receivable ledgers, never a bank ledger
# ============================================================================


def test_non_cash_in_routes_to_liability_not_bank():
    assert resolve_ledger("GIFT_VOUCHER") == "Gift Voucher Liability"
    assert resolve_ledger("LOYALTY") == "Loyalty Points Liability"
    assert resolve_ledger("CREDIT") == "Sundry Debtors"
    assert resolve_ledger("EMI") == "EMI Finance Receivable"
    assert resolve_ledger("STORE_CREDIT") == "Customer Store Credit Liability"
    # None of these are flagged as cash-in.
    for t in ("GIFT_VOUCHER", "LOYALTY", "CREDIT", "EMI", "STORE_CREDIT"):
        assert t not in CASH_IN_TENDERS
        assert "Bank A/c" not in resolve_ledger(t)


# ============================================================================
# 6 - Tally voucher balances (and an unbalanced set raises)
# ============================================================================


def test_assert_voucher_balanced_passes_and_raises():
    # A balanced mixed-tender day voucher: receipt legs (debit, +) vs the party /
    # sales+tax legs (credit, -). Sum to zero paise.
    balanced = [
        {"ledger": "Bank A/c - UPI", "amount": 600.0},
        {"ledger": "Bank A/c - Card EDC", "amount": 400.0},
        {"ledger": "Sales A/c", "amount": -952.38},
        {"ledger": "CGST Output", "amount": -23.81},
        {"ledger": "SGST Output", "amount": -23.81},
    ]
    assert_voucher_balanced(balanced)  # no raise

    unbalanced = [
        {"ledger": "Bank A/c - UPI", "amount": 600.0},
        {"ledger": "Sales A/c", "amount": -500.0},
    ]
    with pytest.raises(ValueError):
        assert_voucher_balanced(unbalanced)


def test_jv_legs_plus_sales_tax_balances():
    # Build the receipt legs from real payments + a matching sales/tax credit set,
    # then prove the whole voucher nets to zero paise.
    payments = [_pay("UPI", 600.0), _pay("CARD", 400.0)]
    receipt_legs = build_tender_jv_legs(payments)  # +600 +400
    taxable = 952.38
    cgst = 23.81
    sgst = 1000.0 - taxable - cgst  # exact residual on SGST
    voucher = receipt_legs + [
        {"ledger": "Sales A/c", "amount": -taxable},
        {"ledger": "CGST Output", "amount": -cgst},
        {"ledger": "SGST Output", "amount": -sgst},
    ]
    assert_voucher_balanced(voucher)


# ============================================================================
# 7 - E2-layered map (store override beats global; source surfaced)
# ============================================================================


def test_store_override_beats_global_for_that_store_only(db):
    # Global default for UPI is the code default; set a STORE-scope override.
    trec.set_tender_ledger(
        db, scope="STORE:BV-1", tender="UPI", ledger="Bank A/c - HDFC UPI",
        actor={"user_id": "U1"},
    )
    # That store sees the override.
    eff_store = trec.get_effective_tender_map(db, store_id="BV-1")
    assert eff_store["UPI"] == "Bank A/c - HDFC UPI"
    # A different store still sees the global default.
    eff_other = trec.get_effective_tender_map(db, store_id="BV-2")
    assert eff_other["UPI"] == IMS_DEFAULT_LEDGERS["UPI"]
    # The with-sources view reports the inheritance level.
    rows = trec.get_effective_tender_map_with_sources(db, store_id="BV-1")
    assert rows["UPI"]["ledger"] == "Bank A/c - HDFC UPI"
    assert rows["UPI"]["source"] == "store"
    # CASH (no override) reports as default.
    assert rows["CASH"]["source"] == "default"


def test_entity_override_layers_under_store(db, monkeypatch):
    # Resolve store BV-9 to entity ENT-7.
    monkeypatch.setattr(
        "api.services.policy_engine._resolve_entity_id",
        lambda store_id: "ENT-7" if store_id == "BV-9" else None,
    )
    trec.set_tender_ledger(db, scope="ENTITY:ENT-7", tender="CARD", ledger="Bank A/c - Entity EDC", actor={"user_id": "U1"})
    eff = trec.get_effective_tender_map(db, store_id="BV-9")
    assert eff["CARD"] == "Bank A/c - Entity EDC"
    rows = trec.get_effective_tender_map_with_sources(db, store_id="BV-9")
    assert rows["CARD"]["source"] == "entity"


def test_map_write_emits_audit_row(db, _capture_audit):
    trec.set_tender_ledger(db, scope="GLOBAL", tender="UPI", ledger="Bank A/c - X", actor={"user_id": "U1"})
    actions = [r["action"] for r in _capture_audit]
    assert "tender_ledger_map_update" in actions


# ============================================================================
# 8 - POS capture UNCHANGED (stamp is additive, capture fields untouched)
# ============================================================================


def test_stamp_is_additive_never_rewrites_capture(db):
    order = {
        "order_id": "ORD-1",
        "store_id": "BV-1",
        "payments": [_pay("UPI", 600.0, payment_id="P1", reference="utr-123")],
    }
    db.get_collection("orders").insert_one(dict(order))
    res = trec.stamp_payment_ledgers(db, order)
    assert res["stamped"] == 1
    stored = db.get_collection("orders").find_one({"order_id": "ORD-1"})
    p = stored["payments"][0]
    # Capture fields are byte-identical.
    assert p["method"] == "UPI"
    assert p["amount"] == 600.0
    assert p["reference"] == "utr-123"
    assert p["payment_id"] == "P1"
    assert p["received_by"] == "U1"
    assert p["idempotency_key"] == "IK-x"
    # Derived fields are ADDED.
    assert p["canonical_tender"] == "UPI"
    assert p["ledger"] == IMS_DEFAULT_LEDGERS["UPI"]
    assert "ledger_stamped_at" in p


def test_resolver_derives_on_the_fly_when_stamp_absent(db):
    # An old order with NO stamp still reconciles correctly (resolver reads method).
    db.get_collection("orders").insert_one(
        {"order_id": "OLD-1", "store_id": "BV-1",
         "created_at": "2026-06-09T10:00:00",
         "payments": [_pay("CASH", 500.0)]}
    )
    recon = trec.reconcile_window(db, "BV-1", "2026-06-09T00:00:00", "2026-06-10T00:00:00")
    assert recon["by_mode"]["CASH"]["net"] == 500.0
    assert recon["by_mode"]["CASH"]["ledger"] == "Cash A/c"


# ============================================================================
# 9 - lock is atomic + immutable
# ============================================================================


def test_snapshot_build_then_lock(db):
    db.get_collection("orders").insert_one(
        {"order_id": "O1", "store_id": "BV-1", "created_at": "2026-06-09T10:00:00",
         "payments": [_pay("CASH", 400.0), _pay("UPI", 600.0)]}
    )
    snap = trec.build_reconciliation_snapshot(db, "BV-1", "2026-06-09T00:00:00", "2026-06-10T00:00:00")
    assert snap["status"] == "OPEN"
    assert snap["total_net"] == 1000.0
    sid = snap["snapshot_id"]
    res = trec.lock_reconciliation(db, sid, actor={"user_id": "U1"})
    assert res["ok"] is True
    assert res["snapshot"]["status"] == "LOCKED"


def test_two_concurrent_locks_exactly_one_wins(db, _capture_audit):
    db.get_collection("orders").insert_one(
        {"order_id": "O2", "store_id": "BV-1", "created_at": "2026-06-09T10:00:00",
         "payments": [_pay("CASH", 100.0)]}
    )
    snap = trec.build_reconciliation_snapshot(db, "BV-1", "2026-06-09T00:00:00", "2026-06-10T00:00:00")
    sid = snap["snapshot_id"]
    r1 = trec.lock_reconciliation(db, sid, actor={"user_id": "U1"})
    r2 = trec.lock_reconciliation(db, sid, actor={"user_id": "U2"})
    oks = [r1["ok"], r2["ok"]]
    assert oks.count(True) == 1, "exactly one lock must succeed"
    loser = r1 if not r1["ok"] else r2
    assert loser["error"] == "already_locked"
    assert loser["http"] == 409
    # Exactly ONE lock-audit row.
    lock_rows = [r for r in _capture_audit if r["action"] == "payment_reconciliation_lock"]
    assert len(lock_rows) == 1


def test_lock_missing_snapshot_is_404(db):
    res = trec.lock_reconciliation(db, "RECON-nope", actor={"user_id": "U1"})
    assert res["ok"] is False
    assert res["http"] == 404


def test_lock_route_403_for_cross_store_actor(db, monkeypatch):
    """P1 regression (adversarial): the lock ROUTE must store-scope the actor
    BEFORE the irreversible lock. A BV-1 ACCOUNTANT must NOT lock a BV-2 snapshot
    (cross-store IDOR permanently freezing another store's cash-variance SoR).
    validate_store_access store-scopes ACCOUNTANT (only SUPERADMIN/ADMIN bypass)."""
    import asyncio
    from fastapi import HTTPException
    from api.routers import reconciliation as recmod

    db.get_collection("orders").insert_one(
        {"order_id": "OX", "store_id": "BV-2", "created_at": "2026-06-09T10:00:00",
         "payments": [_pay("CASH", 100.0)]}
    )
    snap = trec.build_reconciliation_snapshot(db, "BV-2", "2026-06-09T00:00:00", "2026-06-10T00:00:00")
    sid = snap["snapshot_id"]
    monkeypatch.setattr(recmod, "_get_db", lambda: db)
    bv1_accountant = {"user_id": "A1", "roles": ["ACCOUNTANT"],
                      "store_ids": ["BV-1"], "active_store_id": "BV-1"}
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            recmod.lock_reconciliation_snapshot(sid, current_user=bv1_accountant)
        )
    assert exc.value.status_code == 403
    # The foreign snapshot stays OPEN -- the IDOR lock was blocked.
    assert trec.get_snapshot(db, sid)["status"] == "OPEN"


def test_two_concurrent_builds_converge_on_one_open_doc(db):
    """P3 regression (adversarial): two racing FIRST builds for the same
    store/day must converge on ONE deterministic OPEN doc
    (RECON-<store>-<day>), not two uuid-suffixed docs whose later lock 500s on
    the LOCKED partial-unique index. Simulated by hiding the existing-doc
    lookup for both builds (both race BEFORE either insert lands)."""
    db.get_collection("orders").insert_one(
        {"order_id": "O9", "store_id": "BV-1", "created_at": "2026-06-09T10:00:00",
         "payments": [_pay("CASH", 100.0)]}
    )
    coll = db.get_collection("payment_reconciliations")
    orig_find_one = coll.find_one
    coll.find_one = lambda *a, **k: None  # type: ignore[method-assign]
    try:
        s1 = trec.build_reconciliation_snapshot(db, "BV-1", "2026-06-09T00:00:00", "2026-06-10T00:00:00")
        s2 = trec.build_reconciliation_snapshot(db, "BV-1", "2026-06-09T00:00:00", "2026-06-10T00:00:00")
    finally:
        coll.find_one = orig_find_one  # type: ignore[method-assign]
    assert s1["snapshot_id"] == s2["snapshot_id"] == "RECON-BV-1-2026-06-09"
    assert s1["persisted"] is True and s2["persisted"] is True
    assert len(coll.docs) == 1, "concurrent builds must converge on ONE doc"
    # The single doc locks cleanly: first wins, second is a clean 409.
    r1 = trec.lock_reconciliation(db, s1["snapshot_id"], actor={"user_id": "U1"})
    assert r1["ok"] is True
    r2 = trec.lock_reconciliation(db, s2["snapshot_id"], actor={"user_id": "U2"})
    assert r2["ok"] is False and r2["http"] == 409


def test_legacy_uuid_suffixed_open_doc_is_reused_not_duplicated(db):
    """Prod docs minted before the deterministic id keep working: the
    existing-doc lookup is by (store_id, window_start), so a rebuild reuses the
    legacy uuid-suffixed _id verbatim instead of minting a sibling."""
    legacy_id = "RECON-BV-1-2026-06-09-deadbeef"
    db.get_collection("payment_reconciliations").insert_one({
        "_id": legacy_id, "snapshot_id": legacy_id, "store_id": "BV-1",
        "window_start": "2026-06-09T00:00:00", "window_end": "2026-06-10T00:00:00",
        "by_mode": {}, "total_net": 0.0, "status": "OPEN",
    })
    db.get_collection("orders").insert_one(
        {"order_id": "O10", "store_id": "BV-1", "created_at": "2026-06-09T10:00:00",
         "payments": [_pay("UPI", 750.0)]}
    )
    snap = trec.build_reconciliation_snapshot(db, "BV-1", "2026-06-09T00:00:00", "2026-06-10T00:00:00")
    assert snap["snapshot_id"] == legacy_id  # reused, not re-minted
    assert snap["total_net"] == 750.0
    assert len(db.get_collection("payment_reconciliations").docs) == 1
    # And the legacy-id doc locks normally.
    res = trec.lock_reconciliation(db, legacy_id, actor={"user_id": "U1"})
    assert res["ok"] is True


def test_lock_dupkey_from_partial_unique_index_is_409_not_500(db):
    """P3 regression (adversarial): a DuplicateKeyError raised by the LOCKED
    partial-unique index (a sibling doc for the same store/day is already
    LOCKED) must surface as 409 already_locked -- NOT a 500 lock_failed."""
    from pymongo.errors import DuplicateKeyError

    db.get_collection("orders").insert_one(
        {"order_id": "O11", "store_id": "BV-1", "created_at": "2026-06-09T10:00:00",
         "payments": [_pay("CASH", 100.0)]}
    )
    snap = trec.build_reconciliation_snapshot(db, "BV-1", "2026-06-09T00:00:00", "2026-06-10T00:00:00")
    coll = db.get_collection("payment_reconciliations")

    def _raise_dup(*_a, **_k):
        raise DuplicateKeyError("E11000 duplicate key error: uniq_locked_recon_per_store_day")

    orig = coll.find_one_and_update
    coll.find_one_and_update = _raise_dup  # type: ignore[method-assign]
    try:
        res = trec.lock_reconciliation(db, snap["snapshot_id"], actor={"user_id": "U1"})
    finally:
        coll.find_one_and_update = orig  # type: ignore[method-assign]
    assert res["ok"] is False
    assert res["error"] == "already_locked"
    assert res["http"] == 409
    # The snapshot itself is untouched (still OPEN).
    assert trec.get_snapshot(db, snap["snapshot_id"])["status"] == "OPEN"


def test_stale_rebuild_cannot_overwrite_a_locked_snapshot(db):
    """Race-window hardening: a rebuild whose existing-doc read went stale
    (missed the just-LOCKED doc) must NOT overwrite the LOCKED snapshot -- the
    guarded upsert skips it and the figures stay frozen."""
    db.get_collection("orders").insert_one(
        {"order_id": "O12", "store_id": "BV-1", "created_at": "2026-06-09T10:00:00",
         "payments": [_pay("CASH", 500.0)]}
    )
    snap = trec.build_reconciliation_snapshot(db, "BV-1", "2026-06-09T00:00:00", "2026-06-10T00:00:00")
    assert trec.lock_reconciliation(db, snap["snapshot_id"], actor={"user_id": "U1"})["ok"] is True
    # A later sale lands, then a STALE rebuild races (its lookup sees nothing).
    db.get_collection("orders").insert_one(
        {"order_id": "O13", "store_id": "BV-1", "created_at": "2026-06-09T11:00:00",
         "payments": [_pay("UPI", 9999.0)]}
    )
    coll = db.get_collection("payment_reconciliations")
    orig_find_one = coll.find_one
    coll.find_one = lambda *a, **k: None  # type: ignore[method-assign]
    try:
        rebuilt = trec.build_reconciliation_snapshot(db, "BV-1", "2026-06-09T00:00:00", "2026-06-10T00:00:00")
    finally:
        coll.find_one = orig_find_one  # type: ignore[method-assign]
    assert rebuilt.get("persisted") is False  # fail-soft envelope, not a write
    stored = trec.get_snapshot(db, snap["snapshot_id"])
    assert stored["status"] == "LOCKED"
    assert stored["total_net"] == 500.0  # frozen, not 10499


def test_locked_snapshot_is_immutable_on_rebuild(db):
    db.get_collection("orders").insert_one(
        {"order_id": "O3", "store_id": "BV-1", "created_at": "2026-06-09T10:00:00",
         "payments": [_pay("CASH", 500.0)]}
    )
    snap = trec.build_reconciliation_snapshot(db, "BV-1", "2026-06-09T00:00:00", "2026-06-10T00:00:00")
    sid = snap["snapshot_id"]
    trec.lock_reconciliation(db, sid, actor={"user_id": "U1"})
    # A later sale + rebuild must NOT mutate the locked snapshot's figures.
    db.get_collection("orders").insert_one(
        {"order_id": "O4", "store_id": "BV-1", "created_at": "2026-06-09T11:00:00",
         "payments": [_pay("UPI", 9999.0)]}
    )
    rebuilt = trec.build_reconciliation_snapshot(db, "BV-1", "2026-06-09T00:00:00", "2026-06-10T00:00:00")
    assert rebuilt["status"] == "LOCKED"
    assert rebuilt["total_net"] == 500.0  # frozen, not 10499


# ============================================================================
# 10 - STORE_CREDIT deferred (in the default map; forward-compat only)
# ============================================================================


def test_store_credit_in_default_map_but_not_a_pos_capture():
    # Present in the default ledger map (forward-compat).
    assert "STORE_CREDIT" in IMS_DEFAULT_LEDGERS
    assert resolve_ledger("STORE_CREDIT") == "Customer Store Credit Liability"
    # But STORE_CREDIT is NOT one of orders.PaymentMethod's enum values today.
    from api.routers.orders import PaymentMethod

    assert "STORE_CREDIT" not in {m.value for m in PaymentMethod}


# ============================================================================
# Extra: routing guarantees the bug can't regress
# ============================================================================


def test_aliases_normalize_but_unknown_never_becomes_cash():
    assert canonicalize_tender("gpay") == "UPI"
    assert canonicalize_tender("PhonePe") == "UPI"
    assert canonicalize_tender("credit_card") == "CARD"
    assert canonicalize_tender("NEFT") == "BANK_TRANSFER"
    assert canonicalize_tender("points") == "LOYALTY"
    # A garbage value still falls to UNKNOWN -- never CASH.
    assert canonicalize_tender("zzz-machine") == "UNKNOWN"


def test_reconcile_window_db_absent_is_empty_not_crash():
    out = trec.reconcile_window(None, "BV-1", "2026-06-09T00:00:00", "2026-06-10T00:00:00")
    assert out["by_mode"] == {}
    assert out["total_net"] == 0.0


def test_ensure_indexes_is_idempotent_and_failsoft(db):
    trec.ensure_reconciliation_indexes(db)
    trec.ensure_reconciliation_indexes(db)  # second call no-ops
    trec.ensure_reconciliation_indexes(None)  # DB absent -> no raise
