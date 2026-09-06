"""Tripwires for the Wave 5 finance package split (api/routers/finance/).

Two things can silently break when a sub-module is added, moved or renamed:

1. A ROUTE GOES MISSING. Every sub-module registers on the two APIRouter
   objects built in _shared.py. If someone creates a fresh APIRouter in a
   sub-module (or forgets to import the sub-module from __init__.py) the routes
   just never mount and nothing raises -- the endpoint 404s in production.
2. A MONKEYPATCH STOPS BITING. 39 test files (and the lazy
   ``from .finance import check_period_locked`` in orders/vendors/returns/
   payroll/inventory) patch helpers by the package path
   ``api.routers.finance.<name>``. __init__.py forwards those writes into every
   sub-module that binds the name. Drop the forwarding and the patched tests
   still pass their setattr -- then hit the REAL database helper instead of
   their fake, which is a false green on money code.
"""

import os
import sys

import pytest
from fastapi import APIRouter

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import finance  # noqa: E402
from api.routers.finance import cash_drawer, journal_entries, pnl  # noqa: E402


def test_route_count_and_no_duplicate_paths():
    """The count is the tripwire: a lost route is otherwise a silent 404."""
    main = [(tuple(sorted(r.methods)), r.path) for r in finance.router.routes]
    assert len(main) == 50, "finance.router route count changed: %d" % len(main)
    assert len(set(main)) == len(main), "duplicate (method, path) on finance.router"

    ticker = [(tuple(sorted(r.methods)), r.path) for r in finance.ticker_router.routes]
    assert ticker == [(("GET",), "/target-ticker")]


def test_every_submodule_registers_on_the_shared_routers():
    for mod in finance._SUBMODULES:
        for name, value in vars(mod).items():
            if isinstance(value, APIRouter):
                assert value is finance.router or value is finance.ticker_router, (
                    "%s.%s is a private APIRouter -- its routes never mount"
                    % (mod.__name__, name)
                )


def test_patching_the_package_reaches_the_submodules():
    original = finance._get_db
    sentinel = object()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(finance, "_get_db", lambda: sentinel)
        assert pnl._get_db() is sentinel
        assert cash_drawer._get_db() is sentinel
        assert journal_entries._get_db() is sentinel

    # monkeypatch's undo goes through the same forwarding.
    assert finance._get_db is original
    assert pnl._get_db is original
    assert cash_drawer._get_db is original
    assert journal_entries._get_db is original
