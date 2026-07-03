"""
IMS 2.0 - file_store init regression (prod GridFS outage)
=========================================================
get_file_store() used `getattr(db, "db", None) or db`, which truth-tests the
underlying pymongo Database. pymongo FORBIDS bool() on Database objects
(raises NotImplementedError), so with a REAL Mongo connected the accessor
crashed, the broad `except` swallowed it, and every GridFS feature in prod
(catalog product images, GRN attachments, expense bills, HR docs, handoffs,
bulk import) returned 503 "File storage unavailable" -- while dev/test mocks
(normal truthiness) hid the bug. These tests pin the fixed behavior with a
stand-in object that replicates pymongo's truth-test ban.
"""

import os

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402

from api.services import file_store as fs  # noqa: E402


class _PymongoLikeDatabase:
    """Mimics pymongo.database.Database: truth-testing is forbidden."""

    def __bool__(self):
        raise NotImplementedError(
            "Database objects do not implement truth value testing or bool(). "
            "Please compare with None instead: database is not None"
        )


class _ConnectedWrapper:
    """Mimics database.connection's wrapper: .db is the pymongo Database."""

    is_connected = True

    def __init__(self, inner):
        self.db = inner


@pytest.fixture(autouse=True)
def _reset_instance():
    """Isolate the module-level singleton across tests."""
    fs.set_file_store(None)
    yield
    fs.set_file_store(None)


def test_get_file_store_survives_pymongo_truthiness(monkeypatch):
    """THE regression: a connected wrapper whose .db forbids bool() must yield
    a working GridFSFileStore, not a swallowed crash -> None -> prod 503."""
    inner = _PymongoLikeDatabase()

    import database.connection as conn

    monkeypatch.setattr(conn, "get_db", lambda: _ConnectedWrapper(inner))

    store = fs.get_file_store()
    assert isinstance(store, fs.GridFSFileStore)
    assert store._db is inner  # reached the underlying pymongo Database


def test_get_file_store_falls_back_to_wrapper_without_db_attr(monkeypatch):
    """A connected object with NO .db attribute is used directly (legacy path)."""

    class _BareConnected:
        is_connected = True

    bare = _BareConnected()

    import database.connection as conn

    monkeypatch.setattr(conn, "get_db", lambda: bare)

    store = fs.get_file_store()
    assert isinstance(store, fs.GridFSFileStore)
    assert store._db is bare


def test_get_file_store_none_when_disconnected(monkeypatch):
    class _Disconnected:
        is_connected = False
        db = None

    import database.connection as conn

    monkeypatch.setattr(conn, "get_db", lambda: _Disconnected())

    assert fs.get_file_store() is None


def test_image_mimes_include_common_web_formats():
    """products.py serves the image/* subset of this list; AVIF (modern web
    product photos) and GIF must not be 400-rejected."""
    for mime in ("image/jpeg", "image/png", "image/webp", "image/heic",
                 "image/avif", "image/gif"):
        assert mime in fs.ALLOWED_MIME_TYPES
