"""GET /vendors/purchase-orders/{po_id}/timeline -- the PO lifecycle drawer
data (procurement Phase 3): ordered -> sent -> box received -> on shelf ->
bill settled. Read-only, store-scoped, fail-soft.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from api.routers import vendors as v  # noqa: E402


class _PORepo:
    def __init__(self, po):
        self.po = po

    def find_by_id(self, pid):
        return dict(self.po) if self.po and pid == self.po["po_id"] else None


class _GRNRepo:
    def __init__(self, grns):
        self.grns = grns

    def find_many(self, flt, limit=200):
        return [g for g in self.grns if g.get("po_id") == flt.get("po_id")]


class _Coll:
    def __init__(self, rows):
        self.rows = rows

    def find(self, flt, proj=None):
        # naive $or on po_id / grn_id in list
        out = []
        for r in self.rows:
            if r.get("doc_type") != flt.get("doc_type"):
                continue
            for term in flt.get("$or", []):
                if "po_id" in term and r.get("po_id") == term["po_id"]:
                    out.append(r)
                    break
                if "grn_id" in term and r.get("grn_id") in term["grn_id"].get(
                    "$in", []
                ):
                    out.append(r)
                    break
        return out


class _UsersColl:
    """Minimal stand-in for the users collection the name resolver reads."""

    def __init__(self, users):
        self.users = users

    def find(self, flt, proj=None):
        wanted = set()
        for term in flt.get("$or", []):
            for key in ("user_id", "id"):
                if key in term:
                    wanted.update(term[key].get("$in", []))
        return [u for u in self.users if u.get("user_id") in wanted]


class _DB:
    def __init__(self, bills, users=()):
        self._bills = bills
        self._users = list(users)

    def get_collection(self, name):
        if name == "users":
            return _UsersColl(self._users)
        return _Coll(self._bills)


def _user(roles=("STORE_MANAGER",), active="S1"):
    return {
        "user_id": "u1",
        "username": "t",
        "roles": list(roles),
        "active_store_id": active,
        "store_ids": [active],
    }


def _po(store="S1", status="PARTIAL"):
    return {
        "po_id": "PO1",
        "po_number": "PO-1",
        "vendor_id": "V1",
        "vendor_name": "Acme",
        "delivery_store_id": store,
        "status": status,
        "created_at": "2026-06-01T10:00:00",
        "sent_at": "2026-06-02T09:00:00",
        "created_by": "u9",
    }


def _wire(monkeypatch, po, grns=(), bills=(), users=()):
    monkeypatch.setattr(v, "get_purchase_order_repository", lambda: _PORepo(po))
    monkeypatch.setattr(v, "get_grn_repository", lambda: _GRNRepo(list(grns)))
    monkeypatch.setattr(v, "_get_db", lambda: _DB(list(bills), users))


def _call(po_id="PO1", user=None):
    return asyncio.run(v.get_po_timeline(po_id, user or _user()))


def test_full_lifecycle_events_in_order(monkeypatch):
    grns = [
        {
            "po_id": "PO1",
            "grn_id": "G1",
            "grn_number": "RCPT-1",
            "status": "ACCEPTED",
            "created_at": "2026-06-05T11:00:00",
            "accepted_at": "2026-06-05T12:00:00",
            "total_received": 5,
            "total_accepted": 5,
        }
    ]
    bills = [
        {
            "doc_type": "PURCHASE_INVOICE",
            "grn_id": "G1",
            "po_id": "PO1",
            "bill_id": "B1",
            "invoice_number": "INV-9",
            "status": "OUTSTANDING",
            "total": 5250,
            "created_at": "2026-06-06T10:00:00",
        }
    ]
    _wire(monkeypatch, _po(), grns, bills)
    out = _call()
    kinds = [e["kind"] for e in out["events"]]
    # Chronological: ordered < sent < box_received < on_shelf < bill_settled
    assert kinds == ["ordered", "sent", "box_received", "on_shelf", "bill_settled"]
    assert len(out["grns"]) == 1 and len(out["invoices"]) == 1
    assert out["status"] == "PARTIAL"


def test_voided_grn_excluded_from_events_but_listed(monkeypatch):
    grns = [
        {
            "po_id": "PO1",
            "grn_id": "G1",
            "grn_number": "RCPT-1",
            "status": "VOID",
            "created_at": "2026-06-05T11:00:00",
        }
    ]
    _wire(monkeypatch, _po(), grns, [])
    out = _call()
    assert [e["kind"] for e in out["events"]] == ["ordered", "sent"]
    assert out["grns"][0]["status"] == "VOID"  # still shown in the raw list


def test_cross_store_404(monkeypatch):
    _wire(monkeypatch, _po(store="STORE-B"), [], [])
    with pytest.raises(HTTPException) as e:
        _call(user=_user(active="STORE-A"))
    assert e.value.status_code == 404


def test_admin_any_store_ok(monkeypatch):
    _wire(monkeypatch, _po(store="STORE-B"), [], [])
    out = _call(user=_user(roles=("ADMIN",), active="STORE-A"))
    assert out["po_id"] == "PO1"


def test_missing_po_404(monkeypatch):
    _wire(monkeypatch, _po(), [], [])
    with pytest.raises(HTTPException) as e:
        _call(po_id="NOPE")
    assert e.value.status_code == 404


def test_grn_lookup_failure_is_fail_soft(monkeypatch):
    monkeypatch.setattr(v, "get_purchase_order_repository", lambda: _PORepo(_po()))

    class _Boom:
        def find_many(self, *a, **k):
            raise RuntimeError("db down")

    monkeypatch.setattr(v, "get_grn_repository", lambda: _Boom())
    monkeypatch.setattr(v, "_get_db", lambda: None)
    out = _call()
    # PO events still present; no crash.
    assert [e["kind"] for e in out["events"]] == ["ordered", "sent"]
    assert out["grns"] == [] and out["invoices"] == []


def test_timeline_survives_prod_mix_of_datetime_and_string_timestamps(
    monkeypatch,
):
    """P0-2 (launch gate): prod rows MIX types in `at` -- the repo layer's
    _add_timestamps overwrites created_at with a raw datetime on every create,
    while sent_at / accepted_at / vendor_bills created_at stay ISO strings.
    The bare sort raised TypeError (datetime < str) and 500'd this drawer for
    every sent PO -- live on 3 of prod's 7 POs at gate time. The exact prod
    shape (po.created_at datetime, po.sent_at str, grn.created_at datetime,
    grn.accepted_at str) must sort chronologically, not crash."""
    po = _po()
    po["created_at"] = datetime(2026, 6, 1, 10, 0, 0)  # repo layer: datetime
    # po["sent_at"] stays the ISO string "2026-06-02T09:00:00"
    grns = [
        {
            "po_id": "PO1",
            "grn_id": "G1",
            "grn_number": "RCPT-1",
            "status": "ACCEPTED",
            "created_at": datetime(2026, 6, 5, 11, 0, 0),  # datetime
            "accepted_at": "2026-06-05T12:00:00",  # ISO string
            "total_received": 5,
            "total_accepted": 5,
        }
    ]
    bills = [
        {
            "doc_type": "PURCHASE_INVOICE",
            "grn_id": "G1",
            "po_id": "PO1",
            "bill_id": "B1",
            "invoice_number": "INV-9",
            "status": "OUTSTANDING",
            "total": 5250,
            "created_at": "2026-06-06T10:00:00",  # raw insert_one: ISO string
        }
    ]
    _wire(monkeypatch, po, grns, bills)
    out = _call()  # before the fix: TypeError -> 500
    kinds = [e["kind"] for e in out["events"]]
    assert kinds == ["ordered", "sent", "box_received", "on_shelf", "bill_settled"]
    # The event payload keeps its ORIGINAL value -- only the sort key is
    # normalised (readers/serialisers already handle both shapes).
    assert out["events"][0]["at"] == datetime(2026, 6, 1, 10, 0, 0)


# ---------------------------------------------------------------------------
# WHO did it -- the drawer must name the PERSON, never the role and never the
# raw stamped user id. Owner report: the timeline read "SUPERADMIN".
# ---------------------------------------------------------------------------

_USERS = [
    {"user_id": "u9", "username": "dinesh", "full_name": "Dinesh Kumar Gupta"},
    {"user_id": "u7", "username": "shyam", "full_name": "Shyam Sunder"},
    {"user_id": "u5", "username": "sameer"},
]


def _by(out, kind):
    return next(e for e in out["events"] if e["kind"] == kind).get("by")


def test_every_event_names_the_person_who_did_it(monkeypatch):
    """Each event resolves ITS OWN actor: the buyer who ordered, the person who
    sent it, the clerk who logged the box, the manager who accepted it and the
    accountant who booked the bill."""
    po = _po()
    po["sent_by"] = "u7"
    grns = [
        {
            "po_id": "PO1",
            "grn_id": "G1",
            "grn_number": "RCPT-1",
            "status": "ACCEPTED",
            "created_at": "2026-06-05T11:00:00",
            "accepted_at": "2026-06-05T12:00:00",
            "created_by": "u5",
            "accepted_by": "u7",
            "total_received": 5,
            "total_accepted": 5,
        }
    ]
    bills = [
        {
            "doc_type": "PURCHASE_INVOICE",
            "grn_id": "G1",
            "po_id": "PO1",
            "bill_id": "B1",
            "invoice_number": "INV-9",
            "status": "OUTSTANDING",
            "created_by": "u9",
            "created_at": "2026-06-06T10:00:00",
        }
    ]
    _wire(monkeypatch, po, grns, bills, _USERS)
    out = _call()
    assert _by(out, "ordered") == "Dinesh Kumar Gupta"
    assert _by(out, "sent") == "Shyam Sunder"
    assert _by(out, "box_received") == "sameer"  # no full_name -> username
    assert _by(out, "on_shelf") == "Shyam Sunder"
    assert _by(out, "bill_settled") == "Dinesh Kumar Gupta"
    # ...and no raw id leaks into the prose the drawer prints beside it.
    for e in out["events"]:
        assert "u9" not in (e.get("detail") or "")
        assert "actor" not in e


def test_unresolvable_actor_degrades_to_the_stored_id(monkeypatch):
    """A user record that no longer exists must NOT be papered over with a role
    or an invented name -- the stamped id stands, and stays traceable."""
    _wire(monkeypatch, _po(), [], [], users=[])
    out = _call()
    assert _by(out, "ordered") == "u9"


def test_event_with_no_actor_names_nobody(monkeypatch):
    """A legacy row that never stamped anyone reports no person at all rather
    than borrowing a role."""
    po = _po()
    po.pop("created_by")
    _wire(monkeypatch, po, [], [], _USERS)
    out = _call()
    ordered = next(e for e in out["events"] if e["kind"] == "ordered")
    assert "by" not in ordered
    assert "actor" not in ordered


def test_name_lookup_failure_is_fail_soft(monkeypatch):
    """The names read is decoration: if it blows up the timeline still renders
    (with ids), it never 500s."""

    class _BoomDB:
        def get_collection(self, name):
            raise RuntimeError("db down")

    monkeypatch.setattr(v, "get_purchase_order_repository", lambda: _PORepo(_po()))
    monkeypatch.setattr(v, "get_grn_repository", lambda: _GRNRepo([]))
    monkeypatch.setattr(v, "_get_db", lambda: _BoomDB())
    out = _call()
    assert _by(out, "ordered") == "u9"


def test_rbac_row_catalogued():
    from api.services.rbac_policy import POLICY

    rows = [
        r
        for r in POLICY
        if r.get("path") == "/api/v1/vendors/purchase-orders/{po_id}/timeline"
        and r.get("method") == "GET"
    ]
    assert len(rows) == 1
    assert rows[0]["allowed"] == "AUTHENTICATED"
