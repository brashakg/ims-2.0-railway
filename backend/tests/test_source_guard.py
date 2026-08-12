"""
IMS 2.0 -- the getsource desynchronisation, pinned down
========================================================
A full-suite run once failed in
``test_pos_p3_items::test_payment_data_persists_idempotency_key`` with
``inspect.getsource(add_payment)`` returning a SINGLE line -- the last line of
``confirm_order``, the function immediately preceding it. The same test passed
in isolation. Nothing was wrong with the production code.

Root cause (reproduced below): ``inspect.findsource`` calls
``linecache.checkcache(file)``, which drops the cached text whenever the .py
file's mtime/size changed on disk. The function's ``co_firstlineno`` was baked
in at import. So once the file is edited mid-run -- another process, a
``git checkout`` landing during the run, an editor autosave -- the old line
number is applied to the new text and lands inside a DIFFERENT function.

The failure is the lucky case. The dangerous case is when the shifted offset
lands somewhere that HAPPENS to contain the asserted substring: the test then
passes while proving nothing about the function it names.

These tests:
  1. reproduce the desynchronisation deterministically;
  2. prove ``source_guard.verified_source`` refuses the bad block instead of
     returning it (a guard that can never fire is itself worthless);
  3. prove it still returns the real source on a quiescent file;
  4. prove the strict Mongo doubles reject operators they cannot emulate,
     rather than silently matching everything.
"""

from __future__ import annotations

import inspect
import os
import sys
import textwrap

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from source_guard import SourceLookupError, verified_source  # noqa: E402
from strict_fakes import (  # noqa: E402
    StrictCollection,
    UnsupportedMongoFeature,
    matches,
)


_ORIGINAL = textwrap.dedent(
    '''\
    """Stand-in for a router module."""


    def deco(fn):
        return fn


    @deco
    def confirm_order(order_id):
        """Confirm."""
        payload = {"order_id": order_id}
        payload["status"] = "CONFIRMED"
        return payload


    @deco
    def add_payment(order_id, idempotency_key=None):
        """Add a payment."""
        payment_data = {
            "order_id": order_id,
            "idempotency_key": idempotency_key,
        }
        return payment_data
    '''
)


@pytest.fixture
def desyncable_module(tmp_path, monkeypatch):
    """Import a throwaway module, then let the test edit its file on disk."""
    monkeypatch.syspath_prepend(str(tmp_path))
    path = tmp_path / "guard_probe_mod.py"
    path.write_text(_ORIGINAL, encoding="utf-8")

    import importlib

    mod = importlib.import_module("guard_probe_mod")
    try:
        yield mod, path
    finally:
        sys.modules.pop("guard_probe_mod", None)
        import linecache

        linecache.checkcache(str(path))


def _shift_file_down(path, lines=3):
    """Insert `lines` blank comment lines near the top and bump the mtime."""
    shifted = ("#\n" * lines) + path.read_text(encoding="utf-8")
    path.write_text(shifted, encoding="utf-8")
    stat = os.stat(path)
    os.utime(path, (stat.st_atime, stat.st_mtime + 10))


def test_getsource_desynchronises_when_the_file_changes_mid_run(desyncable_module):
    """The bare stdlib call silently returns the WRONG function's body."""
    mod, path = desyncable_module
    add_payment = mod.add_payment

    clean = inspect.getsource(add_payment)
    assert "idempotency_key" in clean
    assert len(clean.splitlines()) > 4

    _shift_file_down(path)

    # Nothing was reloaded: same function object, same compiled line number.
    assert mod.add_payment is add_payment
    dirty = inspect.getsource(add_payment)

    assert dirty != clean, (
        "expected getsource to pick up the edited file; if this ever stops "
        "holding, CPython changed linecache/inspect and the guard below can be "
        "revisited"
    )
    assert "idempotency_key" not in dirty, (
        "the whole point: the returned block is no longer add_payment's body"
    )


def test_verified_source_refuses_the_desynchronised_block(desyncable_module):
    """source_guard turns a silent wrong answer into a loud failure."""
    mod, path = desyncable_module
    add_payment = mod.add_payment

    _shift_file_down(path)

    with pytest.raises(SourceLookupError) as exc:
        verified_source(add_payment)

    message = str(exc.value)
    assert "add_payment" in message
    # The failure must carry enough context to diagnose it at 3am.
    assert "co_firstlineno" in message
    assert "file on disk" in message


def test_verified_source_returns_real_source_on_a_quiescent_file(desyncable_module):
    mod, _path = desyncable_module
    src = verified_source(mod.add_payment)
    assert "idempotency_key" in src
    assert src.splitlines()[0].strip().startswith("@deco")


def test_verified_source_rejects_a_too_short_block(desyncable_module):
    mod, _path = desyncable_module
    with pytest.raises(SourceLookupError):
        verified_source(mod.add_payment, min_lines=500)


def test_verified_source_handles_modules(desyncable_module):
    mod, _path = desyncable_module
    src = verified_source(mod)
    assert "def add_payment" in src


# ---------------------------------------------------------------------------
# The other half of "tests that prove nothing": permissive fakes
# ---------------------------------------------------------------------------


def test_strict_matcher_rejects_operators_it_cannot_emulate():
    """An unknown operator must RAISE, never be treated as a match.

    The suite's older FakeCollection ignored anything outside
    $gte/$lte/$ne/$exists, so e.g. ``{"expires_at": {"$lt": now}}`` matched
    every document and the filter under test became a no-op.
    """
    with pytest.raises(UnsupportedMongoFeature):
        matches({"a": 1}, {"a": {"$mod": [2, 0]}})


def test_strict_matcher_implements_lt_correctly():
    assert matches({"n": 1}, {"n": {"$lt": 5}}) is True
    assert matches({"n": 9}, {"n": {"$lt": 5}}) is False
    # A missing field must not match an ordered comparison.
    assert matches({}, {"n": {"$lt": 5}}) is False


def test_strict_collection_rejects_unknown_update_operators():
    coll = StrictCollection("x", [{"k": 1}])
    with pytest.raises(UnsupportedMongoFeature):
        coll.update_one({"k": 1}, {"$rename": {"k": "j"}})


def test_strict_collection_rejects_unknown_aggregation_stages():
    coll = StrictCollection("x", [{"k": 1}])
    with pytest.raises(UnsupportedMongoFeature):
        list(coll.aggregate([{"$lookup": {"from": "y"}}]))


def test_strict_collection_push_and_set_apply_for_real():
    coll = StrictCollection("x", [{"k": 1}])
    coll.update_one({"k": 1}, {"$set": {"s": "v"}, "$push": {"log": {"e": 1}}})
    coll.update_one({"k": 1}, {"$push": {"log": {"e": 2}}, "$inc": {"n": 3}})
    doc = coll.docs[0]
    assert doc["s"] == "v"
    assert doc["log"] == [{"e": 1}, {"e": 2}]
    assert doc["n"] == 3
