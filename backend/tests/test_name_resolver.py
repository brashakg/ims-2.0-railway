"""Tests for the shared human-name resolver (backlog #4)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.services import name_resolver as nr  # noqa: E402


class _FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    def __iter__(self):
        return iter(self._docs)


class _FakeColl:
    def __init__(self, docs):
        self._docs = docs
        self.last_query = None

    def find(self, query, projection=None):
        self.last_query = query
        # Crude matcher supporting {field: {"$in": [...]}} and {"$or": [...]}.
        def matches(doc):
            if "$or" in query:
                return any(_match_clause(doc, c) for c in query["$or"])
            return _match_clause(doc, query)

        return _FakeCursor([d for d in self._docs if matches(d)])


def _match_clause(doc, clause):
    for k, v in clause.items():
        if isinstance(v, dict) and "$in" in v:
            if str(doc.get(k)) not in [str(x) for x in v["$in"]]:
                return False
        elif doc.get(k) != v:
            return False
    return True


class _FakeDB:
    def __init__(self, colls):
        self._colls = colls

    def get_collection(self, name):
        return self._colls.get(name)


def _db(**collmap):
    return _FakeDB({k: _FakeColl(v) for k, v in collmap.items()})


# --------------------------------------------------------------------------- #
# store_name_map
# --------------------------------------------------------------------------- #


def test_store_name_map_resolves():
    db = _db(stores=[
        {"store_id": "S1", "store_name": "Better Vision Bokaro"},
        {"store_id": "S2", "store_name": "WizOpt Pune"},
    ])
    out = nr.store_name_map(db, ["S1", "S2"])
    assert out == {"S1": "Better Vision Bokaro", "S2": "WizOpt Pune"}


def test_store_name_map_falls_back_to_code_then_id():
    db = _db(stores=[
        {"store_id": "S1", "store_code": "BV-BOK-01"},  # no name
        {"store_id": "S2"},  # nothing
    ])
    out = nr.store_name_map(db, ["S1", "S2"])
    assert out["S1"] == "BV-BOK-01"
    assert out["S2"] == "S2"


def test_store_name_map_none_db_is_empty():
    assert nr.store_name_map(None, ["S1"]) == {}


def test_store_name_map_empty_ids():
    db = _db(stores=[{"store_id": "S1", "store_name": "X"}])
    assert nr.store_name_map(db, []) == {}


# --------------------------------------------------------------------------- #
# user_name_map / employee_name_map
# --------------------------------------------------------------------------- #


def test_user_name_map_full_name_pref():
    db = _db(users=[
        {"user_id": "u1", "full_name": "Asha Verma", "username": "asha"},
        {"user_id": "u2", "username": "ravi"},  # only username
    ])
    out = nr.user_name_map(db, ["u1", "u2"])
    assert out["u1"] == "Asha Verma"
    assert out["u2"] == "ravi"


def test_user_name_map_matches_id_field():
    db = _db(users=[{"id": "u3", "full_name": "Legacy User"}])
    out = nr.user_name_map(db, ["u3"])
    assert out["u3"] == "Legacy User"


def test_user_name_map_skips_nameless():
    db = _db(users=[{"user_id": "u9"}])  # no name fields
    assert nr.user_name_map(db, ["u9"]) == {}


def test_employee_name_map_is_user_name_map():
    assert nr.employee_name_map is nr.user_name_map


# --------------------------------------------------------------------------- #
# vendor_name_map
# --------------------------------------------------------------------------- #


def test_vendor_name_map_trade_then_legal():
    db = _db(vendors=[
        {"vendor_id": "v1", "trade_name": "Luxottica India"},
        {"vendor_id": "v2", "legal_name": "ABC Optics Pvt Ltd"},
    ])
    out = nr.vendor_name_map(db, ["v1", "v2"])
    assert out["v1"] == "Luxottica India"
    assert out["v2"] == "ABC Optics Pvt Ltd"


def test_vendor_name_map_fail_soft_none():
    assert nr.vendor_name_map(None, ["v1"]) == {}
