"""IMS 2.0 - CRM-2 phase 2: contact-lens auto-refill IN-APP trigger
==================================================================
The per-customer cl-refill-status signal already existed (read-only). This
covers the NEW in-app path: a store worklist of customers whose CL refill is
due/overdue, and a creator that turns each due row into a DEDUPED SYSTEM task
via the SAME task engine the SLA/variance reminders use. NO outbound message
is ever sent (the customer WhatsApp/SMS send stays dark).

A regression where the worklist double-counts a customer, ignores the horizon,
sends a message, or the reminder-creator re-creates a task on a re-run (no
dedupe) FAILS here.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
os.environ.setdefault("DISPATCH_MODE", "off")

import pytest  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import crm as crm_mod  # noqa: E402
from api.services import cl_refill as clr  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Faithful-enough fake orders collection (find + sort + limit + projection)
# ---------------------------------------------------------------------------


class _Cursor(list):
    def sort(self, key, direction=-1):
        try:
            self.sort_in_place = True
            super().sort(
                key=lambda d: d.get(key) or "", reverse=(direction == -1)
            )
        except Exception:  # noqa: BLE001
            pass
        return self

    def limit(self, _n):
        return self


class _OrdersColl:
    def __init__(self, docs: List[Dict[str, Any]]):
        self._docs = list(docs)

    def find(self, flt=None, projection=None):
        flt = flt or {}
        out = []
        for d in self._docs:
            if "$or" in flt:
                store_ok = any(
                    d.get(k) == v
                    for sub in flt["$or"]
                    for k, v in sub.items()
                )
                if not store_ok:
                    continue
            out.append(dict(d))
        return _Cursor(out)


class _DB:
    is_connected = True

    def __init__(self, orders):
        self._orders = orders

    def get_collection(self, name):
        if name == "orders":
            return _OrdersColl(self._orders)
        return _OrdersColl([])


class _FakeTaskRepo:
    """Mirrors the task repo surface create_system_task uses: find_many + create."""

    def __init__(self):
        self.tasks: List[Dict[str, Any]] = []

    def find_many(self, flt, **_kw):
        ref = (flt or {}).get("source_ref")
        return [t for t in self.tasks if t.get("source_ref") == ref]

    def create(self, task):
        self.tasks.append(dict(task))
        return task


def _order(cid, days_ago, *, modality="MONTHLY", pack=1, qty=1, store="BV-1", oid=None):
    return {
        "order_id": oid or f"ORD-{cid}-{days_ago}",
        "customer_id": cid,
        "customer_name": f"Cust {cid}",
        "store_id": store,
        "created_at": (datetime.utcnow() - timedelta(days=days_ago)).isoformat(),
        "items": [
            {
                "sku": f"SKU-{cid}",
                "category": "CONTACT_LENS",
                "modality": modality,
                "pack_size": pack,
                "quantity": qty,
            }
        ],
    }


# ---------------------------------------------------------------------------
# Pure service: estimate_supply_days / scan_due_refills
# ---------------------------------------------------------------------------


def test_estimate_supply_days_by_modality():
    # Daily: 2 packs x 30 = 60 lenses / 2 eyes = 30 days.
    assert clr.estimate_supply_days(30, 2, "DAILY") == 30
    # Monthly: 1 ordered pack = 30 days.
    assert clr.estimate_supply_days(1, 1, "MONTHLY") == 30
    # Biweekly: 2 ordered packs x 14 = 28 days.
    assert clr.estimate_supply_days(1, 2, "FORTNIGHTLY") == 28


def test_scan_due_refills_filters_horizon_and_dedupes_per_customer():
    # MONTHLY (30-day supply). Ordered 28 days ago => due in ~2 days (in window).
    # Ordered 40 days ago => overdue ~10 days (in window). Ordered 5 days ago
    # => due in ~25 days (OUTSIDE the default 14-day horizon).
    orders = [
        _order("due-soon", 28),
        _order("overdue", 40),
        _order("not-yet", 5),
        # A SECOND, older CL order for "overdue" -- must be ignored (newest wins).
        _order("overdue", 200, oid="ORD-overdue-OLD"),
    ]
    rows = clr.scan_due_refills(_DB(orders), "BV-1", due_within_days=14)
    ids = [r["customer_id"] for r in rows]
    assert "due-soon" in ids and "overdue" in ids
    assert "not-yet" not in ids  # outside horizon
    # Deduped to ONE row per customer.
    assert ids.count("overdue") == 1
    # Sorted most-overdue first.
    assert rows[0]["customer_id"] == "overdue"
    overdue_row = next(r for r in rows if r["customer_id"] == "overdue")
    assert overdue_row["overdue"] is True
    assert overdue_row["last_cl_order_id"] == "ORD-overdue-40"  # newest, not OLD


def test_scan_due_refills_failsoft_no_db():
    assert clr.scan_due_refills(None, "BV-1") == []


def test_refill_task_priority():
    assert clr.refill_task_priority(-3) == "P2"  # overdue is louder
    assert clr.refill_task_priority(5) == "P3"


# ---------------------------------------------------------------------------
# Router: worklist endpoint (store-scoped, read-only, no message)
# ---------------------------------------------------------------------------


def _mgr(store="BV-1"):
    return {
        "user_id": "M1",
        "roles": ["STORE_MANAGER"],
        "store_ids": [store],
        "active_store_id": store,
    }


def _staff(store="BV-1"):
    return {
        "user_id": "S1",
        "roles": ["SALES_STAFF"],
        "store_ids": [store],
        "active_store_id": store,
    }


def test_worklist_endpoint_returns_due_rows(monkeypatch):
    orders = [_order("a", 28), _order("b", 40), _order("c", 5)]
    monkeypatch.setattr(crm_mod, "_crm_get_db", lambda: _DB(orders))
    out = _run(
        crm_mod.get_cl_refill_worklist(
            store_id="BV-1", due_within_days=14, current_user=_mgr()
        )
    )
    assert out["store_id"] == "BV-1"
    ids = [r["customer_id"] for r in out["items"]]
    assert "a" in ids and "b" in ids and "c" not in ids
    assert out["overdue_count"] == 1  # only "b" is overdue
    assert out["count"] == 2


def test_worklist_store_scope_403_for_other_store(monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(crm_mod, "_crm_get_db", lambda: _DB([]))
    with pytest.raises(HTTPException) as exc:
        _run(
            crm_mod.get_cl_refill_worklist(
                store_id="BV-OTHER", due_within_days=14, current_user=_staff("BV-1")
            )
        )
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# Router: reminder-creator (reuses create_system_task; dedupe; role-gated)
# ---------------------------------------------------------------------------


def test_create_reminders_makes_deduped_system_tasks(monkeypatch):
    orders = [_order("a", 28), _order("b", 40)]
    repo = _FakeTaskRepo()
    monkeypatch.setattr(crm_mod, "_crm_get_db", lambda: _DB(orders))
    monkeypatch.setattr(crm_mod, "get_task_repository", lambda: repo)

    body = crm_mod.CLRefillReminderBody(due_within_days=14)
    first = _run(
        crm_mod.create_cl_refill_reminders(
            store_id="BV-1", body=body, current_user=_mgr()
        )
    )
    assert first["created"] == 2
    assert first["deduped"] == 0
    # Tasks were created via the canonical SYSTEM-task engine.
    assert len(repo.tasks) == 2
    t = repo.tasks[0]
    assert t["source"] == "SYSTEM"
    assert t["category"] == "CRM"
    assert t["store_id"] == "BV-1"
    assert t["source_ref"].startswith("cl_refill:")
    assert t["status"] == "OPEN"
    # The overdue customer gets the louder P2.
    overdue_task = next(
        x for x in repo.tasks if "b" in (x.get("source_ref") or "")
    )
    assert overdue_task["priority"] == "P2"

    # Re-run: every task dedupes on source_ref -> zero new tasks, none doubled.
    second = _run(
        crm_mod.create_cl_refill_reminders(
            store_id="BV-1", body=body, current_user=_mgr()
        )
    )
    assert second["created"] == 0
    assert second["deduped"] == 2
    assert len(repo.tasks) == 2  # NOT 4


def test_create_reminders_role_gated(monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(crm_mod, "_crm_get_db", lambda: _DB([]))
    monkeypatch.setattr(crm_mod, "get_task_repository", lambda: _FakeTaskRepo())
    with pytest.raises(HTTPException) as exc:
        _run(
            crm_mod.create_cl_refill_reminders(
                store_id="BV-1",
                body=crm_mod.CLRefillReminderBody(),
                current_user=_staff(),  # SALES_STAFF cannot create reminders
            )
        )
    assert exc.value.status_code == 403
