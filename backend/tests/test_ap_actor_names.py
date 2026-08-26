"""The AP screens must name the person, not print their user id.

Owner complaint, fourth screen: the 3-way-match override banner read
"Override approved by user-superadmin", and the Recon Console's tick tooltips
and last-updated line printed the same raw ids. Every writer here stamps
``current_user["user_id"]``; the display name was in the users collection all
along and nobody looked it up.

These drive the real endpoint functions (no stubbed subject) over a fake Mongo,
and cover the three ways a bill reaches a screen -- the list, the single read
and the approve-exception echo -- plus both recon endpoints.
"""

from __future__ import annotations

import asyncio
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import api.routers.purchase_invoices as pi  # noqa: E402
import api.routers.purchase_recon as pr  # noqa: E402
from api.services import purchase_match as pmatch  # noqa: E402
from test_purchase_recon import _FakeCollection, _FakeDB  # noqa: E402


class _UsersColl:
    """users stand-in that understands the resolver's $or query, and counts how
    many times it was read (an N+1 must fail, not merely be slow)."""

    def __init__(self, users):
        self.users = list(users)
        self.reads = 0

    def find(self, flt, proj=None):
        self.reads += 1
        wanted = set()
        for term in flt.get("$or", []):
            for key in ("user_id", "id"):
                if key in term:
                    wanted.update(term[key].get("$in", []))
        return [u for u in self.users if u.get("user_id") in wanted]


# Deliberately mirrors production: the SUPERADMIN account's only name is the
# username "admin", and a QA account resolves to nobody at all.
_USERS = [
    {"user_id": "user-superadmin", "username": "admin"},
    {"user_id": "acc-1", "full_name": "Priya Nair"},
]


def _bill(bill_id="B1", override_by="user-superadmin", recon_by="acc-1"):
    doc = {
        "bill_id": bill_id,
        "doc_type": "PURCHASE_INVOICE",
        "vendor_id": "V1",
        "invoice_date": "2026-08-01",
        "match_status": pmatch.MATCH_OVERRIDE,
    }
    if override_by:
        doc["exception_override"] = {
            "approved_by": override_by,
            "reason": "vendor agreed the short-ship",
            "approved_at": "2026-08-01T10:00:00",
            "prior_status": pmatch.MATCH_ON_HOLD,
        }
    if recon_by:
        doc["recon"] = {
            "reconciled": True,
            "reconciled_by": recon_by,
            "reconciled_at": "2026-08-02T09:00:00",
            "last_updated_by": recon_by,
            "last_updated_at": "2026-08-02T09:00:00",
        }
    return doc


def _wire(monkeypatch, bills, users=_USERS):
    users_coll = _UsersColl(users)
    bills_coll = _FakeCollection(bills)
    db = _FakeDB({"vendor_bills": bills_coll, "users": users_coll})
    monkeypatch.setattr(pi, "_get_db", lambda: db)
    monkeypatch.setattr(pr, "_get_db", lambda: db)
    return db, bills_coll, users_coll


def _ap_user(uid="user-superadmin"):
    return {"user_id": uid, "roles": ["ADMIN"], "active_store_id": "BV-01"}


def _list():
    return asyncio.run(pi.list_purchase_invoices(None, None, None, _ap_user()))


# ---------------------------------------------------------------------------
# The override banner (PurchaseInvoicesTab + ReconConsole read the same field)
# ---------------------------------------------------------------------------


def test_invoice_list_names_the_person_who_approved_the_override(monkeypatch):
    """The list row feeds BOTH the override banner and the Recon Console chip,
    so it must carry the approver's name, not just the id."""
    _wire(monkeypatch, [_bill()])
    row = _list()["purchase_invoices"][0]
    assert row["exception_override"]["approved_by_name"] == "admin"
    assert row["exception_override"]["approved_by"] == "user-superadmin"


def test_single_invoice_read_names_the_person_who_approved_the_override(monkeypatch):
    _wire(monkeypatch, [_bill()])
    doc = asyncio.run(pi.get_purchase_invoice("B1", _ap_user()))
    assert doc["exception_override"]["approved_by_name"] == "admin"


def test_approve_exception_echo_names_the_approver(monkeypatch):
    """The banner appears the instant the override is granted -- from the POST
    response, before any reload. That echo must be named too."""
    bill = _bill(override_by=None, recon_by=None)
    bill["match_status"] = pmatch.MATCH_ON_HOLD
    _wire(monkeypatch, [bill])
    monkeypatch.setattr(pi, "get_audit_repository", lambda: None)
    out = asyncio.run(
        pi.approve_invoice_exception(
            "B1", pi.ExceptionOverride(reason="short-ship agreed"), _ap_user()
        )
    )
    assert out["exception_override"]["approved_by_name"] == "admin"


def test_stored_override_keeps_the_raw_id(monkeypatch):
    """The name is presentation, resolved fresh on every read. Persisting it
    would freeze a stale name into the audit trail -- so the stored document
    must still hold the id and nothing else."""
    bill = _bill(override_by=None, recon_by=None)
    bill["match_status"] = pmatch.MATCH_ON_HOLD
    _, bills_coll, _ = _wire(monkeypatch, [bill])
    monkeypatch.setattr(pi, "get_audit_repository", lambda: None)
    asyncio.run(
        pi.approve_invoice_exception(
            "B1", pi.ExceptionOverride(reason="short-ship agreed"), _ap_user()
        )
    )
    stored = bills_coll._docs[0]["exception_override"]
    assert stored["approved_by"] == "user-superadmin"
    assert "approved_by_name" not in stored


# ---------------------------------------------------------------------------
# The Recon Console's own stamps
# ---------------------------------------------------------------------------


def test_invoice_list_names_the_accountant_on_the_recon_block(monkeypatch):
    """The Recon Console seeds its tick tooltips and its "Last updated by" line
    from the recon block embedded in the list row."""
    _wire(monkeypatch, [_bill()])
    recon = _list()["purchase_invoices"][0]["recon"]
    assert recon["reconciled_by_name"] == "Priya Nair"
    assert recon["last_updated_by_name"] == "Priya Nair"


def test_recon_read_names_the_accountant(monkeypatch):
    """Rows with no embedded block fall back to GET /{id}/recon -- same screen,
    same tooltip, so it must be named there too."""
    _wire(monkeypatch, [_bill()])
    recon = asyncio.run(pr.get_recon("B1", _ap_user()))["recon"]
    assert recon["reconciled_by_name"] == "Priya Nair"


def test_recon_write_echo_names_the_accountant_but_stores_the_id(monkeypatch):
    """Ticking a box refreshes the tooltip from the POST echo; the stored block
    keeps the raw id."""
    _, bills_coll, _ = _wire(monkeypatch, [_bill(recon_by=None)])
    out = asyncio.run(
        pr.upsert_recon("B1", pr.ReconUpdate(reconciled=True), _ap_user("acc-1"))
    )
    assert out["recon"]["reconciled_by_name"] == "Priya Nair"
    stored = bills_coll._docs[0]["recon"]
    assert stored["reconciled_by"] == "acc-1"
    assert not [k for k in stored if k.endswith("_by_name")]


def test_every_recon_actor_field_is_covered(monkeypatch):
    """The resolved field list must not drift from the writer: whatever
    _build_recon_block stamps, the reader names."""
    written = pr._build_recon_block(
        {},
        pr.ReconUpdate(
            reconciled=True,
            entered_tally=True,
            filed_gst=True,
            payment_settled=True,
            note="checked against the vendor statement",
        ),
        "acc-1",
        "2026-08-02T09:00:00",
    )
    assert {k for k in written if k.endswith("_by")} == set(pr.RECON_ACTOR_FIELDS)
    _wire(
        monkeypatch,
        [{"bill_id": "B1", "doc_type": "PURCHASE_INVOICE", "recon": written}],
    )
    recon = asyncio.run(pr.get_recon("B1", _ap_user()))["recon"]
    for field in pr.RECON_ACTOR_FIELDS:
        assert recon[field + "_name"] == "Priya Nair", field


# ---------------------------------------------------------------------------
# Degrading honestly
# ---------------------------------------------------------------------------


def test_unresolvable_id_is_left_alone_not_invented(monkeypatch):
    """A QA / deleted account that names nobody must print as the id it is --
    never a role, never a guess. No _name key at all, so the screen falls back
    to the id."""
    _wire(
        monkeypatch,
        [_bill(override_by="qa-efe824be3bc8", recon_by="qa-efe824be3bc8")],
    )
    row = _list()["purchase_invoices"][0]
    assert row["exception_override"]["approved_by"] == "qa-efe824be3bc8"
    assert "approved_by_name" not in row["exception_override"]
    assert "reconciled_by_name" not in row["recon"]


def test_name_lookup_failure_never_breaks_the_screen(monkeypatch):
    """Names are decoration on an AP screen that must still open when the users
    read fails."""

    class _Boom(_FakeDB):
        def get_collection(self, name):
            if name == "users":
                raise RuntimeError("users read failed")
            return super().get_collection(name)

    db = _Boom({"vendor_bills": _FakeCollection([_bill()])})
    monkeypatch.setattr(pi, "_get_db", lambda: db)
    row = _list()["purchase_invoices"][0]
    assert row["exception_override"]["approved_by"] == "user-superadmin"


def test_names_are_batched_not_one_read_per_row(monkeypatch):
    """Twenty-five invoices must not become twenty-five users reads."""
    bills = [_bill(bill_id="B%d" % i) for i in range(25)]
    _, _, users = _wire(monkeypatch, bills)
    out = _list()
    assert len(out["purchase_invoices"]) == 25
    assert all(
        r["exception_override"]["approved_by_name"] == "admin"
        for r in out["purchase_invoices"]
    )
    # One read per id-set (override actors, recon actors) -- never per row.
    assert users.reads <= 2, users.reads
