"""The conftest detector that skips real-Mongo tests on a PC without Mongo
(and never on CI)."""

from types import SimpleNamespace

import conftest as cf


def _real_mongo_fixture():
    from pymongo import MongoClient

    return MongoClient("mongodb://x", serverSelectionTimeoutMS=1)


def _mongomock_fallback_fixture():
    from pymongo import MongoClient
    import mongomock

    try:
        return MongoClient("mongodb://x", serverSelectionTimeoutMS=1)
    except Exception:
        return mongomock.MongoClient()


# Tests reach the fakes through this dict, so the detector (which scans a test's
# own body and the globals it calls) does not mistake THESE tests for real-Mongo
# tests and skip them on a PC without Mongo.
F = {}


class _Item:
    def __init__(self, func, integration=False):
        self.markers = []
        self._integration = integration
        defs = [SimpleNamespace(func=func)]
        self._fixtureinfo = SimpleNamespace(name2fixturedefs={"db": defs})

    def get_closest_marker(self, name):
        return object() if (name == "integration" and self._integration) else None

    def add_marker(self, m):
        self.markers.append(m)


def _skipped(item):
    return any(
        m.mark.name == "skip" and m.mark.kwargs.get("reason") == cf.NO_MONGO_REASON
        for m in item.markers
    )


def test_unreachable_mongo_skips_only_real_mongo_tests():
    real, fallback, plain = (
        _Item(F["_real_mongo_fixture"]),
        _Item(F["_mongomock_fallback_fixture"]),
        _Item(lambda: None),
    )
    marked = _Item(lambda: None, integration=True)
    cf._skip_real_mongo_tests_without_mongo(
        [real, fallback, plain, marked], in_ci=False, reachable=False
    )
    assert _skipped(real) and _skipped(marked)
    assert not _skipped(fallback) and not _skipped(plain)


def test_ci_never_skips_even_without_mongo():
    real = _Item(F["_real_mongo_fixture"])
    cf._skip_real_mongo_tests_without_mongo([real], in_ci=True, reachable=False)
    assert real.markers == []


def test_reachable_mongo_never_skips():
    real = _Item(F["_real_mongo_fixture"])
    cf._skip_real_mongo_tests_without_mongo([real], in_ci=False, reachable=True)
    assert real.markers == []


def test_detector_reports_a_dead_port_unreachable():
    assert cf.mongo_reachable("mongodb://127.0.0.1:1", timeout_ms=200) is False


def _real_mongo_helper():
    from pymongo import MongoClient

    return MongoClient("mongodb://x", serverSelectionTimeoutMS=1)


def _fixture_via_helper():
    return _real_mongo_helper()


def _fixture_via_helper_with_fallback():
    import mongomock

    try:
        return _real_mongo_helper()
    except Exception:
        return mongomock.MongoClient()


def test_fixture_opening_mongo_through_a_helper_is_detected():
    via, fallback = _Item(F["_fixture_via_helper"]), _Item(F["_fixture_via_helper_with_fallback"])
    cf._skip_real_mongo_tests_without_mongo(
        [via, fallback], in_ci=False, reachable=False
    )
    assert _skipped(via)
    assert not _skipped(fallback)


def test_ci_env_var_is_read_and_never_skips(monkeypatch):
    monkeypatch.setenv("CI", "true")
    real = _Item(F["_real_mongo_fixture"])
    cf._skip_real_mongo_tests_without_mongo([real], reachable=False)
    assert real.markers == []


def test_no_ci_env_var_skips(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    real = _Item(F["_real_mongo_fixture"])
    cf._skip_real_mongo_tests_without_mongo([real], reachable=False)
    assert _skipped(real)


def test_collection_hook_runs_the_detector(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(cf, "mongo_reachable", lambda *a, **k: False)
    monkeypatch.setattr(cf, "_skip_own_frozen_clock_modules", lambda *a, **k: None)
    real = _Item(F["_real_mongo_fixture"])
    real.nodeid = "tests/test_x.py::t"
    cf.pytest_collection_modifyitems(SimpleNamespace(), [real])
    assert _skipped(real)


F.update(
    _real_mongo_fixture=_real_mongo_fixture,
    _mongomock_fallback_fixture=_mongomock_fallback_fixture,
    _fixture_via_helper=_fixture_via_helper,
    _fixture_via_helper_with_fallback=_fixture_via_helper_with_fallback,
)
