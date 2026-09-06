"""Tripwires for the Wave 5 orders package split (api/routers/orders/).

This is the POS / money door, and two things can break silently when a
sub-module is added, moved or renamed:

1. A ROUTE GOES MISSING. Every sub-module registers on the ONE APIRouter built
   in _shared.py. If someone creates a fresh APIRouter in a sub-module (or
   forgets to import the sub-module from __init__.py) the routes just never
   mount and nothing raises -- the endpoint 404s in production.
2. A MONKEYPATCH STOPS BITING. 59 test files patch helpers by the package path
   ``api.routers.orders.<name>`` (``_get_db``, ``_compute_per_category_gst``,
   ``_mark_units_sold``, ...). __init__.py forwards those writes into every
   sub-module that binds the name. Drop the forwarding and the patched tests
   still pass their setattr -- then hit the REAL database helper instead of
   their fake, which is a false green on billing, tender and GST code.
"""

import os
import sys

import pytest
from fastapi import APIRouter

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import orders  # noqa: E402
from api.routers.orders import admin_edit, create, delivery, items, release  # noqa: E402


def test_route_count_and_no_duplicate_paths():
    """The count is the tripwire: a lost route is otherwise a silent 404."""
    registered = [(tuple(sorted(r.methods)), r.path) for r in orders.router.routes]
    assert len(registered) == 24, "orders.router route count changed: %d" % len(
        registered
    )
    assert len(set(registered)) == len(
        registered
    ), "duplicate (method, path) on orders.router"


def test_fixed_paths_still_register_before_the_order_id_family():
    """FastAPI is first-registered-wins: /{order_id} must not shadow /search."""
    paths = [r.path for r in orders.router.routes]
    first_param = min(i for i, p in enumerate(paths) if p.startswith("/{order_id}"))
    for fixed in ("/pending/delivery", "/unpaid/list", "/overdue/list", "/search"):
        assert paths.index(fixed) < first_param, "%s moved below /{order_id}" % fixed


def test_every_submodule_registers_on_the_shared_router():
    for mod in orders._SUBMODULES:
        for name, value in vars(mod).items():
            if isinstance(value, APIRouter):
                assert value is orders.router, (
                    "%s.%s is a private APIRouter -- its routes never mount"
                    % (mod.__name__, name)
                )


def test_patching_the_package_reaches_the_submodules():
    original = orders._get_db
    sentinel = object()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(orders, "_get_db", lambda: sentinel)
        assert create._get_db() is sentinel
        assert admin_edit._get_db() is sentinel
        assert delivery._get_db() is sentinel
        assert release._get_db() is sentinel

    # monkeypatch's undo goes through the same forwarding.
    assert orders._get_db is original
    assert create._get_db is original
    assert admin_edit._get_db is original
    assert delivery._get_db is original
    assert release._get_db is original


def test_patching_the_gst_engine_reaches_every_billing_door():
    original = orders._compute_per_category_gst
    marker = object()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(orders, "_compute_per_category_gst", lambda *a, **k: marker)
        assert create._compute_per_category_gst() is marker
        assert items._compute_per_category_gst() is marker
        assert admin_edit._compute_per_category_gst() is marker

    assert create._compute_per_category_gst is original
    assert items._compute_per_category_gst is original
    assert admin_edit._compute_per_category_gst is original
