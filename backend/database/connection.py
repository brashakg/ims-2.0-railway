"""
IMS 2.0 - Database Connection Layer
====================================
MongoDB connection management with connection pooling
"""

from typing import Optional, Dict, Any
from datetime import datetime
import os

# MongoDB driver
try:
    from pymongo import MongoClient
    from pymongo.database import Database
    from pymongo.collection import Collection
    from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

    MONGO_AVAILABLE = True
except ImportError:
    MONGO_AVAILABLE = False
    MongoClient = None
    Database = None
    Collection = None


class DatabaseConfig:
    """Database configuration"""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 27017,
        database: str = "ims_2_0",
        username: str = None,
        password: str = None,
        auth_source: str = "admin",
        replica_set: str = None,
        ssl: bool = False,
        max_pool_size: int = 50,
        min_pool_size: int = 10,
        connect_timeout_ms: int = 5000,
        server_selection_timeout_ms: int = 5000,
    ):
        self.host = host
        self.port = port
        self.database = database
        self.username = username
        self.password = password
        self.auth_source = auth_source
        self.replica_set = replica_set
        self.ssl = ssl
        self.max_pool_size = max_pool_size
        self.min_pool_size = min_pool_size
        self.connect_timeout_ms = connect_timeout_ms
        self.server_selection_timeout_ms = server_selection_timeout_ms

    @classmethod
    def from_env(cls) -> "DatabaseConfig":
        """Create config from environment variables"""
        return cls(
            host=os.getenv("MONGO_HOST", "localhost"),
            port=int(os.getenv("MONGO_PORT", "27017")),
            database=os.getenv("MONGO_DATABASE", "ims_2_0"),
            username=os.getenv("MONGO_USERNAME"),
            password=os.getenv("MONGO_PASSWORD"),
            auth_source=os.getenv("MONGO_AUTH_SOURCE", "admin"),
            replica_set=os.getenv("MONGO_REPLICA_SET"),
            ssl=os.getenv("MONGO_SSL", "false").lower() == "true",
            max_pool_size=int(os.getenv("MONGO_MAX_POOL_SIZE", "50")),
            min_pool_size=int(os.getenv("MONGO_MIN_POOL_SIZE", "10")),
        )

    @classmethod
    def from_uri(cls, uri: str, database: str = "ims_2_0") -> "DatabaseConfig":
        """Create config from MongoDB URI"""
        config = cls()
        config._uri = uri
        config.database = database
        return config

    def get_uri(self) -> str:
        """Build MongoDB connection URI"""
        if hasattr(self, "_uri"):
            return self._uri

        if self.username and self.password:
            auth = f"{self.username}:{self.password}@"
        else:
            auth = ""

        uri = f"mongodb://{auth}{self.host}:{self.port}"

        params = []
        if self.auth_source and self.username:
            params.append(f"authSource={self.auth_source}")
        if self.replica_set:
            params.append(f"replicaSet={self.replica_set}")
        if self.ssl:
            params.append("ssl=true")
        params.append(f"maxPoolSize={self.max_pool_size}")
        params.append(f"minPoolSize={self.min_pool_size}")
        params.append(f"connectTimeoutMS={self.connect_timeout_ms}")
        params.append(f"serverSelectionTimeoutMS={self.server_selection_timeout_ms}")

        if params:
            uri += "?" + "&".join(params)

        return uri


class DatabaseConnection:
    """
    MongoDB Connection Manager
    Singleton pattern for connection pooling
    """

    _instance: Optional["DatabaseConnection"] = None
    _client: Optional[MongoClient] = None
    _db: Optional[Database] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        self._config: Optional[DatabaseConfig] = None
        self._connected = False

    def configure(self, config: DatabaseConfig):
        """Configure database connection"""
        self._config = config

    def connect(self) -> bool:
        """Establish database connection"""
        if not MONGO_AVAILABLE:
            print("[WARN] PyMongo not installed. Running in mock mode.")
            self._connected = False
            return False

        if self._connected and self._client:
            return True

        if not self._config:
            self._config = DatabaseConfig.from_env()

        try:
            self._client = MongoClient(self._config.get_uri())
            # Test connection
            self._client.admin.command("ping")
            self._db = self._client[self._config.database]
            self._connected = True
            print(f"[OK] Connected to MongoDB: {self._config.database}")
            return True
        except (ConnectionFailure, ServerSelectionTimeoutError) as e:
            print(f"[ERROR] MongoDB connection failed: {e}")
            self._connected = False
            return False
        except Exception as e:
            print(f"[ERROR] Unexpected error: {e}")
            self._connected = False
            return False

    def _safe_index(self, collection, keys, **kwargs):
        """Build ONE index in isolation so a single dirty collection can never
        abort the others.

        Before this, ensure_indexes wrapped ALL ~80 create_index calls in one
        try/except, so the FIRST failing index (e.g. a unique index rejected by
        a legacy duplicate/null key) aborted every later build -- one dirty
        collection silently left the whole database unindexed. This actually bit
        prod: orders.order_id was null on 322/343 rows, so its unique build threw
        and took out every index AFTER it, including the GST uniq_invoice_number
        backstop. Now each index stands or falls alone.

        Fail LOUDLY (log the skip with the index name + error) per SYSTEM_INTENT
        'Fail Loudly', but keep going. Idempotent: MongoDB no-ops an index that
        already exists. Returns True on success/already-exists, False on a skip.
        """
        try:
            collection.create_index(keys, **kwargs)
            self._index_ok += 1
            return True
        except Exception as exc:  # noqa: BLE001 -- one bad index must not abort the rest
            label = kwargs.get("name") or keys
            self._index_skipped.append(f"{collection.name}.{label}")
            print(f"[WARN] index build skipped: {collection.name}.{label}: {exc}")
            return False

    def ensure_indexes(self):
        """Create MongoDB indexes for query performance.

        Safe to call multiple times -- MongoDB skips existing indexes. Every
        index is built in ISOLATION via _safe_index, so a dirty collection only
        loses its OWN constrained index, never the rest of the database's. A
        final summary line reports how many built vs were skipped (fail-loud).
        """
        if not self._connected or self._db is None:
            return

        self._index_ok = 0
        self._index_skipped = []
        si = self._safe_index

        # Orders -- most queried collection
        orders = self._db["orders"]
        si(orders, "order_id", unique=True, background=True)
        si(orders, "store_id", background=True)
        si(orders, "customer_id", background=True)
        si(orders, [("store_id", 1), ("status", 1)], background=True)
        si(orders, [("store_id", 1), ("created_at", -1)], background=True)
        si(orders, "order_number", unique=True, sparse=True, background=True)
        si(orders, [("store_id", 1), ("balance_due", 1)], background=True)
        # GST invoice serial (Rule 46(b)): UNIQUE so a duplicate invoice number
        # can never be written -- the per-(prefix, FY) atomic counter already
        # hands out unique serials; this is the DB-level backstop that makes a
        # duplicate physically impossible. PARTIAL so the many legacy / DRAFT
        # orders with NO invoice_number (field absent) aren't indexed.
        si(
            orders,
            "invoice_number",
            unique=True,
            partialFilterExpression={"invoice_number": {"$type": "string"}},
            name="uniq_invoice_number",
            background=True,
        )

        # Customers
        customers = self._db["customers"]
        si(customers, "customer_id", unique=True, background=True)
        si(customers, "mobile", unique=True, sparse=True, background=True)
        si(customers, "email", sparse=True, background=True)
        si(customers, [("store_ids", 1)], background=True)
        si(customers, "name", background=True)

        # Products / Stock
        products = self._db["products"]
        si(products, "product_id", unique=True, background=True)
        si(products, "sku", unique=True, sparse=True, background=True)
        # UNIQUE + sparse: a scan-to-sell product barcode must resolve to exactly
        # one product (two sharing a barcode make a POS scan ambiguous). Sparse so
        # products WITHOUT a master barcode are exempt. The PUT /products
        # validation rejects dupes before the write; this is the DB-level backstop.
        si(products, "barcode", unique=True, sparse=True, background=True)
        si(products, [("store_id", 1), ("category", 1)], background=True)

        # Users
        users = self._db["users"]
        si(users, "user_id", unique=True, background=True)
        si(users, "username", unique=True, background=True)
        si(users, "email", unique=True, sparse=True, background=True)

        # Workshop jobs
        jobs = self._db["workshop_jobs"]
        si(jobs, "job_id", unique=True, background=True)
        si(jobs, "job_number", unique=True, background=True)
        si(jobs, [("store_id", 1), ("status", 1)], background=True)

        # Attendance -- one row PER (employee, day). The UNIQUE index on
        # (employee_id, date) is the DB-level backstop against the "same user
        # recorded twice" bug: check-in / mark de-dupe via find-then-update keyed
        # on (employee_id, date) where `date` is the date-only ISO STRING, and
        # this index makes a duplicate physically impossible even under a race or
        # a stray datetime-vs-string `date` write. Mirrors schemas.ATTENDANCE.
        attendance = self._db["attendance"]
        si(
            attendance,
            [("employee_id", 1), ("date", 1)],
            unique=True,
            name="uniq_employee_date",
            background=True,
        )
        si(attendance, [("store_id", 1), ("date", 1)], background=True)
        si(attendance, [("date", -1)], background=True)

        # Prescriptions
        rx = self._db["prescriptions"]
        si(rx, "prescription_id", unique=True, background=True)
        si(rx, "customer_id", background=True)
        si(rx, [("store_id", 1), ("created_at", -1)], background=True)

        # Stores
        stores = self._db["stores"]
        si(stores, "store_id", unique=True, background=True)
        si(stores, "store_code", unique=True, background=True)

        # Walkouts (Pune Incentive Module i -- Phase 1)
        walkouts = self._db["walkouts"]
        si(walkouts, "walkout_id", unique=True, background=True)
        si(walkouts, [("store_id", 1), ("date_str", -1)], background=True)
        si(
            walkouts,
            [("store_id", 1), ("sales_person_id", 1), ("date_str", -1)],
            background=True,
        )
        si(walkouts, "mobile", background=True)
        si(walkouts, "customer_id", background=True)

        # Walk-in counters (Pune Incentive Module i -- Phase 4)
        walkins = self._db["walk_in_counters"]
        si(walkins, [("store_id", 1), ("date_str", -1)], background=True)

        # Points log (Pune Incentive Module ii). The unique partial index on
        # (store, date_str, staff) where deleted_at is null is the DB-level
        # enforcement of "refuse second save" -- DELETE the existing row first,
        # then re-POST.
        points = self._db["points_log"]
        si(
            points,
            [("store_id", 1), ("date_str", -1), ("staff_id", 1)],
            unique=True,
            partialFilterExpression={"deleted_at": None},
            background=True,
        )
        si(points, [("store_id", 1), ("staff_id", 1), ("date_str", -1)], background=True)
        si(points, [("store_id", 1), ("deleted_at", 1)], background=True)

        settings = self._db["incentive_settings"]
        si(settings, "store_id", unique=True, background=True)

        # Per-store-per-month manual incentive inputs (last_year_sale, etc.)
        inputs = self._db["incentive_inputs"]
        si(
            inputs,
            [("store_id", 1), ("year", 1), ("month", 1)],
            unique=True,
            background=True,
        )

        # Payout snapshots (Pune Incentive Module iii). Multiple DRAFTs allowed;
        # only one LOCKED per (store, year, month).
        payouts = self._db["payout_snapshots"]
        si(
            payouts,
            [("store_id", 1), ("year", 1), ("month", 1)],
            unique=True,
            partialFilterExpression={"status": "LOCKED"},
            background=True,
        )
        si(payouts, [("store_id", 1), ("year", -1), ("month", -1)], background=True)

        # Audit logs (SYSTEM_INTENT 10 -- immutable, hash-chained trail). The
        # UNIQUE index on `seq` is the DB-level guard against two writers ever
        # committing the same sequence number. SPARSE on purpose: the chain is
        # fail-soft and writes UNCHAINED rows (no `seq`) when the head can't be
        # advanced -- sparse excludes those so multiple seq-less rows coexist
        # instead of colliding on a single null key.
        audit = self._db["audit_logs"]
        si(audit, "seq", unique=True, sparse=True, background=True)
        si(audit, "log_id", unique=True, sparse=True, background=True)
        si(audit, [("timestamp", -1)], background=True)
        si(audit, [("user_id", 1), ("timestamp", -1)], background=True)
        si(audit, [("store_id", 1), ("timestamp", -1)], background=True)
        si(audit, [("action", 1), ("timestamp", -1)], background=True)
        si(audit, [("entity_type", 1), ("entity_id", 1)], background=True)
        si(audit, [("severity", 1), ("timestamp", -1)], background=True)

        # Audit chain head -- single-document control row keyed by _id
        # ("primary"). We index `seq` so the guarded head-advance reads stay cheap.
        audit_head = self._db["audit_chain_head"]
        si(audit_head, "seq", background=True)

        # Catalog variants (BVI Phase 1 -- Online Store module). `sku` is the
        # primary identity + the physical-stock join handle; the Shopify GID +
        # store_barcode reverse-lookups are UNIQUE so a Shopify variant / physical
        # unit resolves to exactly one IMS variant. All SPARSE so partial imports
        # (rows not yet mapped to Shopify) aren't constrained on absent keys.
        catalog_variants = self._db["catalog_variants"]
        si(catalog_variants, "sku", unique=True, sparse=True, background=True)
        si(catalog_variants, "parent_product_id", sparse=True, background=True)
        si(catalog_variants, "shopify_variant_id", unique=True, sparse=True, background=True)
        si(catalog_variants, "store_barcode", unique=True, sparse=True, background=True)

        # E-commerce collections (BVI Phase 2). `handle` is the unique storefront
        # slug + idempotent re-import key; `shopify_collection_id` is the
        # Shopify-side reverse-lookup. Both UNIQUE SPARSE so a PUSH-DARK row not
        # yet mapped to Shopify isn't constrained on the missing key.
        ecom_collections = self._db["ecom_collections"]
        si(ecom_collections, "handle", unique=True, sparse=True, background=True)
        si(ecom_collections, "shopify_collection_id", unique=True, sparse=True, background=True)
        si(ecom_collections, "auto_source", sparse=True, background=True)
        si(ecom_collections, "category_anchor", sparse=True, background=True)

        # E-commerce menus / mega-menu (BVI Phase 3). `handle` is the unique menu
        # slug + idempotent re-import key; `shopify_menu_id` is the Shopify-side
        # reverse-lookup. Both UNIQUE SPARSE for the same PUSH-DARK reason.
        ecom_menus = self._db["ecom_menus"]
        si(ecom_menus, "handle", unique=True, sparse=True, background=True)
        si(ecom_menus, "shopify_menu_id", unique=True, sparse=True, background=True)

        # Product images / image design queue (BVI Phase 4). `product_id` backs a
        # product's gallery + per-product queue view; `status` backs the
        # design-queue filter; `assigned_to` backs a designer's "my queue" (SPARSE
        # so unassigned rows aren't indexed on null). No unique constraint: a
        # product legitimately has many images.
        product_images = self._db["product_images"]
        si(product_images, "product_id", background=True)
        si(product_images, "status", background=True)
        si(product_images, "assigned_to", sparse=True, background=True)

        # Vendor bills / purchase invoices (AP + ITC source of truth). `bill_id`
        # is the canonical id (unique, sparse so legacy rows lacking it don't
        # collide on null); (vendor_id, bill_number) backs the per-vendor
        # duplicate-invoice lookup (NON-unique: the dup guard lives in app code
        # because legacy prod data may already hold a duplicate a unique index
        # would reject at build time); `po_id` / `grn_id` back the create-from-GRN
        # + PO/GRN back-links (sparse).
        vendor_bills = self._db["vendor_bills"]
        si(vendor_bills, "bill_id", unique=True, sparse=True, background=True)
        si(vendor_bills, [("vendor_id", 1), ("bill_number", 1)], background=True)
        si(vendor_bills, "po_id", sparse=True, background=True)
        si(vendor_bills, "grn_id", sparse=True, background=True)
        si(vendor_bills, "status", background=True)
        si(vendor_bills, [("bill_date", -1)], background=True)

        # health_checks (SENTINEL telemetry) -- a row every ~60s tick, so it grows
        # UNBOUNDED. A TTL index auto-expires rows >14 days old server-side.
        # Building a TTL index needs >=500MB free disk; on a small/full volume the
        # build raises OutOfDiskSpace -- isolated by _safe_index so it can't abort
        # the others. Until it can build, the SENTINEL in-tick prune bounds it.
        si(
            self._db["health_checks"],
            "timestamp",
            expireAfterSeconds=14 * 24 * 60 * 60,
            name="ttl_timestamp",
            background=True,
        )

        if self._index_skipped:
            print(
                f"[INDEXES] built/verified {self._index_ok}, "
                f"SKIPPED {len(self._index_skipped)} (dirty data?): "
                + ", ".join(self._index_skipped)
            )
        else:
            print(f"[OK] MongoDB indexes ensured ({self._index_ok} built/verified)")

    def disconnect(self):
        """Close database connection.

        No-op under pytest. The test suite shares ONE app-level Mongo client for
        the whole session (tests/conftest.py); if any TestClient lifespan
        teardown -- or a duplicate-module import path that the close_db neuter
        can't reach -- closed it mid-session, every later test 500'd with
        'Cannot use MongoClient after close'. pytest sets PYTEST_CURRENT_TEST for
        the duration of the run and the process exit reclaims the client.
        Production is unaffected (the var is unset there), so the real shutdown
        path still closes the connection cleanly.
        """
        import os

        if os.getenv("PYTEST_CURRENT_TEST"):
            return
        if self._client:
            self._client.close()
            self._client = None
            self._db = None
            self._connected = False
            print("[DB] Disconnected from MongoDB")

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def db(self) -> Optional[Database]:
        """Get database instance"""
        if not self._connected:
            self.connect()
        return self._db

    def get_collection(self, name: str) -> Optional[Collection]:
        """Get a collection by name"""
        if self.db is not None:
            return self.db[name]
        return None

    # Collection shortcuts
    @property
    def users(self) -> Optional[Collection]:
        return self.get_collection("users")

    @property
    def stores(self) -> Optional[Collection]:
        return self.get_collection("stores")

    @property
    def products(self) -> Optional[Collection]:
        return self.get_collection("products")

    @property
    def stock_units(self) -> Optional[Collection]:
        return self.get_collection("stock_units")

    @property
    def orders(self) -> Optional[Collection]:
        return self.get_collection("orders")

    @property
    def customers(self) -> Optional[Collection]:
        return self.get_collection("customers")

    @property
    def prescriptions(self) -> Optional[Collection]:
        return self.get_collection("prescriptions")

    @property
    def vendors(self) -> Optional[Collection]:
        return self.get_collection("vendors")

    @property
    def purchase_orders(self) -> Optional[Collection]:
        return self.get_collection("purchase_orders")

    @property
    def grns(self) -> Optional[Collection]:
        return self.get_collection("grns")

    @property
    def tasks(self) -> Optional[Collection]:
        return self.get_collection("tasks")

    @property
    def expenses(self) -> Optional[Collection]:
        return self.get_collection("expenses")

    @property
    def advances(self) -> Optional[Collection]:
        return self.get_collection("advances")

    @property
    def audit_logs(self) -> Optional[Collection]:
        return self.get_collection("audit_logs")

    @property
    def notifications(self) -> Optional[Collection]:
        return self.get_collection("notifications")

    @property
    def eye_test_queue(self) -> Optional[Collection]:
        return self.get_collection("eye_test_queue")

    @property
    def eye_tests(self) -> Optional[Collection]:
        return self.get_collection("eye_tests")


# Global instance
db = DatabaseConnection()


def get_db() -> DatabaseConnection:
    """Get database connection instance"""
    return db


def init_db(config: DatabaseConfig = None) -> bool:
    """Initialize database connection"""
    if config:
        db.configure(config)
    return db.connect()


def close_db():
    """Close database connection"""
    db.disconnect()


# For testing without MongoDB
class MockCursor:
    """Mock cursor for chaining operations like MongoDB"""

    def __init__(self, data: list):
        self._data = data
        self._sort_key = None
        self._skip = 0
        self._limit = None

    def sort(self, sort_spec, direction=None):
        """Sort the results. Supports both pymongo signatures:
        sort("field", -1)  and  sort([("field", -1), ...]).
        Sorts by the first key only (sufficient for mock use)."""
        try:
            if direction is not None:
                # sort("field", -1) form
                field, dir_ = sort_spec, direction
            elif (
                isinstance(sort_spec, (list, tuple))
                and sort_spec
                and isinstance(sort_spec[0], tuple)
            ):
                field, dir_ = sort_spec[0]
            elif isinstance(sort_spec, str):
                field, dir_ = sort_spec, 1
            else:
                return self
            reverse = dir_ == -1
            self._data = sorted(
                self._data,
                key=lambda x: (x.get(field) is None, x.get(field, "")),
                reverse=reverse,
            )
        except Exception:
            pass
        return self

    def skip(self, n: int):
        """Skip n documents"""
        self._skip = n
        return self

    def limit(self, n: int):
        """Limit to n documents"""
        self._limit = n
        return self

    def __iter__(self):
        data = self._data[self._skip :]
        if self._limit:
            data = data[: self._limit]
        return iter(data)

    def __list__(self):
        return list(self.__iter__())


class MockCollection:
    """Mock collection for testing without MongoDB"""

    def __init__(self, name: str):
        self.name = name
        self._data: Dict[str, Dict] = {}

    def insert_one(self, document: Dict) -> Any:
        doc_id = document.get("_id") or str(len(self._data) + 1)
        document["_id"] = doc_id
        # Make a copy to avoid mutation issues
        self._data[doc_id] = dict(document)
        return type("obj", (object,), {"inserted_id": doc_id})()

    def insert_many(self, documents: list) -> Any:
        inserted_ids = []
        for doc in documents:
            result = self.insert_one(doc)
            inserted_ids.append(result.inserted_id)
        return type("obj", (object,), {"inserted_ids": inserted_ids})()

    def _matches_filter(self, doc: Dict, filter: Dict) -> bool:
        """Check if document matches the filter"""
        if not filter:
            return True

        for key, value in filter.items():
            if key == "$or":
                # Handle $or operator
                if not any(self._matches_filter(doc, cond) for cond in value):
                    return False
            elif key == "$and":
                # Handle $and operator
                if not all(self._matches_filter(doc, cond) for cond in value):
                    return False
            elif isinstance(value, dict):
                # Handle operators like $regex, $gt, $lt, etc.
                doc_value = doc.get(key, "")
                for op, op_value in value.items():
                    if op == "$regex":
                        import re

                        flags = re.IGNORECASE if value.get("$options") == "i" else 0
                        if not re.search(op_value, str(doc_value), flags):
                            return False
                    elif op == "$gt":
                        if not (doc_value > op_value):
                            return False
                    elif op == "$lt":
                        if not (doc_value < op_value):
                            return False
                    elif op == "$gte":
                        if not (doc_value >= op_value):
                            return False
                    elif op == "$lte":
                        if not (doc_value <= op_value):
                            return False
                    elif op == "$in":
                        if doc_value not in op_value:
                            return False
                    elif op == "$ne":
                        if doc_value == op_value:
                            return False
            else:
                # Direct equality check
                if doc.get(key) != value:
                    return False
        return True

    def find_one(self, filter: Dict) -> Optional[Dict]:
        if not filter:
            return next(iter(self._data.values()), None)

        # Direct _id lookup
        if "_id" in filter and len(filter) == 1:
            return self._data.get(filter["_id"])

        for doc in self._data.values():
            if self._matches_filter(doc, filter):
                return doc
        return None

    def find(self, filter: Dict = None, projection: Dict = None) -> MockCursor:
        # Accept (and ignore) a projection arg so callers using the real
        # pymongo signature find(filter, projection) — e.g. {"_id": 0} —
        # don't blow up in no-Mongo mode (was: TASKMASTER find() error).
        if not filter:
            return MockCursor(list(self._data.values()))

        results = [
            doc for doc in self._data.values() if self._matches_filter(doc, filter)
        ]
        return MockCursor(results)

    def update_one(self, filter: Dict, update: Dict) -> Any:
        doc = self.find_one(filter)
        if doc:
            if "$set" in update:
                doc.update(update["$set"])
            if "$inc" in update:
                for field, amount in update["$inc"].items():
                    doc[field] = doc.get(field, 0) + amount
            if "$push" in update:
                for field, value in update["$push"].items():
                    if field not in doc:
                        doc[field] = []
                    doc[field].append(value)
            return type("obj", (object,), {"modified_count": 1})()
        return type("obj", (object,), {"modified_count": 0})()

    def update_many(self, filter: Dict, update: Dict) -> Any:
        count = 0
        for doc in self._data.values():
            if self._matches_filter(doc, filter):
                if "$set" in update:
                    doc.update(update["$set"])
                if "$inc" in update:
                    for field, amount in update["$inc"].items():
                        doc[field] = doc.get(field, 0) + amount
                count += 1
        return type("obj", (object,), {"modified_count": count})()

    def delete_one(self, filter: Dict) -> Any:
        doc = self.find_one(filter)
        if doc and doc.get("_id") in self._data:
            del self._data[doc["_id"]]
            return type("obj", (object,), {"deleted_count": 1})()
        return type("obj", (object,), {"deleted_count": 0})()

    def delete_many(self, filter: Dict = None) -> Any:
        if not filter:
            count = len(self._data)
            self._data.clear()
            return type("obj", (object,), {"deleted_count": count})()

        to_delete = [
            doc["_id"]
            for doc in self._data.values()
            if self._matches_filter(doc, filter)
        ]
        for doc_id in to_delete:
            del self._data[doc_id]
        return type("obj", (object,), {"deleted_count": len(to_delete)})()

    def count_documents(self, filter: Dict = None) -> int:
        if not filter:
            return len(self._data)
        return len(
            [doc for doc in self._data.values() if self._matches_filter(doc, filter)]
        )

    def aggregate(self, pipeline: list) -> list:
        """Basic aggregation support - just returns all documents for now"""
        # This is a simplified implementation
        return list(self._data.values())


class MockDatabase:
    """Mock database for testing without MongoDB"""

    def __init__(self):
        self._collections: Dict[str, MockCollection] = {}

    def __getitem__(self, name: str) -> MockCollection:
        if name not in self._collections:
            self._collections[name] = MockCollection(name)
        return self._collections[name]

    def list_collection_names(self) -> list:
        return list(self._collections.keys())


def get_mock_db() -> MockDatabase:
    """Get mock database for testing"""
    return MockDatabase()


# Seeded Mock Database singleton
_seeded_mock_db = None


def get_seeded_mock_db() -> MockDatabase:
    """Get a mock database pre-seeded with sample data"""
    global _seeded_mock_db
    if _seeded_mock_db is None:
        _seeded_mock_db = MockDatabase()
        try:
            from .seed_data import get_all_seed_data

            seed_data = get_all_seed_data()
            for collection_name, data in seed_data.items():
                collection = _seeded_mock_db[collection_name]
                for doc in data:
                    collection.insert_one(doc)
            print("[OK] Seeded mock database with sample data")
        except ImportError as e:
            print(f"[WARN] Could not load seed data: {e}")
    return _seeded_mock_db


class SeededDatabaseConnection:
    """
    Database connection that falls back to seeded mock data
    when MongoDB is not available
    """

    _instance = None
    _mock_db = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        self._real_db = db  # Use the real database connection
        self._use_mock = False

    @property
    def is_connected(self) -> bool:
        if self._real_db.is_connected:
            return True
        # If not connected to real DB, use mock
        self._use_mock = True
        return True  # Mock is always "connected"

    @property
    def db(self):
        if self._real_db.is_connected:
            return self._real_db.db
        return get_seeded_mock_db()

    def get_collection(self, name: str):
        if self._real_db.is_connected:
            return self._real_db.get_collection(name)
        return get_seeded_mock_db()[name]

    # Collection shortcuts
    @property
    def users(self):
        return self.get_collection("users")

    @property
    def stores(self):
        return self.get_collection("stores")

    @property
    def products(self):
        return self.get_collection("products")

    @property
    def stock_units(self):
        return self.get_collection("stock_units")

    @property
    def orders(self):
        return self.get_collection("orders")

    @property
    def customers(self):
        return self.get_collection("customers")

    @property
    def prescriptions(self):
        return self.get_collection("prescriptions")

    @property
    def vendors(self):
        return self.get_collection("vendors")

    @property
    def purchase_orders(self):
        return self.get_collection("purchase_orders")

    @property
    def grns(self):
        return self.get_collection("grns")

    @property
    def tasks(self):
        return self.get_collection("tasks")

    @property
    def expenses(self):
        return self.get_collection("expenses")

    @property
    def advances(self):
        return self.get_collection("advances")

    @property
    def audit_logs(self):
        return self.get_collection("audit_logs")

    @property
    def notifications(self):
        return self.get_collection("notifications")

    @property
    def eye_test_queue(self):
        return self.get_collection("eye_test_queue")

    @property
    def eye_tests(self):
        return self.get_collection("eye_tests")


# Seeded database instance
seeded_db = SeededDatabaseConnection()


def get_seeded_db() -> SeededDatabaseConnection:
    """Get database connection with fallback to seeded mock data"""
    return seeded_db


if __name__ == "__main__":
    print("=" * 60)
    print("IMS 2.0 DATABASE CONNECTION TEST")
    print("=" * 60)

    # Test mock database
    print("\n📦 Testing Mock Database")
    mock_db = get_mock_db()

    # Insert
    users = mock_db["users"]
    result = users.insert_one({"name": "Rahul", "role": "SALES_STAFF"})
    print(f"  Inserted: {result.inserted_id}")

    # Find
    user = users.find_one({"name": "Rahul"})
    print(f"  Found: {user}")

    # Update
    users.update_one({"name": "Rahul"}, {"$set": {"role": "STORE_MANAGER"}})
    user = users.find_one({"name": "Rahul"})
    print(f"  Updated: {user}")

    # Count
    count = users.count_documents({})
    print(f"  Count: {count}")

    print("\n" + "=" * 60)
