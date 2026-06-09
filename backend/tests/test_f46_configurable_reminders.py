"""
IMS 2.0 - F46 configurable reminders acceptance tests (intent-level)
====================================================================
F46 = the CONFIG surface over the merged E6 reminder rail. The rail engine
itself (gates / cap / send / OTP / segments) is proven by
``test_reminder_rail_e6.py``. THIS file proves the configurable-rule CRUD +
activation toggle behave to intent through the HTTP router
(routers/reminders.py), and -- critically -- that CONFIGURING / ACTIVATING a
rule writes config ONLY and NEVER fires a live send (send-dark per the COMMS
directive). A hollow shell that 200s but does not persist, validate, or stay
send-dark FAILS these.

Coverage (per the F46 packet Delta + TESTS list):
  1. create persists a rule (round-trips through GET).
  2. update persists; engine-owned counters are NOT editable via PUT.
  3. toggle flips active and persists; a second toggle flips back.
  4. validation: bad segment_key / bad channel / bad rule_type / STORE-scope
     missing store_id / EVENT-trigger shape are all rejected.
  5. ACTIVATING a rule does NOT fire a live send -- toggle on writes config
     only; ZERO send_notification calls, ZERO notification_logs rows, ZERO
     comms_ledger rows. (The send path is the MEGAPHONE tick / run-now, not the
     toggle.) This is the COMMS send-dark lock.
  6. preview (dry_run) is read-only -- writes nothing.
  7. RBAC: a STORE_MANAGER cannot create a GLOBAL rule (403); cannot mutate
     another store's rule (403); CAN act on their own store's rule.
  8. created rule defaults to active=False (inert until toggled).

CI-ROBUSTNESS (HARD lessons folded):
  - EVERY repo/db accessor the handler reads is monkeypatched to a fully-seeded
    fake DB; there is no local-vs-CI fail-soft divergence (no real Mongo, no
    real provider, send_notification is monkeypatched on the module the rail
    lazy-imports).
  - We never assert a value's absence via a whole-JSON substring check; absence
    is asserted on the explicit collection's doc list (== []), and presence on
    the explicit field.

Self-contained: a fake in-memory DB + a captured send_notification that NEVER
touches a provider. DISPATCH_MODE=off. Nothing here flips DISPATCH_MODE.
"""

from __future__ import annotations

import copy
import os
import sys
import uuid
from datetime import datetime, timedelta

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
os.environ.setdefault("DISPATCH_MODE", "off")

import jwt  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import reminders as rem_mod  # noqa: E402
from api.routers import auth as auth_mod  # noqa: E402
from api.services import reminder_rail as rail  # noqa: E402


# ---------------------------------------------------------------------------
# Auth token helper
# ---------------------------------------------------------------------------


def _tok(roles, uid="u1", store_id="BV-PUN-01", store_ids=None):
    return jwt.encode(
        {
            "sub": uid,
            "user_id": uid,
            "username": "tester",
            "roles": list(roles),
            "active_store_id": store_id,
            "store_ids": store_ids if store_ids is not None else [store_id],
            "exp": datetime.utcnow() + timedelta(hours=1),
        },
        auth_mod.SECRET_KEY,
        algorithm=auth_mod.ALGORITHM,
    )


def _hdr(roles=("ADMIN",), store_id="BV-PUN-01", store_ids=None):
    return {"Authorization": f"Bearer {_tok(roles, store_id=store_id, store_ids=store_ids)}"}


# ---------------------------------------------------------------------------
# Fake Mongo (mirrors the campaigns/E6 test harness)
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


class _SendSpy:
    """Captures send_notification calls AND writes a notification_logs row like
    the real path. If THIS is ever called by a config/toggle action, the
    send-dark contract is violated. NEVER touches a provider."""

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
                "template_id": kwargs.get("template_id", ""),
                "channel": kwargs.get("channel", "WHATSAPP"),
                "category": kwargs.get("category", "MARKETING"),
                "status": "PENDING",
                "created_at": datetime.now().isoformat(),
            }
        )
        return {"notification_id": nid, "dispatched": False, "status": "PENDING"}


def _base_db():
    """A DB seeded with all collections the handlers + rail read, so there is no
    create-on-miss difference between local and CI."""
    return _FakeDB(
        {
            "reminder_rules": _FakeColl(),
            "reminder_audit": _FakeColl(),
            "comms_ledger": _FakeColl(),
            "notification_logs": _FakeColl(),
            "vouchers": _FakeColl(),
            "tasks": _FakeColl(),
            "customers": _FakeColl(),
        }
    )


def _mk_client(db, *, monkeypatch, send_spy=None):
    # The router pulls the DB via marketing._get_db, imported as _marketing_get_db.
    monkeypatch.setattr(rem_mod, "_marketing_get_db", lambda: db)
    # run-now rate limiter: never throttle in tests.
    monkeypatch.setattr(rem_mod, "_check_notification_rate", lambda *_a, **_k: None)
    # The rail engine's gates -> isolate to deterministic stubs (no real DB).
    monkeypatch.setattr(rail, "_is_opted_out", lambda db, cid: False)
    monkeypatch.setattr(rail, "_in_quiet_hours", lambda now=None: False)
    monkeypatch.setattr(rail, "_cap_limit", lambda rule=None: 3)
    # The lazy send_notification import the rail resolves -> a non-provider spy.
    spy = send_spy or _SendSpy(db)
    import api.services.notification_service as ns

    monkeypatch.setattr(ns, "send_notification", spy)
    app = FastAPI()
    app.include_router(rem_mod.router, prefix="/api/v1/reminders")
    return TestClient(app), spy


def _payload(**over):
    base = {
        "name": "Prescription expiry reminder",
        "rule_type": "rx_expiry",
        "segment_key": "rx_expiry",
        "segment_params": {"window_days": 30},
        "channel": "WHATSAPP",
        "template_id": "PRESCRIPTION_EXPIRY",
        "trigger": {"kind": "CRON", "cron": "DAILY 09:00", "event_key": None},
        "scope": "GLOBAL",
        "active": False,
    }
    base.update(over)
    return base


# ===========================================================================
# 1. Create persists a rule (round-trips through GET)
# ===========================================================================


def test_create_rule_persists(monkeypatch):
    db = _base_db()
    client, _ = _mk_client(db, monkeypatch=monkeypatch)
    r = client.post("/api/v1/reminders/rules", json=_payload(), headers=_hdr())
    assert r.status_code == 200, r.text
    rule_id = r.json()["rule"]["rule_id"]
    assert rule_id.startswith("RMD-")
    # Persisted in the collection.
    stored = db.get_collection("reminder_rules").find_one({"rule_id": rule_id})
    assert stored is not None
    assert stored["name"] == "Prescription expiry reminder"
    assert stored["segment_key"] == "rx_expiry"
    assert stored["segment_params"] == {"window_days": 30}
    # Round-trips through GET.
    g = client.get(f"/api/v1/reminders/rules/{rule_id}", headers=_hdr())
    assert g.status_code == 200
    assert g.json()["rule_id"] == rule_id


def test_create_defaults_active_false(monkeypatch):
    db = _base_db()
    client, spy = _mk_client(db, monkeypatch=monkeypatch)
    r = client.post("/api/v1/reminders/rules", json=_payload(), headers=_hdr())
    assert r.status_code == 200
    rule_id = r.json()["rule"]["rule_id"]
    stored = db.get_collection("reminder_rules").find_one({"rule_id": rule_id})
    # Inert by default (no auto-send on create).
    assert stored["active"] is False
    # Creating a rule NEVER sends.
    assert spy.calls == []
    assert db.get_collection("notification_logs").docs == []


# ===========================================================================
# 2. Update persists; engine-owned counters are NOT editable via PUT
# ===========================================================================


def test_update_persists_and_protects_counters(monkeypatch):
    db = _base_db()
    client, _ = _mk_client(db, monkeypatch=monkeypatch)
    rule_id = client.post(
        "/api/v1/reminders/rules", json=_payload(), headers=_hdr()
    ).json()["rule"]["rule_id"]
    # Seed a counter as the engine would.
    db.get_collection("reminder_rules").update_one(
        {"rule_id": rule_id}, {"$set": {"sent_count": 7}}
    )
    # PUT a new name + try to overwrite the protected counter.
    r = client.put(
        f"/api/v1/reminders/rules/{rule_id}",
        json={"name": "Renamed reminder", "sent_count": 0},
        headers=_hdr(),
    )
    assert r.status_code == 200, r.text
    stored = db.get_collection("reminder_rules").find_one({"rule_id": rule_id})
    assert stored["name"] == "Renamed reminder"  # editable field changed
    assert stored["sent_count"] == 7  # protected counter UNCHANGED


def test_update_segment_params_persists(monkeypatch):
    db = _base_db()
    client, _ = _mk_client(db, monkeypatch=monkeypatch)
    rule_id = client.post(
        "/api/v1/reminders/rules", json=_payload(), headers=_hdr()
    ).json()["rule"]["rule_id"]
    r = client.put(
        f"/api/v1/reminders/rules/{rule_id}",
        json={"segment_params": {"window_days": 60}},
        headers=_hdr(),
    )
    assert r.status_code == 200
    stored = db.get_collection("reminder_rules").find_one({"rule_id": rule_id})
    assert stored["segment_params"] == {"window_days": 60}


# ===========================================================================
# 3. Toggle flips active and persists (and a second toggle flips back)
# ===========================================================================


def test_toggle_flips_and_persists(monkeypatch):
    db = _base_db()
    client, _ = _mk_client(db, monkeypatch=monkeypatch)
    rule_id = client.post(
        "/api/v1/reminders/rules", json=_payload(active=False), headers=_hdr()
    ).json()["rule"]["rule_id"]
    # Toggle ON.
    r1 = client.post(f"/api/v1/reminders/rules/{rule_id}/toggle", headers=_hdr())
    assert r1.status_code == 200
    assert r1.json()["active"] is True
    assert db.get_collection("reminder_rules").find_one({"rule_id": rule_id})["active"] is True
    # Toggle OFF.
    r2 = client.post(f"/api/v1/reminders/rules/{rule_id}/toggle", headers=_hdr())
    assert r2.json()["active"] is False
    assert db.get_collection("reminder_rules").find_one({"rule_id": rule_id})["active"] is False


# ===========================================================================
# 4. Validation: bad segment / channel / rule_type / scope shape
# ===========================================================================


def test_create_rejects_bad_segment_key(monkeypatch):
    db = _base_db()
    client, _ = _mk_client(db, monkeypatch=monkeypatch)
    r = client.post(
        "/api/v1/reminders/rules",
        json=_payload(segment_key="not_a_real_segment"),
        headers=_hdr(),
    )
    assert r.status_code == 422  # pydantic validator rejects


def test_create_rejects_bad_channel(monkeypatch):
    db = _base_db()
    client, _ = _mk_client(db, monkeypatch=monkeypatch)
    r = client.post(
        "/api/v1/reminders/rules", json=_payload(channel="PIGEON"), headers=_hdr()
    )
    assert r.status_code == 422


def test_create_rejects_bad_rule_type(monkeypatch):
    db = _base_db()
    client, _ = _mk_client(db, monkeypatch=monkeypatch)
    r = client.post(
        "/api/v1/reminders/rules", json=_payload(rule_type="teleport"), headers=_hdr()
    )
    assert r.status_code == 422


def test_create_store_scope_requires_store_id(monkeypatch):
    db = _base_db()
    client, _ = _mk_client(db, monkeypatch=monkeypatch)
    r = client.post(
        "/api/v1/reminders/rules",
        json=_payload(scope="STORE", store_id=None),
        headers=_hdr(),
    )
    assert r.status_code == 400  # STORE-scope rule requires store_id


def test_create_entity_scope_requires_entity_id(monkeypatch):
    db = _base_db()
    client, _ = _mk_client(db, monkeypatch=monkeypatch)
    r = client.post(
        "/api/v1/reminders/rules",
        json=_payload(scope="ENTITY", entity_id=None),
        headers=_hdr(),
    )
    assert r.status_code == 400


def test_event_trigger_shape_persists(monkeypatch):
    db = _base_db()
    client, _ = _mk_client(db, monkeypatch=monkeypatch)
    r = client.post(
        "/api/v1/reminders/rules",
        json=_payload(
            rule_type="churn_risk",
            segment_key="churn_risk",
            trigger={"kind": "EVENT", "cron": None, "event_key": "churn.detected"},
        ),
        headers=_hdr(),
    )
    assert r.status_code == 200, r.text
    stored = db.get_collection("reminder_rules").find_one(
        {"rule_id": r.json()["rule"]["rule_id"]}
    )
    assert stored["trigger"]["kind"] == "EVENT"
    assert stored["trigger"]["event_key"] == "churn.detected"


# ===========================================================================
# 5. ACTIVATING a rule does NOT fire a live send (config only, send-dark)
# ===========================================================================


def test_activating_rule_does_not_send(monkeypatch):
    """The COMMS send-dark lock: toggling a rule active writes config ONLY. No
    send_notification call, no notification_logs row, no comms_ledger row. The
    send path is the MEGAPHONE tick / explicit run-now -- never the toggle."""
    db = _base_db()
    # Seed a customer the segment WOULD resolve, to prove the toggle still does
    # not resolve+send.
    db.get_collection("customers").docs.append(
        {
            "customer_id": "C1",
            "name": "Alpha",
            "mobile": "9000000001",
            "store_id": "BV-PUN-01",
        }
    )
    client, spy = _mk_client(db, monkeypatch=monkeypatch)
    rule_id = client.post(
        "/api/v1/reminders/rules",
        json=_payload(segment_key="by_store", store_id=None),
        headers=_hdr(),
    ).json()["rule"]["rule_id"]

    # Activate.
    r = client.post(f"/api/v1/reminders/rules/{rule_id}/toggle", headers=_hdr())
    assert r.status_code == 200 and r.json()["active"] is True

    # SEND-DARK: nothing left the building.
    assert spy.calls == []
    assert db.get_collection("notification_logs").docs == []
    assert db.get_collection("comms_ledger").docs == []
    # The rule is active config only -- it persisted the flag.
    assert db.get_collection("reminder_rules").find_one({"rule_id": rule_id})["active"] is True


def test_create_active_true_does_not_send(monkeypatch):
    """Even creating a rule with active=True writes config only -- creating a
    config row never resolves an audience or sends."""
    db = _base_db()
    db.get_collection("customers").docs.append(
        {"customer_id": "C1", "name": "Alpha", "mobile": "9000000001", "store_id": "BV-PUN-01"}
    )
    client, spy = _mk_client(db, monkeypatch=monkeypatch)
    r = client.post(
        "/api/v1/reminders/rules",
        json=_payload(segment_key="by_store", store_id=None, active=True),
        headers=_hdr(),
    )
    assert r.status_code == 200
    assert spy.calls == []
    assert db.get_collection("notification_logs").docs == []
    assert db.get_collection("comms_ledger").docs == []


# ===========================================================================
# 6. preview (dry_run) is read-only
# ===========================================================================


def test_preview_writes_nothing(monkeypatch):
    db = _base_db()
    db.get_collection("customers").docs.extend(
        [
            {"customer_id": "C1", "name": "Alpha", "mobile": "9000000001", "store_id": "BV-PUN-01"},
            {"customer_id": "C2", "name": "Beta", "mobile": "9000000002", "store_id": "BV-PUN-01"},
        ]
    )
    client, spy = _mk_client(db, monkeypatch=monkeypatch)
    rule_id = client.post(
        "/api/v1/reminders/rules",
        json=_payload(segment_key="by_store", store_id=None, active=True),
        headers=_hdr(),
    ).json()["rule"]["rule_id"]
    r = client.post(f"/api/v1/reminders/rules/{rule_id}/preview", headers=_hdr())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["resolved"] == 2
    assert body["queued"] == 0
    # Read-only: explicit absence on each collection's doc list (not substring).
    assert spy.calls == []
    assert db.get_collection("notification_logs").docs == []
    assert db.get_collection("comms_ledger").docs == []
    assert db.get_collection("vouchers").docs == []
    assert db.get_collection("tasks").docs == []


# ===========================================================================
# 7. RBAC: STORE_MANAGER cross-store + GLOBAL scope blocked; own store allowed
# ===========================================================================


def test_store_manager_cannot_create_global(monkeypatch):
    db = _base_db()
    client, _ = _mk_client(db, monkeypatch=monkeypatch)
    r = client.post(
        "/api/v1/reminders/rules",
        json=_payload(scope="GLOBAL"),
        headers=_hdr(roles=("STORE_MANAGER",), store_id="STORE_A"),
    )
    assert r.status_code == 403  # GLOBAL/ENTITY require ADMIN+


def test_store_manager_cannot_mutate_other_store_rule(monkeypatch):
    db = _base_db()
    client, _ = _mk_client(db, monkeypatch=monkeypatch)
    # ADMIN creates a STORE-scope rule for STORE_B.
    rule_id = client.post(
        "/api/v1/reminders/rules",
        json=_payload(scope="STORE", store_id="STORE_B"),
        headers=_hdr(roles=("ADMIN",)),
    ).json()["rule"]["rule_id"]
    # A STORE_MANAGER of STORE_A tries to edit STORE_B's rule -> 403.
    r = client.put(
        f"/api/v1/reminders/rules/{rule_id}",
        json={"name": "hijack"},
        headers=_hdr(roles=("STORE_MANAGER",), store_id="STORE_A", store_ids=["STORE_A"]),
    )
    assert r.status_code == 403
    # And a toggle on the same cross-store rule is blocked too.
    r2 = client.post(
        f"/api/v1/reminders/rules/{rule_id}/toggle",
        headers=_hdr(roles=("STORE_MANAGER",), store_id="STORE_A", store_ids=["STORE_A"]),
    )
    assert r2.status_code == 403


def test_store_manager_can_act_on_own_store_rule(monkeypatch):
    db = _base_db()
    client, _ = _mk_client(db, monkeypatch=monkeypatch)
    rule_id = client.post(
        "/api/v1/reminders/rules",
        json=_payload(scope="STORE", store_id="STORE_A"),
        headers=_hdr(roles=("ADMIN",)),
    ).json()["rule"]["rule_id"]
    # The STORE_MANAGER of STORE_A may toggle their own store's rule.
    r = client.post(
        f"/api/v1/reminders/rules/{rule_id}/toggle",
        headers=_hdr(roles=("STORE_MANAGER",), store_id="STORE_A", store_ids=["STORE_A"]),
    )
    assert r.status_code == 200
    assert r.json()["active"] is True


def test_non_privileged_role_blocked(monkeypatch):
    db = _base_db()
    client, _ = _mk_client(db, monkeypatch=monkeypatch)
    # SALES_STAFF has no reminders access at all.
    r = client.get(
        "/api/v1/reminders/rules", headers=_hdr(roles=("SALES_STAFF",))
    )
    assert r.status_code == 403


# ===========================================================================
# 8. delete is soft (active=False + deleted_at), drops from list, no send
# ===========================================================================


def test_delete_is_soft_and_drops_from_list(monkeypatch):
    db = _base_db()
    client, spy = _mk_client(db, monkeypatch=monkeypatch)
    rule_id = client.post(
        "/api/v1/reminders/rules", json=_payload(active=True), headers=_hdr()
    ).json()["rule"]["rule_id"]
    d = client.delete(f"/api/v1/reminders/rules/{rule_id}", headers=_hdr())
    assert d.status_code == 200
    stored = db.get_collection("reminder_rules").find_one({"rule_id": rule_id})
    assert stored["active"] is False and stored["deleted_at"] is not None
    # Soft-deleted rule drops out of the list.
    listing = client.get("/api/v1/reminders/rules", headers=_hdr()).json()
    assert all(r["rule_id"] != rule_id for r in listing["rules"])
    # The audit row is preserved.
    assert any(
        a["rule_id"] == rule_id and a["action"] == "DELETE"
        for a in db.get_collection("reminder_audit").docs
    )
    # Deleting never sends.
    assert spy.calls == []
