"""
IMS 2.0 - Marketing quiet-hours + honest-status tests
=====================================================
Covers the improvement-initiative council items:

  S9   Shared IST quiet-hours guard (agents.quiet_hours) is the single source of
       truth for MEGAPHONE, task-escalation WhatsApp, and the manual send API.
       23:00 IST is inside the quiet window; 14:00 IST is outside.

  S18  The manual marketing send API blocks PROMOTIONAL templates outside the
       9 AM - 9 PM IST window (409), while transactional/service templates in
       _TRANSACTIONAL_TEMPLATES are exempt.

  S8   The settings /notifications/test endpoint reports an HONEST status: in
       DISPATCH_MODE=off (default) it returns dispatched=False / SIMULATED with
       a "not dispatched" message, never a fabricated "sent".

All tests are self-contained: no live DB, no real provider calls, injected /
monkeypatched clock + dispatch gate.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
# Force the default dispatch posture for the honest-status test before the
# provider module is imported (it snapshots DISPATCH_MODE at import).
os.environ["DISPATCH_MODE"] = "off"

import jwt  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents import quiet_hours  # noqa: E402
from agents.quiet_hours import _IST, in_quiet_hours, promo_send_allowed  # noqa: E402
from api.services.task_notify import whatsapp_allowed  # noqa: E402
from api.routers import marketing as marketing_mod  # noqa: E402
from api.routers import settings as settings_mod  # noqa: E402
from api.routers import auth as auth_mod  # noqa: E402


def _ist(hour: int, minute: int = 0, day: int = 15) -> datetime:
    """tz-aware IST wall-clock datetime using the module's resolved zone."""
    tz = _IST or timezone(timedelta(hours=5, minutes=30))
    return datetime(2026, 5, day, hour, minute, 0, tzinfo=tz)


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


# ===========================================================================
# S9 - shared IST quiet-hours guard (pure)
# ===========================================================================


def test_2300_ist_is_quiet():
    assert in_quiet_hours(_ist(23, 0)) is True
    assert promo_send_allowed(_ist(23, 0)) is False


def test_1400_ist_is_open():
    assert in_quiet_hours(_ist(14, 0)) is False
    assert promo_send_allowed(_ist(14, 0)) is True


def test_boundaries_ist():
    # 21:00 quiet (>=21); 09:00 open (not <9); 08:59 quiet; 20:59 open.
    assert in_quiet_hours(_ist(21, 0)) is True
    assert in_quiet_hours(_ist(9, 0)) is False
    assert in_quiet_hours(_ist(8, 59)) is True
    assert in_quiet_hours(_ist(20, 59)) is False


def test_naive_datetime_treated_as_ist():
    # A naive 23:00 means 23:00 IST -> quiet.
    assert in_quiet_hours(datetime(2026, 5, 15, 23, 0, 0)) is True


def test_task_notify_uses_shared_ist_window():
    """task_notify.whatsapp_allowed now evaluates quiet hours in IST via the
    shared guard. 23:00 IST blocks a low-priority escalation; emergencies
    (P0/P1) still bypass."""
    assert whatsapp_allowed("P3", now=_ist(23, 0)) is False
    assert whatsapp_allowed("P2", now=_ist(3, 0)) is False
    assert whatsapp_allowed("P3", now=_ist(14, 0)) is True
    # Emergencies always notify regardless of the hour.
    assert whatsapp_allowed("P0", now=_ist(23, 0)) is True
    assert whatsapp_allowed("P1", now=_ist(3, 0)) is True


# ===========================================================================
# S18 - promo window enforced on the manual send API
# ===========================================================================


async def _noop_send(**kwargs):
    return {"status": "PENDING", "notification_id": "NTF-TEST", "dispatched": False}


def _noop_rate(*_a, **_k):
    return None


class _FakeColl:
    def __init__(self, docs=None):
        self.docs = list(docs or [])

    def find_one(self, query=None, projection=None, sort=None):
        return None  # no customer -> no opt-out -> consent gate passes

    def insert_one(self, doc):
        self.docs.append(dict(doc))


class _FakeDB:
    is_connected = True

    def __init__(self, cols=None):
        self._cols = cols or {}

    def get_collection(self, name):
        return self._cols.get(name, _FakeColl())


def _mk_marketing_client(monkeypatch, *, quiet: bool) -> TestClient:
    db = _FakeDB({"customers": _FakeColl()})
    monkeypatch.setattr(marketing_mod, "_get_db", lambda: db)
    monkeypatch.setattr(marketing_mod, "send_notification", _noop_send)
    monkeypatch.setattr(marketing_mod, "_check_notification_rate", _noop_rate)
    # Force the clock by pinning the underlying shared predicate. Both
    # promo_send_allowed() and the guard call quiet_hours.in_quiet_hours.
    monkeypatch.setattr(quiet_hours, "in_quiet_hours", lambda now=None: quiet)
    app = FastAPI()
    app.include_router(marketing_mod.router, prefix="/api/v1/marketing")
    return TestClient(app)


def _send_body(template_id: str) -> dict:
    return {
        "customer_id": "C1",
        "customer_phone": "9812345678",
        "customer_name": "Test",
        "template_id": template_id,
        "channel": "WHATSAPP",
    }


def test_promotional_send_blocked_in_quiet_hours(monkeypatch):
    """A PROMOTIONAL template (not in _TRANSACTIONAL_TEMPLATES) at 23:00 IST is
    blocked with 409."""
    client = _mk_marketing_client(monkeypatch, quiet=True)
    tok = _tok(["STORE_MANAGER"])
    r = client.post(
        "/api/v1/marketing/notifications/send",
        json=_send_body("WALKOUT_RECOVERY"),
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 409, r.text
    assert "quiet hours" in r.text.lower()


def test_transactional_send_allowed_in_quiet_hours(monkeypatch):
    """A transactional template (ORDER_DELIVERED) is EXEMPT from the window and
    goes through even at 23:00 IST."""
    client = _mk_marketing_client(monkeypatch, quiet=True)
    tok = _tok(["STORE_MANAGER"])
    r = client.post(
        "/api/v1/marketing/notifications/send",
        json=_send_body("ORDER_DELIVERED"),
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200, r.text


def test_promotional_send_allowed_during_open_window(monkeypatch):
    """The same PROMOTIONAL template goes through at 14:00 IST (window open)."""
    client = _mk_marketing_client(monkeypatch, quiet=False)
    tok = _tok(["STORE_MANAGER"])
    r = client.post(
        "/api/v1/marketing/notifications/send",
        json=_send_body("WALKOUT_RECOVERY"),
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200, r.text


def test_bulk_promotional_blocked_in_quiet_hours(monkeypatch):
    """send-bulk is promotional unless transactional -> blocked at 23:00 IST."""
    client = _mk_marketing_client(monkeypatch, quiet=True)
    tok = _tok(["STORE_MANAGER"])
    r = client.post(
        "/api/v1/marketing/notifications/send-bulk",
        json={
            "template_id": "WALKOUT_RECOVERY",
            "channel": "WHATSAPP",
            "recipients": [{"phone": "9812345678", "variables": {"name": "A"}}],
        },
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 409, r.text


# ===========================================================================
# S8 - honest /notifications/test status (no fake success)
# ===========================================================================


def _mk_settings_client() -> TestClient:
    app = FastAPI()
    app.include_router(settings_mod.router, prefix="/api/v1/settings")
    return TestClient(app)


def test_test_notification_off_mode_is_honest():
    """With DISPATCH_MODE=off the test endpoint must report it did NOT dispatch
    (status SIMULATED, dispatched False) -- not a fabricated 'sent'."""
    client = _mk_settings_client()
    tok = _tok(["SUPERADMIN"])
    r = client.post(
        "/api/v1/settings/notifications/test",
        params={"template_id": "ORDER_DELIVERED", "test_phone": "9812345678"},
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dispatched"] is False
    assert body["status"] == "SIMULATED"
    assert "not dispatched" in body["message"].lower()
    # And it must NOT claim it was sent.
    assert "test notification sent" not in body["message"].lower()


def test_test_notification_without_phone_is_honest():
    """No phone -> nothing dispatched, explicit SKIPPED, not a false success."""
    client = _mk_settings_client()
    tok = _tok(["SUPERADMIN"])
    r = client.post(
        "/api/v1/settings/notifications/test",
        params={"template_id": "ORDER_DELIVERED"},
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dispatched"] is False
    assert body["status"] == "SKIPPED"


def test_test_notification_requires_admin():
    """A non-admin role cannot probe (it can trigger a real send in live mode)."""
    client = _mk_settings_client()
    tok = _tok(["SALES_STAFF"])
    r = client.post(
        "/api/v1/settings/notifications/test",
        params={"template_id": "ORDER_DELIVERED", "test_phone": "9812345678"},
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 403, r.text
