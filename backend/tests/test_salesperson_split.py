"""Owner spec 13: sales credit may be SPLIT TWO WAYS (default 50/50).

The split records who shares the CREDIT (incentive points + attribution) —
never the customer's money. Two invariants matter:
  1. a supplied split OWNS the primary attribution, so salesperson_id and
     salespersons[] can never tell two readers different stories;
  2. the shares are validated (<=2 people, sum 100%, no duplicate person),
     because a bad split silently mis-pays staff.

Discriminating: every assert fails if its rule is reverted.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402

from tests.test_fcostfloor import floor_env, _seed_product, _item, _post  # noqa: F401,E402


@pytest.fixture
def named_headers():
    """A logged-in user who HAS a full_name -- the conftest admin token has
    none, so the current-user name fallback is invisible with it."""
    from api.routers.auth import create_access_token

    token = create_access_token(
        {
            "user_id": "u-loggedin",
            "username": "loggedin",
            "full_name": "Logged In",
            "roles": ["SUPERADMIN"],
            "store_ids": ["BV-TEST-01"],
            "active_store_id": "BV-TEST-01",
        }
    )
    return {"Authorization": f"Bearer {token}"}


def _order_doc(env, resp):
    data = resp.json()
    oid = data.get("order_id") or (data.get("order") or {}).get("order_id")
    assert oid, data
    return env["order_repo"].find_by_id(oid)


def test_split_is_persisted_and_sets_the_primary(client, auth_headers, floor_env):
    pid = _seed_product(floor_env, pid="p-sp1", cost_price=50.0, mrp=200.0)
    r = _post(
        client, auth_headers, [_item(pid, 200.0)],
        salespersons=[
            {"salesperson_id": "u-arjun", "salesperson_name": "Arjun", "percent": 40},
            {"salesperson_id": "u-priya", "salesperson_name": "Priya", "percent": 60},
        ],
    )
    assert r.status_code == 201, r.text
    doc = _order_doc(floor_env, r)
    # The CREDIT NUMBERS are the whole point of the record: a future payout
    # reader pays on these, so assert them, not just the row count.
    assert [(s["salesperson_id"], s["percent"]) for s in doc["salespersons"]] == [
        ("u-arjun", 40.0),
        ("u-priya", 60.0),
    ]
    # The LARGER share owns the primary attribution every existing reader uses.
    assert doc["salesperson_id"] == "u-priya"
    assert doc["salesperson_name"] == "Priya"


def test_default_5050_split_keeps_first_as_primary(client, auth_headers, floor_env):
    pid = _seed_product(floor_env, pid="p-sp2", cost_price=50.0, mrp=200.0)
    r = _post(
        client, auth_headers, [_item(pid, 200.0)],
        salespersons=[
            {"salesperson_id": "u-a", "salesperson_name": "A", "percent": 50},
            {"salesperson_id": "u-b", "salesperson_name": "B", "percent": 50},
        ],
    )
    assert r.status_code == 201, r.text
    doc = _order_doc(floor_env, r)
    assert doc["salesperson_id"] == "u-a"


def test_no_split_leaves_the_order_shape_untouched(client, auth_headers, floor_env):
    """Single-seller sales must be byte-identical to before the feature."""
    pid = _seed_product(floor_env, pid="p-sp3", cost_price=50.0, mrp=200.0)
    r = _post(client, auth_headers, [_item(pid, 200.0)], salesperson_id="u-solo",
              salesperson_name="Solo")
    assert r.status_code == 201, r.text
    doc = _order_doc(floor_env, r)
    assert doc["salesperson_id"] == "u-solo"
    assert "salespersons" not in doc


def test_shares_must_total_100(client, auth_headers, floor_env):
    pid = _seed_product(floor_env, pid="p-sp4", cost_price=50.0, mrp=200.0)
    r = _post(
        client, auth_headers, [_item(pid, 200.0)],
        salespersons=[
            {"salesperson_id": "u-a", "percent": 50},
            {"salesperson_id": "u-b", "percent": 30},
        ],
    )
    assert r.status_code == 422, r.text


def test_at_most_two_people(client, auth_headers, floor_env):
    pid = _seed_product(floor_env, pid="p-sp5", cost_price=50.0, mrp=200.0)
    r = _post(
        client, auth_headers, [_item(pid, 200.0)],
        salespersons=[
            {"salesperson_id": "u-a", "percent": 34},
            {"salesperson_id": "u-b", "percent": 33},
            {"salesperson_id": "u-c", "percent": 33},
        ],
    )
    assert r.status_code == 422, r.text


def test_same_person_cannot_take_both_shares(client, auth_headers, floor_env):
    pid = _seed_product(floor_env, pid="p-sp6", cost_price=50.0, mrp=200.0)
    r = _post(
        client, auth_headers, [_item(pid, 200.0)],
        salespersons=[
            {"salesperson_id": "u-a", "percent": 50},
            {"salesperson_id": "u-a", "percent": 50},
        ],
    )
    assert r.status_code == 422, r.text


def test_zero_share_rejected(client, auth_headers, floor_env):
    pid = _seed_product(floor_env, pid="p-sp7", cost_price=50.0, mrp=200.0)
    r = _post(
        client, auth_headers, [_item(pid, 200.0)],
        salespersons=[
            {"salesperson_id": "u-a", "percent": 100},
            {"salesperson_id": "u-b", "percent": 0},
        ],
    )
    assert r.status_code == 422, r.text


def test_picker_conflicting_with_the_split_is_rejected(client, auth_headers, floor_env):
    """A picker naming someone the split does not is a CLIENT BUG. Deriving the
    primary from the split and dropping the picker would bury it -- and this
    feeds staff pay."""
    pid = _seed_product(floor_env, pid="p-sp8", cost_price=50.0, mrp=200.0)
    r = _post(
        client, auth_headers, [_item(pid, 200.0)],
        salesperson_id="u-picker",
        salespersons=[
            {"salesperson_id": "u-a", "percent": 60},
            {"salesperson_id": "u-b", "percent": 40},
        ],
    )
    assert r.status_code == 422, r.text


def test_picker_inside_the_split_is_accepted(client, auth_headers, floor_env):
    """The reject above must not swallow the legitimate case: a picker naming
    one of the two still books, and the LARGER share still owns the primary
    (here the picker named the SMALLER share)."""
    pid = _seed_product(floor_env, pid="p-sp9", cost_price=50.0, mrp=200.0)
    r = _post(
        client, auth_headers, [_item(pid, 200.0)],
        salesperson_id="u-b", salesperson_name="B",
        salespersons=[
            {"salesperson_id": "u-a", "salesperson_name": "A", "percent": 60},
            {"salesperson_id": "u-b", "salesperson_name": "B", "percent": 40},
        ],
    )
    assert r.status_code == 201, r.text
    doc = _order_doc(floor_env, r)
    assert doc["salesperson_id"] == "u-a"
    assert doc["salesperson_name"] == "A"


def test_picker_name_fills_the_matching_entry(client, auth_headers, floor_env):
    """Split carries ids only; the picker named the primary, so that name is
    the primary's -- and stays on the entry it describes."""
    pid = _seed_product(floor_env, pid="p-sp10", cost_price=50.0, mrp=200.0)
    r = _post(
        client, auth_headers, [_item(pid, 200.0)],
        salesperson_id="u-a", salesperson_name="Arjun Picked",
        salespersons=[
            {"salesperson_id": "u-a", "percent": 60},
            {"salesperson_id": "u-b", "percent": 40},
        ],
    )
    assert r.status_code == 201, r.text
    doc = _order_doc(floor_env, r)
    assert doc["salesperson_name"] == "Arjun Picked"
    assert doc["salespersons"][0]["salesperson_name"] == "Arjun Picked"
    # ...and only the entry it names.
    assert doc["salespersons"][1]["salesperson_name"] is None


def test_nameless_split_still_falls_back_to_the_current_user(
    client, named_headers, floor_env
):
    """REGRESSION GUARD: a plain sale with no picker name stores the logged-in
    user's name. A split must not lose that fallback and store None."""
    pid = _seed_product(floor_env, pid="p-sp11", cost_price=50.0, mrp=200.0)
    r = _post(
        client, named_headers, [_item(pid, 200.0)],
        salespersons=[
            {"salesperson_id": "u-a", "percent": 60},
            {"salesperson_id": "u-b", "percent": 40},
        ],
    )
    assert r.status_code == 201, r.text
    doc = _order_doc(floor_env, r)
    assert doc["salesperson_id"] == "u-a"
    assert doc["salesperson_name"] == "Logged In"


def test_split_ids_are_stored_stripped(client, auth_headers, floor_env):
    """The id that is VALIDATED must be the id that is PERSISTED: ' u-a '
    stored verbatim is a primary that matches no user."""
    pid = _seed_product(floor_env, pid="p-sp12", cost_price=50.0, mrp=200.0)
    r = _post(
        client, auth_headers, [_item(pid, 200.0)],
        salespersons=[
            {"salesperson_id": " u-a ", "salesperson_name": "A", "percent": 60},
            {"salesperson_id": "u-b", "salesperson_name": "B", "percent": 40},
        ],
    )
    assert r.status_code == 201, r.text
    doc = _order_doc(floor_env, r)
    assert doc["salesperson_id"] == "u-a"
    assert [s["salesperson_id"] for s in doc["salespersons"]] == ["u-a", "u-b"]
