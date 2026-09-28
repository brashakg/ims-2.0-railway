"""Tripwires for the Wave 6 clinical package split (api/routers/clinical/).

Three things can break silently when a sub-module is added, moved or renamed:

1. A ROUTE GOES MISSING OR MOVES. Every sub-module registers on the ONE
   APIRouter built in _shared.py, in the order __init__.py imports them. A
   fresh APIRouter in a sub-module (or a forgotten import) never mounts and
   nothing raises -- the endpoint 404s in production. A reordered import
   changes the route table and the OpenAPI document.
2. A MONKEYPATCH STOPS BITING. The clinical tests patch repositories and
   helpers by the package path ``api.routers.clinical.<name>``. __init__.py
   forwards those writes into every sub-module that binds the name. Drop the
   forwarding and the patched tests still pass their setattr -- then hit the
   REAL repository instead of their fake: a false green on eye-test, Rx and
   IDOR code.
3. A RE-EXPORT GOES MISSING. test_f50_handover patches only
   ``if hasattr(clinical, name)``, so a lost re-export silently skips the
   patch instead of failing.
"""

import os
import sys

import pytest
from fastapi import APIRouter

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import clinical  # noqa: E402
from api.routers.clinical import (  # noqa: E402
    abuse,
    eye_queue,
    eye_tests,
    handover,
    lens_combos,
    patient_history,
    soap,
    stats,
)

# The flat clinical.py's route table, in registration order (stacked
# decorators apply bottom-up, so "/" registers before "").
_ROUTE_TABLE = [
    ("GET", "/"),
    ("GET", ""),
    ("GET", "/queue"),
    ("POST", "/queue"),
    ("PATCH", "/queue/{queue_id}/status"),
    ("DELETE", "/queue/{queue_id}"),
    ("POST", "/queue/{queue_id}/start-test"),
    ("GET", "/queue/stats"),
    ("GET", "/tests"),
    ("GET", "/tests/{test_id}"),
    ("POST", "/tests/{test_id}/complete"),
    ("PUT", "/tests/{test_id}/exam"),
    ("POST", "/tests/{test_id}/send-to-floor"),
    ("GET", "/tests/patient/{customer_phone}"),
    ("GET", "/tests/customer/{customer_id}"),
    ("GET", "/optometrist/{optometrist_id}/stats"),
    ("GET", "/conversion-dashboard"),
    ("GET", "/tests/{test_id}/soap-note"),
    ("POST", "/tests/{test_id}/soap-note"),
    ("GET", "/prescriptions/{prescription_id}/print"),
    ("POST", "/prescriptions/{prescription_id}/redo"),
    ("GET", "/prescriptions/{prescription_id}/redos"),
    ("GET", "/abuse-detection"),
    ("GET", "/lens-power-combos"),
    ("POST", "/lens-power-combos"),
    ("DELETE", "/lens-power-combos/{combo_id}"),
    ("POST", "/manufacturability-check"),
]


def test_route_table_is_the_flat_files_in_the_same_order():
    table = [(m, r.path) for r in clinical.router.routes for m in sorted(r.methods)]
    assert table == _ROUTE_TABLE


def test_every_submodule_registers_on_the_shared_router():
    for mod in clinical._SUBMODULES:
        for name, value in vars(mod).items():
            if isinstance(value, APIRouter):
                assert (
                    value is clinical.router
                ), "%s.%s is a private APIRouter -- its routes never mount" % (
                    mod.__name__,
                    name,
                )


def test_patching_a_repository_reaches_every_submodule_that_reads_it():
    original = clinical.get_eye_test_repository
    sentinel = object()
    readers = (eye_queue, eye_tests, handover, patient_history, stats, soap)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(clinical, "get_eye_test_repository", lambda: sentinel)
        for mod in readers:
            assert mod.get_eye_test_repository() is sentinel, mod.__name__

    # monkeypatch's undo goes through the same forwarding.
    assert clinical.get_eye_test_repository is original
    for mod in readers:
        assert mod.get_eye_test_repository is original, mod.__name__


def test_patching_a_helper_reaches_the_submodule_that_defines_it():
    original_flag = clinical._clinical_handover_enabled
    original_col = clinical._get_lens_power_combos_col
    original_db = clinical.get_db

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(clinical, "_clinical_handover_enabled", lambda store_id: "flag")
        mp.setattr(clinical, "_get_lens_power_combos_col", lambda: "col")
        mp.setattr(clinical, "get_db", lambda: "db")
        assert handover._clinical_handover_enabled("S1") == "flag"
        assert lens_combos._get_lens_power_combos_col() == "col"
        for mod in (handover, abuse, lens_combos):
            assert mod.get_db() == "db", mod.__name__

    assert handover._clinical_handover_enabled is original_flag
    assert lens_combos._get_lens_power_combos_col is original_col
    assert handover.get_db is original_db


def test_names_other_tests_probe_with_hasattr_are_still_exported():
    for name in (
        "get_handoff_repository",
        "get_prescription_repository",
        "get_user_repository",
        "get_eye_test_repository",
        "get_audit_repository",
        "get_db",
    ):
        assert hasattr(clinical, name), name
