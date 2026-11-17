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

from tests.test_order_edit_recovery import ledger, stalled_save_case  # noqa: E402,F401
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


# Codex round 7 (15 Nov 2026), on the same native shop: each case is the
# in-memory suite's own test body, run here against real Mongo.


def _native(shop):
    assert type(shop["db"].client).__module__.startswith("pymongo"), "native Mongo only"


def test_a_stale_removal_keeps_the_resold_piece_on_real_mongo(client, shop, door, monkeypatch):
    from tests.test_order_edit_recovery import (
        test_a_stale_removal_never_frees_the_piece_sold_again_to_this_bill as case,
    )

    _native(shop)
    case(client, shop, door, monkeypatch)


def test_a_lost_reserve_record_still_goes_back_on_real_mongo(client, shop, cells, door, monkeypatch):
    from tests.test_order_edit_recovery import test_a_reservation_whose_record_was_lost_still_goes_back as case

    _native(shop)
    case(client, shop, cells, door, monkeypatch)


@pytest.mark.parametrize("where", ["before the take", "between the takes"])
def test_a_dead_press_strands_nothing_on_real_mongo(client, shop, cells, where, monkeypatch):
    from tests.test_order_edit_recovery import test_a_take_after_the_sweep_whose_press_died_is_swept_back as case

    _native(shop)
    case(client, shop, cells, where, monkeypatch)


def test_overlapping_regates_claw_once_on_real_mongo(shop, ledger, monkeypatch):
    from tests.test_order_edit_recovery import test_two_overlapping_regates_claw_the_earn_once as case

    _native(shop)
    case(ledger, monkeypatch)


def test_a_lost_claw_move_is_completed_once_on_real_mongo(shop, ledger, monkeypatch):
    from tests.test_order_edit_recovery import test_a_claw_whose_balance_move_was_lost_is_completed_once as case

    _native(shop)
    case(ledger, monkeypatch)


# Round 10 (lead's review of head f8b722f0), the same in-memory test bodies on
# real Mongo: a real unique _id decides the claw step race.


@pytest.mark.parametrize("first", ["the smaller claw lands first", "the full claw lands first"])
def test_overlapping_regates_with_different_bills_on_real_mongo(shop, ledger, monkeypatch, first):
    from tests.test_order_edit_recovery import test_overlapping_regates_with_different_bills_end_at_the_lowest as case

    _native(shop)
    case(ledger, monkeypatch, first)


@pytest.mark.parametrize("name", [
    "test_a_claw_move_that_fails_again_is_still_owed",
    "test_a_loser_whose_completing_move_fails_is_still_owed",
    "test_a_claw_row_whose_move_underflowed_stays_owed",
])
def test_an_unlanded_claw_stays_owed_on_real_mongo(shop, ledger, monkeypatch, name):
    from tests import test_order_edit_recovery as suite

    _native(shop)
    getattr(suite, name)(ledger, monkeypatch)


def test_a_lens_given_back_before_its_cell_existed_on_real_mongo(client, shop, cells, monkeypatch):
    from tests.test_order_edit_recovery import test_a_lens_given_back_before_its_cell_existed_is_swept_back as case

    _native(shop)
    case(client, shop, cells, monkeypatch)


def test_a_row_naming_no_sale_frees_nothing_on_real_mongo(client, shop, door, monkeypatch):
    from tests.test_order_edit_recovery import test_a_row_naming_no_sale_never_frees_a_unit_unfenced as case

    _native(shop)
    case(client, shop, door, monkeypatch)


# Round 11 (lead's review of head c217cdb1): an edit's claw paused at its row
# while a cancel or return reverses the same bill, and the mirror. A real
# unique _id is the shared fence.


@pytest.mark.parametrize("door", ["cancel", "return"])
@pytest.mark.parametrize("paused", ["the edit claw", "the reversal"])
def test_an_edit_claw_and_a_reversal_take_the_earn_once_on_real_mongo(shop, ledger, monkeypatch, door, paused):
    from tests.test_order_edit_recovery import test_an_edit_claw_and_a_reversal_take_the_earn_once as case

    _native(shop)
    case(ledger, monkeypatch, door, paused)


@pytest.mark.parametrize("when", ["before the claw row", "after the claw row"])
def test_points_already_spent_reach_the_tasks_board_on_real_mongo(client, shop, door, monkeypatch, when):
    from tests.test_order_edit_recovery import test_points_already_spent_reach_the_tasks_board as case
    from api import dependencies as dm
    from database.repositories.task_repository import TaskRepository

    _native(shop)
    repo = TaskRepository(shop["db"].get_collection("tasks"))
    monkeypatch.setattr(dm, "get_task_repository", lambda: repo)
    case(client, shop, door, shop["db"].get_collection("tasks"), monkeypatch, when)


def test_the_sweep_reaches_every_open_edit_row_in_turn_on_real_mongo(shop, monkeypatch):
    from tests.test_order_edit_recovery import test_the_sweep_reaches_every_open_edit_row_in_turn as case

    _native(shop)
    case(shop, monkeypatch)


# Codex round 8 R8-A (17 Nov 2026): the D81 points task whose write failed, on
# real Mongo, both edit doors -- the in-memory suite's own test bodies.


def _native_tasks(shop):
    _native(shop)
    return shop["db"].get_collection("tasks")


@pytest.mark.parametrize("recovery", ["the sweep", "an identical retry", "a different edit"])
@pytest.mark.parametrize("fault", ["the insert fails", "the store is unreachable", "the read fails"])
def test_a_points_task_that_could_not_be_raised_stays_owed_on_real_mongo(
    client, shop, door, monkeypatch, fault, recovery
):
    from tests.test_order_edit_recovery import test_a_points_task_that_could_not_be_raised_stays_owed as case

    case(client, shop, door, _native_tasks(shop), monkeypatch, fault, recovery)


def test_a_points_task_whose_insert_reply_was_lost_on_real_mongo(client, shop, door, monkeypatch):
    from tests.test_order_edit_recovery import test_a_points_task_whose_insert_reply_was_lost_is_the_task as case

    case(client, shop, door, _native_tasks(shop), monkeypatch)


def test_a_points_task_ims_could_not_close_stays_owed_on_real_mongo(client, shop, door, monkeypatch):
    from tests.test_order_edit_recovery import test_a_points_task_ims_could_not_close_stays_owed as case

    case(client, shop, door, _native_tasks(shop), monkeypatch)


def test_overlapping_runners_raise_one_points_task_on_real_mongo(shop, monkeypatch):
    """A real unique _id decides the race."""
    from api import dependencies as dm
    from database.repositories.user_repository import UserRepository
    from tests.test_order_points_task_head_office import REF, test_overlapping_runners_raise_one_task as case

    _native(shop)
    db = shop["db"]
    monkeypatch.setattr(dm, "get_user_repository", lambda: UserRepository(db.get_collection("users")))
    case(db, monkeypatch)
    assert db.tasks.count_documents({"source_ref": REF}) == 1
