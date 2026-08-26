"""The Status Timeline must name the person, not print their user id.

Owner complaint, last screen: the order drawer's timeline read

    Changed by: user-superadmin

because every status_history writer stamps ``current_user["user_id"]``
(create_order, the confirm door, _claim_order_status) and the response mapper
handed that id straight to the UI. Verified against the live database
2026-08-26: the only actor ids in the collection are ``user-superadmin``
(whose only name is the username "admin") and ``qa-efe824be3bc8``, which
resolves to nobody at all.

These drive the REAL endpoint functions over a fake repo + fake users
collection -- no stubbed subject -- and cover both ways a timeline reaches a
screen: the list (the drawer renders the row it already has) and the single
order read.

Two things are load-bearing and asserted, not assumed:
  * the RAW id survives in the response beside the name (the audit trail is
    the id; the name is decoration on top of it), and
  * an id that resolves to NOBODY prints verbatim -- never an invented name.
"""

from __future__ import annotations

import asyncio
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import orders as orders_mod  # noqa: E402

STORE = "BV-PUN-01"

# Deliberately mirrors production: SUPERADMIN's only name is the username
# "admin", and the QA account resolves to nobody at all.
_USERS = [
    {"user_id": "user-superadmin", "username": "admin"},
    {"user_id": "u-priya", "full_name": "Priya Nair"},
]


class _UsersColl:
    """users stand-in that understands the resolver's $or query and COUNTS its
    reads -- an N+1 must fail the test, not merely be slow."""

    def __init__(self, users):
        self.users = list(users)
        self.reads = 0

    def find(self, flt, proj=None):
        self.reads += 1
        wanted = set()
        for term in flt.get("$or", []):
            for key in ("user_id", "id"):
                if key in term:
                    wanted.update(term[key].get("$in", []))
        return [dict(u) for u in self.users if u.get("user_id") in wanted]


class _FakeDB:
    def __init__(self, colls):
        self.colls = colls

    def get_collection(self, name):
        return self.colls.get(name)


class _Repo:
    def __init__(self, orders):
        self.orders = [dict(o) for o in orders]

    def find_by_store(self, store, **kwargs):
        return [dict(o) for o in self.orders if o.get("store_id") == store]

    def find_by_id(self, oid):
        for o in self.orders:
            if o.get("order_id") == oid:
                return dict(o)
        return None


def _order(order_id="ord-1", created_by="user-superadmin", changed_by="u-priya"):
    return {
        "order_id": order_id,
        "order_number": "BV-PUN-01-0001",
        "store_id": STORE,
        "status": "CONFIRMED",
        "created_at": "2026-08-20T10:00:00",
        "created_by": created_by,
        "status_history": [
            {
                "status": "DRAFT",
                "timestamp": "2026-08-20T10:00:00",
                "changed_by": created_by,
            },
            {
                "status": "CONFIRMED",
                "timestamp": "2026-08-20T10:05:00",
                "changed_by": changed_by,
            },
        ],
    }


def _wire(monkeypatch, orders, users=_USERS):
    repo = _Repo(orders)
    users_coll = _UsersColl(users)
    db = _FakeDB({"users": users_coll})
    monkeypatch.setattr(orders_mod, "get_order_repository", lambda: repo)
    monkeypatch.setattr(orders_mod, "_get_db", lambda: db)
    monkeypatch.setattr(
        orders_mod, "validate_store_access", lambda sid, user: sid or STORE
    )
    return users_coll


def _user():
    return {"user_id": "user-superadmin", "roles": ["ADMIN"], "active_store_id": STORE}


def _list():
    return asyncio.run(
        orders_mod.list_orders(STORE, None, None, None, None, 0, 50, _user())
    )["orders"]


# ---------------------------------------------------------------------------
# The drawer renders the row the LIST already handed it -- name it there.
# ---------------------------------------------------------------------------


def test_list_row_names_every_person_on_the_timeline(monkeypatch):
    _wire(monkeypatch, [_order()])
    row = _list()[0]
    history = row["statusHistory"]
    got = [h.get("changed_by_name") for h in history]
    assert got == [
        "admin",
        "Priya Nair",
    ], f"every status_history entry must carry the person's name; got {got!r}"
    assert row.get("created_by_name") == "admin", (
        "the timeline's first row prints createdBy on the same line -- it needs "
        f"a name too; got {row.get('created_by_name')!r}"
    )


def test_the_raw_id_survives_beside_the_name(monkeypatch):
    """The audit record IS the id. The name is added beside it, never over it."""
    _wire(monkeypatch, [_order()])
    row = _list()[0]
    assert row["createdBy"] == "user-superadmin"
    assert [h["changedBy"] for h in row["statusHistory"]] == [
        "user-superadmin",
        "u-priya",
    ]


def test_single_order_read_names_the_timeline_too(monkeypatch):
    _wire(monkeypatch, [_order()])
    row = asyncio.run(orders_mod.get_order("ord-1", _user()))
    assert [h.get("changed_by_name") for h in row["statusHistory"]] == [
        "admin",
        "Priya Nair",
    ]
    assert row.get("created_by_name") == "admin"


# ---------------------------------------------------------------------------
# Never invent a name
# ---------------------------------------------------------------------------


def test_an_id_that_resolves_to_nobody_gets_no_name(monkeypatch):
    """qa-efe824be3bc8 is really in the live data and is in no users row. The
    screen must print the id verbatim -- a fabricated name in an audit trail is
    worse than an ugly one."""
    _wire(
        monkeypatch,
        [_order(created_by="qa-efe824be3bc8", changed_by="qa-efe824be3bc8")],
    )
    row = _list()[0]
    assert "created_by_name" not in row
    for h in row["statusHistory"]:
        assert "changed_by_name" not in h, f"invented a name for {h['changedBy']}"
        assert h["changedBy"] == "qa-efe824be3bc8"


def test_a_dead_users_collection_still_returns_the_order(monkeypatch):
    """Fail-soft: no name is a cosmetic loss; a 500 on the order drawer is not."""

    class _Boom:
        def find(self, *a, **k):
            raise RuntimeError("users unavailable")

    repo = _Repo([_order()])
    monkeypatch.setattr(orders_mod, "get_order_repository", lambda: repo)
    monkeypatch.setattr(orders_mod, "_get_db", lambda: _FakeDB({"users": _Boom()}))
    monkeypatch.setattr(
        orders_mod, "validate_store_access", lambda sid, user: sid or STORE
    )
    row = _list()[0]
    assert row["statusHistory"][0]["changedBy"] == "user-superadmin"
    assert "changed_by_name" not in row["statusHistory"][0]


# ---------------------------------------------------------------------------
# ONE users read for the whole page, whatever the page size
# ---------------------------------------------------------------------------


def test_a_whole_page_of_orders_costs_one_users_read(monkeypatch):
    orders = [_order(order_id=f"ord-{i}") for i in range(25)]
    users_coll = _wire(monkeypatch, orders)
    rows = _list()
    assert len(rows) == 25
    assert (
        users_coll.reads == 1
    ), f"batched resolution must issue exactly ONE users read, got {users_coll.reads}"
    assert {h.get("changed_by_name") for r in rows for h in r["statusHistory"]} == {
        "admin",
        "Priya Nair",
    }
