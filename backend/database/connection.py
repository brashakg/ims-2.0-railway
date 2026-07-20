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

    def ensure_indexes(self):
        """Create MongoDB indexes for query performance.

        Safe to call multiple times -- MongoDB skips existing indexes. Each index
        is built in its OWN try/except (via `_idx`) so a single failing index
        (e.g. a UNIQUE index blocked by pre-existing duplicate/null data, or a TTL
        index that needs free disk) can NEVER abort the rest. PREVIOUSLY all of
        these shared one outer try, so the FIRST failure (historically the
        orders.order_id unique build on legacy null order_ids) silently took out
        every later collection's indexes too. Failures are collected and logged
        once at the end; this method never raises.
        """
        if not self._connected or self._db is None:
            return

        failures: list = []

        def _idx(coll_name, keys, **kw):
            try:
                self._db[coll_name].create_index(keys, **kw)
            except Exception as e:  # noqa: BLE001
                label = kw.get("name") or (keys if isinstance(keys, str) else str(keys))
                failures.append("%s/%s: %s" % (coll_name, label, str(e)[:160]))

        # Orders -- most queried collection.
        _idx("orders", "order_id", unique=True, background=True)
        _idx("orders", "store_id", background=True)
        _idx("orders", "customer_id", background=True)
        _idx("orders", "salesperson_id", background=True)
        _idx("orders", [("store_id", 1), ("status", 1)], background=True)
        _idx("orders", [("store_id", 1), ("created_at", -1)], background=True)
        # F34 target ticker: the MTD-range scan filters {created_at: {$gte:
        # month_start}, status: {$nin:[...]}, store_id}. The existing
        # {store_id, created_at:-1} is DESC and store-first; this ASC
        # created_at-leading compound serves the open-ended >= month bound.
        _idx(
            "orders",
            [("created_at", 1), ("status", 1), ("store_id", 1)],
            background=True,
        )
        _idx("orders", [("salesperson_id", 1), ("created_at", -1)], background=True)
        # Collections movement analytics: every per-collection sales aggregation
        # matches {items.product_id $in members, created_at >= cutoff} -- the
        # multikey items index is what keeps it off a collection scan. Also
        # serves the non-moving-stock report's per-product last-sold lookups.
        _idx(
            "orders",
            [("items.product_id", 1), ("created_at", -1)],
            background=True,
        )
        _idx("orders", "order_number", unique=True, sparse=True, background=True)
        _idx("orders", [("store_id", 1), ("balance_due", 1)], background=True)
        # GST invoice serial (Rule 46(b)): UNIQUE backstop so a duplicate invoice
        # number can never be written. PARTIAL so legacy / DRAFT orders with no
        # invoice_number aren't indexed and can't collide on a missing value.
        _idx(
            "orders",
            "invoice_number",
            unique=True,
            partialFilterExpression={"invoice_number": {"$type": "string"}},
            name="uniq_invoice_number",
            background=True,
        )
        # F24 conversion dashboard: the optometrist->retail join pulls a
        # customer's orders by {customer_id, created_at}. The existing
        # customer_id single-field index doesn't cover the created_at window
        # bound; this compound serves the $in(customer_ids)+date-range lookup.
        _idx("orders", [("customer_id", 1), ("created_at", -1)], background=True)

        # Customers
        _idx("customers", "customer_id", unique=True, background=True)
        _idx("customers", "mobile", unique=True, sparse=True, background=True)
        _idx("customers", "email", sparse=True, background=True)
        _idx("customers", [("store_ids", 1)], background=True)
        _idx("customers", "name", background=True)

        # Products / Stock. barcode is UNIQUE+sparse: a scan-to-sell barcode must
        # resolve to exactly one product; sparse exempts products without one.
        _idx("products", "product_id", unique=True, background=True)
        _idx("products", "sku", unique=True, sparse=True, background=True)
        _idx("products", "barcode", unique=True, sparse=True, background=True)
        # Hub Phase 6: brand+model+colour(+size) identity is now a UNIQUE backstop
        # for the Phase-1 duplicate guard -- the race-safe arm behind the
        # check-then-write pre-check (a concurrent create that slips the pre-check
        # hits this index -> DuplicateKeyError -> create_product re-queries -> 409).
        # Flipped to unique now that born-DRAFT + size-in-identity + the dedupe-prep
        # data hygiene shipped (a clean/fresh slate has no residual identity dups).
        # sparse: products without an identity_key (e.g. SERVICES) are exempt; _idx
        # is fail-soft so any residual dup can't abort startup.
        _idx("products", "identity_key", unique=True, sparse=True, background=True)
        _idx("products", "is_active", background=True)
        _idx("products", [("store_id", 1), ("category", 1)], background=True)
        # Prefix-search indexes for the searchable fields (brand, model, sku, variant).
        # The search() method now anchors regex patterns with ^, enabling MongoDB to
        # use these indexes instead of full collection scans. Compound indexes on each
        # field + is_active for the most common filter pattern.
        _idx("products", [("brand", 1), ("is_active", 1)], background=True)
        _idx("products", [("model", 1), ("is_active", 1)], background=True)
        _idx("products", [("variant", 1), ("is_active", 1)], background=True)
        # PM (N5) product-master: pim_product_id back-link (sparse FK lookup),
        # sku_prefix, and the 5-field dedupe grid. Declared in schemas.py but only
        # created here (ensure_indexes is the live path; collMod/run_migrations isn't
        # wired), so without these the PM FK lookups + dedupe collection-scan.
        _idx("products", [("pim_product_id", 1)], sparse=True, background=True)
        _idx("products", [("sku_prefix", 1)], background=True)
        _idx(
            "products",
            [("category", 1), ("brand", 1), ("model", 1), ("color", 1), ("size", 1)],
            background=True,
        )
        # Hub Phase 3: the vendor SKU-alias flywheel. One alias per
        # (vendor_id, vendor_sku) -- a unique index makes the import LINK / GRN
        # upserts idempotent under concurrency (no dup aliases that would make a
        # vendor SKU resolve ambiguously). Fail-soft via _idx if pre-existing dup
        # rows block the build.
        _idx(
            "vendor_sku_aliases",
            [("vendor_id", 1), ("vendor_sku", 1)],
            unique=True,
            background=True,
        )

        # Stock units: composite indexes for inventory ledger queries.
        # (store_id, status) supports the $match stage in _build_store_ledger.
        # (product_id, store_id, status) covers both filtering + grouping in the
        # aggregation for per-product rolled-up stock lookups across the catalog.
        _idx("stock_units", [("store_id", 1), ("status", 1)], background=True)
        _idx(
            "stock_units",
            [("product_id", 1), ("store_id", 1), ("status", 1)],
            background=True,
        )
        # F6 per-unit SERIAL tracking: a serialized high-value unit (hearing aid,
        # luxury frame/watch) carries a UNIQUE serial captured at stock-in. PARTIAL
        # so the millions of NON-serialized stock_units (no `serial` field) aren't
        # indexed and can't collide on a missing value -- only documents whose
        # `serial` is a string are constrained. This index is the real race
        # backstop behind serial_tracking.capture_serial: two concurrent intakes of
        # the same serial -> exactly one insert wins, the other DuplicateKeyErrors.
        _idx(
            "stock_units",
            "serial",
            unique=True,
            partialFilterExpression={"serial": {"$type": "string"}},
            name="uniq_stock_unit_serial",
            background=True,
        )

        # Users
        _idx("users", "user_id", unique=True, background=True)
        _idx("users", "username", unique=True, background=True)
        _idx("users", "email", unique=True, sparse=True, background=True)

        # Workshop jobs
        _idx("workshop_jobs", "job_id", unique=True, background=True)
        _idx("workshop_jobs", "job_number", unique=True, background=True)
        _idx("workshop_jobs", [("store_id", 1), ("status", 1)], background=True)

        # Attendance -- one row PER (employee, day). The UNIQUE (employee_id, date)
        # index is the DB-level backstop against the "same user recorded twice"
        # bug. Mirrors schemas.ATTENDANCE. (Pre-existing duplicate rows must be
        # de-duped before this can build; failure here no longer blocks the rest.)
        _idx(
            "attendance",
            [("employee_id", 1), ("date", 1)],
            unique=True,
            name="uniq_employee_date",
            background=True,
        )
        _idx("attendance", [("store_id", 1), ("date", 1)], background=True)
        _idx("attendance", [("date", -1)], background=True)

        # Prescriptions
        _idx("prescriptions", "prescription_id", unique=True, background=True)
        _idx("prescriptions", "customer_id", background=True)
        _idx("prescriptions", [("store_id", 1), ("created_at", -1)], background=True)

        # Eye tests (F24 conversion dashboard). The per-optometrist scorecard
        # filters {optometrist_id, test_date}; the store-scope conversion join
        # filters {store_id, test_date, status:COMPLETED}.
        _idx("eye_tests", [("optometrist_id", 1), ("test_date", 1)], background=True)
        _idx(
            "eye_tests",
            [("store_id", 1), ("test_date", 1), ("status", 1)],
            background=True,
        )

        # Stores
        _idx("stores", "store_id", unique=True, background=True)
        _idx("stores", "store_code", unique=True, background=True)

        # Walkouts (Pune Incentive Module i)
        _idx("walkouts", "walkout_id", unique=True, background=True)
        _idx("walkouts", [("store_id", 1), ("date_str", -1)], background=True)
        _idx(
            "walkouts",
            [("store_id", 1), ("sales_person_id", 1), ("date_str", -1)],
            background=True,
        )
        _idx("walkouts", "mobile", background=True)
        _idx("walkouts", "customer_id", background=True)
        _idx("walk_in_counters", [("store_id", 1), ("date_str", -1)], background=True)

        # Points log (Pune Incentive Module ii). UNIQUE partial on
        # (store, date_str, staff) where deleted_at is null = "refuse second save".
        _idx(
            "points_log",
            [("store_id", 1), ("date_str", -1), ("staff_id", 1)],
            unique=True,
            partialFilterExpression={"deleted_at": None},
            background=True,
        )
        _idx(
            "points_log",
            [("store_id", 1), ("staff_id", 1), ("date_str", -1)],
            background=True,
        )
        _idx("points_log", [("store_id", 1), ("deleted_at", 1)], background=True)
        _idx("incentive_settings", "store_id", unique=True, background=True)
        _idx(
            "incentive_inputs",
            [("store_id", 1), ("year", 1), ("month", 1)],
            unique=True,
            background=True,
        )

        # Payout snapshots (Pune Incentive Module iii). One LOCKED per (store,
        # year, month); multiple DRAFTs allowed.
        _idx(
            "payout_snapshots",
            [("store_id", 1), ("year", 1), ("month", 1)],
            unique=True,
            partialFilterExpression={"status": "LOCKED"},
            background=True,
        )
        _idx(
            "payout_snapshots",
            [("store_id", 1), ("year", -1), ("month", -1)],
            background=True,
        )

        # Product-Incentive (Kicker) log (SC). Monthly rollup + idempotency.
        _idx(
            "product_incentive_log",
            [("store_id", 1), ("date_str", -1), ("staff_id", 1)],
            background=True,
        )
        _idx(
            "product_incentive_log",
            [("store_id", 1), ("ym", 1), ("staff_id", 1)],
            background=True,
        )
        # Idempotency: one kicker per (order_id, sku). PARTIAL on a string
        # order_id so manual entries (order_id null) aren't constrained and a
        # manager can log multiple manual kickers.
        _idx(
            "product_incentive_log",
            [("order_id", 1), ("sku", 1)],
            unique=True,
            partialFilterExpression={"order_id": {"$type": "string"}},
            name="uniq_kicker_order_sku",
            background=True,
        )
        # incentive_settings E2 scope lookup (global / entity / store rows).
        _idx(
            "incentive_settings",
            [("scope", 1), ("entity_id", 1)],
            background=True,
        )

        # Loyalty ledger. The per-customer history scan filters {customer_id}
        # (+ optional type), newest-first.
        _idx(
            "loyalty_transactions",
            [("customer_id", 1), ("created_at", -1)],
            background=True,
        )
        # Idempotent EARN: exactly one EARN row per (customer, order). This is the
        # DB-level backstop behind LoyaltyTransactionRepository.claim_earn_for_order
        # (the atomic upsert) -- two concurrent earns for the same order can never
        # both insert -> no double points. PARTIAL on type=EARN + a string
        # order_id so REDEEM/EXPIRE/ADJUST rows (which can share an order_id) and
        # manual earns (no order_id) are exempt and never collide. Fail-soft build.
        _idx(
            "loyalty_transactions",
            [("customer_id", 1), ("order_id", 1), ("type", 1)],
            unique=True,
            partialFilterExpression={
                "type": "EARN",
                "order_id": {"$type": "string"},
            },
            name="uniq_loyalty_earn_customer_order",
            background=True,
        )

        # Audit logs (SYSTEM_INTENT 10 -- immutable, hash-chained trail). UNIQUE
        # sparse `seq` is the belt-and-braces against a forked tamper-evident
        # chain; sparse excludes the fail-soft UNCHAINED (seq-less) rows.
        _idx("audit_logs", "seq", unique=True, sparse=True, background=True)
        _idx("audit_logs", "log_id", unique=True, sparse=True, background=True)
        _idx("audit_logs", [("timestamp", -1)], background=True)
        _idx("audit_logs", [("user_id", 1), ("timestamp", -1)], background=True)
        _idx("audit_logs", [("store_id", 1), ("timestamp", -1)], background=True)
        _idx("audit_logs", [("action", 1), ("timestamp", -1)], background=True)
        _idx("audit_logs", [("entity_type", 1), ("entity_id", 1)], background=True)
        _idx("audit_logs", [("severity", 1), ("timestamp", -1)], background=True)
        # Cataloguing-scorecard corrections scan: {entity_id: {$in: pids},
        # action: {$in: [...]}, timestamp: {$gte: cutoff}}. The (entity_type,
        # entity_id) compound above cannot serve it (no entity_type predicate,
        # prefix rule) and (action, timestamp) matches every middleware UPDATE
        # row app-wide -- this makes each chunk a pure index seek that stays
        # flat as the append-only trail grows.
        _idx("audit_logs", [("entity_id", 1), ("action", 1)], background=True)
        _idx("audit_chain_head", "seq", background=True)

        # QC samples (cataloguing scorecard phase 2). PARTIAL UNIQUE on the
        # OPEN pair is the concurrency backstop for POST /products/qc-samples/
        # generate (multi-worker: two overlapping generates both read the
        # pre-insert exclusion set; the index makes the second insert of the
        # same open (cataloguer, product) pair lose with a duplicate key,
        # which the endpoint drops from its reported counts). Partial on
        # PENDING only: reviewed history rows are excluded by the endpoint's
        # own any-status exclusion query, not by the index.
        _idx(
            "qc_samples",
            [("cataloguer_id", 1), ("product_id", 1)],
            unique=True,
            partialFilterExpression={"status": "PENDING"},
            name="uniq_qc_open_pair",
            background=True,
        )
        # List endpoint: filter (batch_id/cataloguer_id/status) + sort
        # sampled_at desc, all server-side.
        _idx(
            "qc_samples",
            [("batch_id", 1), ("status", 1), ("sampled_at", -1)],
            background=True,
        )
        _idx(
            "qc_samples",
            [("cataloguer_id", 1), ("sampled_at", -1)],
            background=True,
        )

        # Catalog variants (BVI Phase 1). UNIQUE sparse Shopify/barcode reverse
        # lookups so a Shopify variant / physical unit resolves to one IMS variant.
        _idx("catalog_variants", "sku", unique=True, sparse=True, background=True)
        _idx("catalog_variants", "parent_product_id", sparse=True, background=True)
        _idx(
            "catalog_variants",
            "shopify_variant_id",
            unique=True,
            sparse=True,
            background=True,
        )
        _idx(
            "catalog_variants",
            "store_barcode",
            unique=True,
            sparse=True,
            background=True,
        )

        # Catalog products (PIM superset; BVI/Shopify lineage). Unification
        # step 1: this collection previously had ZERO DB indexes, so a
        # duplicate PIM doc was physically possible (the catalog router and
        # the PM mirror both do check-then-write upserts keyed on `id`).
        # UNIQUE sparse on `id` (the primary PIM identity) and `sku` (carried
        # by catalog-router docs; PM mirror docs carry parent_sku instead, so
        # sparse exempts them). FAIL-SOFT like the grns dc_number precedent:
        # pre-existing prod dupes make the build WARN via _idx, never abort.
        # Declared in schemas.py COLLECTIONS["catalog_products"] for parity.
        _idx("catalog_products", "id", unique=True, sparse=True, background=True)
        _idx("catalog_products", "sku", unique=True, sparse=True, background=True)

        # Lens catalog (Branch B' rebuild). schemas.py has declared these since
        # the rebuild, but schemas.py is documentation-only -- ensure_indexes is
        # the live startup path and never built them, so a duplicate lens LINE
        # (same slug, or same 6-field identity combo) was DB-possible under a
        # create race. UNIQUE slug + UNIQUE identity grid; the two non-unique
        # filters mirror schemas.py COLLECTIONS["lens_catalog"] exactly. No
        # custom names so a manual migrations.py run can't IndexOptionsConflict.
        _idx("lens_catalog", "lens_line_id", unique=True, background=True)
        _idx(
            "lens_catalog",
            [
                ("brand", 1),
                ("series", 1),
                ("index", 1),
                ("material", 1),
                ("lens_type", 1),
                ("coating", 1),
            ],
            unique=True,
            background=True,
        )
        _idx("lens_catalog", [("brand", 1), ("is_active", 1)], background=True)
        _idx("lens_catalog", [("is_active", 1)], background=True)

        # E-commerce collections / menus (BVI Phases 2-3). UNIQUE sparse handle +
        # Shopify GID so a PUSH-DARK row (handle present, GID absent) isn't
        # constrained on the missing key.
        _idx("ecom_collections", "handle", unique=True, sparse=True, background=True)
        _idx(
            "ecom_collections",
            "shopify_collection_id",
            unique=True,
            sparse=True,
            background=True,
        )
        _idx("ecom_collections", "auto_source", sparse=True, background=True)
        _idx("ecom_collections", "category_anchor", sparse=True, background=True)
        # Temporary "share as PDF" collections (Share collection as PDF feature)
        # auto-expire: Mongo removes the doc once expires_at passes. PARTIAL to
        # is_temporary=true ONLY so it can NEVER touch a real storefront
        # collection (which has no expires_at). expireAfterSeconds=0 means "expire
        # exactly at the stored date". Fail-soft via _idx.
        _idx(
            "ecom_collections",
            "expires_at",
            expireAfterSeconds=0,
            partialFilterExpression={"is_temporary": True},
            name="ttl_temp_collection_expires_at",
            background=True,
        )
        _idx("ecom_menus", "handle", unique=True, sparse=True, background=True)
        _idx(
            "ecom_menus",
            "shopify_menu_id",
            unique=True,
            sparse=True,
            background=True,
        )

        # Product images / image design queue (BVI Phase 4). Non-unique -- a
        # product has many images (RAW + EDITED rows of the same asset).
        _idx("product_images", "product_id", background=True)
        _idx("product_images", "status", background=True)
        _idx("product_images", "assigned_to", sparse=True, background=True)

        # GRNs / Delivery Challans (F9 P3). Partial UNIQUE backstop on
        # (vendor_id, dc_number, store_id) for DELIVERY_CHALLAN rows only --
        # the app-level duplicate check in vendors.create_grn is check-then-
        # insert and racy across workers. Declared in schemas.py too; created
        # HERE because ensure_indexes is the live startup path. FAIL-SOFT: prod
        # may hold duplicate DC rows until the dedup script runs -- _idx
        # collects the failure as a startup warning, never aborts.
        _idx(
            "grns",
            [("vendor_id", 1), ("dc_number", 1), ("store_id", 1)],
            unique=True,
            partialFilterExpression={
                "grn_subtype": "DELIVERY_CHALLAN",
                "dc_number": {"$exists": True},
            },
            name="uniq_dc_vendor_number_store",
            background=True,
        )

        # Vendor bills / purchase invoices (AP + ITC). bill_id UNIQUE sparse;
        # (vendor_id, bill_number) is NON-unique on purpose -- the duplicate-
        # invoice guard lives in app code (legacy prod data may already have one).
        _idx("vendor_bills", "bill_id", unique=True, sparse=True, background=True)
        _idx("vendor_bills", [("vendor_id", 1), ("bill_number", 1)], background=True)
        # F4: DB-level duplicate-invoice backstop. The same vendor tax-invoice
        # number must never be booked twice as a PURCHASE_INVOICE -- a double
        # entry doubles the payable AND the ITC. The app-level check-then-insert
        # in create_purchase_invoice races; this UNIQUE index is the atomic
        # guard (insert loser -> DuplicateKeyError -> 409). PARTIAL to
        # doc_type == PURCHASE_INVOICE so legacy header-only vendor_bills (and
        # rows with a null/missing bill_number) are never indexed and can't
        # collide. If prod already holds duplicate PURCHASE_INVOICE rows this
        # build fails SOFT (logged, never aborts boot -- see _idx); a data-hygiene
        # pass must de-dupe before the index can take effect.
        _idx(
            "vendor_bills",
            [("vendor_id", 1), ("bill_number", 1)],
            unique=True,
            partialFilterExpression={
                "doc_type": "PURCHASE_INVOICE",
                "bill_number": {"$type": "string"},
            },
            name="uniq_purchase_invoice_vendor_number",
            background=True,
        )
        _idx("vendor_bills", "po_id", sparse=True, background=True)
        _idx("vendor_bills", "grn_id", sparse=True, background=True)
        _idx("vendor_bills", "status", background=True)
        _idx("vendor_bills", [("bill_date", -1)], background=True)

        # GST cross-check sign-offs are keyed (year, month, entity_id) and
        # written via update_one(..., upsert=True). Without a UNIQUE index two
        # concurrent sign-offs (double-click / two admins) both take the insert
        # path and leave duplicate docs, after which find_one returns an
        # arbitrary one and later upserts touch only one duplicate. Entity-less
        # sign-offs store entity_id == "_all", so the field is always present.
        _idx(
            "gst_crosscheck_signoffs",
            [("year", 1), ("month", 1), ("entity_id", 1)],
            unique=True,
            name="uniq_gst_crosscheck_signoff",
            background=True,
        )

        # health_checks (SENTINEL telemetry) grows unbounded; TTL auto-expires
        # rows >14d. Building a TTL index needs free disk -- already isolated by
        # _idx so an OutOfDiskSpace here can't abort the others (SENTINEL's in-tick
        # prune bounds the collection until it builds).
        _idx(
            "health_checks",
            "timestamp",
            expireAfterSeconds=14 * 24 * 60 * 60,
            name="ttl_timestamp",
            background=True,
        )

        if failures:
            print(
                "[WARN] %d index(es) could not build (non-fatal, others OK):"
                % len(failures)
            )
            for f in failures:
                print("   - " + f)
        else:
            print("[OK] MongoDB indexes ensured")

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
                    elif op == "$nin":
                        if doc_value in op_value:
                            return False
                    elif op == "$ne":
                        if doc_value == op_value:
                            return False
                    elif op == "$exists":
                        if bool(op_value) != (key in doc):
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

    def find_one_and_update(
        self,
        filter: Dict,
        update: Dict,
        upsert: bool = False,
        return_document: Any = False,
        sort: Any = None,
        **kwargs,
    ) -> Optional[Dict]:
        """Atomic find-and-update for no-Mongo mode.

        Mirrors the $set/$inc/$push handling of update_one and returns the
        matched document, so the serialized-stock atomic claims
        (claim_one_available / claim_for_transfer) and the per-FY invoice
        counter actually work in local mock mode instead of silently no-opping.
        Single-threaded, so the real concurrency guarantee is moot here -- this
        just preserves FUNCTIONAL behaviour. return_document follows pymongo's
        ReturnDocument (AFTER == True -> post-update doc; default -> pre-image).
        sort: pymongo-style [(field, direction)] list; picks WHICH matching doc
        is updated (FEFO expiry-first claim relies on this in mock mode). Values
        are compared as strings (missing/None last), which orders ISO dates and
        datetimes chronologically -- good enough for the mock.
        """
        if sort:
            candidates = [
                d for d in self._data.values() if self._matches_filter(d, filter)
            ]
            for sort_key, direction in reversed(list(sort)):
                candidates.sort(
                    key=lambda d, k=sort_key: (d.get(k) is None, str(d.get(k))),
                    reverse=direction == -1,
                )
            doc = candidates[0] if candidates else None
        else:
            doc = self.find_one(filter)
        if doc is None:
            if not upsert:
                return None
            # Minimal upsert: seed the new doc from the filter's equality keys.
            doc = {
                k: v
                for k, v in filter.items()
                if not k.startswith("$") and not isinstance(v, dict)
            }
            _id = doc.get("_id") or doc.get("id") or str(len(self._data) + 1)
            doc["_id"] = _id
            self._data[_id] = doc
        before = dict(doc)
        if "$set" in update:
            doc.update(update["$set"])
        if "$inc" in update:
            for field, amount in update["$inc"].items():
                doc[field] = doc.get(field, 0) + amount
        if "$push" in update:
            for field, value in update["$push"].items():
                doc.setdefault(field, []).append(value)
        return dict(doc) if return_document else before

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


def _is_production() -> bool:
    """True on a deployed (production) environment. Mirrors api.main's detector:
    ENVIRONMENT=production/prod, OR any Railway deploy marker present. Railway
    always sets RAILWAY_DEPLOYMENT_ID, so prod is reliably detected; local + test
    (no Railway vars, ENVIRONMENT unset/dev/test) are NOT production."""
    import os

    return os.getenv("ENVIRONMENT", "").lower() in ("production", "prod") or bool(
        os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_DEPLOYMENT_ID")
    )


class SeededDatabaseConnection:
    """
    Database connection that falls back to seeded mock data
    when MongoDB is not available -- BUT ONLY off production.

    LAUNCH-SAFETY: in production the seeded mock is NEVER served. If the real
    Mongo is unreachable, is_connected reports False and db/get_collection return
    None, so every caller's `if db is None -> 503` path fails LOUD. Previously a
    prod Mongo outage silently fell back to fake seed data -> the app would bill
    orders against fabricated stock/prices while reporting is_connected=True. The
    mock fallback remains for local dev + tests (no Railway markers).
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
        # Real DB down. In production NEVER pretend (no mock) -> report the true
        # disconnected state so callers fail loud (503) instead of serving fake
        # data. Off production, fall back to the seeded mock as before.
        if _is_production():
            return False
        self._use_mock = True
        return True  # dev/test: the seeded mock is "connected"

    @property
    def db(self):
        if self._real_db.is_connected:
            return self._real_db.db
        if _is_production():
            return None  # fail loud in prod; never serve the seeded mock
        return get_seeded_mock_db()

    def get_collection(self, name: str):
        if self._real_db.is_connected:
            return self._real_db.get_collection(name)
        if _is_production():
            return None  # fail loud in prod; never serve the seeded mock
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
    print("\n[DB] Testing Mock Database")
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
