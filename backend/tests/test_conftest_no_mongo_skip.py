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
        _Item(_real_mongo_fixture),
        _Item(_mongomock_fallback_fixture),
        _Item(lambda: None),
    )
    marked = _Item(lambda: None, integration=True)
    cf._skip_real_mongo_tests_without_mongo(
        [real, fallback, plain, marked], in_ci=False, reachable=False
    )
    assert _skipped(real) and _skipped(marked)
    assert not _skipped(fallback) and not _skipped(plain)


def test_ci_never_skips_even_without_mongo():
    real = _Item(_real_mongo_fixture)
    cf._skip_real_mongo_tests_without_mongo([real], in_ci=True, reachable=False)
    assert real.markers == []


def test_reachable_mongo_never_skips():
    real = _Item(_real_mongo_fixture)
    cf._skip_real_mongo_tests_without_mongo([real], in_ci=False, reachable=True)
    assert real.markers == []


def test_detector_reports_a_dead_port_unreachable():
    assert cf.mongo_reachable("mongodb://127.0.0.1:1", timeout_ms=200) is False
