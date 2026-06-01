"""
IMS 2.0 - Profile preferences persistence
==========================================
PUT /settings/profile/preferences must WRITE the user's email/SMS notification
preferences to Mongo, and GET must read them back. Previously the PUT echoed the
input without persisting and the GET returned hardcoded email/sms = True -- so
the Profile notification toggles were a silent no-op (every reload reverted).

Round-trips PUT -> GET against the real `user_preferences` collection. When no
DB is connected (local runs) the handler returns a "(no DB)" message, so the
persistence assertions are skipped rather than failing.
"""

import pytest


def _no_db(put_resp) -> bool:
    try:
        return "(no DB)" in (put_resp.json().get("message", "") or "")
    except Exception:  # noqa: BLE001
        return False


def test_get_preferences_defaults(client, auth_headers):
    """With no saved row, GET returns the opt-in defaults (email/sms True)."""
    got = client.get("/api/v1/settings/profile/preferences", headers=auth_headers)
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["email_notifications"] is True
    assert body["sms_notifications"] is True


def test_preferences_persist_email_sms(client, auth_headers):
    """PUT email/SMS = False, then GET must return False (not the hardcoded True)."""
    put = client.put(
        "/api/v1/settings/profile/preferences",
        json={
            "email_notifications": False,
            "sms_notifications": False,
            "language": "hi",
        },
        headers=auth_headers,
    )
    assert put.status_code == 200, put.text
    if _no_db(put):
        pytest.skip("user_preferences collection unavailable (no DB)")
    got = client.get(
        "/api/v1/settings/profile/preferences", headers=auth_headers
    ).json()
    assert got["email_notifications"] is False
    assert got["sms_notifications"] is False
    assert got["language"] == "hi"
    # Unset keys still fall back to the defaults (partial save never drops keys).
    assert got["currency"] == "INR"


def test_preferences_survive_reload(client, auth_headers):
    """A second independent GET (simulating a page reload / new request) still
    returns the saved values -- they are read from the DB, not request state."""
    put = client.put(
        "/api/v1/settings/profile/preferences",
        json={"email_notifications": False, "sms_notifications": True},
        headers=auth_headers,
    )
    assert put.status_code == 200, put.text
    if _no_db(put):
        pytest.skip("user_preferences collection unavailable (no DB)")
    # First read.
    first = client.get(
        "/api/v1/settings/profile/preferences", headers=auth_headers
    ).json()
    # Second read -- a fresh request, no shared in-memory state.
    second = client.get(
        "/api/v1/settings/profile/preferences", headers=auth_headers
    ).json()
    assert first["email_notifications"] is False
    assert second["email_notifications"] is False
    assert second["sms_notifications"] is True
