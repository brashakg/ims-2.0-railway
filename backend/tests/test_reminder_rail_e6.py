"""
IMS 2.0 - E6 reminder-rail acceptance tests (intent-level)
==========================================================
Asserts the INTENDED behavior of the reminder rail, not mere existence. A hollow
shell (router returns 200 with empty counts but never runs the gates, writes the
ledger, or calls send_notification) FAILS these.

Coverage (per the build packet TESTS list):
  (a) frequency cap is a SOFT ceiling -- a MARKETING send beyond max in the
      window is blocked; an OTP / transactional send is NOT (short-circuits).
  (b) passes_gates blocks an opted-out recipient AND a quiet-hours recipient
      (the latter DEFERS rather than drops).
  (c) each new segment resolver (cl_reorder / churn_risk / fu_due_today) returns
      the right cohort on seeded data.
  (d) fu_due_today routes CALL -> staff task vs WHATSAPP -> send.
  (e) preview / dry_run NEVER writes a send / ledger / voucher / task row.
  (f) seeded rules are active=False (no auto-send on deploy).
  (g) every /reminders/* route is present in rbac_policy.POLICY (coverage-lock).

Plus: family-wallet OTP atomic consume (two verifiers -> at most one ok).

Self-contained: a fake in-memory DB (supports find_one_and_update for the OTP
atomic test) + a stubbed send_notification (NO live DB, NO provider). Nothing
here touches MSG91 or flips DISPATCH_MODE.
"""

from __future__ import annotations

import asyncio
import copy
import os
import sys
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
os.environ.setdefault("DISPATCH_MODE", "off")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services import reminder_rail as rail  # noqa: E402
from api.services import campaign_segments as seg  # noqa: E402


# ---------------------------------------------------------------------------
# Fake Mongo (adds find_one_and_update on top of the campaigns-test harness)
# ---------------------------------------------------------------------------


def _matches(doc, query):
    for key, val in (query or {}).items():
        if key == "$or":
            if not any(_matches(doc, sub) for sub in val):
                return False
            continue
        if key == "$and":
            if not all(_matches(doc, sub) for sub in val):
                return False
            continue
        actual = doc.get(key)
        if isinstance(val, dict):
            if "$in" in val and actual not in val["$in"]:
                return False
            if "$nin" in val and actual in val["$nin"]:
                return False
            if "$gte" in val and (actual is None or actual < val["$gte"]):
                return False
            if "$lte" in val and (actual is None or actual > val["$lte"]):
                return False
            if "$ne" in val and actual == val["$ne"]:
                return False
            if "$exists" in val:
                present = key in doc
                if val["$exists"] != present:
                    return False
        else:
            if actual != val:
                return False
    return True


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, *_a, **_k):
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    def __iter__(self):
        return iter(self._docs)


class _FakeColl:
    def __init__(self, docs=None):
        self.docs = [copy.deepcopy(d) for d in (docs or [])]
        self._lock = threading.Lock()

    def find_one(self, query=None, projection=None, sort=None):
        for d in self.docs:
            if _matches(d, query or {}):
                return copy.deepcopy(d)
        return None

    def find(self, query=None, projection=None):
        return _Cursor([copy.deepcopy(d) for d in self.docs if _matches(d, query or {})])

    def count_documents(self, query=None):
        return sum(1 for d in self.docs if _matches(d, query or {}))

    def insert_one(self, doc):
        self.docs.append(copy.deepcopy(doc))

    def _apply_update(self, doc, update):
        if "$set" in update:
            for k, v in update["$set"].items():
                doc[k] = v
        if "$inc" in update:
            for k, v in update["$inc"].items():
                doc[k] = (doc.get(k, 0) or 0) + v

    def update_one(self, query, update, **kw):
        for d in self.docs:
            if _matches(d, query or {}):
                self._apply_update(d, update)
                return type("R", (), {"modified_count": 1, "matched_count": 1})()
        return type("R", (), {"modified_count": 0, "matched_count": 0})()

    def find_one_and_update(self, query, update, **kw):
        # Atomic match-and-modify under a lock so two concurrent callers cannot
        # both match a guarded PENDING filter (mirrors Mongo's guarantee).
        with self._lock:
            for d in self.docs:
                if _matches(d, query or {}):
                    self._apply_update(d, update)
                    return copy.deepcopy(d)
            return None

    def delete_one(self, query):
        for i, d in enumerate(self.docs):
            if _matches(d, query or {}):
                del self.docs[i]
                return type("R", (), {"deleted_count": 1})()
        return type("R", (), {"deleted_count": 0})()

    def create_index(self, *_a, **_k):
        return "idx"


class _FakeDB:
    is_connected = True

    def __init__(self, collections=None):
        self._cols = collections or {}

    def get_collection(self, name):
        if name not in self._cols:
            self._cols[name] = _FakeColl()
        return self._cols[name]


class _SendRecorder:
    """Captures send_notification calls + writes a notification_logs row exactly
    like the real one. NEVER touches a provider."""

    def __init__(self, db):
        self.db = db
        self.calls = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        nid = f"NTF-{uuid.uuid4().hex[:8].upper()}"
        self.db.get_collection("notification_logs").insert_one(
            {
                "notification_id": nid,
                "customer_id": kwargs.get("customer_id", ""),
                "customer_phone": kwargs.get("customer_phone", ""),
                "template_id": kwargs.get("template_id", ""),
                "channel": kwargs.get("channel", "WHATSAPP"),
                "category": kwargs.get("category", "MARKETING"),
                "status": "PENDING",
                "created_at": datetime.now().isoformat(),
            }
        )
        return {"notification_id": nid, "dispatched": False, "status": "PENDING"}


def _patch_send(monkeypatch, db):
    """Patch the lazy send_notification import that evaluate_rule pulls in, plus
    consent + quiet-hours, to fully isolated stubs."""
    rec = _SendRecorder(db)
    import api.services.notification_service as ns

    monkeypatch.setattr(ns, "send_notification", rec)
    # Default: not opted out, not in quiet hours, cap default 3 (no E2 DB).
    monkeypatch.setattr(rail, "_is_opted_out", lambda db, cid: False)
    monkeypatch.setattr(rail, "_in_quiet_hours", lambda now=None: False)
    monkeypatch.setattr(rail, "_cap_limit", lambda rule=None: 3)
    return rec


def _rule(**over):
    base = {
        "rule_id": "RMD-TEST-1",
        "name": "Test rule",
        "rule_type": "winback",
        "segment_key": "by_store",
        "segment_params": {},
        "channel": "WHATSAPP",
        "template_id": "WALKOUT_RECOVERY",
        "store_id": "BV-PUN-01",
        "is_transactional": False,
        "freq_cap_exempt": False,
        "voucher_template": None,
        "active": True,
    }
    base.update(over)
    return base


def _run(coro):
    return asyncio.run(coro)


def _cust(cid, **over):
    d = {
        "customer_id": cid,
        "name": f"Cust {cid}",
        "mobile": f"90000000{cid[-2:] if len(cid) >= 2 else cid}",
        "store_id": "BV-PUN-01",
    }
    d.update(over)
    return d


# ===========================================================================
# (a) Frequency cap is a SOFT ceiling; OTP/transactional short-circuits
# ===========================================================================


def test_freq_cap_blocks_marketing_beyond_max(monkeypatch):
    now = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    # 3 MARKETING rows for X in the last 29 days -> at the cap.
    ledger = _FakeColl(
        [
            {"customer_id": "X", "category": "MARKETING", "sent_at": (now - timedelta(days=d)).isoformat()}
            for d in (1, 5, 28)
        ]
    )
    db = _FakeDB({"comms_ledger": ledger})
    monkeypatch.setattr(rail, "_cap_limit", lambda rule=None: 3)
    # MARKETING send is blocked (count 3 >= 3).
    assert rail.check_frequency_cap(db, "X", now=now, category="MARKETING") is False
    # OTP / transactional short-circuits the cap entirely.
    assert rail.check_frequency_cap(db, "X", now=now, category="OTP") is True
    assert rail.check_frequency_cap(db, "X", now=now, category="SERVICE") is True


def test_freq_cap_window_excludes_old_row(monkeypatch):
    now = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    # 2 inside the window + 1 at day 31 (outside) -> count is 2 -> allowed.
    ledger = _FakeColl(
        [
            {"customer_id": "X", "category": "MARKETING", "sent_at": (now - timedelta(days=2)).isoformat()},
            {"customer_id": "X", "category": "MARKETING", "sent_at": (now - timedelta(days=10)).isoformat()},
            {"customer_id": "X", "category": "MARKETING", "sent_at": (now - timedelta(days=31)).isoformat()},
        ]
    )
    db = _FakeDB({"comms_ledger": ledger})
    monkeypatch.setattr(rail, "_cap_limit", lambda rule=None: 3)
    assert rail.check_frequency_cap(db, "X", now=now, category="MARKETING") is True


def test_passes_gates_transactional_bypasses_cap(monkeypatch):
    now = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    ledger = _FakeColl(
        [
            {"customer_id": "X", "category": "MARKETING", "sent_at": (now - timedelta(days=d)).isoformat()}
            for d in (1, 5, 28)
        ]
    )
    db = _FakeDB({"comms_ledger": ledger, "customers": _FakeColl([_cust("X")])})
    monkeypatch.setattr(rail, "_is_opted_out", lambda db, cid: True)  # even opted out
    monkeypatch.setattr(rail, "_in_quiet_hours", lambda now=None: True)  # even quiet
    monkeypatch.setattr(rail, "_cap_limit", lambda rule=None: 3)
    recipient = {"customer_id": "X", "phone": "9000000001", "name": "X"}
    # Marketing rule: blocked by consent first.
    ok, reason, _ = rail.passes_gates(db, _rule(), recipient, now=now)
    assert ok is False and reason == "consent"
    # Transactional rule: bypasses consent + quiet + cap.
    ok, reason, _ = rail.passes_gates(
        db, _rule(is_transactional=True), recipient, now=now
    )
    assert ok is True and reason is None


# ===========================================================================
# (b) passes_gates blocks opted-out + defers quiet-hours
# ===========================================================================


def test_gate_blocks_opted_out(monkeypatch):
    db = _FakeDB({"comms_ledger": _FakeColl()})
    monkeypatch.setattr(rail, "_is_opted_out", lambda db, cid: True)
    monkeypatch.setattr(rail, "_in_quiet_hours", lambda now=None: False)
    ok, reason, _ = rail.passes_gates(
        db, _rule(), {"customer_id": "Y", "phone": "9", "name": "Y"}
    )
    assert ok is False and reason == "consent"


def test_gate_quiet_hours_defers_not_drops(monkeypatch):
    db = _FakeDB({"comms_ledger": _FakeColl()})
    monkeypatch.setattr(rail, "_is_opted_out", lambda db, cid: False)
    monkeypatch.setattr(rail, "_in_quiet_hours", lambda now=None: True)
    monkeypatch.setattr(
        rail, "_next_quiet_end_utc_iso", lambda now=None: "2026-06-02T03:30:00+00:00"
    )
    monkeypatch.setattr(rail, "_cap_limit", lambda rule=None: 3)
    ok, reason, meta = rail.passes_gates(
        db, _rule(), {"customer_id": "Z", "phone": "9", "name": "Z"}
    )
    # Passes (not dropped) but carries a deferred scheduled_for.
    assert ok is True and reason is None
    assert meta["scheduled_for"] == "2026-06-02T03:30:00+00:00"


def test_transactional_is_not_deferred(monkeypatch):
    db = _FakeDB({"comms_ledger": _FakeColl()})
    monkeypatch.setattr(rail, "_in_quiet_hours", lambda now=None: True)
    ok, reason, meta = rail.passes_gates(
        db, _rule(is_transactional=True), {"customer_id": "Z", "phone": "9", "name": "Z"}
    )
    assert ok is True and meta["scheduled_for"] is None


# ===========================================================================
# (c) new segment resolvers return the right cohort
# ===========================================================================


def test_segment_cl_reorder():
    now = datetime(2026, 6, 1, 12, 0)
    custs = _FakeColl([_cust("C1"), _cust("C2")])
    orders = _FakeColl(
        [
            # C1 bought CL 45 days ago -> due for reorder (cadence 30).
            {
                "order_id": "O1",
                "customer_id": "C1",
                "store_id": "BV-PUN-01",
                "created_at": (now - timedelta(days=45)).isoformat(),
                "items": [{"item_type": "CONTACT_LENS"}],
            },
            # C2 bought CL 5 days ago -> NOT due yet.
            {
                "order_id": "O2",
                "customer_id": "C2",
                "store_id": "BV-PUN-01",
                "created_at": (now - timedelta(days=5)).isoformat(),
                "items": [{"item_type": "CONTACT_LENS"}],
            },
        ]
    )
    db = _FakeDB({"customers": custs, "orders": orders})
    rows = seg.resolve_segment(db, "cl_reorder", store_id="BV-PUN-01", params={"now": now})
    assert {r["customer_id"] for r in rows} == {"C1"}


def test_segment_churn_risk():
    now = datetime(2026, 6, 1, 12, 0)
    custs = _FakeColl([_cust("A"), _cust("B"), _cust("D")])
    orders = _FakeColl(
        [
            # A bought 120 days ago, never since -> churn risk (>90d lapse).
            {"order_id": "O1", "customer_id": "A", "store_id": "BV-PUN-01", "created_at": (now - timedelta(days=120)).isoformat()},
            # B bought 10 days ago -> active, not churn.
            {"order_id": "O2", "customer_id": "B", "store_id": "BV-PUN-01", "created_at": (now - timedelta(days=10)).isoformat()},
            # D never ordered -> not churn risk (never engaged).
        ]
    )
    db = _FakeDB({"customers": custs, "orders": orders})
    rows = seg.resolve_segment(db, "churn_risk", store_id="BV-PUN-01", params={"now": now})
    ids = {r["customer_id"] for r in rows}
    assert "A" in ids
    assert "B" not in ids
    assert "D" not in ids


def test_segment_fu_due_today_cohort():
    today = datetime.now().date().isoformat()
    custs = _FakeColl([_cust("A"), _cust("B"), _cust("C")])
    follow_ups = _FakeColl(
        [
            {"follow_up_id": "F1", "customer_id": "A", "store_id": "BV-PUN-01", "status": "pending", "scheduled_date": today, "mode": "WHATSAPP", "customer_phone": "9000000001", "customer_name": "A"},
            {"follow_up_id": "F2", "customer_id": "B", "store_id": "BV-PUN-01", "status": "pending", "scheduled_date": today, "mode": "CALL", "customer_phone": "9000000002", "customer_name": "B"},
            {"follow_up_id": "F3", "customer_id": "C", "store_id": "BV-PUN-01", "status": "completed", "scheduled_date": today, "mode": "WHATSAPP", "customer_phone": "9000000003", "customer_name": "C"},
        ]
    )
    db = _FakeDB({"customers": custs, "follow_ups": follow_ups})
    rows = seg.resolve_segment(db, "fu_due_today", store_id="BV-PUN-01")
    ids = {r["customer_id"] for r in rows}
    assert "A" in ids and "B" in ids  # both due (A->send, B->task)
    assert "C" not in ids  # completed -> excluded


# ===========================================================================
# (d) fu_due_today routes CALL -> task vs WHATSAPP -> send
# ===========================================================================


def test_fu_due_today_routing(monkeypatch):
    today = datetime.now().date().isoformat()
    custs = _FakeColl([_cust("A"), _cust("B")])
    follow_ups = _FakeColl(
        [
            {"follow_up_id": "F1", "customer_id": "A", "store_id": "BV-PUN-01", "status": "pending", "scheduled_date": today, "mode": "WHATSAPP", "customer_phone": "9000000001", "customer_name": "A"},
            {"follow_up_id": "F2", "customer_id": "B", "store_id": "BV-PUN-01", "status": "pending", "scheduled_date": today, "mode": "CALL", "customer_phone": "9000000002", "customer_name": "B"},
        ]
    )
    db = _FakeDB(
        {
            "customers": custs,
            "follow_ups": follow_ups,
            "comms_ledger": _FakeColl(),
            "notification_logs": _FakeColl(),
            "tasks": _FakeColl(),
        }
    )
    rec = _patch_send(monkeypatch, db)
    rule = _rule(rule_type="fu_due_today", segment_key="fu_due_today", template_id="ANNUAL_CHECKUP_REMINDER")
    res = _run(rail.evaluate_rule(db, rule, dry_run=False))
    # A -> a queued message; B -> a staff task.
    assert res["queued"] == 1
    assert res["tasks_created"] == 1
    sent_ids = {c["customer_id"] for c in rec.calls}
    assert sent_ids == {"A"}  # only the WHATSAPP follow-up was messaged
    tasks = db.get_collection("tasks").docs
    assert len(tasks) == 1 and tasks[0]["customer_id"] == "B"
    assert tasks[0]["task_type"] == "follow_up"


# ===========================================================================
# (e) preview / dry_run writes NOTHING
# ===========================================================================


def test_dry_run_writes_nothing(monkeypatch):
    custs = _FakeColl([_cust("C1"), _cust("C2")])
    db = _FakeDB(
        {
            "customers": custs,
            "comms_ledger": _FakeColl(),
            "notification_logs": _FakeColl(),
            "vouchers": _FakeColl(),
            "tasks": _FakeColl(),
        }
    )
    _patch_send(monkeypatch, db)
    rule = _rule(voucher_template={"type": "DISCOUNT", "amount": 200, "validity_days": 30})
    res = _run(rail.evaluate_rule(db, rule, dry_run=True))
    assert res["resolved"] == 2
    assert res["queued"] == 0
    # Nothing written.
    assert db.get_collection("notification_logs").docs == []
    assert db.get_collection("comms_ledger").docs == []
    assert db.get_collection("vouchers").docs == []
    assert db.get_collection("tasks").docs == []


# ===========================================================================
# Honest status / DISPATCH_MODE=off: send -> PENDING, ledger written, no provider
# ===========================================================================


def test_send_queues_pending_and_records_ledger(monkeypatch):
    custs = _FakeColl([_cust("C1")])
    db = _FakeDB(
        {
            "customers": custs,
            "comms_ledger": _FakeColl(),
            "notification_logs": _FakeColl(),
        }
    )
    rec = _patch_send(monkeypatch, db)
    res = _run(rail.evaluate_rule(db, _rule(), dry_run=False))
    assert res["queued"] == 1
    logs = db.get_collection("notification_logs").docs
    assert len(logs) == 1 and logs[0]["status"] == "PENDING"
    assert logs[0].get("rule_id") == "RMD-TEST-1"  # stamped
    # Cap ledger written (MARKETING).
    ledger = db.get_collection("comms_ledger").docs
    assert len(ledger) == 1 and ledger[0]["category"] == "MARKETING"
    # send_notification was the only outbound path; no provider call.
    assert len(rec.calls) == 1


def test_transactional_send_does_not_write_cap_ledger(monkeypatch):
    custs = _FakeColl([_cust("C1")])
    db = _FakeDB(
        {
            "customers": custs,
            "comms_ledger": _FakeColl(),
            "notification_logs": _FakeColl(),
        }
    )
    _patch_send(monkeypatch, db)
    res = _run(rail.evaluate_rule(db, _rule(is_transactional=True), dry_run=False))
    assert res["queued"] == 1
    # Transactional is exempt from the cap -> NO comms_ledger row.
    assert db.get_collection("comms_ledger").docs == []


# ===========================================================================
# Voucher gate: exactly one voucher per (customer, rule, day) -- idempotent
# ===========================================================================


def test_voucher_minted_once_per_customer_rule_day(monkeypatch):
    custs = _FakeColl([_cust("C1")])
    db = _FakeDB(
        {
            "customers": custs,
            "comms_ledger": _FakeColl(),
            "notification_logs": _FakeColl(),
            "vouchers": _FakeColl(),
        }
    )
    rec = _patch_send(monkeypatch, db)
    rule = _rule(voucher_template={"type": "DISCOUNT", "amount": 200, "validity_days": 30})
    _run(rail.evaluate_rule(db, rule, dry_run=False))
    _run(rail.evaluate_rule(db, rule, dry_run=False))  # same day, same rule
    vouchers = db.get_collection("vouchers").docs
    assert len(vouchers) == 1  # idempotent dedupe
    assert vouchers[0]["status"] == "ACTIVE" and vouchers[0]["initial_amount"] == 200
    # The queued message carried voucher_code.
    assert any("voucher_code" in (c.get("variables") or {}) for c in rec.calls)


def test_voucher_minted_via_canonical_path_not_parallel(monkeypatch):
    """T5 / PROTOCOL S6 regression: the reminder voucher MUST be minted through
    the canonical vouchers.mint_voucher (single source of truth + the exact
    ACTIVE doc shape that redeem_voucher_atomic + the E1 money-guard read at
    redemption), NOT a parallel insert. Spy proves the engine was the mint path."""
    import api.routers.vouchers as vmod

    calls = []
    real_mint = vmod.mint_voucher

    def _spy(coll, **kw):
        calls.append(kw)
        return real_mint(coll, **kw)

    monkeypatch.setattr(vmod, "mint_voucher", _spy)

    db = _FakeDB(
        {
            "customers": _FakeColl([_cust("C1")]),
            "comms_ledger": _FakeColl(),
            "notification_logs": _FakeColl(),
            "vouchers": _FakeColl(),
        }
    )
    _patch_send(monkeypatch, db)
    rule = _rule(voucher_template={"type": "DISCOUNT", "amount": 200, "validity_days": 30})
    _run(rail.evaluate_rule(db, rule, dry_run=False))

    assert len(calls) == 1  # the canonical engine was called exactly once
    assert calls[0]["vtype"] == "DISCOUNT" and calls[0]["amount"] == 200
    v = db.get_collection("vouchers").docs[0]
    # Canonical ACTIVE shape -- the fields redeem/E1 depend on:
    assert v["status"] == "ACTIVE" and v["balance"] == 200 and v["currency"] == "INR"
    assert v["redemptions"] == [] and v["voucher_id"]
    assert v["code"].startswith("GC-")  # canonical _generate_code, not a forked RMD-
    assert v["reminder_dedupe"].startswith("C1:RMD-TEST-1:")  # feature field rode `extra`


# ===========================================================================
# (f) seeded rules are active=False (no auto-send on deploy)
# ===========================================================================


def test_seeded_rules_are_inactive():
    db = _FakeDB({"reminder_rules": _FakeColl()})
    n = rail.seed_reminder_rules(db)
    assert n == 6
    rules = db.get_collection("reminder_rules").docs
    assert len(rules) == 6
    assert all(r["active"] is False for r in rules)
    assert all(r["scope"] == "GLOBAL" for r in rules)
    types = {r["rule_type"] for r in rules}
    assert {"rx_expiry", "birthday", "winback", "cl_reorder", "churn_risk", "feedback"} <= types


def test_seed_is_idempotent_and_nondestructive():
    db = _FakeDB({"reminder_rules": _FakeColl()})
    assert rail.seed_reminder_rules(db) == 6
    # An owner activates one seeded rule.
    db.get_collection("reminder_rules").update_one(
        {"rule_id": "RMD-SEED-RX-EXPIRY"}, {"$set": {"active": True}}
    )
    # Re-seeding inserts nothing and does NOT reset the owner's edit.
    assert rail.seed_reminder_rules(db) == 0
    rx = db.get_collection("reminder_rules").find_one({"rule_id": "RMD-SEED-RX-EXPIRY"})
    assert rx["active"] is True


# ===========================================================================
# (g) every /reminders/* route present in rbac_policy.POLICY (coverage-lock)
# ===========================================================================


def test_reminders_routes_in_rbac_policy():
    from api.services import rbac_policy

    expected = {
        ("GET", "/api/v1/reminders/rules"),
        ("POST", "/api/v1/reminders/rules"),
        ("GET", "/api/v1/reminders/rules/{rule_id}"),
        ("PUT", "/api/v1/reminders/rules/{rule_id}"),
        ("DELETE", "/api/v1/reminders/rules/{rule_id}"),
        ("POST", "/api/v1/reminders/rules/{rule_id}/toggle"),
        ("POST", "/api/v1/reminders/rules/{rule_id}/preview"),
        ("POST", "/api/v1/reminders/rules/{rule_id}/run-now"),
        ("GET", "/api/v1/reminders/rules/{rule_id}/history"),
    }
    present = {
        (str(e["method"]).upper(), str(e["path"]))
        for e in rbac_policy.POLICY
        if str(e["path"]).startswith("/api/v1/reminders/")
    }
    assert expected <= present


# ===========================================================================
# Family-wallet OTP: hash stored, atomic consume (two verifiers -> one ok)
# ===========================================================================


def test_pool_otp_issue_and_verify(monkeypatch):
    db = _FakeDB(
        {
            "pool_otp": _FakeColl(),
            "customers": _FakeColl([_cust("P", mobile="9000000099")]),
            "notification_logs": _FakeColl(),
        }
    )
    _patch_send(monkeypatch, db)
    # Capture the raw code by intercepting the OTP message variables.
    sent = {}
    import api.services.notification_service as ns

    async def _cap(**kw):
        if kw.get("category") == "OTP":
            sent["code"] = (kw.get("variables") or {}).get("otp")
        db.get_collection("notification_logs").insert_one(
            {
                "notification_id": "N1",
                "customer_id": kw.get("customer_id", ""),
                "category": kw.get("category", "SERVICE"),
                "status": "PENDING",
            }
        )
        return {"notification_id": "N1", "dispatched": False, "status": "PENDING"}

    monkeypatch.setattr(ns, "send_notification", _cap)

    out = _run(
        rail.send_pool_redemption_otp(
            db, primary_customer_id="P", household_id="H", amount=500, requested_by="U"
        )
    )
    assert out["ok"] is True
    otp_id = out["otp_id"]
    row = db.get_collection("pool_otp").find_one({"otp_id": otp_id})
    assert row["status"] == "PENDING"
    assert row["code_hash"] and row["code_hash"] != sent["code"]  # hash, not raw
    assert row["primary_customer_id"] == "P"
    # An OTP notification row exists for P with category OTP.
    assert any(
        n.get("category") == "OTP" for n in db.get_collection("notification_logs").docs
    )

    # Wrong code 5x -> FAILED on the 5th, then max_attempts.
    for _ in range(4):
        r = rail.verify_pool_redemption_otp(db, otp_id=otp_id, code="000000-WRONG")
        assert r["ok"] is False
    r5 = rail.verify_pool_redemption_otp(db, otp_id=otp_id, code="000000-WRONG")
    assert r5 == {"ok": False, "reason": "max_attempts"}
    r6 = rail.verify_pool_redemption_otp(db, otp_id=otp_id, code="anything")
    assert r6["ok"] is False  # already FAILED


def test_pool_otp_atomic_single_winner(monkeypatch):
    db = _FakeDB(
        {
            "pool_otp": _FakeColl(),
            "customers": _FakeColl([_cust("P", mobile="9000000099")]),
            "notification_logs": _FakeColl(),
        }
    )
    code_box = {}
    import api.services.notification_service as ns

    async def _cap(**kw):
        if kw.get("category") == "OTP":
            code_box["code"] = (kw.get("variables") or {}).get("otp")
        return {"notification_id": "N1", "dispatched": False, "status": "PENDING"}

    monkeypatch.setattr(ns, "send_notification", _cap)
    out = _run(
        rail.send_pool_redemption_otp(
            db, primary_customer_id="P", household_id="H", amount=500, requested_by="U"
        )
    )
    otp_id = out["otp_id"]
    code = code_box["code"]

    results = []

    def _verify():
        results.append(rail.verify_pool_redemption_otp(db, otp_id=otp_id, code=code))

    t1 = threading.Thread(target=_verify)
    t2 = threading.Thread(target=_verify)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    oks = [r for r in results if r.get("ok")]
    assert len(oks) == 1  # at most one verifier wins the atomic consume
