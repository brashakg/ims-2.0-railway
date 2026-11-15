"""
IMS 2.0 - a failed approval-PIN status read is not "no PIN" (audit R43)
======================================================================
The Approvals screen showed "No PIN set" (and dropped the Current PIN box)
when the status read failed. The server had the same shortcut: a failed user
read, or no user store at all, answered 200 {"has_pin": false}. That answer is
also what the self-rotation check reads, so a failed read skipped the
current-PIN check. Unknown now stays unknown: the read raises / answers 503,
and a rotation is refused before anything is written.
"""

from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-key-pin-status")

import database.connection as conn  # noqa: E402
from api.routers import users as users_router  # noqa: E402
from api.services import approvals as appr  # noqa: E402
from tests.strict_fakes import StrictCollection  # noqa: E402

MANAGER = {"user_id": "u-mgr", "roles": ["STORE_MANAGER"]}


class _DownUsers:
    """A users collection whose reads fail; any write is recorded."""

    def __init__(self):
        self.writes = []

    def find_one(self, *_a, **_k):
        raise RuntimeError("users read failed")

    def update_one(self, *a, **k):
        self.writes.append((a, k))


def _use_db(monkeypatch, db):
    monkeypatch.setattr(conn, "get_db", lambda: SimpleNamespace(db=db))


def test_a_failed_status_read_is_an_error_not_no_pin(monkeypatch):
    _use_db(monkeypatch, {"users": _DownUsers()})
    with pytest.raises(RuntimeError):
        asyncio.run(users_router.get_approval_pin_status("u-mgr", MANAGER))


def test_no_user_store_answers_503_not_no_pin(monkeypatch):
    _use_db(monkeypatch, None)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(users_router.get_approval_pin_status("u-mgr", MANAGER))
    assert exc.value.status_code == 503


def test_a_rotation_is_refused_when_the_existing_pin_cannot_be_read(monkeypatch):
    """Before: has_pin came back False, the current-PIN check was skipped."""
    down = _DownUsers()
    _use_db(monkeypatch, {"users": down})
    body = users_router.ApprovalPinSet(pin="4321")  # no current_pin supplied
    with pytest.raises(RuntimeError):
        asyncio.run(users_router.set_approval_pin("u-mgr", body, MANAGER))
    assert down.writes == []


def test_a_real_answer_is_unchanged(monkeypatch):
    users = StrictCollection()
    users.insert_one({"user_id": "u-mgr", "approval_pin_hash": "x", "approval_pin_set_at": None})
    users.insert_one({"user_id": "u-new"})
    _use_db(monkeypatch, {"users": users})
    assert asyncio.run(users_router.get_approval_pin_status("u-mgr", MANAGER))["has_pin"] is True
    new = {"user_id": "u-new", "roles": ["STORE_MANAGER"]}
    assert asyncio.run(users_router.get_approval_pin_status("u-new", new))["has_pin"] is False
    # The pure service keeps its documented no-store answer.
    assert appr.has_approver_pin(None, "u1") == {"has_pin": False}
