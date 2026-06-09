"""
IMS 2.0 -- F50 Clinical->retail handover tests
==============================================

Acceptance-INTENT tests for #50 (clinical->retail handover). A hollow shell
that returns 200 without minting the handoff / firing the bell / recording the
50/50 credit FAILS these.

Covered intent:
  T1  send mints a CLINICAL_RX handoff (8h TTL) + one in-app notification per
      eligible recipient + a CLINICAL_HANDOVER_SENT audit row.
  T2  an IN_PROGRESS test is rejected (422); no handoff written.
  T3  double-send is idempotent (already_sent=True; no second doc/notify batch).
  T4  cross-store IDOR is blocked (403); no handoff written.
  T5  clinical-inbox returns only CLINICAL_RX for the caller's store; a
      Store-B recipient sees nothing.
  T6  acknowledge sets acknowledged_by exactly ONCE (idempotent on 2nd call).
  T7  mark-served writes the audit row with the 50/50 conversion credit.
  T8  double mark-served -> 409; no second audit row.
  T9  feature-flag OFF blocks send (403, "not enabled").
  T10 an expired handoff is absent from clinical-inbox.
  T11 ACCOUNTANT cannot read clinical-inbox (403).
  T12 the only delivery channel is the IN_APP bell -- NO outbound message send
      path is ever invoked (the comms channel is dark).

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_f50_handover.py -q
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")


# ============================================================================
# Test fakes -- a small Mongo emulator (single-doc writes; no transactions)
# ============================================================================


def _matches(doc, filter_):
    if not filter_:
        return True
    for k, expected in filter_.items():
        if k == "$or":
            if not any(_matches(doc, sub) for sub in expected):
                return False
            continue
        if "." in k:
            head, tail = k.split(".", 1)
            arr = doc.get(head)
            if isinstance(arr, list):
                if not any(
                    _matches(item, {tail: expected})
                    for item in arr
                    if isinstance(item, dict)
                ):
                    return False
                continue
            return False

        actual = doc.get(k)
        if isinstance(expected, dict):
            for op, op_val in expected.items():
                if op == "$gt" and not (actual is not None and actual > op_val):
                    return False
                if op == "$gte" and not (actual is not None and actual >= op_val):
                    return False
                if op == "$lt" and not (actual is not None and actual < op_val):
                    return False
                if op == "$lte" and not (actual is not None and actual <= op_val):
                    return False
                if op == "$ne" and actual == op_val:
                    return False
                if op == "$exists" and bool(actual is not None) != bool(op_val):
                    return False
                if op == "$in":
                    # Mongo: matches when the field value (or any element of an
                    # array field) is in op_val.
                    if isinstance(actual, list):
                        if not any(a in op_val for a in actual):
                            return False
                    elif actual not in op_val:
                        return False
        else:
            # Mongo array-contains: {store_ids: "S"} matches a list field
            # containing "S".
            if isinstance(actual, list):
                if expected not in actual:
                    return False
            elif actual != expected:
                return False
    return True


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, *a, **k):
        return self

    def skip(self, n):
        self._docs = self._docs[int(n or 0):]
        return self

    def limit(self, n):
        if n:
            self._docs = self._docs[: int(n)]
        return self

    def __iter__(self):
        return iter(self._docs)


class FakeCollection:
    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc.get("_id")})()

    def find_one(self, filter_=None, projection=None):
        for d in self.docs:
            if _matches(d, filter_):
                return dict(d)
        return None

    def find(self, filter_=None, projection=None):
        return _Cursor(dict(d) for d in self.docs if _matches(d, filter_))

    def count_documents(self, filter_=None):
        return sum(1 for d in self.docs if _matches(d, filter_))

    def update_one(self, filter_, update, array_filters=None, upsert=False):
        for d in self.docs:
            if not _matches(d, filter_):
                continue
            self._apply_set(d, update, array_filters)
            return type("R", (), {"modified_count": 1, "matched_count": 1})()
        if upsert:
            new = {}
            # honour the filter's literal _id when upserting (policy_settings)
            for k, v in (filter_ or {}).items():
                if not isinstance(v, dict):
                    new[k] = v
            self._apply_set(new, update, array_filters)
            self.docs.append(new)
        return type("R", (), {"modified_count": 0, "matched_count": 0})()

    def find_one_and_update(
        self, filter_, update, array_filters=None, return_document=None, upsert=False
    ):
        for d in self.docs:
            if not _matches(d, filter_):
                continue
            self._apply_set(d, update, array_filters)
            return dict(d)
        return None

    @staticmethod
    def _apply_set(d, update, array_filters):
        set_block = (update or {}).get("$set", {}) or {}
        for k, v in set_block.items():
            if ".$[" in k:
                head, _, rest = k.partition(".$[")
                alias, _, tail = rest.partition("].")
                arr = d.get(head)
                if not isinstance(arr, list):
                    continue
                af = next(
                    (
                        af
                        for af in (array_filters or [])
                        if any(key.startswith(f"{alias}.") for key in af.keys())
                    ),
                    None,
                )
                inner = (
                    {
                        key.split(".", 1)[1]: val
                        for key, val in af.items()
                        if key.startswith(f"{alias}.")
                    }
                    if af
                    else {}
                )
                for item in arr:
                    if isinstance(item, dict) and _matches(item, inner):
                        item[tail] = v
            else:
                d[k] = v
        for k, v in ((update or {}).get("$setOnInsert", {}) or {}).items():
            d.setdefault(k, v)

    def delete_one(self, filter_):
        for i, d in enumerate(list(self.docs)):
            if _matches(d, filter_):
                self.docs.pop(i)
                return type("R", (), {"deleted_count": 1})()
        return type("R", (), {"deleted_count": 0})()

    def create_index(self, *a, **k):
        return None


class FakeDB:
    is_connected = True

    def __init__(self):
        self._collections = {}

    def get_collection(self, name):
        if name not in self._collections:
            self._collections[name] = FakeCollection()
        return self._collections[name]

    def __getattr__(self, name):
        if name in {"is_connected", "_collections"}:
            raise AttributeError(name)
        return self.get_collection(name)


# ============================================================================
# Fixtures
# ============================================================================

STORE_A = "BV-A-01"
STORE_B = "BV-B-01"


@pytest.fixture
def f50(monkeypatch):
    """Wire clinical + handoffs routers + repos to a shared fake DB."""
    from database.repositories.handoff_repository import HandoffRepository
    from database.repositories.user_repository import UserRepository
    from database.repositories.prescription_repository import PrescriptionRepository
    from database.repositories.clinical_repository import EyeTestRepository
    from database.repositories.audit_repository import AuditRepository

    db = FakeDB()
    handoff_repo = HandoffRepository(db.get_collection("handoffs"))
    rx_repo = PrescriptionRepository(db.get_collection("prescriptions"))
    test_repo = EyeTestRepository(db.get_collection("eye_tests"))
    audit_repo = AuditRepository(db.get_collection("audit_logs"))

    # Roster: 2 sales + 1 manager at Store A; 1 sales at Store B; 1 optometrist.
    users = db.get_collection("users")
    for u in [
        {"user_id": "opto-1", "username": "opto", "name": "Dr Mehta",
         "full_name": "Dr Mehta", "roles": ["OPTOMETRIST"],
         "store_ids": [STORE_A], "is_active": True},
        {"user_id": "sales-a1", "username": "sa1", "name": "Sales A1",
         "roles": ["SALES_STAFF"], "store_ids": [STORE_A], "is_active": True},
        {"user_id": "sales-a2", "username": "sa2", "name": "Sales A2",
         "roles": ["SALES_CASHIER"], "store_ids": [STORE_A], "is_active": True},
        {"user_id": "mgr-a", "username": "mgra", "name": "Mgr A",
         "roles": ["STORE_MANAGER"], "store_ids": [STORE_A], "is_active": True},
        {"user_id": "sales-b1", "username": "sb1", "name": "Sales B1",
         "roles": ["SALES_STAFF"], "store_ids": [STORE_B], "is_active": True},
    ]:
        users.insert_one(u)
    user_repo = UserRepository(users)

    # Track outbound message sends -- there must be ZERO (comms channel is dark).
    sent = {"calls": []}

    async def _forbidden_send(*a, **k):  # pragma: no cover - asserts it's never hit
        sent["calls"].append((a, k))
        return type("R", (), {"status": "sent"})()

    import api.dependencies as deps
    from api.routers import clinical as clin
    from api.routers import handoffs as hand

    for mod in (deps, clin, hand):
        if hasattr(mod, "get_handoff_repository"):
            monkeypatch.setattr(mod, "get_handoff_repository", lambda: handoff_repo)
        if hasattr(mod, "get_prescription_repository"):
            monkeypatch.setattr(mod, "get_prescription_repository", lambda: rx_repo)
        if hasattr(mod, "get_user_repository"):
            monkeypatch.setattr(mod, "get_user_repository", lambda: user_repo)
        if hasattr(mod, "get_eye_test_repository"):
            monkeypatch.setattr(mod, "get_eye_test_repository", lambda: test_repo)
        if hasattr(mod, "get_audit_repository"):
            monkeypatch.setattr(mod, "get_audit_repository", lambda: audit_repo)
        if hasattr(mod, "get_db"):
            monkeypatch.setattr(mod, "get_db", lambda: db)

    # Feature flag: default ON for Store A (most tests). T9 overrides to OFF.
    flag = {"value": True}
    monkeypatch.setattr(clin, "_clinical_handover_enabled", lambda store_id: flag["value"])

    # Block the WhatsApp provider entirely -- if anything tries to send, T12 fails.
    try:
        from agents import providers as prov
        monkeypatch.setattr(prov, "send_whatsapp", _forbidden_send, raising=False)
    except Exception:
        pass

    yield {
        "db": db,
        "handoffs": handoff_repo,
        "rx": rx_repo,
        "tests": test_repo,
        "audit": audit_repo,
        "flag": flag,
        "sent": sent,
    }


def _token(user_id, roles, store=STORE_A):
    from api.routers.auth import create_access_token

    return create_access_token(
        {
            "user_id": user_id,
            "username": user_id,
            "full_name": user_id,
            "roles": list(roles),
            "active_role": list(roles)[0],
            "store_ids": [store],
            "active_store_id": store,
        }
    )


def _seed_test(f50, *, test_id="ET-1", status="COMPLETED", store=STORE_A,
               with_rx=True, customer_id="CUST-1", patient_id="PAT-1"):
    """Insert an eye test (+ optional linked Rx) into the fake DB."""
    f50["tests"].create(
        {
            "test_id": test_id,
            "store_id": store,
            "status": status,
            "patient_name": "Riya Sharma",
            "customer_id": customer_id,
            "patient_id": patient_id,
        }
    )
    if with_rx:
        f50["rx"].create(
            {
                "prescription_id": f"RX-{test_id}",
                "prescription_number": f"NUM-{test_id}",
                "eye_test_id": test_id,
                "customer_id": customer_id,
                "patient_id": patient_id,
                "store_id": store,
                "right_eye": {"sph": "-1.00", "cyl": "-0.50", "axis": 90},
                "left_eye": {"sph": "-1.25", "cyl": "-0.25", "axis": 85},
                "expiry_date": "2027-01-01",
                "lens_recommendation": "Progressive",
            }
        )


def _send(client, token, test_id="ET-1", recs=None, summary="Prefers light frames"):
    return client.post(
        f"/api/v1/clinical/tests/{test_id}/send-to-floor",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "productRecommendations": recs or [{"category": "Progressive", "brandPreference": "Zeiss", "notes": "AR coat"}],
            "clinicalSummary": summary,
        },
    )


# ============================================================================
# Tests
# ============================================================================


def test_T1_send_mints_handoff_and_bell_and_audit(client, f50):
    _seed_test(f50)
    before = datetime.now(timezone.utc)
    r = _send(client, _token("opto-1", ["OPTOMETRIST"]))
    after = datetime.now(timezone.utc)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["already_sent"] is False
    # 3 eligible recipients at Store A (2 sales + 1 manager); opto excluded.
    assert body["recipient_count"] == 3

    handoffs = f50["handoffs"].collection.docs
    assert len(handoffs) == 1
    h = handoffs[0]
    assert h["handoff_type"] == "CLINICAL_RX"
    assert h["prescription_id"] == "RX-ET-1"
    assert h["eye_test_id"] == "ET-1"
    # 8h TTL (owner-locked). expires_at is timezone-aware (set via _now()); assert
    # it lands ~8h after send (tolerance 2 min), measured against the call window.
    assert before + timedelta(hours=8) - timedelta(minutes=2) <= h["expires_at"]
    assert h["expires_at"] <= after + timedelta(hours=8) + timedelta(minutes=2)
    # one in-app notification per recipient
    notifs = f50["db"].get_collection("notifications").docs
    assert len(notifs) == 3
    assert all(n["notification_type"] == "clinical_handover" for n in notifs)
    assert all(n["channels"] == ["IN_APP"] for n in notifs)
    # audit row
    audits = f50["audit"].collection.docs
    assert any(a["action"] == "CLINICAL_HANDOVER_SENT" for a in audits)


def test_T2_in_progress_test_rejected(client, f50):
    _seed_test(f50, status="IN_PROGRESS")
    r = _send(client, _token("opto-1", ["OPTOMETRIST"]))
    assert r.status_code == 422
    assert f50["handoffs"].collection.docs == []


def test_T3_double_send_is_idempotent(client, f50):
    _seed_test(f50)
    token = _token("opto-1", ["OPTOMETRIST"])
    r1 = _send(client, token)
    assert r1.status_code == 201
    r2 = _send(client, token)
    assert r2.status_code == 201
    assert r2.json()["already_sent"] is True
    assert r2.json()["handoff_id"] == r1.json()["handoff_id"]
    # exactly ONE handoff, and ONE batch of notifications (3, not 6)
    assert len(f50["handoffs"].collection.docs) == 1
    assert len(f50["db"].get_collection("notifications").docs) == 3


def test_T4_cross_store_idor_blocked(client, f50):
    _seed_test(f50, store=STORE_B)  # test belongs to Store B
    # optometrist's token is scoped to Store A
    r = _send(client, _token("opto-1", ["OPTOMETRIST"], store=STORE_A))
    assert r.status_code == 403
    assert f50["handoffs"].collection.docs == []


def test_T5_clinical_inbox_only_clinical_rx_for_store(client, f50):
    _seed_test(f50)
    _send(client, _token("opto-1", ["OPTOMETRIST"]))
    # Store-A sales recipient sees the handover.
    r = client.get(
        "/api/v1/handoffs/clinical-inbox",
        headers={"Authorization": f"Bearer {_token('sales-a1', ['SALES_STAFF'])}"},
    )
    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert r.json()["handoffs"][0]["handoff_type"] == "CLINICAL_RX"
    assert r.json()["handoffs"][0]["rx_summary"] is not None

    # Store-B sales staff is NOT a recipient -> sees nothing.
    rb = client.get(
        "/api/v1/handoffs/clinical-inbox",
        headers={"Authorization": f"Bearer {_token('sales-b1', ['SALES_STAFF'], store=STORE_B)}"},
    )
    assert rb.status_code == 200
    assert rb.json()["total"] == 0


def test_T6_acknowledge_sets_once(client, f50):
    _seed_test(f50)
    _send(client, _token("opto-1", ["OPTOMETRIST"]))
    hid = f50["handoffs"].collection.docs[0]["handoff_id"]
    tok = _token("sales-a1", ["SALES_STAFF"])
    r1 = client.patch(f"/api/v1/handoffs/{hid}/acknowledge", headers={"Authorization": f"Bearer {tok}"})
    assert r1.status_code == 200
    first_at = r1.json()["acknowledged_at"]
    assert r1.json()["acknowledged_by"] == "sales-a1"

    # A second acknowledge by a different recipient must NOT overwrite the first.
    tok2 = _token("sales-a2", ["SALES_CASHIER"])
    r2 = client.patch(f"/api/v1/handoffs/{hid}/acknowledge", headers={"Authorization": f"Bearer {tok2}"})
    assert r2.status_code == 200
    assert r2.json()["acknowledged_by"] == "sales-a1"  # original preserved
    assert r2.json()["acknowledged_at"] == first_at


def test_T7_mark_served_records_50_50_credit(client, f50):
    _seed_test(f50)
    _send(client, _token("opto-1", ["OPTOMETRIST"]))
    hid = f50["handoffs"].collection.docs[0]["handoff_id"]
    tok = _token("sales-a1", ["SALES_STAFF"])
    client.patch(f"/api/v1/handoffs/{hid}/acknowledge", headers={"Authorization": f"Bearer {tok}"})
    r = client.patch(f"/api/v1/handoffs/{hid}/mark-served", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert r.json()["served_by"] == "sales-a1"

    h = f50["handoffs"].collection.docs[0]
    assert h["mark_served"] is True
    assert h["served_by"] == "sales-a1"

    served = [a for a in f50["audit"].collection.docs if a["action"] == "CLINICAL_HANDOVER_SERVED"]
    assert len(served) == 1
    detail = served[0]["detail"]
    assert detail["optometrist_id"] == "opto-1"
    assert detail["served_by"] == "sales-a1"
    assert detail["conversion_credit"] == "50_50_split"
    assert detail["credit_split"]["optometrist_share"] == 0.5
    assert detail["credit_split"]["served_by_share"] == 0.5


def test_T8_double_mark_served_409(client, f50):
    _seed_test(f50)
    _send(client, _token("opto-1", ["OPTOMETRIST"]))
    hid = f50["handoffs"].collection.docs[0]["handoff_id"]
    tok = _token("sales-a1", ["SALES_STAFF"])
    client.patch(f"/api/v1/handoffs/{hid}/acknowledge", headers={"Authorization": f"Bearer {tok}"})
    r1 = client.patch(f"/api/v1/handoffs/{hid}/mark-served", headers={"Authorization": f"Bearer {tok}"})
    assert r1.status_code == 200
    r2 = client.patch(f"/api/v1/handoffs/{hid}/mark-served", headers={"Authorization": f"Bearer {tok}"})
    assert r2.status_code == 409
    served = [a for a in f50["audit"].collection.docs if a["action"] == "CLINICAL_HANDOVER_SERVED"]
    assert len(served) == 1  # no second audit row


def test_T9_feature_flag_off_blocks_send(client, f50):
    _seed_test(f50)
    f50["flag"]["value"] = False
    r = _send(client, _token("opto-1", ["OPTOMETRIST"]))
    assert r.status_code == 403
    assert "not enabled" in r.json()["detail"].lower()
    assert f50["handoffs"].collection.docs == []


def test_T10_expired_handoff_absent_from_inbox(client, f50):
    _seed_test(f50)
    _send(client, _token("opto-1", ["OPTOMETRIST"]))
    # Force the handoff to be expired.
    f50["handoffs"].collection.docs[0]["expires_at"] = datetime.now(timezone.utc) - timedelta(minutes=1)
    r = client.get(
        "/api/v1/handoffs/clinical-inbox",
        headers={"Authorization": f"Bearer {_token('sales-a1', ['SALES_STAFF'])}"},
    )
    assert r.status_code == 200
    assert r.json()["total"] == 0


def test_T11_accountant_cannot_read_inbox(client, f50):
    r = client.get(
        "/api/v1/handoffs/clinical-inbox",
        headers={"Authorization": f"Bearer {_token('acct-1', ['ACCOUNTANT'])}"},
    )
    assert r.status_code == 403


def test_T12_no_outbound_send_path(client, f50):
    """The ONLY delivery channel is the in-app bell. No WhatsApp/SMS send is ever
    invoked across the full send -> acknowledge -> mark-served lifecycle."""
    _seed_test(f50)
    _send(client, _token("opto-1", ["OPTOMETRIST"]))
    hid = f50["handoffs"].collection.docs[0]["handoff_id"]
    tok = _token("sales-a1", ["SALES_STAFF"])
    client.patch(f"/api/v1/handoffs/{hid}/acknowledge", headers={"Authorization": f"Bearer {tok}"})
    client.patch(f"/api/v1/handoffs/{hid}/mark-served", headers={"Authorization": f"Bearer {tok}"})
    # The blocked provider was never called.
    assert f50["sent"]["calls"] == []
    # And the bell DID fire (positive confirmation the channel is in-app).
    notifs = f50["db"].get_collection("notifications").docs
    assert len(notifs) == 3 and all(n["channels"] == ["IN_APP"] for n in notifs)
