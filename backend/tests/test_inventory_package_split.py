"""Tripwires for the Wave 5 inventory package split (api/routers/inventory/).

Three things can silently break when a sub-module is added, moved or renamed:

1. A ROUTE GOES MISSING. Every sub-module registers on the one APIRouter built
   in _shared.py. If someone creates a fresh APIRouter in a sub-module (or
   forgets to import the sub-module from __init__.py) the routes just never
   mount and nothing raises -- the endpoint 404s in production.
2. A ROUTE GETS SHADOWED. FastAPI resolves in registration order, so the
   import order in __init__.py IS the route table order. Move `barcode_trace`
   below `stock_lookups` and `/barcode/{barcode}/trace` is swallowed by the
   parametric `/barcode/{barcode}` -- a 200 with the wrong body, not an error.
3. A MONKEYPATCH STOPS BITING. ~30 test files patch helpers by the package
   path ``api.routers.inventory.<name>`` (and catalog / endless_aisle /
   blind_stock_take / vendors import helpers from it). __init__.py forwards
   those writes into every sub-module that binds the name. Drop the forwarding
   and the patched tests still pass their setattr -- then hit the REAL database
   helper instead of their fake, which is a false green on stock code.
"""

import os
import sys

import pytest
from fastapi import APIRouter

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import inventory  # noqa: E402
from api.routers.inventory import alerts, quarantine, stock  # noqa: E402


def test_route_count_and_no_duplicate_paths():
    """The count is the tripwire: a lost route is otherwise a silent 404."""
    routes = [(tuple(sorted(r.methods)), r.path) for r in inventory.router.routes]
    assert len(routes) == 43, "inventory.router route count changed: %d" % len(routes)
    assert len(set(routes)) == len(routes), "duplicate (method, path) on the router"


def test_specific_paths_are_registered_before_their_parametric_shadows():
    """Same method, and the parametric one matches the specific one's shape:
    `/barcode/anything` would swallow `/barcode/{barcode}/trace` only if it came
    first, and it would answer 200 with the wrong body rather than erroring."""
    getters = [r.path for r in inventory.router.routes if "GET" in r.methods]
    assert getters.index("/barcode/{barcode}/trace") < getters.index(
        "/barcode/{barcode}"
    ), "barcode_trace must be imported before stock_lookups in __init__.py"


def test_every_submodule_registers_on_the_shared_router():
    for mod in inventory._SUBMODULES:
        for name, value in vars(mod).items():
            if isinstance(value, APIRouter):
                assert (
                    value is inventory.router
                ), "%s.%s is a private APIRouter -- its routes never mount" % (
                    mod.__name__,
                    name,
                )


def test_patching_the_package_reaches_the_submodules():
    original = inventory._get_db
    sentinel = object()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(inventory, "_get_db", lambda: sentinel)
        assert stock._get_db() is sentinel
        assert alerts._get_db() is sentinel
        assert quarantine._get_db() is sentinel

    # monkeypatch's undo goes through the same forwarding.
    assert inventory._get_db is original
    assert stock._get_db is original
    assert alerts._get_db is original
    assert quarantine._get_db is original
