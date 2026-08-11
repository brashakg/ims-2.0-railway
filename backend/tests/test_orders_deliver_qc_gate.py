"""
IMS 2.0 - Order-side patient handover is QC-gated (PR #971 fix round)
=====================================================================
THE SHIP-BLOCKER the adversarial panel found: PR #971 gated the Workshop screen
but not the Orders screen -- and the Orders screen is the one the counter
actually uses, because payment and the invoice live there.

POST /orders/{id}/deliver checked only store access, the Rx hold, the
READY -> DELIVERED transition and payment != UNPAID. It never read
order["workshop_job_id"] (the reverse pointer the order itself carries) and
never consulted the QC predicate, so a job the workshop gate was actively
holding walked out in one click, the NPS survey fired, and the workshop row
stayed READY forever.

Both endpoints also carried NO role gate at all (bare Depends(get_current_user)),
unlike /cancel -- any authenticated user could deliver an order.

Covers: deliver refuses an un-QC'd linked job, allows a QC-cleared one, resolves
the job by reverse pointer AND by find_by_order fallback, leaves non-workshop
orders alone, fails soft on infrastructure, and is POS_WRITE_ROLES-gated. Same
for /ready.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from api.routers import orders as om  # noqa: E402
from api.routers import workshop as wm  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402

STORE = "BV-TEST-01"


def _order(**kw) -> Dict[str, Any]:
    doc = {
        "order_id": "ORD-1",
        "order_number": "SO-1",
        "store_id": STORE,
        "customer_id": "CUST-1",
        "status": "READY",
        "payment_status": "PAID",
        "items": [{"item_type": "OPTICAL_LENS", "product_id": "L1"}],
    }
    doc.update(kw)
    return doc


def _wjob(**kw) -> Dict[str, Any]:
    doc = {
        "job_id": "JID-1",
        "job_number": "WS-1",
        "order_id": "ORD-1",
        "status": "READY",
        "store_id": STORE,
    }
    doc.update(kw)
    return doc


class _OrderRepo:
    def __init__(self, order):
        self._order = order
        self.status_updates: List[str] = []

    def find_by_id(self, order_id):
        return self._order if self._order.get("order_id") == order_id else None

    def update_status(self, order_id, status, by_user=None):
        self.status_updates.append(status)
        self._order["status"] = status
        return True

    def update(self, *_a, **_k):
        return True


class _WorkshopRepo:
    """Doubles the two lookups assert_linked_job_qc_cleared uses."""

    def __init__(self, jobs: Optional[List[Dict[str, Any]]] = None, boom: bool = False):
        self._jobs = list(jobs or [])
        self._boom = boom

    def find_by_id(self, job_id):
        if self._boom:
            raise RuntimeError("workshop store unreachable")
        return next((j for j in self._jobs if j.get("job_id") == job_id), None)

    def find_by_order(self, order_id):
        if self._boom:
            raise RuntimeError("workshop store unreachable")
        return [j for j in self._jobs if j.get("order_id") == order_id]


def _client(monkeypatch, order, jobs, roles=("SALES_CASHIER",), wrepo=None):
    app = FastAPI()
    app.include_router(om.router, prefix="/api/v1/orders")
    orepo = _OrderRepo(order)
    workshop_repo = wrepo if wrepo is not None else _WorkshopRepo(jobs)

    monkeypatch.setattr(om, "get_order_repository", lambda: orepo)
    # assert_linked_job_qc_cleared resolves the repo through the workshop module.
    monkeypatch.setattr(wm, "get_workshop_repository", lambda: workshop_repo)

    async def _user():
        return {
            "user_id": "u1",
            "username": "counter",
            "roles": list(roles),
            "store_ids": [STORE],
            "active_store_id": STORE,
        }

    app.dependency_overrides[get_current_user] = _user
    return TestClient(app), orepo


# ===========================================================================
# POST /orders/{id}/deliver -- the handover door the counter actually uses
# ===========================================================================


def test_deliver_blocked_when_linked_job_has_no_qc(monkeypatch):
    """THE SHIP-BLOCKER regression: an un-QC'd linked job must stop the handover."""
    client, orepo = _client(
        monkeypatch, _order(workshop_job_id="JID-1"), [_wjob()]  # no qc_passed
    )
    resp = client.post("/api/v1/orders/ORD-1/deliver")
    assert resp.status_code == 400, resp.text
    body = resp.json()["detail"]
    assert "QC" in body
    assert "WS-1" in body  # names the job so the counter can find it
    assert orepo.status_updates == []  # never flipped, so NPS never fired


def test_deliver_allowed_when_linked_job_qc_passed(monkeypatch):
    client, orepo = _client(
        monkeypatch, _order(workshop_job_id="JID-1"), [_wjob(qc_passed=True)]
    )
    resp = client.post("/api/v1/orders/ORD-1/deliver")
    assert resp.status_code == 200, resp.text
    assert orepo.status_updates == ["DELIVERED"]


def test_deliver_allowed_when_linked_job_qc_waived(monkeypatch):
    client, orepo = _client(
        monkeypatch, _order(workshop_job_id="JID-1"), [_wjob(qc_waived=True)]
    )
    assert client.post("/api/v1/orders/ORD-1/deliver").status_code == 200
    assert orepo.status_updates == ["DELIVERED"]


def test_deliver_finds_the_job_without_a_reverse_pointer(monkeypatch):
    """Orders predating the workshop_job_id pointer still resolve via
    find_by_order -- otherwise the gate would silently pass on legacy rows."""
    client, orepo = _client(monkeypatch, _order(), [_wjob()])  # no pointer
    resp = client.post("/api/v1/orders/ORD-1/deliver")
    assert resp.status_code == 400, resp.text
    assert orepo.status_updates == []


def test_deliver_unaffected_for_an_order_with_no_workshop_job(monkeypatch):
    """A frame-only / accessory sale has nothing to check and must not be
    blocked -- the mirror failure would be turning away a paying customer."""
    client, orepo = _client(monkeypatch, _order(), [])
    assert client.post("/api/v1/orders/ORD-1/deliver").status_code == 200
    assert orepo.status_updates == ["DELIVERED"]


def test_deliver_ignores_a_cancelled_job(monkeypatch):
    """A cancelled workshop job is not being handed over."""
    client, orepo = _client(
        monkeypatch, _order(workshop_job_id="JID-1"), [_wjob(status="CANCELLED")]
    )
    assert client.post("/api/v1/orders/ORD-1/deliver").status_code == 200
    assert orepo.status_updates == ["DELIVERED"]


def test_deliver_fails_soft_when_workshop_store_is_down(monkeypatch):
    """Infrastructure fail-soft only: an outage must not strand a paid customer
    at the counter. (A job that IS readable and un-QC'd is still a hard block --
    see the first test.)"""
    client, orepo = _client(
        monkeypatch, _order(workshop_job_id="JID-1"), None, wrepo=_WorkshopRepo(boom=True)
    )
    assert client.post("/api/v1/orders/ORD-1/deliver").status_code == 200
    assert orepo.status_updates == ["DELIVERED"]


def test_deliver_uses_the_same_predicate_as_the_workshop_gate(monkeypatch):
    """One source of truth: orders imports workshop.qc_cleared, so a truthy-junk
    qc_passed fails closed on BOTH doors identically."""
    assert wm.qc_cleared is wm._qc_cleared
    client, orepo = _client(
        monkeypatch, _order(workshop_job_id="JID-1"), [_wjob(qc_passed="yes")]
    )
    assert client.post("/api/v1/orders/ORD-1/deliver").status_code == 400
    assert orepo.status_updates == []


# ===========================================================================
# Role gate -- both endpoints previously had NONE
# ===========================================================================


def test_deliver_is_role_gated(monkeypatch):
    """Previously ANY authenticated user could hand goods to a customer.
    ACCOUNTANT is out of scope for a counter handover."""
    client, orepo = _client(
        monkeypatch,
        _order(workshop_job_id="JID-1"),
        [_wjob(qc_passed=True)],
        roles=("ACCOUNTANT",),
    )
    resp = client.post("/api/v1/orders/ORD-1/deliver")
    assert resp.status_code == 403, resp.text
    assert orepo.status_updates == []


def test_ready_is_role_gated(monkeypatch):
    """ACCOUNTANT is out of scope for a counter handover. (This test previously
    used WORKSHOP_STAFF, which was itself the asymmetry being fixed -- it can
    scan a job to DELIVERED, so it must be able to close the order.)"""
    client, orepo = _client(
        monkeypatch,
        _order(status="CONFIRMED", workshop_job_id="JID-1"),
        [_wjob(qc_passed=True)],
        roles=("ACCOUNTANT",),
    )
    assert client.post("/api/v1/orders/ORD-1/ready").status_code == 403
    assert orepo.status_updates == []


def test_pos_roles_still_permitted(monkeypatch):
    for role in ("SALES_CASHIER", "SALES_STAFF", "STORE_MANAGER", "ADMIN"):
        client, orepo = _client(
            monkeypatch,
            _order(workshop_job_id="JID-1"),
            [_wjob(qc_passed=True)],
            roles=(role,),
        )
        assert client.post("/api/v1/orders/ORD-1/deliver").status_code == 200, role
        assert orepo.status_updates == ["DELIVERED"], role


# ===========================================================================
# POST /orders/{id}/ready -- an order must not be advertised as collectable
# ===========================================================================


def test_ready_blocked_when_linked_job_has_no_qc(monkeypatch):
    client, orepo = _client(
        monkeypatch, _order(status="CONFIRMED", workshop_job_id="JID-1"), [_wjob()]
    )
    resp = client.post("/api/v1/orders/ORD-1/ready")
    assert resp.status_code == 400, resp.text
    assert "QC" in resp.json()["detail"]
    assert orepo.status_updates == []


def test_ready_allowed_when_qc_cleared(monkeypatch):
    client, orepo = _client(
        monkeypatch,
        _order(status="CONFIRMED", workshop_job_id="JID-1"),
        [_wjob(qc_passed=True)],
    )
    assert client.post("/api/v1/orders/ORD-1/ready").status_code == 200
    assert orepo.status_updates == ["READY"]


# ===========================================================================
# MULTI-JOB ORDERS -- the P0 the first 13 tests could not reach
# ===========================================================================
# Every test above puts ONE job on the order, so the resolver's find_by_order
# branch was only ever exercised with the pointer absent. That is how a resolver
# which examined exactly ONE job when the pointer resolved stayed green: it is a
# NO-OP for a duplicate-job order, and one such order exists in prod today.


def test_pointer_at_qcd_job_does_not_hide_an_unqcd_sibling(monkeypatch):
    """THE P0. Order points at the QC'd job; the sibling the bench actually
    ground has no QC. Checking only the pointed-at job hands the patient
    un-QC'd spectacles at HTTP 200."""
    client, orepo = _client(
        monkeypatch,
        _order(workshop_job_id="JID-1"),
        [
            _wjob(job_id="JID-1", job_number="WS-1", qc_passed=True),
            _wjob(job_id="JID-2", job_number="WS-2", status="COMPLETED"),  # no QC
        ],
    )
    resp = client.post("/api/v1/orders/ORD-1/deliver")
    assert resp.status_code == 400, resp.text
    assert "WS-2" in resp.json()["detail"]  # names the offending sibling
    assert orepo.status_updates == []


def test_multi_job_order_delivers_once_every_job_is_cleared(monkeypatch):
    """The mirror: ALL jobs cleared -> the handover proceeds. A gate that
    blocked a fully-QC'd duplicate pair forever would strand the customer."""
    client, orepo = _client(
        monkeypatch,
        _order(workshop_job_id="JID-1"),
        [
            _wjob(job_id="JID-1", job_number="WS-1", qc_passed=True),
            _wjob(job_id="JID-2", job_number="WS-2", qc_waived=True),
        ],
    )
    assert client.post("/api/v1/orders/ORD-1/deliver").status_code == 200
    assert orepo.status_updates == ["DELIVERED"]


def test_unworked_ghost_sibling_does_not_strand_a_finished_job(monkeypatch):
    """The live prod shape: the safety net made a PENDING ghost nobody worked,
    the real job is QC-passed. The ghost has produced nothing that could be
    un-QC'd, and QC refuses PENDING -- blocking here would strand a customer
    whose glasses are finished and on the shelf."""
    client, orepo = _client(
        monkeypatch,
        _order(workshop_job_id="JID-GHOST"),
        [
            _wjob(job_id="JID-GHOST", job_number="WS-GHOST", status="PENDING"),
            _wjob(job_id="JID-REAL", job_number="WS-REAL", qc_passed=True),
        ],
    )
    assert client.post("/api/v1/orders/ORD-1/deliver").status_code == 200
    assert orepo.status_updates == ["DELIVERED"]


def _worked_pending(**kw):
    """A PENDING job that walked the WHOLE bench.

    Reachable through the real scan path: only INTAKE advances the status, and
    PENDING is not in lab_routing._TERMINAL_JOB_STATUSES, so when the INTAKE leg
    is HELD (SALES_CONFIRM_REQUIRED) the job still routes through
    EDGING/COATING/QC_LAB -- which carry advances_job_status=None and hit no gate
    at all -- and on to DISPATCH and PICKUP, while its status stays PENDING.
    """
    doc = _wjob(
        job_id="JID-W",
        job_number="WS-WORKED",
        status="PENDING",
        current_station="PICKUP",
        scan_history=[
            {"station": s}
            for s in ("INTAKE", "EDGING", "COATING", "QC_LAB", "DISPATCH", "PICKUP")
        ],
        station_timestamps={
            s: "2026-08-10T10:00:00"
            for s in ("INTAKE", "EDGING", "COATING", "QC_LAB", "DISPATCH", "PICKUP")
        },
    )
    doc.update(kw)
    return doc


@pytest.mark.parametrize(
    "sibling_label, sibling",
    [
        ("qc_passed", {"qc_passed": True}),
        ("qc_waived", {"qc_waived": True}),
        # B5 is the real prod duplicate shape: one job already handed over.
        ("delivered_and_qcd", {"status": "DELIVERED", "qc_passed": True}),
    ],
)
def test_worked_pending_is_not_a_ghost(monkeypatch, sibling_label, sibling):
    """THE ROUND-3 BLOCKER. The ghost carve-out keyed on the STATUS STRING alone,
    so a PENDING job that had actually been ground at the bench was waved through
    as if it had produced nothing -- as long as some sibling was QC-cleared.

    Patient consequence: a two-job order where one job is QC'd and the other was
    ground but never inspected. The counter hands over BOTH at 200, and the
    un-QC'd lens is the one the patient walks out wearing, while the bench
    scanner is simultaneously refusing that same document with QC_REQUIRED."""
    jobs = [_worked_pending(), _wjob(job_id="JID-1", job_number="WS-1", **sibling)]

    client, orepo = _client(monkeypatch, _order(workshop_job_id="JID-1"), jobs)
    resp = client.post("/api/v1/orders/ORD-1/deliver")
    assert resp.status_code == 400, f"{sibling_label}: {resp.text}"
    assert "WS-WORKED" in resp.json()["detail"]
    assert orepo.status_updates == []


@pytest.mark.parametrize(
    "sibling",
    [{"qc_passed": True}, {"qc_waived": True}, {"status": "DELIVERED", "qc_passed": True}],
)
def test_worked_pending_blocks_ready_and_shipping_too(monkeypatch, sibling):
    """All three handover doors agree on the worked-PENDING shape."""
    jobs = [_worked_pending(), _wjob(job_id="JID-1", job_number="WS-1", **sibling)]

    client, orepo = _client(
        monkeypatch, _order(status="CONFIRMED", workshop_job_id="JID-1"), jobs
    )
    assert client.post("/api/v1/orders/ORD-1/ready").status_code == 400
    assert orepo.status_updates == []

    ship = _shipping_client(monkeypatch, _order(workshop_job_id="JID-1"), jobs)
    assert ship.post("/api/v1/shipping/shipments", json=_SHIPMENT_BODY).status_code == 400


def test_pristine_predicate_is_inverted_and_channel_agnostic():
    """THE POINT OF THE INVERSION. Three rounds enumerated ways a job might have
    been worked and reality had one more each time (bench scans, then the lens
    lifecycle, then the vendor channel). The predicate now proves the POSITIVE --
    this doc is exactly what a create door produced -- so a channel nobody has
    thought of yet is closed by construction, not by being listed."""
    ghost = {
        "job_id": "JID-G", "job_number": "WS-G", "order_id": "ORD-1",
        "store_id": STORE, "status": "PENDING", "auto_created": True,
    }
    assert wm._is_pristine_ghost(ghost) is True

    # Each known channel disqualifies it...
    for key, value in (
        ("scan_history", [{"station": "INTAKE"}]),   # round 3's channel
        ("station_timestamps", {"INTAKE": "x"}),
        ("current_station", "EDGING"),
        ("lens_status", "MOUNTED"),                  # round 4's channel
        ("vendor_status", "RECEIVED"),               # round 4's other channel
        ("qc_passed", True),
    ):
        assert wm._is_pristine_ghost({**ghost, key: value}) is False, key

    # ...and so does a channel that does not exist yet. THIS is the guard the
    # coordinator asked for: any future writer recording progress writes SOME
    # field, and whatever it invents lands here without anyone updating a list.
    assert wm._is_pristine_ghost({**ghost, "teleporter_status": "BEAMED"}) is False
    assert wm._is_pristine_ghost({**ghost, "some_future_channel": 1}) is False

    # A moved updated_at is NOT disqualifying on its own -- see
    # test_administrative_touches_do_not_disqualify_a_ghost for why that check
    # was removed (it fired on every write, including purely administrative ones,
    # and rejected 100% of the live population).
    assert (
        wm._is_pristine_ghost(
            {**ghost, "created_at": "2026-08-10T10:00:00",
             "updated_at": "2026-08-10T11:00:00"}
        )
        is True
    )
    # A status write leaves non-allowlisted traces, so the one allowlisted field
    # that could encode progress is still caught even if status returned to
    # PENDING.
    assert wm._is_pristine_ghost({**ghost, "status_history": [{"status": "READY"}]}) is False
    assert wm._is_pristine_ghost({**ghost, "status_updated_at": "2026-08-10T11:00:00"}) is False

    # Falsy extras record nothing -- an empty-but-present container is still
    # pristine (behaviour the panel confirmed correct).
    assert (
        wm._is_pristine_ghost(
            {**ghost, "current_station": None, "scan_history": [],
             "station_timestamps": {}}
        )
        is True
    )


def test_administrative_touches_do_not_disqualify_a_ghost():
    """A signal that fires on every write is not a signal.

    An earlier version also compared updated_at against created_at. Because
    BaseRepository.update stamps updated_at on EVERY write, that could not tell
    "someone recorded work" from "someone confirmed the fitting", and it
    disqualified 100% of the live population: both live PENDING rows carried NO
    extra keys at all (n_extra_truthy=0) and were rejected purely on a moved
    timestamp 30s / 80s after creation."""
    ghost = {
        "job_id": "JID-G", "job_number": "WS-G", "order_id": "ORD-1",
        "store_id": STORE, "status": "PENDING", "auto_created": True,
        "created_at": "2026-08-10T10:00:00",
    }

    # BOTH live prod PENDING shapes: no extra keys, updated_at moved after an
    # administrative touch. These must be handleable.
    assert wm._is_pristine_ghost({**ghost, "updated_at": "2026-08-10T10:00:31"}) is True
    assert wm._is_pristine_ghost({**ghost, "updated_at": "2026-08-10T10:01:20"}) is True

    # Confirming the fitting writes only an allowlisted key.
    assert (
        wm._is_pristine_ghost(
            {**ghost, "updated_at": "2026-08-10T11:00:00",
             "fitting_details": {"confirmed_by_sales": True}}
        )
        is True
    )
    # Assigning a technician is a work-queue decision -- nothing has been cut.
    assert (
        wm._is_pristine_ghost(
            {**ghost, "updated_at": "2026-08-10T11:00:00",
             "technician_id": "u7", "assigned_at": "2026-08-10T11:00:00"}
        )
        is True
    )
    # Editing notes / expected date stamps updated_by.
    assert (
        wm._is_pristine_ghost(
            {**ghost, "updated_at": "2026-08-10T11:00:00", "updated_by": "u7",
             "special_notes": "call before pickup"}
        )
        is True
    )

    # ...but the two known work channels still disqualify, timestamp or not.
    assert wm._is_pristine_ghost({**ghost, "lens_status": "MOUNTED"}) is False
    assert wm._is_pristine_ghost({**ghost, "vendor_status": "RECEIVED"}) is False
    # ...and so does a channel nobody has written yet. THE GUARD SURVIVES.
    assert wm._is_pristine_ghost({**ghost, "teleporter_status": "BEAMED"}) is False


def test_administrative_ghost_still_delivers_end_to_end(monkeypatch):
    """The carve-out must actually fire for a real, administratively-touched
    ghost -- otherwise it exists on paper only."""
    ghost = _wjob(
        job_id="JID-G", job_number="WS-G", status="PENDING",
        created_at="2026-08-10T10:00:00", updated_at="2026-08-10T10:00:31",
        fitting_details={"confirmed_by_sales": True},
        technician_id="u7",
    )
    sibling = _wjob(job_id="JID-1", job_number="WS-1", qc_passed=True)
    client, orepo = _client(monkeypatch, _order(workshop_job_id="JID-1"), [ghost, sibling])
    resp = client.post("/api/v1/orders/ORD-1/deliver")
    assert resp.status_code == 200, resp.text
    assert orepo.status_updates == ["DELIVERED"]


def test_pristine_key_set_matches_what_creation_actually_writes(monkeypatch):
    """Pins the allowlist against the REAL create door rather than a copy of it.
    Add a field to orders._ensure_workshop_job_for_order and this goes red,
    telling you to update _PRISTINE_GHOST_KEYS -- so the inversion cannot rot
    into a stale list."""
    created: Dict[str, Any] = {}

    class _CapturingRepo:
        def find_by_order(self, _oid):
            return []

        def create(self, data):
            # Mirror BaseRepository.create: it stamps the id field and both
            # timestamps on top of whatever the caller supplied.
            doc = dict(data)
            doc.setdefault("job_id", "JID-NEW")
            doc.setdefault("created_at", "2026-08-10T10:00:00")
            doc.setdefault("updated_at", "2026-08-10T10:00:00")
            created.update(doc)
            return doc

    import api.dependencies as deps

    monkeypatch.setattr(deps, "get_workshop_repository", lambda: _CapturingRepo())
    monkeypatch.setattr(om, "get_order_repository", lambda: _OrderRepo(_order()))

    om._ensure_workshop_job_for_order(
        {
            "order_id": "ORD-1",
            "store_id": STORE,
            "expected_delivery": "2026-09-01T00:00:00",
            "items": [
                {"item_type": "FRAME", "product_id": "F1", "product_name": "RB"},
                {"item_type": "LENS", "product_id": "L1", "prescription_id": "RX-9"},
            ],
        },
        "user-1",
    )

    assert created, "the safety net did not create a job"
    unknown = set(created) - set(wm._PRISTINE_GHOST_KEYS)
    assert not unknown, (
        f"creation writes keys the pristine allowlist does not know: {sorted(unknown)}. "
        "Add them to _PRISTINE_GHOST_KEYS or the ghost carve-out will never fire."
    )
    # And the freshly-created doc really does read as pristine end to end.
    assert wm._is_pristine_ghost(created) is True


@pytest.mark.parametrize(
    "channel_label, worked_fields",
    [
        # Round 3's channel: six bench scans with the INTAKE leg held.
        (
            "bench_scans",
            {
                "current_station": "PICKUP",
                "scan_history": [{"station": "PICKUP"}],
                "station_timestamps": {"PICKUP": "2026-08-10T10:00:00"},
            },
        ),
        # Round 4's channel: the LENS LIFECYCLE. update_lens_status writes only
        # these fields, has no job-status guard, and hard-commits the reserved
        # lens-catalog cell on MOUNTED because the lens is already cut and in the
        # customer's frame. The F9 DC hardlock parks the job at PENDING
        # throughout -- a system gate holding the status while the lens is worked
        # through a channel that carries no gate.
        (
            "lens_lifecycle_mounted",
            {
                "lens_status": "MOUNTED",
                "lens_mounted_at": "2026-08-10T10:00:00",
                "lens_status_updated_by": "u9",
            },
        ),
        # Round 4's other channel: the vendor lifecycle.
        (
            "vendor_status",
            {
                "vendor_status": "RECEIVED",
                "vendor_status_updated_at": "2026-08-10T10:00:00",
                "vendor_status_history": [{"status": "RECEIVED"}],
            },
        ),
        # A channel nobody has written yet.
        ("future_unknown_channel", {"teleporter_status": "BEAMED"}),
    ],
)
def test_worked_through_any_channel_is_not_a_ghost(
    monkeypatch, channel_label, worked_fields
):
    """A PENDING job progressed through ANY channel must block at all three
    doors, even beside a QC-cleared sibling. Same document, same instant: the
    bench gate says QC_REQUIRED, so the counter must not say 200."""
    worked = _wjob(
        job_id="JID-W", job_number="WS-WORKED", status="PENDING", **worked_fields
    )
    sibling = _wjob(job_id="JID-1", job_number="WS-1", qc_passed=True)
    jobs = [worked, sibling]

    # The bench door already refuses this doc.
    assert wm.evaluate_scan_transition_gate(None, worked, "DELIVERED") == "QC_REQUIRED"

    client, orepo = _client(monkeypatch, _order(workshop_job_id="JID-1"), jobs)
    resp = client.post("/api/v1/orders/ORD-1/deliver")
    assert resp.status_code == 400, f"{channel_label}: {resp.text}"
    assert "WS-WORKED" in resp.json()["detail"]
    assert orepo.status_updates == []

    ready_client, ready_orepo = _client(
        monkeypatch, _order(status="CONFIRMED", workshop_job_id="JID-1"), jobs
    )
    assert ready_client.post("/api/v1/orders/ORD-1/ready").status_code == 400, channel_label
    assert ready_orepo.status_updates == []

    ship = _shipping_client(monkeypatch, _order(workshop_job_id="JID-1"), jobs)
    assert (
        ship.post("/api/v1/shipping/shipments", json=_SHIPMENT_BODY).status_code == 400
    ), channel_label


def test_lens_status_channel_end_to_end_through_the_real_endpoints(monkeypatch):
    """The chair's sequence, driven rather than hand-seeded: the DC hardlock holds
    the job at PENDING while the lens is ordered, received and MOUNTED through the
    lens-status endpoint, which never touches job.status."""
    from api.routers.auth import get_current_user as _gcu

    job = _wjob(job_id="JID-A", job_number="WS-A", status="PENDING")

    class _Repo:
        def find_by_id(self, jid):
            return job if job["job_id"] == jid else None

        def find_by_order(self, oid):
            return [job] if job["order_id"] == oid else []

        def update(self, jid, data):
            job.update(data)
            return True

        def update_status(self, *a, **k):
            return True

    app = FastAPI()
    app.include_router(wm.router, prefix="/api/v1/workshop")
    monkeypatch.setattr(wm, "get_workshop_repository", lambda: _Repo())
    monkeypatch.setattr(wm, "get_audit_repository", lambda: None)
    monkeypatch.setattr(wm, "get_db", lambda: None)

    async def _user():
        return {
            "user_id": "u9", "roles": ["STORE_MANAGER"],
            "store_ids": [STORE], "active_store_id": STORE,
        }

    app.dependency_overrides[_gcu] = _user
    wclient = TestClient(app)

    for step in ("ORDERED", "RECEIVED", "MOUNTED"):
        r = wclient.post("/api/v1/workshop/jobs/JID-A/lens-status", json={"status": step})
        assert r.status_code == 200, f"{step}: {r.text}"

    # The lens is cut and in the frame; the job status never moved.
    assert job["status"] == "PENDING"
    assert job["lens_status"] == "MOUNTED"
    assert not job.get("scan_history")  # the round-3 predicate would see nothing
    assert wm._is_pristine_ghost(job) is False  # ...but this one does

    sibling = _wjob(job_id="JID-1", job_number="WS-1", qc_passed=True)
    client, orepo = _client(
        monkeypatch, _order(workshop_job_id="JID-1"), [job, sibling]
    )
    resp = client.post("/api/v1/orders/ORD-1/deliver")
    assert resp.status_code == 400, resp.text
    assert orepo.status_updates == []


def test_cross_order_pointer_is_ignored(monkeypatch):
    """find_by_id is keyed on job_id alone, so a stale/cross-order pointer used
    to make the gate judge a DIFFERENT order's QC record -- failing open here."""
    client, orepo = _client(
        monkeypatch,
        _order(workshop_job_id="JID-OTHER"),
        [
            # Belongs to a different order but is QC'd -- must NOT satisfy us.
            _wjob(job_id="JID-OTHER", job_number="WS-OTHER", order_id="ORD-9", qc_passed=True),
            # This order's own job has no QC.
            _wjob(job_id="JID-1", job_number="WS-1", status="COMPLETED"),
        ],
    )
    resp = client.post("/api/v1/orders/ORD-1/deliver")
    assert resp.status_code == 400, resp.text
    assert "WS-1" in resp.json()["detail"]
    assert orepo.status_updates == []


def test_foreign_pointer_does_not_falsely_block_a_cleared_order(monkeypatch):
    """PINS THE OWNERSHIP CHECK, which was load-bearing but unpinned: replacing
    it with a bare isinstance() left the whole suite green.

    Here every job that BELONGS to this order is QC-cleared and only the
    foreign job (another order's) is un-QC'd. Without the ownership check the
    foreign job is pulled in and falsely blocks a correctly-QC'd handover.
    test_cross_order_pointer_is_ignored cannot catch this -- it makes the
    foreign job the QC'd one, so the sweep alone produces its 400."""
    client, orepo = _client(
        monkeypatch,
        _order(workshop_job_id="JID-FOREIGN"),
        [
            # Another order's job, NOT QC'd -- must be ignored entirely.
            _wjob(
                job_id="JID-FOREIGN",
                job_number="WS-FOREIGN",
                order_id="ORD-9",
                status="COMPLETED",
            ),
            # This order's own job IS cleared.
            _wjob(job_id="JID-1", job_number="WS-1", qc_passed=True),
        ],
    )
    resp = client.post("/api/v1/orders/ORD-1/deliver")
    assert resp.status_code == 200, resp.text
    assert orepo.status_updates == ["DELIVERED"]


def test_stale_pointer_to_a_deleted_job_still_checks_the_real_one(monkeypatch):
    """A dangling pointer must not make the gate silently pass."""
    client, orepo = _client(
        monkeypatch,
        _order(workshop_job_id="JID-GONE"),
        [_wjob(job_id="JID-1", job_number="WS-1", status="COMPLETED")],
    )
    assert client.post("/api/v1/orders/ORD-1/deliver").status_code == 400
    assert orepo.status_updates == []


# ===========================================================================
# SKIP RULES -- the gate must block only states QC can actually fix
# ===========================================================================


def test_legacy_delivered_job_does_not_strand_its_order(monkeypatch):
    """A job already handed over cannot be the handover this call is gating.
    QC refuses DELIVERED and VALID_JOB_TRANSITIONS leaves it terminal, so
    blocking would strand the order forever for EVERY role. Prod has one."""
    client, orepo = _client(
        monkeypatch, _order(workshop_job_id="JID-1"), [_wjob(status="DELIVERED")]
    )
    assert client.post("/api/v1/orders/ORD-1/deliver").status_code == 200
    assert orepo.status_updates == ["DELIVERED"]


def test_lone_pending_job_blocks_the_handover(monkeypatch):
    """THE LIVE PROD SHAPE, and the regression for the round that skipped it.

    Both un-QC'd jobs in production are PENDING on in-flight orders, so skipping
    PENDING re-opened this PR's own hole for 100% of the spectacle work: the
    counter door returned 200 on jobs the bench door was holding. PENDING is NOT
    "no work done" -- only INTAKE advances the status, so a job whose INTAKE leg
    was held walks the whole bench while its status stays PENDING."""
    client, orepo = _client(
        monkeypatch, _order(workshop_job_id="JID-1"), [_wjob(status="PENDING")]
    )
    resp = client.post("/api/v1/orders/ORD-1/deliver")
    assert resp.status_code == 400, resp.text
    assert orepo.status_updates == []


@pytest.mark.parametrize("confirmed", [True, False])
def test_pending_block_message_names_a_performable_remedy(monkeypatch, confirmed):
    """A PENDING job whose fitting was never sales-confirmed cannot be started,
    completed or QC'd -- /start, PATCH IN_PROGRESS, /complete, PATCH READY and
    /qc ALL 400 -- so telling the counter to "Start the job" named an instruction
    every door refuses. The two sentences must differ, and the one for the
    unconfirmed case must name the step that actually works."""
    fitting = {"confirmed_by_sales": True} if confirmed else None
    job = _wjob(status="PENDING", fitting_details=fitting)
    client, orepo = _client(monkeypatch, _order(workshop_job_id="JID-1"), [job])
    detail = client.post("/api/v1/orders/ORD-1/deliver").json()["detail"]
    assert orepo.status_updates == []

    if confirmed:
        assert "Start the job" in detail
        assert "fitting" not in detail.lower()
    else:
        assert "fitting details" in detail
        assert "Start the job" not in detail


def test_unconfirmed_pending_remedy_is_actually_performable(monkeypatch):
    """Drive the remedy the message names: confirm the fitting, then start the
    job. Both must succeed, or the block is a deadlock again."""
    from api.routers.auth import get_current_user as _gcu

    job = _wjob(status="PENDING", fitting_details=None)

    class _Repo:
        def find_by_id(self, jid):
            return job if job["job_id"] == jid else None

        def update(self, jid, data):
            job.update(data)
            return True

        def update_status(self, jid, status, *a, **k):
            job["status"] = status
            return True

    app = FastAPI()
    app.include_router(wm.router, prefix="/api/v1/workshop")
    monkeypatch.setattr(wm, "get_workshop_repository", lambda: _Repo())
    monkeypatch.setattr(wm, "get_audit_repository", lambda: None)
    monkeypatch.setattr(wm, "get_db", lambda: None)

    async def _user():
        return {
            "user_id": "u1", "roles": ["STORE_MANAGER"],
            "store_ids": [STORE], "active_store_id": STORE,
        }

    app.dependency_overrides[_gcu] = _user
    client = TestClient(app)

    # Before: the job cannot be started -- this is the deadlock.
    assert client.post("/api/v1/workshop/jobs/JID-1/start").status_code == 400

    # The remedy the message names.
    patched = client.patch(
        "/api/v1/workshop/jobs/JID-1/fitting-details",
        json={"fitting_details": {"confirmed_by_sales": True, "dia": "65"}},
    )
    assert patched.status_code == 200, patched.text

    # After: it works.
    started = client.post("/api/v1/workshop/jobs/JID-1/start")
    assert started.status_code == 200, started.text
    assert job["status"] == "IN_PROGRESS"


def test_lone_pending_job_names_the_real_remedy(monkeypatch):
    """The round-1 defect was that the 400 named a remedy the API refuses. The
    fix is an honest MESSAGE, not a skip: QC rejects PENDING, so the sentence
    must say 'start the job', never 'run QC'."""
    # Sales HAVE confirmed the fitting, so "start the job" is the true next step
    # (the unconfirmed case gets a different sentence -- see
    # test_pending_block_message_names_a_performable_remedy).
    client, _orepo = _client(
        monkeypatch,
        _order(workshop_job_id="JID-1"),
        [_wjob(status="PENDING", fitting_details={"confirmed_by_sales": True})],
    )
    detail = client.post("/api/v1/orders/ORD-1/deliver").json()["detail"]
    assert "not been started" in detail
    assert "Start the job" in detail


def test_lone_pending_blocks_ready_and_shipping_too(monkeypatch):
    """All three handover doors agree on the live shape."""
    client, orepo = _client(
        monkeypatch,
        _order(status="CONFIRMED", workshop_job_id="JID-1"),
        [_wjob(status="PENDING")],
    )
    assert client.post("/api/v1/orders/ORD-1/ready").status_code == 400
    assert orepo.status_updates == []

    ship = _shipping_client(
        monkeypatch, _order(workshop_job_id="JID-1"), [_wjob(status="PENDING")]
    )
    assert ship.post("/api/v1/shipping/shipments", json=_SHIPMENT_BODY).status_code == 400


def test_pending_job_that_walked_to_pickup_blocks(monkeypatch):
    """The status never moved but the job is physically at the pickup counter --
    exactly the case the 'no lab work has begun' justification got wrong."""
    client, orepo = _client(
        monkeypatch,
        _order(workshop_job_id="JID-1"),
        [_wjob(status="PENDING", current_station="PICKUP")],
    )
    assert client.post("/api/v1/orders/ORD-1/deliver").status_code == 400
    assert orepo.status_updates == []


def test_gate_blocks_only_states_with_a_real_remedy(monkeypatch):
    """THE INVARIANT, enforced rather than described: every status the gate
    blocks on must have a remedy that EXISTS -- either QC accepts it, or it has
    its own named non-QC remedy. What must never happen is a block pointing at an
    API that refuses. (Blocking with the wrong message is fixed by fixing the
    message; removing the block is what re-opened the hole.)"""
    # NON-DERIVED. The skip set is asserted against a LITERAL, not read into the
    # expectation -- this test used to compute `blocked` FROM
    # _HANDOVER_GATE_SKIP_STATUSES, so any status ADDED to that set silently
    # stopped being tested while every assertion still held. That is exactly how
    # a one-word edit adding PENDING to the skip set reached a chair with a green
    # suite. Widening the skip set must now be a RED test, not a silent policy
    # change.
    assert set(wm._HANDOVER_GATE_SKIP_STATUSES) == {"CANCELLED", "DELIVERED"}
    assert set(wm._HANDOVER_NON_QC_REMEDY_STATUSES) == {"PENDING"}

    blocked = set(wm.VALID_JOB_TRANSITIONS) - {"CANCELLED", "DELIVERED"}
    assert blocked
    assert (blocked - set(wm._HANDOVER_NON_QC_REMEDY_STATUSES)) <= set(
        wm._QC_INPUT_STATUSES
    )
    # PENDING is blocked, and its remedy is deliberately NOT QC.
    assert "PENDING" in blocked
    assert "PENDING" not in set(wm._QC_INPUT_STATUSES)
    # Every blocked status really does 400, and never with a QC instruction it
    # cannot act on.
    for status in sorted(blocked):
        client, orepo = _client(
            monkeypatch, _order(workshop_job_id="JID-1"), [_wjob(status=status)]
        )
        resp = client.post("/api/v1/orders/ORD-1/deliver")
        assert resp.status_code == 400, status
        assert orepo.status_updates == [], status
        if status in wm._HANDOVER_NON_QC_REMEDY_STATUSES:
            assert "run QC" not in resp.json()["detail"], status


def test_both_doors_agree_on_one_shared_document(monkeypatch):
    """THE TEST THAT WOULD HAVE CAUGHT THE LAST ROUND. The bench scan gate and
    the counter gate must reach the SAME verdict on the SAME job doc -- the
    regression shipped a PR whose two gates contradicted each other."""
    for status in ("PENDING", "IN_PROGRESS", "COMPLETED", "QC_FAILED", "READY"):
        job = _wjob(status=status)  # un-QC'd

        bench_blocks = (
            wm.evaluate_scan_transition_gate(None, job, "DELIVERED") is not None
        )

        client, orepo = _client(monkeypatch, _order(workshop_job_id="JID-1"), [job])
        counter_blocks = (
            client.post("/api/v1/orders/ORD-1/deliver").status_code == 400
        )

        assert bench_blocks == counter_blocks is True, (
            f"doors disagree on {status}: bench={bench_blocks} counter={counter_blocks}"
        )
        assert orepo.status_updates == [], status

    # ...and both must AGREE to let a QC-cleared job through.
    cleared = _wjob(status="READY", qc_passed=True)
    assert wm.evaluate_scan_transition_gate(None, cleared, "DELIVERED") is None
    client, orepo = _client(monkeypatch, _order(workshop_job_id="JID-1"), [cleared])
    assert client.post("/api/v1/orders/ORD-1/deliver").status_code == 200
    assert orepo.status_updates == ["DELIVERED"]


def test_ready_gate_runs_after_the_legality_check(monkeypatch):
    """An order in the wrong status must be told THAT, not handed a QC
    instruction it does not yet need."""
    client, _orepo = _client(
        monkeypatch, _order(status="DRAFT", workshop_job_id="JID-1"), [_wjob()]
    )
    resp = client.post("/api/v1/orders/ORD-1/deliver")
    assert resp.status_code == 400
    assert "Must be READY" in resp.json()["detail"]


# ===========================================================================
# The front-desk CASHIER must still be able to close a handover
# ===========================================================================


def test_cashier_can_close_a_handover(monkeypatch):
    """labels.SCAN_ROLES says verbatim that CASHIER is the front-desk role that
    scans a job to DELIVERED at pickup. Gating handover on POS_WRITE_ROLES (whose
    exclusion is scoped to order CREATION) left a cashier able to hand the
    glasses over but unable to close the order."""
    client, orepo = _client(
        monkeypatch,
        _order(workshop_job_id="JID-1"),
        [_wjob(qc_passed=True)],
        roles=("CASHIER",),
    )
    assert client.post("/api/v1/orders/ORD-1/deliver").status_code == 200
    assert orepo.status_updates == ["DELIVERED"]


def test_cashier_is_still_stopped_by_the_qc_gate(monkeypatch):
    """The QC gate, not the role list, is the patient-safety control."""
    client, orepo = _client(
        monkeypatch, _order(workshop_job_id="JID-1"), [_wjob()], roles=("CASHIER",)
    )
    assert client.post("/api/v1/orders/ORD-1/deliver").status_code == 400
    assert orepo.status_updates == []


def test_workshop_staff_can_close_a_handover(monkeypatch):
    """Same rule as CASHIER, applied consistently: WORKSHOP_STAFF is in
    labels.SCAN_ROLES and workshop._LAB_SCAN_ROLES, so it may scan a job to
    DELIVERED at pickup. Leaving it out of HANDOVER_ROLES left it able to hand
    the glasses over but unable to close the order -- and the Orders screen
    renders Mark Delivered with no role condition, so the button 403'd in front
    of the customer."""
    client, orepo = _client(
        monkeypatch,
        _order(workshop_job_id="JID-1"),
        [_wjob(qc_passed=True)],
        roles=("WORKSHOP_STAFF",),
    )
    assert client.post("/api/v1/orders/ORD-1/deliver").status_code == 200
    assert orepo.status_updates == ["DELIVERED"]


def test_every_scan_role_can_close_a_handover(monkeypatch):
    """THE RULE, pinned mechanically rather than role-by-role: whoever may scan
    a job to DELIVERED must be able to close the order. Derived from the scan
    tuples so adding a role there can never silently reintroduce the asymmetry."""
    from api.routers import labels as labels_mod

    scan_roles = set(labels_mod.SCAN_ROLES) | set(wm._LAB_SCAN_ROLES)
    handover = set(om.HANDOVER_ROLES) | {"SUPERADMIN"}
    assert scan_roles <= handover, f"can scan but cannot close: {scan_roles - handover}"


def test_out_of_scope_roles_are_still_refused(monkeypatch):
    """OPTOMETRIST is deliberately included: it is in NEITHER scan tuple, so by
    the rule above it does not close handovers. The decision is pinned on the
    FRONTEND side too -- frontend/src/pages/orders/handoverRoles.ts hides the
    Mark Delivered button for exactly this set, because it previously rendered
    enabled for OPTOMETRIST and 403'd in front of the customer."""
    for role in ("ACCOUNTANT", "CATALOG_MANAGER", "INVESTOR", "OPTOMETRIST"):
        client, orepo = _client(
            monkeypatch,
            _order(workshop_job_id="JID-1"),
            [_wjob(qc_passed=True)],
            roles=(role,),
        )
        assert client.post("/api/v1/orders/ORD-1/deliver").status_code == 403, role
        assert orepo.status_updates == [], role


# ===========================================================================
# THE THIRD DOOR -- booking a courier shipment is also a handover
# ===========================================================================
# shipping.book_shipment calls assert_no_active_rx_hold under the comment
# "Mirrors the orders.py deliver/ready guard" but was never given the QC check,
# so a job the Orders screen refuses could simply be SHIPPED instead -- to a
# strictly wider role set (_FULFILMENT_ROLES includes CASHIER), and an ONLINE
# order also gets a "Fulfilled" push back to Shopify.


def _shipping_client(monkeypatch, order, jobs, roles=("STORE_MANAGER",)):
    from api.routers import shipping as sm

    app = FastAPI()
    app.include_router(sm.router, prefix="/api/v1/shipping")
    monkeypatch.setattr(sm, "get_order_repository", lambda: _OrderRepo(order))
    monkeypatch.setattr(wm, "get_workshop_repository", lambda: _WorkshopRepo(jobs))

    async def _user():
        return {
            "user_id": "u1",
            "roles": list(roles),
            "store_ids": [STORE],
            "active_store_id": STORE,
        }

    app.dependency_overrides[get_current_user] = _user
    return TestClient(app)


_SHIPMENT_BODY = {
    "order_id": "ORD-1",
    "address": {
        "name": "Asha Verma",
        "phone": "9876500000",
        "address_line1": "12 MG Road",
        "city": "Bokaro",
        "state": "Jharkhand",
        "pincode": "827004",
    },
}


def test_booking_a_shipment_is_blocked_without_qc(monkeypatch):
    client = _shipping_client(monkeypatch, _order(workshop_job_id="JID-1"), [_wjob()])
    resp = client.post("/api/v1/shipping/shipments", json=_SHIPMENT_BODY)
    assert resp.status_code == 400, resp.text
    assert "QC" in resp.json()["detail"]


def test_booking_a_shipment_allowed_once_qc_is_cleared(monkeypatch):
    client = _shipping_client(
        monkeypatch, _order(workshop_job_id="JID-1"), [_wjob(qc_passed=True)]
    )
    resp = client.post("/api/v1/shipping/shipments", json=_SHIPMENT_BODY)
    assert resp.status_code == 201, resp.text


def test_shipment_gate_catches_the_unqcd_sibling_too(monkeypatch):
    client = _shipping_client(
        monkeypatch,
        _order(workshop_job_id="JID-1"),
        [
            _wjob(job_id="JID-1", job_number="WS-1", qc_passed=True),
            _wjob(job_id="JID-2", job_number="WS-2", status="COMPLETED"),
        ],
    )
    resp = client.post("/api/v1/shipping/shipments", json=_SHIPMENT_BODY)
    assert resp.status_code == 400, resp.text
    assert "WS-2" in resp.json()["detail"]
