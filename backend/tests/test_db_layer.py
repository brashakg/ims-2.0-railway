"""
IMS 2.0 - Database-layer PRIMITIVES tests
=========================================
Exercises the low-level building blocks in `database/connection.py` that the
rest of the codebase leans on, NOT the higher-level repositories (those have
their own suites). Three groups:

1. MockCollection / MockDatabase -- the no-Mongo in-memory shim used in tests
   and in production fail-soft paths. Asserts insert_one/insert_many, find_one
   + find with the full operator set the routers actually use ($gt/$lt/$gte/
   $lte/$in/$ne/$or/$and/$regex), update_one ($set/$inc/$push), delete_one/
   delete_many and count_documents behave like the real driver.

2. DatabaseConfig -- from_uri() round-trips a URI verbatim, from_env() reads the
   MONGO_* env vars, and get_uri() builds a well-formed mongodb:// URL with auth
   + connection params.

3. INTEGRATION (marked `integration`) -- a real pymongo round-trip against the
   live local Mongo on a THROWAWAY database: insert, find, a UNIQUE index, a
   duplicate insert raising DuplicateKeyError, then drop the db in teardown.
   Skips cleanly (pytest.skip) when Mongo is unreachable so it is safe on a CI
   runner / laptop without Mongo.
"""
from __future__ import annotations

import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.connection import (  # noqa: E402
    DatabaseConfig,
    MockCollection,
    MockDatabase,
    get_mock_db,
)


# ===========================================================================
# 1) MockCollection / MockDatabase primitives (no DB needed)
# ===========================================================================
class TestMockCollectionInsert:
    def test_insert_one_assigns_id_and_is_findable(self):
        col = MockCollection("widgets")
        res = col.insert_one({"name": "alpha", "qty": 3})
        assert res.inserted_id is not None
        found = col.find_one({"name": "alpha"})
        assert found is not None
        assert found["qty"] == 3
        assert found["_id"] == res.inserted_id

    def test_insert_one_honours_explicit_id(self):
        col = MockCollection("widgets")
        res = col.insert_one({"_id": "fixed-1", "name": "beta"})
        assert res.inserted_id == "fixed-1"
        assert col.find_one({"_id": "fixed-1"})["name"] == "beta"

    def test_insert_one_copies_document(self):
        # insert_one stores a COPY -- mutating the caller's dict after insert
        # must not change the stored row.
        col = MockCollection("widgets")
        src = {"name": "gamma", "tags": ["a"]}
        col.insert_one(src)
        src["name"] = "MUTATED"
        assert col.find_one({"name": "gamma"}) is not None
        assert col.find_one({"name": "MUTATED"}) is None

    def test_insert_many_returns_all_ids(self):
        col = MockCollection("widgets")
        res = col.insert_many(
            [{"name": "a"}, {"name": "b"}, {"name": "c"}]
        )
        assert len(res.inserted_ids) == 3
        assert col.count_documents({}) == 3


class TestMockCollectionFindOperators:
    def _seed(self):
        col = MockCollection("orders")
        col.insert_many(
            [
                {"_id": "o1", "store": "S1", "total": 100, "status": "PAID", "city": "Ranchi"},
                {"_id": "o2", "store": "S1", "total": 250, "status": "DRAFT", "city": "Pune"},
                {"_id": "o3", "store": "S2", "total": 250, "status": "PAID", "city": "Mumbai"},
                {"_id": "o4", "store": "S2", "total": 500, "status": "VOID", "city": "Ranchi"},
            ]
        )
        return col

    def test_find_one_by_id_shortcut(self):
        col = self._seed()
        assert col.find_one({"_id": "o3"})["city"] == "Mumbai"

    def test_find_one_empty_filter_returns_some_doc(self):
        col = self._seed()
        assert col.find_one({}) is not None

    def test_find_one_no_match_returns_none(self):
        col = self._seed()
        assert col.find_one({"store": "NOPE"}) is None

    def test_direct_equality(self):
        col = self._seed()
        results = list(col.find({"store": "S1"}))
        assert {d["_id"] for d in results} == {"o1", "o2"}

    def test_gt_lt(self):
        col = self._seed()
        gt = {d["_id"] for d in col.find({"total": {"$gt": 200}})}
        assert gt == {"o2", "o3", "o4"}
        lt = {d["_id"] for d in col.find({"total": {"$lt": 250}})}
        assert lt == {"o1"}

    def test_gte_lte(self):
        col = self._seed()
        gte = {d["_id"] for d in col.find({"total": {"$gte": 250}})}
        assert gte == {"o2", "o3", "o4"}
        lte = {d["_id"] for d in col.find({"total": {"$lte": 250}})}
        assert lte == {"o1", "o2", "o3"}

    def test_in(self):
        col = self._seed()
        res = {d["_id"] for d in col.find({"status": {"$in": ["PAID", "VOID"]}})}
        assert res == {"o1", "o3", "o4"}

    def test_ne(self):
        col = self._seed()
        res = {d["_id"] for d in col.find({"status": {"$ne": "PAID"}})}
        assert res == {"o2", "o4"}

    def test_or(self):
        col = self._seed()
        res = {
            d["_id"]
            for d in col.find({"$or": [{"store": "S2"}, {"status": "DRAFT"}]})
        }
        assert res == {"o2", "o3", "o4"}

    def test_and(self):
        col = self._seed()
        res = {
            d["_id"]
            for d in col.find(
                {"$and": [{"city": "Ranchi"}, {"status": "PAID"}]}
            )
        }
        assert res == {"o1"}

    def test_regex_case_insensitive(self):
        col = self._seed()
        res = {
            d["_id"]
            for d in col.find({"city": {"$regex": "ran", "$options": "i"}})
        }
        assert res == {"o1", "o4"}

    def test_regex_case_sensitive_no_match(self):
        col = self._seed()
        # No $options -> case-sensitive; lowercase 'ran' does not match 'Ranchi'.
        res = list(col.find({"city": {"$regex": "ran"}}))
        assert res == []

    def test_find_accepts_projection_arg(self):
        # The real pymongo signature find(filter, projection) must not blow up.
        col = self._seed()
        res = list(col.find({"store": "S1"}, {"_id": 0}))
        assert len(res) == 2

    def test_find_empty_filter_returns_all(self):
        col = self._seed()
        assert len(list(col.find({}))) == 4
        assert len(list(col.find())) == 4


class TestMockCursor:
    def _seed(self):
        col = MockCollection("nums")
        col.insert_many([{"_id": str(i), "n": i} for i in [5, 1, 9, 3, 7]])
        return col

    def test_sort_field_direction_form(self):
        col = self._seed()
        ns = [d["n"] for d in col.find({}).sort("n", 1)]
        assert ns == [1, 3, 5, 7, 9]
        ns_desc = [d["n"] for d in col.find({}).sort("n", -1)]
        assert ns_desc == [9, 7, 5, 3, 1]

    def test_sort_list_form(self):
        col = self._seed()
        ns = [d["n"] for d in col.find({}).sort([("n", -1)])]
        assert ns == [9, 7, 5, 3, 1]

    def test_skip_and_limit(self):
        col = self._seed()
        ns = [d["n"] for d in col.find({}).sort("n", 1).skip(1).limit(2)]
        assert ns == [3, 5]


class TestMockCollectionUpdate:
    def test_set(self):
        col = MockCollection("c")
        col.insert_one({"_id": "1", "status": "DRAFT"})
        res = col.update_one({"_id": "1"}, {"$set": {"status": "PAID"}})
        assert res.modified_count == 1
        assert col.find_one({"_id": "1"})["status"] == "PAID"

    def test_inc_existing_and_missing_field(self):
        col = MockCollection("c")
        col.insert_one({"_id": "1", "count": 10})
        col.update_one({"_id": "1"}, {"$inc": {"count": 5}})
        assert col.find_one({"_id": "1"})["count"] == 15
        # $inc on an absent field starts from 0.
        col.update_one({"_id": "1"}, {"$inc": {"fresh": 2}})
        assert col.find_one({"_id": "1"})["fresh"] == 2

    def test_push_creates_then_appends(self):
        col = MockCollection("c")
        col.insert_one({"_id": "1", "name": "x"})
        col.update_one({"_id": "1"}, {"$push": {"events": "e1"}})
        col.update_one({"_id": "1"}, {"$push": {"events": "e2"}})
        assert col.find_one({"_id": "1"})["events"] == ["e1", "e2"]

    def test_update_no_match_returns_zero(self):
        col = MockCollection("c")
        res = col.update_one({"_id": "nope"}, {"$set": {"a": 1}})
        assert res.modified_count == 0

    def test_update_many(self):
        col = MockCollection("c")
        col.insert_many(
            [
                {"_id": "1", "g": "A", "flag": False},
                {"_id": "2", "g": "A", "flag": False},
                {"_id": "3", "g": "B", "flag": False},
            ]
        )
        res = col.update_many({"g": "A"}, {"$set": {"flag": True}})
        assert res.modified_count == 2
        assert col.find_one({"_id": "1"})["flag"] is True
        assert col.find_one({"_id": "3"})["flag"] is False


class TestMockCollectionDelete:
    def test_delete_one(self):
        col = MockCollection("c")
        col.insert_many([{"_id": "1"}, {"_id": "2"}])
        res = col.delete_one({"_id": "1"})
        assert res.deleted_count == 1
        assert col.count_documents({}) == 1
        assert col.find_one({"_id": "1"}) is None

    def test_delete_one_no_match(self):
        col = MockCollection("c")
        col.insert_one({"_id": "1"})
        assert col.delete_one({"_id": "nope"}).deleted_count == 0
        assert col.count_documents({}) == 1

    def test_delete_many_with_filter(self):
        col = MockCollection("c")
        col.insert_many(
            [{"_id": "1", "g": "A"}, {"_id": "2", "g": "A"}, {"_id": "3", "g": "B"}]
        )
        res = col.delete_many({"g": "A"})
        assert res.deleted_count == 2
        assert col.count_documents({}) == 1
        assert col.find_one({"_id": "3"}) is not None

    def test_delete_many_no_filter_clears_all(self):
        col = MockCollection("c")
        col.insert_many([{"_id": "1"}, {"_id": "2"}])
        res = col.delete_many()
        assert res.deleted_count == 2
        assert col.count_documents({}) == 0


class TestMockCollectionCount:
    def test_count_total_and_filtered(self):
        col = MockCollection("c")
        col.insert_many(
            [{"s": "S1"}, {"s": "S1"}, {"s": "S2"}]
        )
        assert col.count_documents({}) == 3
        assert col.count_documents({"s": "S1"}) == 2
        assert col.count_documents({"s": "NONE"}) == 0


class TestMockDatabase:
    def test_getitem_returns_same_collection_instance(self):
        mdb = MockDatabase()
        c1 = mdb["orders"]
        c1.insert_one({"_id": "1", "x": 1})
        # Re-fetching the same name must return the SAME collection (data
        # persists), not a fresh empty one.
        c2 = mdb["orders"]
        assert c2 is c1
        assert c2.count_documents({}) == 1

    def test_collections_are_isolated(self):
        mdb = MockDatabase()
        mdb["a"].insert_one({"_id": "1"})
        assert mdb["b"].count_documents({}) == 0

    def test_list_collection_names(self):
        mdb = MockDatabase()
        _ = mdb["orders"]
        _ = mdb["customers"]
        names = mdb.list_collection_names()
        assert set(names) == {"orders", "customers"}

    def test_get_mock_db_factory(self):
        mdb = get_mock_db()
        assert isinstance(mdb, MockDatabase)


# ===========================================================================
# 2) DatabaseConfig
# ===========================================================================
class TestDatabaseConfig:
    def test_from_uri_returns_uri_verbatim(self):
        raw = "mongodb://user:pw@host1:27017,host2:27017/?replicaSet=rs0&ssl=true"
        cfg = DatabaseConfig.from_uri(raw, database="ims_custom")
        # get_uri() must hand back exactly what was passed -- no re-assembly.
        assert cfg.get_uri() == raw
        assert cfg.database == "ims_custom"

    def test_from_uri_default_database(self):
        cfg = DatabaseConfig.from_uri("mongodb://localhost:27017")
        assert cfg.database == "ims_2_0"

    def test_from_env_reads_mongo_vars(self, monkeypatch):
        monkeypatch.setenv("MONGO_HOST", "db.example.com")
        monkeypatch.setenv("MONGO_PORT", "27018")
        monkeypatch.setenv("MONGO_DATABASE", "ims_prod")
        monkeypatch.setenv("MONGO_USERNAME", "ims")
        monkeypatch.setenv("MONGO_PASSWORD", "s3cret")
        monkeypatch.setenv("MONGO_AUTH_SOURCE", "admin")
        monkeypatch.setenv("MONGO_SSL", "true")
        monkeypatch.setenv("MONGO_MAX_POOL_SIZE", "77")
        monkeypatch.setenv("MONGO_MIN_POOL_SIZE", "7")

        cfg = DatabaseConfig.from_env()
        assert cfg.host == "db.example.com"
        assert cfg.port == 27018
        assert cfg.database == "ims_prod"
        assert cfg.username == "ims"
        assert cfg.password == "s3cret"
        assert cfg.auth_source == "admin"
        assert cfg.ssl is True
        assert cfg.max_pool_size == 77
        assert cfg.min_pool_size == 7

    def test_from_env_defaults_when_unset(self, monkeypatch):
        for var in (
            "MONGO_HOST",
            "MONGO_PORT",
            "MONGO_DATABASE",
            "MONGO_USERNAME",
            "MONGO_PASSWORD",
            "MONGO_SSL",
            "MONGO_REPLICA_SET",
        ):
            monkeypatch.delenv(var, raising=False)
        cfg = DatabaseConfig.from_env()
        assert cfg.host == "localhost"
        assert cfg.port == 27017
        assert cfg.database == "ims_2_0"
        assert cfg.username is None
        assert cfg.ssl is False

    def test_get_uri_no_auth(self):
        cfg = DatabaseConfig(host="localhost", port=27017)
        uri = cfg.get_uri()
        assert uri.startswith("mongodb://localhost:27017?")
        # No credentials -> no userinfo and no authSource param.
        assert "@" not in uri
        assert "authSource" not in uri
        assert "maxPoolSize=50" in uri
        assert "minPoolSize=10" in uri
        assert "connectTimeoutMS=5000" in uri
        assert "serverSelectionTimeoutMS=5000" in uri

    def test_get_uri_with_auth_and_params(self):
        cfg = DatabaseConfig(
            host="mongo.internal",
            port=27017,
            username="ims",
            password="pw",
            auth_source="admin",
            replica_set="rs0",
            ssl=True,
        )
        uri = cfg.get_uri()
        assert uri.startswith("mongodb://ims:pw@mongo.internal:27017?")
        assert "authSource=admin" in uri
        assert "replicaSet=rs0" in uri
        assert "ssl=true" in uri

    def test_get_uri_authsource_omitted_without_username(self):
        # authSource is only emitted when a username is present.
        cfg = DatabaseConfig(host="h", auth_source="admin", username=None)
        assert "authSource" not in cfg.get_uri()


# ===========================================================================
# 3) INTEGRATION -- real pymongo against the live local Mongo, throwaway DB
# ===========================================================================
pytestmark_integration = pytest.mark.integration


@pytest.fixture(scope="module")
def live_db():
    """Real Mongo connection on a THROWAWAY database. Skips fail-soft if Mongo
    is unreachable so this is safe on CI/laptops without it.

    Tries MONGODB_URL (the task's documented var), then MONGODB_URI, then
    localhost. A short timeout keeps it from hanging. The whole db is dropped
    in teardown -- it never touches real data.
    """
    try:
        from pymongo import MongoClient
        from pymongo.errors import ServerSelectionTimeoutError, ConnectionFailure
    except ImportError:
        pytest.skip("pymongo unavailable")
        return

    uri = (
        os.getenv("MONGODB_URL")
        or os.getenv("MONGODB_URI")
        or "mongodb://localhost:27017"
    )
    client = None
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=2000)
        client.admin.command("ping")  # raises if unreachable
    except (ServerSelectionTimeoutError, ConnectionFailure, Exception):  # noqa: BLE001
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
        pytest.skip(f"Mongo unreachable at {uri}; skipping DB-layer integration tests")
        return

    db_name = f"ims_test_dblayer_{uuid.uuid4().hex[:8]}"
    db = client[db_name]
    try:
        yield db
    finally:
        try:
            client.drop_database(db_name)
        except Exception:  # noqa: BLE001
            pass
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass


@pytest.mark.integration
class TestLiveMongoRoundTrip:
    def test_insert_and_find(self, live_db):
        col = live_db["round_trip"]
        res = col.insert_one({"sku": "RB-2140", "qty": 4})
        assert res.inserted_id is not None
        found = col.find_one({"sku": "RB-2140"})
        assert found is not None
        assert found["qty"] == 4

    def test_insert_many_and_count(self, live_db):
        col = live_db["bulk"]
        col.insert_many([{"n": i} for i in range(5)])
        assert col.count_documents({}) == 5
        assert col.count_documents({"n": {"$gte": 3}}) == 2

    def test_update_and_operators_through_driver(self, live_db):
        col = live_db["upd"]
        col.insert_one({"_id": "u1", "count": 0, "events": []})
        col.update_one(
            {"_id": "u1"},
            {"$inc": {"count": 3}, "$push": {"events": "first"}},
        )
        doc = col.find_one({"_id": "u1"})
        assert doc["count"] == 3
        assert doc["events"] == ["first"]

    def test_unique_index_blocks_duplicate(self, live_db):
        """Build a UNIQUE index, then assert a duplicate insert raises
        DuplicateKeyError. This is the same DB-level backstop the app relies on
        for order_id / invoice_number / sku uniqueness.
        """
        from pymongo.errors import DuplicateKeyError

        col = live_db["uniq_demo"]
        col.create_index("code", unique=True, name="uniq_code")
        col.insert_one({"code": "ABC-001", "v": 1})

        with pytest.raises(DuplicateKeyError):
            col.insert_one({"code": "ABC-001", "v": 2})

        # The first row stands; the duplicate never landed.
        assert col.count_documents({"code": "ABC-001"}) == 1

    def test_sparse_unique_allows_multiple_missing_keys(self, live_db):
        """A SPARSE unique index (the pattern used for sku/barcode/order_number)
        must let many docs with the field ABSENT coexist while still blocking a
        real duplicate value.
        """
        from pymongo.errors import DuplicateKeyError

        col = live_db["sparse_demo"]
        col.create_index("barcode", unique=True, sparse=True, name="uniq_barcode_sparse")
        # Two docs with NO barcode -- allowed under sparse.
        col.insert_one({"name": "no-barcode-1"})
        col.insert_one({"name": "no-barcode-2"})
        # A real barcode value, then its duplicate -> rejected.
        col.insert_one({"name": "scanned", "barcode": "890123456"})
        with pytest.raises(DuplicateKeyError):
            col.insert_one({"name": "dupe-scan", "barcode": "890123456"})
        assert col.count_documents({}) == 3
