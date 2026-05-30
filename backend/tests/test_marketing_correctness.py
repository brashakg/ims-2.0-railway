"""
IMS 2.0 - Marketing correctness regression tests
=================================================
Covers the correctness bugs fixed in this PR:

  BUG-1  WalkinRequest phone pattern accepted 0-5 prefix numbers (not valid
         Indian mobiles).  Fixed: pattern now requires [6-9] leading digit.

  BUG-2  /notifications/send had no marketing_consent gate for non-transactional
         templates.  Fixed: non-transactional sends to opted-out customers -> 422.

  BUG-3  /rx-reminder/{id} had no marketing_consent check.  Fixed: 422 on
         opted-out customer.

  BUG-4  /rx-reminder/{id} had no phone validation (empty/invalid phone ->
         silent PENDING notification that never delivered).  Fixed: 422 on
         bad phone.

  BUG-5  /rx-reminder/{id} was missing duplicate / spam guard.  Fixed: 429 if
         PENDING/SENT/SIMULATED reminder exists within the last 24 hours.

  BUG-6  /rx-reminder/{id} used a hard-coded placeholder "soon" for expiry_date
         instead of the actual prescription expiry.  Fixed: calculates from
         most-recent prescription created_at + 730 days.

  BUG-7  /referral-invite/{id} had no marketing_consent check.  Fixed: 422 on
         opted-out customer.

  BUG-8  /walkout/{id} sent WALKOUT_RECOVERY (PROMOTIONAL) to opted-out
         customers.  Fixed: walkout is still recorded but notification is
         skipped; response reports "recovery_message: skipped (opted out)".

  BUG-9  /follow-ups POST accepted past scheduled_date values silently.
         Fixed: 422 with explanatory message.

All tests are self-contained: fake in-memory DB + stubbed send_notification,
no live DB, no provider calls.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, date, timezone

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import jwt  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import marketing as marketing_mod  # noqa: E402
from api.routers import follow_ups as fu_mod  # noqa: E402
from api.routers import auth as auth_mod  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tok(roles, uid="u1", store_id="BV-PUN-01"):
    return jwt.encode(
        {
            "sub": uid,
            "user_id": uid,
            "username": "tester",
            "roles": list(roles),
            "active_store_id": store_id,
            "exp": datetime.utcnow() + timedelta(hours=1),
        },
        auth_mod.SECRET_KEY,
        algorithm=auth_mod.ALGORITHM,
    )


class _FakeColl:
    """Minimal in-memory collection."""

    def __init__(self, docs=None):
        self.docs = list(docs or [])

    # --- read ---
    def find_one(self, query=None, projection=None, sort=None):
        q = query or {}
        for d in self.docs:
            if _matches(d, q):
                return dict(d)
        return None

    def find(self, query=None, projection=None):
        q = query or {}
        return _Cursor([d for d in self.docs if _matches(d, q)])

    def count_documents(self, query=None):
        q = query or {}
        return sum(1 for d in self.docs if _matches(d, q))

    # --- write ---
    def insert_one(self, doc):
        self.docs.append(dict(doc))

    def update_one(self, query, update, **kw):
        pass

    def update_many(self, query, update):
        pass


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


def _matches(doc: dict, query: dict) -> bool:
    """Very small Mongo query evaluator (equality + $in + $gte + $nin)."""
    for key, val in query.items():
        if key == "$or":
            if not any(_matches(doc, sub) for sub in val):
                return False
            continue
        actual = doc.get(key)
        if isinstance(val, dict):
            # operator queries
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
        else:
            if actual != val:
                return False
    return True


class _FakeDB:
    is_connected = True

    def __init__(self, collections=None):
        self._cols = collections or {}

    def get_collection(self, name):
        return self._cols.get(name, _FakeColl())


async def _noop_send(**kwargs):
    return {"status": "queued", "notification_id": "NTF-TEST"}


def _noop_rate(*_a, **_k):
    return None


def _mk_marketing_client(db, *, monkeypatch) -> TestClient:
    monkeypatch.setattr(marketing_mod, "_get_db", lambda: db)
    monkeypatch.setattr(marketing_mod, "send_notification", _noop_send)
    monkeypatch.setattr(marketing_mod, "_check_notification_rate", _noop_rate)
    app = FastAPI()
    app.include_router(marketing_mod.router, prefix="/api/v1/marketing")
    return TestClient(app)


# ---------------------------------------------------------------------------
# BUG-1: WalkinRequest phone pattern
# ---------------------------------------------------------------------------


def test_walkin_rejects_0_prefix_phone(monkeypatch):
    """Phone starting with '0' is not a valid Indian mobile -> 422."""
    db = _FakeDB({"walkins": _FakeColl(), "follow_ups": _FakeColl(), "customers": _FakeColl()})
    client = _mk_marketing_client(db, monkeypatch=monkeypatch)
    tok = _tok(["STORE_MANAGER"])
    r = client.post(
        "/api/v1/marketing/walkin",
        json={"phone": "0123456789", "interest": "frames"},
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 422, r.text


def test_walkin_rejects_5_prefix_phone(monkeypatch):
    """Phone starting with '5' is not a valid Indian mobile -> 422."""
    db = _FakeDB({"walkins": _FakeColl(), "follow_ups": _FakeColl(), "customers": _FakeColl()})
    client = _mk_marketing_client(db, monkeypatch=monkeypatch)
    tok = _tok(["STORE_MANAGER"])
    r = client.post(
        "/api/v1/marketing/walkin",
        json={"phone": "5123456789", "interest": "frames"},
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 422, r.text


def test_walkin_accepts_6_prefix_phone(monkeypatch):
    """Phone starting with '6' is a valid Indian mobile -> 200/201."""
    db = _FakeDB({"walkins": _FakeColl(), "follow_ups": _FakeColl(), "customers": _FakeColl()})
    client = _mk_marketing_client(db, monkeypatch=monkeypatch)
    tok = _tok(["STORE_MANAGER"])
    r = client.post(
        "/api/v1/marketing/walkin",
        json={"phone": "6123456789", "interest": "frames"},
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200, r.text


def test_walkin_accepts_9_prefix_phone(monkeypatch):
    """Phone starting with '9' is a valid Indian mobile -> 200/201."""
    db = _FakeDB({"walkins": _FakeColl(), "follow_ups": _FakeColl(), "customers": _FakeColl()})
    client = _mk_marketing_client(db, monkeypatch=monkeypatch)
    tok = _tok(["STORE_MANAGER"])
    r = client.post(
        "/api/v1/marketing/walkin",
        json={"phone": "9812345678", "interest": "frames"},
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# BUG-2: /notifications/send — consent gate for non-transactional templates
# ---------------------------------------------------------------------------


def test_single_send_blocks_opted_out_for_non_transactional(monkeypatch):
    """Sending a non-transactional template to an opted-out customer -> 422."""
    db = _FakeDB(
        {"customers": _FakeColl([{"customer_id": "C1", "marketing_consent": False}])}
    )
    client = _mk_marketing_client(db, monkeypatch=monkeypatch)
    tok = _tok(["STORE_MANAGER"])
    r = client.post(
        "/api/v1/marketing/notifications/send",
        json={
            "customer_id": "C1",
            "customer_phone": "9000000001",
            "customer_name": "Test",
            "template_id": "PRESCRIPTION_EXPIRY",
            "channel": "WHATSAPP",
        },
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 422, r.text
    assert "opted out" in r.json()["detail"].lower()


def test_single_send_allows_transactional_to_opted_out(monkeypatch):
    """Transactional templates (ORDER_DELIVERED, GOOGLE_REVIEW_REQUEST,
    NPS_SURVEY) bypass the marketing_consent gate -> 200."""
    db = _FakeDB(
        {"customers": _FakeColl([{"customer_id": "C1", "marketing_consent": False}])}
    )
    client = _mk_marketing_client(db, monkeypatch=monkeypatch)
    tok = _tok(["STORE_MANAGER"])
    for tmpl in ("ORDER_DELIVERED", "GOOGLE_REVIEW_REQUEST", "NPS_SURVEY"):
        r = client.post(
            "/api/v1/marketing/notifications/send",
            json={
                "customer_id": "C1",
                "customer_phone": "9000000001",
                "customer_name": "Test",
                "template_id": tmpl,
                "channel": "WHATSAPP",
            },
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert r.status_code == 200, f"template {tmpl}: {r.text}"


def test_single_send_allows_consented_customer(monkeypatch):
    """An opted-in customer can receive non-transactional templates -> 200."""
    db = _FakeDB(
        {"customers": _FakeColl([{"customer_id": "C1", "marketing_consent": True}])}
    )
    client = _mk_marketing_client(db, monkeypatch=monkeypatch)
    tok = _tok(["STORE_MANAGER"])
    r = client.post(
        "/api/v1/marketing/notifications/send",
        json={
            "customer_id": "C1",
            "customer_phone": "9000000001",
            "customer_name": "Test",
            "template_id": "PRESCRIPTION_EXPIRY",
            "channel": "WHATSAPP",
        },
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# BUG-3 + BUG-4: /rx-reminder — consent + phone validation
# ---------------------------------------------------------------------------


def test_rx_reminder_blocks_opted_out(monkeypatch):
    """Rx reminder to opted-out customer -> 422."""
    db = _FakeDB(
        {
            "customers": _FakeColl(
                [{"customer_id": "C1", "mobile": "9000000001", "marketing_consent": False}]
            ),
            "stores": _FakeColl([]),
            "prescriptions": _FakeColl([]),
            "notification_logs": _FakeColl([]),
        }
    )
    client = _mk_marketing_client(db, monkeypatch=monkeypatch)
    tok = _tok(["STORE_MANAGER"])
    r = client.post(
        "/api/v1/marketing/rx-reminder/C1",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 422, r.text
    assert "opted out" in r.json()["detail"].lower()


def test_rx_reminder_blocks_missing_phone(monkeypatch):
    """Rx reminder with missing/empty phone -> 422 (invalid phone)."""
    db = _FakeDB(
        {
            "customers": _FakeColl(
                [{"customer_id": "C1", "mobile": "", "marketing_consent": True}]
            ),
            "stores": _FakeColl([]),
            "prescriptions": _FakeColl([]),
            "notification_logs": _FakeColl([]),
        }
    )
    client = _mk_marketing_client(db, monkeypatch=monkeypatch)
    tok = _tok(["STORE_MANAGER"])
    r = client.post(
        "/api/v1/marketing/rx-reminder/C1",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 422, r.text
    assert "phone" in r.json()["detail"].lower()


def test_rx_reminder_blocks_invalid_phone(monkeypatch):
    """Rx reminder with a number that starts with 0 -> 422."""
    db = _FakeDB(
        {
            "customers": _FakeColl(
                [{"customer_id": "C1", "mobile": "0123456789", "marketing_consent": True}]
            ),
            "stores": _FakeColl([]),
            "prescriptions": _FakeColl([]),
            "notification_logs": _FakeColl([]),
        }
    )
    client = _mk_marketing_client(db, monkeypatch=monkeypatch)
    tok = _tok(["STORE_MANAGER"])
    r = client.post(
        "/api/v1/marketing/rx-reminder/C1",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 422, r.text


def test_rx_reminder_succeeds_for_consented_valid_phone(monkeypatch):
    """Consented customer with valid phone -> 200."""
    db = _FakeDB(
        {
            "customers": _FakeColl(
                [{"customer_id": "C1", "mobile": "9000000001", "marketing_consent": True}]
            ),
            "stores": _FakeColl([{"store_id": "BV-PUN-01", "name": "Better Vision Pune"}]),
            "prescriptions": _FakeColl([]),
            "notification_logs": _FakeColl([]),
        }
    )
    client = _mk_marketing_client(db, monkeypatch=monkeypatch)
    tok = _tok(["STORE_MANAGER"])
    r = client.post(
        "/api/v1/marketing/rx-reminder/C1",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# BUG-5: /rx-reminder — duplicate / spam guard
# ---------------------------------------------------------------------------


def test_rx_reminder_dedup_blocks_within_24h(monkeypatch):
    """A second Rx reminder for the same customer within 24 hours -> 429."""
    recent_log = {
        "customer_id": "C1",
        "template_id": "PRESCRIPTION_EXPIRY",
        "status": "SENT",
        "created_at": (datetime.now() - timedelta(hours=2)).isoformat(),
    }
    db = _FakeDB(
        {
            "customers": _FakeColl(
                [{"customer_id": "C1", "mobile": "9000000001", "marketing_consent": True}]
            ),
            "stores": _FakeColl([]),
            "prescriptions": _FakeColl([]),
            "notification_logs": _FakeColl([recent_log]),
        }
    )
    client = _mk_marketing_client(db, monkeypatch=monkeypatch)
    tok = _tok(["STORE_MANAGER"])
    r = client.post(
        "/api/v1/marketing/rx-reminder/C1",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 429, r.text
    assert "24 hours" in r.json()["detail"].lower()


def test_rx_reminder_dedup_allows_after_24h(monkeypatch):
    """An Rx reminder logged 25 hours ago does NOT block a new send."""
    old_log = {
        "customer_id": "C1",
        "template_id": "PRESCRIPTION_EXPIRY",
        "status": "SENT",
        "created_at": (datetime.now() - timedelta(hours=25)).isoformat(),
    }
    db = _FakeDB(
        {
            "customers": _FakeColl(
                [{"customer_id": "C1", "mobile": "9000000001", "marketing_consent": True}]
            ),
            "stores": _FakeColl([]),
            "prescriptions": _FakeColl([]),
            "notification_logs": _FakeColl([old_log]),
        }
    )
    client = _mk_marketing_client(db, monkeypatch=monkeypatch)
    tok = _tok(["STORE_MANAGER"])
    r = client.post(
        "/api/v1/marketing/rx-reminder/C1",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200, r.text


def test_rx_reminder_dedup_allows_retry_on_failed(monkeypatch):
    """A FAILED log within 24 hours does NOT block a retry (message never
    reached the customer, so a retry is safe and desirable)."""
    failed_log = {
        "customer_id": "C1",
        "template_id": "PRESCRIPTION_EXPIRY",
        "status": "FAILED",
        "created_at": (datetime.now() - timedelta(minutes=30)).isoformat(),
    }
    db = _FakeDB(
        {
            "customers": _FakeColl(
                [{"customer_id": "C1", "mobile": "9000000001", "marketing_consent": True}]
            ),
            "stores": _FakeColl([]),
            "prescriptions": _FakeColl([]),
            "notification_logs": _FakeColl([failed_log]),
        }
    )
    client = _mk_marketing_client(db, monkeypatch=monkeypatch)
    tok = _tok(["STORE_MANAGER"])
    r = client.post(
        "/api/v1/marketing/rx-reminder/C1",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# BUG-6: /rx-reminder — expiry_date populated from actual prescription
# ---------------------------------------------------------------------------


def test_rx_reminder_populates_expiry_date_from_prescription(monkeypatch):
    """The notification variables must contain the real expiry date (not
    the placeholder 'soon') when a prescription exists."""
    created_date = datetime(2024, 6, 1, 10, 0, 0)
    expected_expiry = (created_date + timedelta(days=730)).strftime("%d %b %Y")

    captured_vars = {}

    async def _capture_send(**kwargs):
        captured_vars.update(kwargs.get("variables", {}))
        return {"status": "queued", "notification_id": "NTF-TEST"}

    db = _FakeDB(
        {
            "customers": _FakeColl(
                [{"customer_id": "C1", "mobile": "9000000001", "marketing_consent": True}]
            ),
            "stores": _FakeColl([{"store_id": "BV-PUN-01", "name": "Better Vision"}]),
            "prescriptions": _FakeColl(
                [{"customer_id": "C1", "created_at": created_date.isoformat()}]
            ),
            "notification_logs": _FakeColl([]),
        }
    )

    monkeypatch.setattr(marketing_mod, "_get_db", lambda: db)
    monkeypatch.setattr(marketing_mod, "send_notification", _capture_send)
    monkeypatch.setattr(marketing_mod, "_check_notification_rate", _noop_rate)

    app = FastAPI()
    app.include_router(marketing_mod.router, prefix="/api/v1/marketing")
    client = TestClient(app)

    tok = _tok(["STORE_MANAGER"])
    r = client.post(
        "/api/v1/marketing/rx-reminder/C1",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200, r.text
    assert captured_vars.get("expiry_date") == expected_expiry, (
        f"expected {expected_expiry!r}, got {captured_vars.get('expiry_date')!r}"
    )


def test_rx_reminder_falls_back_to_soon_when_no_prescription(monkeypatch):
    """If there is no prescription in the DB, expiry_date stays 'soon' (graceful
    fallback, not a crash)."""
    captured_vars = {}

    async def _capture_send(**kwargs):
        captured_vars.update(kwargs.get("variables", {}))
        return {"status": "queued"}

    db = _FakeDB(
        {
            "customers": _FakeColl(
                [{"customer_id": "C1", "mobile": "9000000001", "marketing_consent": True}]
            ),
            "stores": _FakeColl([]),
            "prescriptions": _FakeColl([]),
            "notification_logs": _FakeColl([]),
        }
    )

    monkeypatch.setattr(marketing_mod, "_get_db", lambda: db)
    monkeypatch.setattr(marketing_mod, "send_notification", _capture_send)
    monkeypatch.setattr(marketing_mod, "_check_notification_rate", _noop_rate)

    app = FastAPI()
    app.include_router(marketing_mod.router, prefix="/api/v1/marketing")
    client = TestClient(app)

    tok = _tok(["STORE_MANAGER"])
    r = client.post(
        "/api/v1/marketing/rx-reminder/C1",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200, r.text
    assert captured_vars.get("expiry_date") == "soon"


# ---------------------------------------------------------------------------
# BUG-7: /referral-invite — consent gate
# ---------------------------------------------------------------------------


def test_referral_invite_blocks_opted_out(monkeypatch):
    """Referral invite (PROMOTIONAL) must not be sent to opted-out customer."""
    db = _FakeDB(
        {
            "customers": _FakeColl(
                [{"customer_id": "C1", "mobile": "9000000001", "marketing_consent": False}]
            ),
            "referrals": _FakeColl([]),
        }
    )
    client = _mk_marketing_client(db, monkeypatch=monkeypatch)
    tok = _tok(["STORE_MANAGER"])
    r = client.post(
        "/api/v1/marketing/referral-invite/C1",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 422, r.text
    assert "opted out" in r.json()["detail"].lower()


def test_referral_invite_allows_consented(monkeypatch):
    """Consented customer can receive the referral invite -> 200."""
    db = _FakeDB(
        {
            "customers": _FakeColl(
                [{"customer_id": "C1", "mobile": "9000000001", "name": "Alice", "marketing_consent": True}]
            ),
            "referrals": _FakeColl([]),
        }
    )
    client = _mk_marketing_client(db, monkeypatch=monkeypatch)
    tok = _tok(["STORE_MANAGER"])
    r = client.post(
        "/api/v1/marketing/referral-invite/C1",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200, r.text


def test_referral_invite_allows_no_consent_preference(monkeypatch):
    """Missing marketing_consent (None/absent) defaults to consented -> 200."""
    db = _FakeDB(
        {
            "customers": _FakeColl(
                [{"customer_id": "C1", "mobile": "9000000001", "name": "Bob"}]
            ),
            "referrals": _FakeColl([]),
        }
    )
    client = _mk_marketing_client(db, monkeypatch=monkeypatch)
    tok = _tok(["STORE_MANAGER"])
    r = client.post(
        "/api/v1/marketing/referral-invite/C1",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# BUG-8: /walkout — consent gate for recovery notification
# ---------------------------------------------------------------------------


def test_walkout_records_but_skips_notification_for_opted_out(monkeypatch):
    """Walkout must be stored for all customers; the PROMOTIONAL recovery
    notification must be silently skipped for opted-out customers."""
    notif_calls = []

    async def _capture_send(**kwargs):
        notif_calls.append(kwargs)
        return {"status": "queued"}

    db = _FakeDB(
        {
            "customers": _FakeColl(
                [{"customer_id": "C1", "mobile": "9000000001", "marketing_consent": False}]
            ),
            "walkouts": _FakeColl([]),
            "stores": _FakeColl([]),
        }
    )

    monkeypatch.setattr(marketing_mod, "_get_db", lambda: db)
    monkeypatch.setattr(marketing_mod, "send_notification", _capture_send)
    monkeypatch.setattr(marketing_mod, "_check_notification_rate", _noop_rate)

    app = FastAPI()
    app.include_router(marketing_mod.router, prefix="/api/v1/marketing")
    client = TestClient(app)

    tok = _tok(["STORE_MANAGER"])
    r = client.post(
        "/api/v1/marketing/walkout/C1",
        json={"frames_tried": ["Ray-Ban RB3025"]},
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # Walkout recorded
    assert "walkout_id" in body["walkout"]
    # No notification fired
    assert len(notif_calls) == 0
    # Response clearly reports the skip
    assert body["recovery_message"] == "skipped (opted out)"


def test_walkout_sends_notification_for_consented(monkeypatch):
    """Consented customer's walkout triggers the recovery notification."""
    notif_calls = []

    async def _capture_send(**kwargs):
        notif_calls.append(kwargs)
        return {"status": "queued"}

    db = _FakeDB(
        {
            "customers": _FakeColl(
                [{"customer_id": "C1", "mobile": "9000000001", "marketing_consent": True}]
            ),
            "walkouts": _FakeColl([]),
            "stores": _FakeColl([]),
        }
    )

    monkeypatch.setattr(marketing_mod, "_get_db", lambda: db)
    monkeypatch.setattr(marketing_mod, "send_notification", _capture_send)
    monkeypatch.setattr(marketing_mod, "_check_notification_rate", _noop_rate)

    app = FastAPI()
    app.include_router(marketing_mod.router, prefix="/api/v1/marketing")
    client = TestClient(app)

    tok = _tok(["STORE_MANAGER"])
    r = client.post(
        "/api/v1/marketing/walkout/C1",
        json={"frames_tried": ["Ray-Ban RB3025"]},
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # Notification fired
    assert len(notif_calls) == 1
    assert notif_calls[0]["template_id"] == "WALKOUT_RECOVERY"
    assert body["recovery_message"] == "scheduled"


# ---------------------------------------------------------------------------
# BUG-9: /follow-ups POST — past scheduled_date rejected
# ---------------------------------------------------------------------------


def _mk_fu_client(db, *, monkeypatch) -> TestClient:
    monkeypatch.setattr(fu_mod, "_get_db", lambda: db)
    app = FastAPI()
    app.include_router(fu_mod.router, prefix="/api/v1/follow-ups")
    return TestClient(app)


def test_follow_up_create_rejects_past_date(monkeypatch):
    """Creating a follow-up with yesterday's date -> 422."""
    class _Wrapper:
        is_connected = True

        def get_collection(self, name):
            return _FakeColl()

    client = _mk_fu_client(_Wrapper(), monkeypatch=monkeypatch)
    tok = _tok(["STORE_MANAGER"])
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    r = client.post(
        "/api/v1/follow-ups/",
        json={
            "customer_id": "C1",
            "customer_name": "Test Customer",
            "customer_phone": "9000000001",
            "store_id": "BV-PUN-01",
            "type": "general",
            "scheduled_date": yesterday,
        },
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 422, r.text
    assert "past" in r.json()["detail"].lower()


def test_follow_up_create_accepts_today(monkeypatch):
    """Scheduling a follow-up for today is valid -> 200."""

    class _InsertResult:
        inserted_id = "fake-oid"

    class _InsertColl(_FakeColl):
        def insert_one(self, doc):
            self.docs.append(doc)
            return _InsertResult()

    class _Wrapper:
        is_connected = True

        def get_collection(self, name):
            return _InsertColl()

    client = _mk_fu_client(_Wrapper(), monkeypatch=monkeypatch)
    tok = _tok(["STORE_MANAGER"])
    today = date.today().isoformat()
    r = client.post(
        "/api/v1/follow-ups/",
        json={
            "customer_id": "C1",
            "customer_name": "Test Customer",
            "customer_phone": "9000000001",
            "store_id": "BV-PUN-01",
            "type": "general",
            "scheduled_date": today,
        },
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200, r.text


def test_follow_up_create_accepts_future_date(monkeypatch):
    """Scheduling a follow-up for a future date is valid -> 200."""

    class _InsertResult:
        inserted_id = "fake-oid"

    class _InsertColl(_FakeColl):
        def insert_one(self, doc):
            self.docs.append(doc)
            return _InsertResult()

    class _Wrapper:
        is_connected = True

        def get_collection(self, name):
            return _InsertColl()

    client = _mk_fu_client(_Wrapper(), monkeypatch=monkeypatch)
    tok = _tok(["STORE_MANAGER"])
    future_date = (date.today() + timedelta(days=7)).isoformat()
    r = client.post(
        "/api/v1/follow-ups/",
        json={
            "customer_id": "C1",
            "customer_name": "Test Customer",
            "customer_phone": "9000000001",
            "store_id": "BV-PUN-01",
            "type": "eye_test_reminder",
            "scheduled_date": future_date,
        },
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200, r.text


def test_follow_up_create_rejects_invalid_date_format(monkeypatch):
    """Malformed date string -> 422 (helpful error, not 500)."""
    class _Wrapper:
        is_connected = True

        def get_collection(self, name):
            return _FakeColl()

    client = _mk_fu_client(_Wrapper(), monkeypatch=monkeypatch)
    tok = _tok(["STORE_MANAGER"])
    r = client.post(
        "/api/v1/follow-ups/",
        json={
            "customer_id": "C1",
            "customer_name": "Test Customer",
            "customer_phone": "9000000001",
            "store_id": "BV-PUN-01",
            "type": "general",
            "scheduled_date": "not-a-date",
        },
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# Channel + phone enum validation (already present pre-PR, regression cover)
# ---------------------------------------------------------------------------


def test_send_rejects_invalid_channel(monkeypatch):
    """Sending with an unknown channel -> 422 (not 500)."""
    db = _FakeDB({"customers": _FakeColl()})
    client = _mk_marketing_client(db, monkeypatch=monkeypatch)
    tok = _tok(["STORE_MANAGER"])
    r = client.post(
        "/api/v1/marketing/notifications/send",
        json={
            "customer_id": "C1",
            "customer_phone": "9000000001",
            "customer_name": "Test",
            "template_id": "PRESCRIPTION_EXPIRY",
            "channel": "TELEGRAM",
        },
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 422, r.text


def test_send_rejects_invalid_phone(monkeypatch):
    """Single-send with phone that can't be an Indian mobile -> 422."""
    db = _FakeDB({"customers": _FakeColl()})
    client = _mk_marketing_client(db, monkeypatch=monkeypatch)
    tok = _tok(["STORE_MANAGER"])
    r = client.post(
        "/api/v1/marketing/notifications/send",
        json={
            "customer_id": "C1",
            "customer_phone": "1234567890",   # starts with 1
            "customer_name": "Test",
            "template_id": "PRESCRIPTION_EXPIRY",
            "channel": "WHATSAPP",
        },
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 422, r.text


def test_send_accepts_phone_with_country_code(monkeypatch):
    """Single-send with +91 prefix should be stripped and accepted -> 200."""
    db = _FakeDB(
        {"customers": _FakeColl([{"customer_id": "C1", "marketing_consent": True}])}
    )
    client = _mk_marketing_client(db, monkeypatch=monkeypatch)
    tok = _tok(["STORE_MANAGER"])
    r = client.post(
        "/api/v1/marketing/notifications/send",
        json={
            "customer_id": "C1",
            "customer_phone": "+919000000001",
            "customer_name": "Test",
            "template_id": "PRESCRIPTION_EXPIRY",
            "channel": "WHATSAPP",
        },
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200, r.text
