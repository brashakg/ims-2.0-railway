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

from tests.test_fcostfloor import floor_env, _seed_product, _item, _post  # noqa: F401,E402


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
    assert len(doc["salespersons"]) == 2
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
