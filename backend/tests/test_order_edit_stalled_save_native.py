"""D61 on REAL Mongo: an edit save that stalls past the sweep lands no line without stock.

Claude's own review of PR #1393 head 0915b336 (held off Ready): an admin edit that
adds a lens or a frame takes its stock before its save, with no fence. A press
stalled past CLAIM_SWEEP_MINUTES let the sweep give that stock back (GIVEN_BACK),
and the stalled save then landed a bill line holding no reservation or unit.
Reproduced first over real HTTP on native Mongo (before: 200, cell reserved 1 for
two lens lines). The sweep now shuts the bill to the op in one write on the bill
itself and the save filters on it: the late save is a 409, the bill unchanged.

Same case as the in-memory suite (`stalled_save_case`), on the native shop of
test_removed_goods_return_native. Locally: PSI_NATIVE_MONGO_URL / _DB; on CI the
backend job's mongo:7.0 service. No emoji.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")

pytest.importorskip("mongomock")  # the shared helpers import it

from tests.test_order_edit_recovery import stalled_save_case  # noqa: E402
from tests.test_order_edit_stock_identity import cells, door, invoiced  # noqa: E402,F401
from tests.test_removed_goods_return_native import shop  # noqa: E402,F401  (native shop)


@pytest.mark.parametrize("goods", ["lens", "frame"])
def test_a_stalled_save_is_refused_on_real_mongo(client, shop, cells, door, goods, monkeypatch):
    assert type(shop["db"].client).__module__.startswith("pymongo"), "native Mongo only"
    stalled_save_case(client, shop, cells, door, goods, monkeypatch)


@pytest.mark.parametrize("where", ["before the take", "between the takes"])
def test_a_take_after_the_sweep_goes_back_on_real_mongo(client, shop, cells, door, where, monkeypatch):
    from tests.test_order_edit_recovery import stalled_take_case

    assert type(shop["db"].client).__module__.startswith("pymongo"), "native Mongo only"
    stalled_take_case(client, shop, cells, door, where, monkeypatch)
