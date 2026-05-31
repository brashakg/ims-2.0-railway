"""
IMS 2.0 — PUT /settings/printers must be role-gated
====================================================
Every settings write is role-gated except printer-settings, which only required
a valid login -- so any authenticated user (workshop staff, cashier) could
overwrite the store's printer configuration. This locks the gate: non-privileged
roles get 403; SUPERADMIN/ADMIN/STORE_MANAGER pass the check.
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-key-printer-gate")


def _call(user_roles):
    from fastapi import HTTPException

    from api.routers.settings import PrinterSettings, update_printer_settings

    user = {"user_id": "u1", "roles": user_roles}
    # asyncio.run() spins up a FRESH event loop per call. get_event_loop() +
    # run_until_complete reuses a process-wide loop that an earlier async test in
    # the full suite can leave closed -> "RuntimeError: Event loop is closed".
    return asyncio.run(update_printer_settings(PrinterSettings(), user)), HTTPException


def test_workshop_staff_blocked():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        _call(["WORKSHOP_STAFF"])
    assert exc.value.status_code == 403


def test_cashier_blocked():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        _call(["SALES_CASHIER"])
    assert exc.value.status_code == 403


def test_privileged_roles_pass_the_gate():
    # SUPERADMIN/ADMIN/STORE_MANAGER must get PAST the 403 (they may then hit the
    # no-DB return, which is fine -- the point is they are NOT rejected).
    for role in ("SUPERADMIN", "ADMIN", "STORE_MANAGER"):
        result, _ = _call([role])
        assert "Printer settings updated" in result["message"]
