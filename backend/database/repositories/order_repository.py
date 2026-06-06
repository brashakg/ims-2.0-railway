"""
IMS 2.0 - Order Repository
===========================
Order data access operations
"""
import logging
from typing import List, Optional, Dict, Tuple
from datetime import datetime, date, timedelta
from api.utils.ist import now_ist, now_ist_naive, ist_day_start_utc
from decimal import Decimal
from .base_repository import BaseRepository

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Indian financial-year helpers (GST: tax invoice serial is per FY Apr-Mar)
# ---------------------------------------------------------------------------
# Indian GST law (Rule 46(b) of the CGST Rules) requires a tax invoice to carry
# a consecutive serial number, unique for a financial year. India's FY runs
# 1 Apr -> 31 Mar, so an invoice dated 2026-03-31 belongs to FY 2025-26 while
# one dated 2026-04-01 starts the fresh FY 2026-27 series.

# Default invoice prefix when neither the store nor the global invoice settings
# configure one. Matches settings.InvoiceSettings.invoice_prefix default so a
# fresh deployment is internally consistent.
DEFAULT_INVOICE_PREFIX = "INV"


def sanitize_invoice_prefix(value: object) -> Optional[str]:
    """Normalise a human-entered invoice prefix into a safe serial segment.

    Uppercases, strips surrounding whitespace, and keeps only characters that
    are legal in a printed/scanned invoice number: alphanumerics plus a small
    set of separators (``- _ /``). Caps the length at 10 (mirrors the
    settings.InvoiceSettings validator). Returns None for an empty / unusable
    input so callers can fall through to the next source rather than emit a
    blank prefix that would collapse every store's series together.
    """
    if value is None:
        return None
    raw = str(value).strip().upper()
    cleaned = "".join(c for c in raw if c.isalnum() or c in ("-", "_", "/"))
    cleaned = cleaned.strip("/-_ ")
    if not cleaned:
        return None
    return cleaned[:10]


def fy_start_year(dt: datetime) -> int:
    """Return the calendar year the financial year STARTS in for ``dt``.

    Apr-Dec -> that year; Jan-Mar -> previous year. So both 2026-04-01 and
    2027-03-31 return 2026 (they share FY 2026-27).
    """
    return dt.year if dt.month >= 4 else dt.year - 1


def fy_label(dt: datetime) -> str:
    """Indian financial-year label in 'YYYY-YY' form, e.g. '2026-27'.

    Used as the year segment of a GST invoice number so the serial is
    visibly scoped to its FY (BV/INV/2026-27/000123).
    """
    start = fy_start_year(dt)
    return f"{start}-{(start + 1) % 100:02d}"


class OrderRepository(BaseRepository):
    """Repository for Order operations"""
    
    @property
    def entity_name(self) -> str:
        return "Order"
    
    @property
    def id_field(self) -> str:
        return "order_id"
    
    # =========================================================================
    # Order-specific queries
    # =========================================================================
    
    def find_by_order_number(self, order_number: str) -> Optional[Dict]:
        """Find order by order number"""
        return self.find_one({"order_number": order_number})
    
    def find_by_customer(self, customer_id: str, limit: int = 50) -> List[Dict]:
        """Find orders for customer"""
        return self.find_many(
            {"customer_id": customer_id},
            sort=[("created_at", -1)],
            limit=limit
        )
    
    def find_by_store(self, store_id: str, from_date: date = None, 
                      to_date: date = None, status: str = None) -> List[Dict]:
        """Find orders for store with optional filters.
        Handles both camelCase (storeId) and snake_case (store_id) field names."""
        filter = {"$or": [{"store_id": store_id}, {"storeId": store_id}]}
        
        if from_date:
            dt = ist_day_start_utc(from_date).isoformat()
            filter["$or"] = [
                {"store_id": store_id, "created_at": {"$gte": dt}},
                {"storeId": store_id, "createdAt": {"$gte": dt}},
            ]
        if status:
            filter["$or"] = [
                {"store_id": store_id, "status": status},
                {"storeId": store_id, "orderStatus": status},
            ]
        
        return self.find_many(filter, sort=[("created_at", -1)], limit=500)
    
    def find_by_salesperson(self, user_id: str, from_date: date = None, 
                            to_date: date = None) -> List[Dict]:
        """Find orders by salesperson"""
        filter = {"salesperson_id": user_id}
        
        if from_date:
            filter["created_at"] = {"$gte": ist_day_start_utc(from_date)}
        if to_date:
            filter.setdefault("created_at", {})["$lte"] = ist_day_start_utc(to_date + timedelta(days=1))
        
        return self.find_many(filter, sort=[("created_at", -1)])
    
    # =========================================================================
    # Status-based queries
    # =========================================================================
    
    def find_pending(self, store_id: str = None) -> List[Dict]:
        """Find pending orders"""
        filter = {"status": {"$in": ["CONFIRMED", "PROCESSING"]}}
        if store_id:
            filter["store_id"] = store_id
        return self.find_many(filter, sort=[("created_at", 1)])
    
    def find_ready_for_delivery(self, store_id: str = None) -> List[Dict]:
        """Find orders ready for delivery"""
        filter = {"status": "READY"}
        if store_id:
            filter["store_id"] = store_id
        return self.find_many(filter, sort=[("created_at", 1)])
    
    def find_unpaid(self, store_id: str = None) -> List[Dict]:
        """Find unpaid orders"""
        filter = {"payment_status": {"$in": ["UNPAID", "PARTIAL"]}}
        if store_id:
            filter["store_id"] = store_id
        return self.find_many(filter, sort=[("created_at", -1)])
    
    def find_overdue(self, store_id: str = None) -> List[Dict]:
        """Find overdue orders (past expected delivery)"""
        filter = {
            "status": {"$in": ["CONFIRMED", "PROCESSING", "READY"]},
            "expected_delivery": {"$lt": now_ist_naive()}
        }
        if store_id:
            filter["store_id"] = store_id
        return self.find_many(filter, sort=[("expected_delivery", 1)])
    
    # =========================================================================
    # Order operations
    # =========================================================================
    
    def update_status(self, order_id: str, status: str, by_user: str = None) -> bool:
        """Update order status and add to status_history"""
        update_data = {
            "status": status,
            "status_updated_at": datetime.now()
        }
        if by_user:
            update_data["status_updated_by"] = by_user
        
        if status == "DELIVERED":
            update_data["delivered_at"] = datetime.now()
        
        # Add to status_history array
        status_history_entry = {
            "status": status,
            "timestamp": datetime.now().isoformat(),
            "changed_by": by_user or "system"
        }
        
        try:
            self.collection.update_one(
                {self.id_field: order_id},
                {
                    "$set": update_data,
                    "$push": {"status_history": status_history_entry}
                }
            )
            return True
        except Exception as e:
            print(f"Error updating status: {e}")
            return False
    
    def add_payment(self, order_id: str, payment: Dict) -> bool:
        """Add a tender to an order and recompute its AR (receivable) status.

        A CREDIT tender is a pay-later promise, NOT cash received: it is excluded
        from amount_paid and instead flags the order as a credit sale
        (payment_status 'CREDIT') so it surfaces as a receivable in finance
        /outstanding (which treats CREDIT as unpaid). Real money tenders
        (cash/card/UPI/cheque/etc.) reduce the balance as before.

        Invariants (council Branch A residuals on top of PR #256):
          * Over-tender protection: actual cash collected (non-CREDIT) must
            not exceed grand_total. Refunds offset (negative tenders allowed).
            CREDIT tenders are a separate promise stream, not cash, so they
            don't count toward over-tender (an unsettled CREDIT can coexist
            with cash that later pays it off).
          * Sticky credit_sale flag: once an order ever takes a CREDIT tender,
            the flag stays True even after the customer settles it. Auditors
            need to know it was sold on credit, not just whether it's paid.
          * Multiple CREDIT rows: summed correctly. Each one adds to the
            credit-extended count but never to amount_paid.
          * Refund (negative amount): re-aggregated like any other tender;
            balance_due recomputes; status may flip back from PAID.

        Raises ValueError on over-tender so the POS layer surfaces it cleanly.
        """
        try:
            order = self.find_by_id(order_id)
            if not order:
                return False

            def _is_credit(p):
                return str((p or {}).get("method", "")).upper() == "CREDIT"

            def _amt(p):
                try:
                    return float((p or {}).get("amount", 0) or 0)
                except (TypeError, ValueError):
                    return 0.0

            # Pre-validate against over-tender BEFORE recording. Cash collected
            # (everything that is NOT a CREDIT promise) must not exceed
            # grand_total. CREDIT rows don't count -- they're a pay-later flag,
            # and adding "CASH 5000" to settle an earlier "CREDIT 5000" is a
            # legitimate flow (not double-payment).
            existing_payments = list(order.get("payments") or [])
            all_payments = existing_payments + [payment]
            grand_total = float(order.get("grand_total", 0) or 0)
            cash_collected = round(
                sum(_amt(p) for p in all_payments if not _is_credit(p)), 2
            )
            # 1-paisa rounding tolerance to absorb 5+5+0.01 float noise.
            if cash_collected - grand_total > 0.01:
                raise ValueError(
                    f"Over-tender: cash collected {cash_collected} exceeds "
                    f"grand_total {grand_total} on order {order_id}"
                )

            # Record the tender now that we know it's valid.
            self.collection.update_one(
                {"order_id": order_id},
                {"$push": {"payments": payment}}
            )

            # Recompute from the full tender list. CREDIT tenders never count
            # toward cash received; cash tenders include refunds (negative
            # amounts subtract from amount_paid).
            amount_paid = round(
                sum(_amt(p) for p in all_payments if not _is_credit(p)),
                2,
            )
            has_credit_now = any(_is_credit(p) for p in all_payments)
            # Sticky audit marker: once a credit sale, always flagged as one.
            credit_sale = bool(order.get("credit_sale")) or has_credit_now

            balance_due = round(grand_total - amount_paid, 2)

            if balance_due <= 0.01 and not has_credit_now:
                # Fully settled with cash/card/etc.
                payment_status = "PAID"
            elif balance_due <= 0.01 and has_credit_now:
                # Has a CREDIT promise + balance cleared by cash -> PAID.
                # Treat the order as settled. credit_sale flag still True.
                payment_status = "PAID"
            elif has_credit_now:
                payment_status = "CREDIT"
            elif amount_paid > 0:
                payment_status = "PARTIAL"
            else:
                payment_status = "UNPAID"

            return self.update(order_id, {
                "amount_paid": amount_paid,
                "balance_due": max(0.0, balance_due),
                "payment_status": payment_status,
                "credit_sale": credit_sale,
            })
        except ValueError:
            # Re-raise so the POS layer can surface it as 400 -- not a silent
            # False that the caller can't tell from a real DB error.
            raise
        except Exception as e:
            print(f"Error adding payment: {e}")
            return False
    
    def set_invoice(self, order_id: str, invoice_number: str) -> bool:
        """Set invoice number"""
        return self.update(order_id, {
            "invoice_number": invoice_number,
            "invoice_date": datetime.now()
        })

    # =========================================================================
    # GST invoice numbering (consecutive serial per store + financial year)
    # =========================================================================

    @staticmethod
    def _store_invoice_prefix(store_id: Optional[str]) -> str:
        """Stable alnum key fragment derived from the store_id string.

        LAST-RESORT fallback only -- the configured ``invoice_prefix`` (per the
        store doc, then global invoice settings) is the source of truth and is
        resolved by ``_resolve_invoice_prefix``. This derivation is used only
        when nothing is configured AND no store doc is available, so a missing
        config still yields a stable, store-distinct key rather than collapsing
        every store onto one series.

        Mirrors the order-number convention: drop the chain prefix (BV/WO/BVO),
        keep the store part, alnum-only, uppercase. "BV-BOK-01" -> "BOK01".
        Falls back to "IMS" so a missing/garbage store_id can't collapse the
        counter key to an empty string.
        """
        raw = (store_id or "").strip().upper()
        parts = [p for p in raw.split("-") if p and p not in ("BV", "WO", "BVO")]
        if len(parts) >= 2:
            frag = (parts[0] + parts[1])[:8]
        elif len(parts) == 1:
            frag = parts[0][:8]
        else:
            frag = "IMS"
        frag = "".join(c for c in frag if c.isalnum()) or "IMS"
        return frag

    def _lookup_configured_prefix(self, store_id: Optional[str]) -> Optional[str]:
        """Read the configured invoice prefix from the DB, fail-soft.

        Order of precedence:
          1. ``stores.invoice_prefix`` for this ``store_id`` (per-store identity).
          2. ``invoice_settings`` global doc (``_id == "default"``).
        Returns a sanitized prefix or None if neither is configured / the DB is
        unavailable. Never raises -- invoicing must not 500 on a config read.
        """
        db = None
        try:
            db = getattr(self.collection, "database", None)
        except Exception:  # noqa: BLE001
            db = None
        if db is None:
            return None
        # 1) Per-store configured prefix.
        if store_id:
            try:
                store_doc = db["stores"].find_one(
                    {"store_id": store_id}, {"invoice_prefix": 1}
                )
                prefix = sanitize_invoice_prefix(
                    (store_doc or {}).get("invoice_prefix")
                )
                if prefix:
                    return prefix
            except Exception:  # noqa: BLE001
                logger.debug("store invoice_prefix lookup failed", exc_info=True)
        # 2) Global invoice settings.
        try:
            settings_doc = db["invoice_settings"].find_one(
                {"_id": "default"}, {"invoice_prefix": 1}
            )
            prefix = sanitize_invoice_prefix(
                (settings_doc or {}).get("invoice_prefix")
            )
            if prefix:
                return prefix
        except Exception:  # noqa: BLE001
            logger.debug("invoice_settings prefix lookup failed", exc_info=True)
        return None

    def _resolve_invoice_prefix(
        self, store_id: Optional[str], store_doc: Optional[Dict] = None
    ) -> str:
        """Resolve the invoice prefix to use for ``store_id``.

        Honors the CONFIGURED prefix rather than deriving it from the store_id
        string. Precedence:
          1. ``store_doc.invoice_prefix`` (when the caller already has the doc).
          2. ``stores.invoice_prefix`` / global ``invoice_settings`` from the DB.
          3. ``DEFAULT_INVOICE_PREFIX`` ("INV") -- a sane, non-empty default.

        The result is sanitized (uppercased, serial-safe characters, <=10 chars)
        and is GUARANTEED non-empty: an empty prefix would merge every store's
        series and break Rule 46(b) uniqueness, so we never return one.
        """
        if store_doc is not None:
            prefix = sanitize_invoice_prefix(store_doc.get("invoice_prefix"))
            if prefix:
                return prefix
        prefix = self._lookup_configured_prefix(store_id)
        if prefix:
            return prefix
        return DEFAULT_INVOICE_PREFIX

    def _counters_collection(self):
        """Best-effort handle on the shared ``counters`` collection.

        The order collection is a real pymongo Collection in production
        (exposes ``.database``); in DB-less / mock modes it may not, in which
        case we return None and the caller falls back to a non-atomic number.
        Never raises.
        """
        try:
            db = getattr(self.collection, "database", None)
            if db is None:
                return None
            return db["counters"]
        except Exception:  # noqa: BLE001 - fail-soft, invoicing must not 500
            return None

    def ensure_invoice_index(self) -> None:
        """Best-effort UNIQUE index on ``invoice_number``.

        PARTIAL so it only covers docs that actually carry a string
        invoice_number -- the millions of legacy / DRAFT orders with no
        invoice_number (field absent) are NOT indexed and therefore can't
        collide with each other on a missing value. Defense in depth: the
        per-(store, FY) counter already hands out unique serials; this index
        is the backstop that makes a duplicate physically impossible.
        Never raises (a missing index degrades safety, not correctness of a
        single-worker allocation).
        """
        try:
            self.collection.create_index(
                "invoice_number",
                unique=True,
                partialFilterExpression={"invoice_number": {"$type": "string"}},
                name="uniq_invoice_number",
            )
        except Exception:  # noqa: BLE001
            logger.debug("invoice_number index create skipped", exc_info=True)

    def next_invoice_number(
        self,
        store_id: Optional[str],
        when: Optional[datetime] = None,
        store_doc: Optional[Dict] = None,
    ) -> str:
        """Allocate the next GST invoice number for ``store_id`` in its FY.

        The prefix is the CONFIGURED ``invoice_prefix`` -- per the store doc,
        then global invoice settings, then the "INV" default -- NOT a fragment
        derived from the store_id string (see ``_resolve_invoice_prefix``). So a
        store configured with prefix "BV" bills ``BV/2026-27/000123`` and one
        configured "WO" bills ``WO/2026-27/000123``; the operator's configured
        identity is honored.

        Atomic per (prefix, financial-year): a single ``find_one_and_update``
        with ``$inc`` claims the next serial from a ``counters`` doc keyed
        ``invoice:{prefix}:{fy_start_year}``. Mongo serialises the increment, so
        two cashiers raising invoices at the same instant get distinct serials
        -- no read-modify-write window, no duplicates (Rule 46(b)).

        Format: ``{PREFIX}/{FY}/{serial}`` e.g. ``BV/2026-27/000123`` --
        FY-scoped, zero-padded to 6. The serial RESETS for each new financial
        year (a fresh counter doc) and is consecutive within the FY.

        We do NOT renumber existing invoices: callers only invoke this for an
        order with no stored invoice_number, and the stored value is never
        overwritten -- historical invoices keep their original number.

        Fail-soft: with no counters collection (DB-less / mock), falls back to
        a timestamp-derived suffix so the caller still gets a usable, unique
        string (with the same configured prefix + FY) rather than a 500. That
        fallback path is only hit when the DB is unavailable, in which case
        nothing is persisted anyway.
        """
        now = when or now_ist()
        label = fy_label(now)
        prefix = self._resolve_invoice_prefix(store_id, store_doc)
        counters = self._counters_collection()
        if counters is not None:
            try:
                from pymongo import ReturnDocument

                key = f"invoice:{prefix}:{fy_start_year(now)}"
                doc = counters.find_one_and_update(
                    {"_id": key},
                    {"$inc": {"seq": 1}},
                    upsert=True,
                    return_document=ReturnDocument.AFTER,
                )
                seq = (doc or {}).get("seq")
                if isinstance(seq, int) and seq > 0:
                    return f"{prefix}/{label}/{seq:06d}"
            except Exception:  # noqa: BLE001 - fall through to safe fallback
                logger.warning(
                    "invoice counter $inc failed; using fallback serial",
                    exc_info=True,
                )
        # Fail-soft fallback (DB-less / counter error): time-derived, still
        # unique and still FY-labelled so the format stays consistent.
        suffix = now.strftime("%m%d%H%M%S")
        return f"{prefix}/{label}/{suffix}"

    def create_unique(
        self, data: Dict, number_field: str, regenerate, max_retries: int = 6
    ) -> Optional[Dict]:
        """Insert ``data``, retrying ``number_field`` on a duplicate-key clash.

        ``number_field`` (e.g. ``order_number``) carries a UNIQUE sparse index.
        Under concurrency two requests can mint the same value; the loser hits
        a Mongo E11000 on insert. Rather than 500, we regenerate JUST that
        field via ``regenerate()`` and retry, bounded by ``max_retries``.
        Mirrors ``vouchers.issue_voucher`` exactly.

        Unlike ``BaseRepository.create`` (which swallows every exception into
        None, so a dup-key is indistinguishable from a real DB error), this
        re-raises any non-duplicate error and only loops on a genuine
        duplicate-key. The success path is otherwise identical to ``create``:
        same id_field defaulting, same timestamps, same ``_id`` mirroring.

        Returns the created doc, or None when DB-less (insert returns falsy) or
        after exhausting retries on persistent collisions.
        """
        from pymongo.errors import DuplicateKeyError

        if self.id_field not in data:
            data[self.id_field] = self._generate_id()
        data = self._add_timestamps(data)
        data["_id"] = data[self.id_field]

        last_exc: Optional[Exception] = None
        for _ in range(max(1, max_retries)):
            try:
                self.collection.insert_one(data)
                return data
            except Exception as exc:  # noqa: BLE001
                is_dup = isinstance(exc, DuplicateKeyError) or (
                    "e11000" in str(exc).lower()
                    or "duplicate key" in str(exc).lower()
                )
                if not is_dup:
                    raise
                last_exc = exc
                # Only the human-facing number collided -- regenerate it and
                # retry. order_id / _id stay as-is (they're UUIDs, not the
                # colliding key).
                data[number_field] = regenerate()
        logger.error(
            "create_unique exhausted retries for %s on field %s",
            self.entity_name,
            number_field,
        )
        if last_exc is not None:
            raise last_exc
        return None


    # =========================================================================
    # Analytics
    # =========================================================================
    
    def get_sales_summary(self, store_id: str, from_date: date, to_date: date) -> Dict:
        """Get sales summary for period"""
        pipeline = [
            {"$match": {
                "store_id": store_id,
                "created_at": {
                    "$gte": ist_day_start_utc(from_date),
                    "$lte": ist_day_start_utc(to_date + timedelta(days=1))
                },
                "status": {"$nin": ["CANCELLED", "DRAFT"]}
            }},
            {"$group": {
                "_id": None,
                "total_orders": {"$sum": 1},
                "total_revenue": {"$sum": "$grand_total"},
                "total_paid": {"$sum": "$amount_paid"},
                "avg_order_value": {"$avg": "$grand_total"},
                "total_items": {"$sum": {"$size": "$items"}}
            }}
        ]
        
        results = self.aggregate(pipeline)
        if results:
            return results[0]
        return {
            "total_orders": 0,
            "total_revenue": 0,
            "total_paid": 0,
            "avg_order_value": 0,
            "total_items": 0
        }
    
    def get_salesperson_performance(self, store_id: str, from_date: date, to_date: date) -> List[Dict]:
        """Get salesperson performance"""
        pipeline = [
            {"$match": {
                "store_id": store_id,
                "created_at": {
                    "$gte": ist_day_start_utc(from_date),
                    "$lte": ist_day_start_utc(to_date + timedelta(days=1))
                },
                "status": {"$nin": ["CANCELLED", "DRAFT"]}
            }},
            {"$group": {
                "_id": "$salesperson_id",
                "order_count": {"$sum": 1},
                "total_sales": {"$sum": "$grand_total"},
                "avg_order": {"$avg": "$grand_total"}
            }},
            {"$sort": {"total_sales": -1}}
        ]
        
        return self.aggregate(pipeline)
    
    def get_daily_sales(self, store_id: str, days: int = 30) -> List[Dict]:
        """Get daily sales for last N days"""
        start_date = ist_day_start_utc(now_ist().date() - timedelta(days=days))

        pipeline = [
            {"$match": {
                "store_id": store_id,
                "created_at": {"$gte": start_date},
                "status": {"$nin": ["CANCELLED", "DRAFT"]}
            }},
            {"$group": {
                "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at", "timezone": "+05:30"}},
                "order_count": {"$sum": 1},
                "total_sales": {"$sum": "$grand_total"}
            }},
            {"$sort": {"_id": 1}}
        ]
        
        return self.aggregate(pipeline)
    
    def get_status_counts(self, store_id: str = None) -> Dict:
        """Get order counts by status"""
        pipeline = [
            {"$match": {"store_id": store_id} if store_id else {}},
            {"$group": {
                "_id": "$status",
                "count": {"$sum": 1}
            }}
        ]
        
        results = self.aggregate(pipeline)
        return {r["_id"]: r["count"] for r in results}
    
    # =========================================================================
    # Search
    # =========================================================================
    
    def search_orders(self, query: str, store_id: str = None) -> List[Dict]:
        """Search orders by number, customer name, or phone"""
        return self.search(query, ["order_number", "customer_name", "customer_phone"],
                          {"store_id": store_id} if store_id else None)
