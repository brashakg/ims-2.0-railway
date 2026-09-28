"""Tripwires for the Wave 6 workshop package split (api/routers/workshop/).

Three things can break silently when a sub-module is added, moved or renamed:

1. A ROUTE GOES MISSING OR MOVES. Every sub-module registers on the ONE
   APIRouter built in _shared.py, in the order __init__.py imports them. A
   fresh APIRouter in a sub-module (or a forgotten import) never mounts and
   nothing raises -- the endpoint 404s in production. A reordered import
   changes the route table (and /jobs/by-vendor/{vendor_id} must stay above
   /jobs/{job_id}, first-registered-wins).
2. A MONKEYPATCH STOPS BITING. The workshop tests patch repositories, get_db
   and datetime by the package path ``api.routers.workshop.<name>``.
   __init__.py forwards those writes into every sub-module that binds the
   name. Drop the forwarding and the patched tests still pass their setattr
   -- then hit the REAL repository instead of their fake: a false green on
   the QC, handover-money and IDOR code.
3. THE GATE API OTHER DOORS IMPORT GOES MISSING. Orders, Labels, Shipping and
   services.lab_routing import the scan / QC / money gates by the package
   path, mostly inside functions -- a lost re-export only fails at call time.
"""

import logging
import os
import sys

import pytest
from fastapi import APIRouter

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import workshop  # noqa: E402
from api.routers.workshop import (  # noqa: E402
    _shared,
    board,
    gates,
    jobs,
    lab_stations,
    lens,
    qc,
    spoilage,
    status,
    vendor,
)

# The flat workshop.py's route table, in registration order (stacked
# decorators apply bottom-up, so "/" registers before "").
_ROUTE_TABLE = [
    ("GET", "/"),
    ("GET", ""),
    ("GET", "/pending"),
    ("GET", "/overdue"),
    ("GET", "/ready"),
    ("GET", "/technician-workload"),
    ("GET", "/dashboard-kpis"),
    ("GET", "/jobs/by-vendor/{vendor_id}"),
    ("GET", "/jobs"),
    ("POST", "/jobs"),
    ("PATCH", "/jobs/{job_id}/fitting-details"),
    ("GET", "/jobs/{job_id}"),
    ("PUT", "/jobs/{job_id}"),
    ("PATCH", "/jobs/{job_id}/status"),
    ("POST", "/jobs/{job_id}/assign"),
    ("POST", "/jobs/{job_id}/start"),
    ("POST", "/jobs/{job_id}/complete"),
    ("POST", "/jobs/{job_id}/qc"),
    ("POST", "/jobs/{job_id}/qc-checklist"),
    ("POST", "/jobs/{job_id}/rework"),
    ("GET", "/remake-reason-codes"),
    ("PUT", "/remake-reason-codes"),
    ("GET", "/spoilage-analytics"),
    ("POST", "/jobs/{job_id}/lens-status"),
    ("POST", "/jobs/{job_id}/notify-ready"),
    ("GET", "/stations"),
    ("POST", "/stations"),
    ("GET", "/stations/{code}/queue"),
    ("POST", "/scan"),
    ("POST", "/jobs/{job_id}/print-job-card"),
    ("PATCH", "/jobs/{job_id}/vendor"),
    ("POST", "/jobs/{job_id}/vendor-status"),
]


def test_route_table_is_the_flat_files_in_the_same_order():
    table = [(m, r.path) for r in workshop.router.routes for m in sorted(r.methods)]
    assert table == _ROUTE_TABLE


def test_every_submodule_registers_on_the_shared_router():
    for mod in workshop._SUBMODULES:
        for name, value in vars(mod).items():
            if isinstance(value, APIRouter):
                assert (
                    value is workshop.router
                ), "%s.%s is a private APIRouter -- its routes never mount" % (
                    mod.__name__,
                    name,
                )


def test_patching_a_repository_reaches_every_submodule_that_reads_it():
    original = workshop.get_workshop_repository
    sentinel = object()
    readers = (gates, board, jobs, status, qc, spoilage, lens, lab_stations, vendor)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(workshop, "get_workshop_repository", lambda: sentinel)
        for mod in readers:
            assert mod.get_workshop_repository() is sentinel, mod.__name__

    # monkeypatch's undo goes through the same forwarding.
    assert workshop.get_workshop_repository is original
    for mod in readers:
        assert mod.get_workshop_repository is original, mod.__name__


def test_patching_db_datetime_and_helpers_reaches_their_readers():
    originals = {
        n: getattr(workshop, n)
        for n in ("get_db", "get_order_repository", "datetime", "_check_dc_hardlock",
                  "_perform_ready_notify")
    }

    class FakeDT:
        pass

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(workshop, "get_db", lambda: "db")
        mp.setattr(workshop, "get_order_repository", lambda: "orders")
        mp.setattr(workshop, "datetime", FakeDT)
        mp.setattr(workshop, "_check_dc_hardlock", "hardlock")
        mp.setattr(workshop, "_perform_ready_notify", "notify")
        for mod in (board, jobs, status, qc, spoilage, lens, lab_stations):
            assert mod.get_db() == "db", mod.__name__
        # The money gate resolves the job's order through gates' binding.
        assert gates.get_order_repository() == "orders"
        for mod in (board, jobs, qc, spoilage, lens, lab_stations, vendor):
            assert mod.datetime is FakeDT, mod.__name__
        for mod in (_shared, gates, jobs, status):
            assert mod._check_dc_hardlock == "hardlock", mod.__name__
        assert lab_stations._perform_ready_notify == "notify"

    for name, value in originals.items():
        assert getattr(workshop, name) is value, name
    assert gates._check_dc_hardlock is originals["_check_dc_hardlock"]
    assert lab_stations._perform_ready_notify is originals["_perform_ready_notify"]


def test_gate_api_other_doors_import_is_still_exported():
    # orders.delivery / shipping / labels / services.lab_routing / orders.workshop
    for name in (
        "assert_linked_job_qc_cleared",
        "evaluate_scan_transition_gate",
        "SCAN_GATE_MESSAGES",
        "gate_job_handover_payment",
        "generate_job_number",
        "qc_cleared",
        "QC_HANDOVER_BLOCKED_MESSAGE",
    ):
        assert hasattr(workshop, name), name
    assert workshop.qc_cleared is workshop._qc_cleared
    # Same logger name as the flat module, whichever sub-module logs.
    assert workshop.logger is logging.getLogger("api.routers.workshop")
