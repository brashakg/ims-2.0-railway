"""
IMS 2.0 - Order Repository
===========================
Order data access operations
"""

from api.utils.event_time import utc_event_iso, utc_now, utc_now_iso
import logging
import re
from typing import Iterable, List, Optional, Dict, Tuple
from datetime import datetime, date, timedelta, timezone
from api.utils.ist import IST, now_ist, ist_today, ist_day_start_utc
from api.utils.dates import iso_date_window
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

# Tax invoice number (owner ruling 2026-10-18): SHOPCODE/FYFY/NNNNN, e.g.
# BOK01/2627/00001 -- exactly 16 characters, the most Rule 46(b) allows (letters,
# digits, '-' or '/'). The IRP e-invoice schema is stricter (capitals; the first
# character a letter or 1-9), so ONE pattern serves the mint, GSTR-1 and IRP.
_GST_INVOICE_NUMBER_RE = re.compile(r"[A-Z1-9][A-Z0-9/-]{0,15}")
# A store's 5-character shop code: derived, or the store's own invoice_code.
INVOICE_CODE_RE = re.compile(r"[A-Z1-9][A-Z0-9]{4}")
# CHAIN-TOWN-NN store codes the shop code is derived from.
_STORE_CODE_PARTS_RE = re.compile(r"(BV|BVO|WO|WIZ)-([A-Z]{3})[A-Z]*-(\d+)")
# (Every pattern is used with fullmatch: "$" would let a trailing newline in.)


class InvoiceNumberError(ValueError):
    """No LEGAL tax invoice number can be minted -- refused, never guessed."""


def gst_invoice_number_problem(number: object) -> Optional[str]:
    """Why ``number`` breaks the GST invoice-number rule, else None."""
    s = str(number or "")
    if len(s) > 16:
        return f"{s} is {len(s)} characters; GST allows at most 16"
    if not _GST_INVOICE_NUMBER_RE.fullmatch(s):
        return (
            f"{s or '(blank)'} may use only capital letters, digits, '-' and '/' "
            "and must start with a letter or 1-9"
        )
    return None


def historical_order_problem(order: Optional[Dict]) -> Optional[str]:
    """Why ``order`` may never take a number from the live invoice series, else
    None: a historical import (``historical`` / the order-history import) was
    billed before IMS, so no door mints for it (CGST Rule 46(b))."""
    o = order or {}
    if o.get("historical") or o.get("import_source") == "shopify_order_history":
        return (
            "this is a historical imported order -- it was billed before IMS "
            "and never takes a number from this year's invoice series"
        )
    return None


def derive_invoice_code(store_code: object) -> Optional[str]:
    """Stable 5-character shop code from a CHAIN-TOWN-NN store code, else None.

    Better Vision: first 3 letters of the town + 2-digit number --
    BV-BOK-02 -> BOK02, BV-ONLINE-01 -> ONL01. WizOpt: W + town + 1-digit
    number -- WIZ-DHN-01 -> WDHN1, WO-ONLINE-01 -> WONL1; the W keeps a WizOpt
    shop apart from a Better Vision shop in the same town. A code that does not
    fit (no chain, a town under 3 letters, WizOpt number over 9) -> None: the
    store then needs an ``invoice_code``, never a squeezed guess."""
    m = _STORE_CODE_PARTS_RE.fullmatch(str(store_code or "").strip().upper())
    if not m:
        return None
    chain, town, n = m.group(1), m.group(2), int(m.group(3))
    if chain in ("WO", "WIZ"):
        return f"W{town}{n}" if n <= 9 else None
    return f"{town}{n:02d}" if n <= 99 else None


def store_invoice_code(store_doc: Optional[Dict]) -> Optional[str]:
    """The shop code a store RECORD's tax invoices carry: its ``invoice_code``
    when set (None if that is malformed), else derived from its store_code --
    the prod store_id can be a UUID (Pune), so the code comes first. No record,
    no code: a bare store_id string never yields one."""
    if not store_doc:
        return None
    override = str(store_doc.get("invoice_code") or "").strip().upper()
    if override:
        return override if INVOICE_CODE_RE.fullmatch(override) else None
    return derive_invoice_code(store_doc.get("store_code") or store_doc.get("store_id"))


def invoice_code_clash(
    code: str, own_ids: Iterable[Optional[str]], store_docs: Iterable[Dict]
) -> Optional[str]:
    """The store code of ANOTHER store whose invoices carry ``code``, else None.
    Codes are unique across ALL stores, so two shops under one GSTIN can never
    print the same number."""
    own = {i for i in own_ids if i}
    for d in store_docs:
        if own & {d.get("store_id"), d.get("store_code")}:
            continue
        if store_invoice_code(d) == code:
            return str(d.get("store_code") or d.get("store_id") or "another store")
    return None


def invoice_fy_start(when: Optional[datetime] = None) -> int:
    """FY start year of ``when`` on the IST calendar (default: now). A NAIVE
    ``when`` is UTC -- the frame every stored instant is in -- never an IST
    wall clock."""
    now = when or now_ist()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return fy_start_year(now.astimezone(IST))


# The shop's GST document series (owner/CA 2026-10-18), one counter each per
# shop per FY, all under the shop's ONE code: "" = tax invoice
# (BOK02/2627/00001), "T" = inter-GSTIN stock-transfer tax invoice
# (BOK02/T2627/0001), "D" = debit note, "C" = credit note (Rule 53) -- the
# letter keeps each series apart; every number is exactly 16 characters.
DOCUMENT_SERIES = ("", "T", "D", "C")


def invoice_series_key(code: str, fy: int, series: str = "") -> str:
    """The ``counters`` _id of one shop code's ``series`` in one FY."""
    return f"invoice:{code}:{fy}" if not series else f"gstdoc-{series}:{code}:{fy}"


def invoice_series_problem(counters, code: str, store_id: str, fy: int) -> Optional[str]:
    """Why ``store_id`` may not issue under ``code`` in FY ``fy``, else None.

    ONE code per shop per financial year, never shared, across ALL its
    document series: each counter doc records the store that opened it, so a
    code another store issued under this FY (any series) stays theirs (even
    after they moved off it), and a shop that issued under one code cannot
    open a second before 1 April. The mint and the store form both ask this
    one question."""
    keys = [invoice_series_key(code, fy, s) for s in DOCUMENT_SERIES]
    held = counters.find_one({"_id": {"$in": keys}, "store_id": {"$ne": store_id}})
    if held:
        return (
            f"invoice code {code} was already used this financial year by store "
            f"{held.get('store_code') or held.get('store_id')}"
        )
    other = counters.find_one(
        {"store_id": store_id, "fy": fy, "seq": {"$gt": 0}, "_id": {"$nin": keys}}
    )
    if other:
        used = str(other.get("_id") or "").split(":")
        return (
            f"this store already issued invoices as {used[1] if len(used) > 2 else '?'} "
            "this financial year -- its invoice code can change only from 1 April"
        )
    return None


def next_document_number(
    db,
    store_id: Optional[str],
    series: str = "",
    when: Optional[datetime] = None,
    store_doc: Optional[Dict] = None,
) -> str:
    """Allocate the next number in ``store_id``'s GST document ``series``.

    THE one mint -- tax invoices (the counter, online and admin-edit doors,
    through OrderRepository.next_invoice_number), inter-GSTIN transfer tax
    invoices ("T"), debit notes ("D") and credit notes ("C"). Format (owner
    rulings 2026-10-18), 16 characters each -- Rule 46(b) / Rule 53's cap:
      * tax invoice  ``SHOPCODE/FYFY/NNNNN``  e.g. ``BOK01/2627/00001``
      * lettered     ``SHOPCODE/SFYFY/NNNN``  e.g. ``BOK02/T2627/0001``
    where
      * SHOPCODE -- the store RECORD's 5-character code
        (``store_invoice_code``), refused if another store carries it, so
        shops sharing a GSTIN can never print the same number.
      * FYFY -- the Indian financial year in IST (``invoice_fy_start``):
        Apr 2026-Mar 2027 = 2627.
      * the serial -- consecutive in the shop's series for the FY: the
        ``counters`` doc ``invoice_series_key(code, fy, series)``, opened by
        and owned by this store (``invoice_series_problem``), claimed with ONE
        atomic ``find_one_and_update($inc)``; restarts at 1 each FY. Mongo
        serialises the $inc, so concurrent mints get distinct serials. A
        duplicate key on the upsert (the series doc was opened between the
        check and the claim) is retried once -- a racing mint of this shop
        then $incs it -- and a second one is answered with the real reason
        (another store holds the code).

    REFUSES with InvoiceNumberError -- never guesses, never truncates: no
    store record, no valid shop code, a code another store carries or issued
    under this FY, a second code for the shop this FY, an unreadable
    stores/counters collection or no database (fail CLOSED), or a number that
    breaks the GST rule (serial 100000 / 10000 is 17 chars). Callers mint only
    for a document with no stored number; an issued number (old format
    included) is never rewritten.
    """
    if series not in DOCUMENT_SERIES:
        raise ValueError(f"unknown GST document series {series!r}")
    start = invoice_fy_start(when)
    fy = f"{start % 100:02d}{(start + 1) % 100:02d}"
    if db is None:  # DB-less: no atomic counter, so no number
        raise InvoiceNumberError("invoice counter unavailable -- try again in a minute")
    try:
        stores = db.get_collection("stores")
        if store_doc is None and store_id:
            store_doc = stores.find_one({"store_id": store_id}) or stores.find_one(
                {"store_code": store_id}
            )
        docs = list(
            stores.find({}, {"_id": 0, "store_id": 1, "store_code": 1, "invoice_code": 1})
        )
    except Exception as exc:  # noqa: BLE001 - fail CLOSED
        raise InvoiceNumberError(
            "store records unreadable -- try again in a minute"
        ) from exc
    if not store_doc:
        raise InvoiceNumberError(f"store {store_id} has no store record")
    name = store_doc.get("store_code") or store_id
    owner = store_doc.get("store_id") or store_id
    code = store_invoice_code(store_doc)
    if not code:
        raise InvoiceNumberError(
            f"store {name} has no valid invoice code -- set a 5-character "
            "invoice code (capital letters/digits) on the store"
        )
    other = invoice_code_clash(code, (store_id, owner, name), docs)
    if other:
        raise InvoiceNumberError(
            f"invoice code {code} of store {name} is also store {other}'s "
            "-- give one of them a different invoice code"
        )
    from pymongo import ReturnDocument
    from pymongo.errors import DuplicateKeyError

    counters = db.get_collection("counters")
    key = invoice_series_key(code, start, series)

    def claim():
        # The filter names the owner: a series another store opened in a race
        # is a duplicate _id on upsert -- refused, not shared.
        return counters.find_one_and_update(
            {"_id": key, "store_id": owner, "fy": start},
            {"$inc": {"seq": 1}, "$setOnInsert": {"store_code": name}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )

    doc = None
    try:
        problem = invoice_series_problem(counters, code, owner, start)
        if problem is None:
            try:
                doc = claim()
            except DuplicateKeyError:
                try:
                    doc = claim()
                except DuplicateKeyError:
                    problem = invoice_series_problem(counters, code, owner, start) or (
                        f"invoice code {code}'s series this financial year is "
                        "held by another store's record"
                    )
    except Exception as exc:  # noqa: BLE001
        raise InvoiceNumberError(
            "invoice counter unavailable -- try again in a minute"
        ) from exc
    if problem:
        raise InvoiceNumberError(problem)
    seq = (doc or {}).get("seq")
    if not isinstance(seq, int) or seq < 1:
        raise InvoiceNumberError(f"invoice counter returned {seq!r}")
    number = (
        f"{code}/{fy}/{seq:05d}" if not series else f"{code}/{series}{fy}/{seq:04d}"
    )
    problem = gst_invoice_number_problem(number)
    if problem:
        raise InvoiceNumberError(problem)
    return number


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


# Owner ruling 2026-08-30: the BILL TYPE follows the money — no separate
# order-type step at the POS. Derived from payment_status in ONE place so the
# rule can never fork (payment_status is computed in add_payment below, in
# the superadmin-edit recomputes in orders.py, and in the UPI auto-reconcile
# in services/upi_qr.py — the POS-path writers and the online sync stamp bill_type (ladder-known statuses only; ONDC creation docs and refund/void webhooks do not — refund bill-type semantics await an owner ruling, and bill_type has no consumers there today) through this helper;
# grep payment_status writers before adding another).
#   PAID    -> FINAL   (full amount received: final bill)
#   PARTIAL -> ADVANCE (part received: advance, balance at delivery)
#   CREDIT  -> CREDIT  (approved pay-later promise)
#   UNPAID  -> PENDING (no tender yet)
BILL_TYPE_BY_PAYMENT_STATUS = {
    "PAID": "FINAL",
    "PARTIAL": "ADVANCE",
    "CREDIT": "CREDIT",
    "UNPAID": "PENDING",
}


def derive_bill_type(payment_status: str) -> str:
    return BILL_TYPE_BY_PAYMENT_STATUS.get(
        str(payment_status or "").upper(), "PENDING"
    )



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
        Handles both camelCase (storeId) and snake_case (store_id) field names.

        TZ-P3 latent-bug fix: the from_date branch used
        ``ist_day_start_utc(from_date).isoformat()`` -- a STRING ``$gte`` bound
        against the BSON-Date ``created_at``, which matches NOTHING in Mongo
        (strings and dates never compare). It also REPLACED the whole ``$or``,
        so passing both from_date and status silently dropped the date filter.
        Now each branch ANDs onto the two store-shape arms (mirrors
        find_by_salesperson) and the bound stays a datetime.
        """
        snake: Dict = {"store_id": store_id}
        camel: Dict = {"storeId": store_id}

        if from_date:
            start = ist_day_start_utc(from_date)
            snake["created_at"] = {"$gte": start}
            camel["createdAt"] = {"$gte": start}
        if to_date:
            end = ist_day_start_utc(to_date + timedelta(days=1))
            snake.setdefault("created_at", {})["$lte"] = end
            camel.setdefault("createdAt", {})["$lte"] = end
        if status:
            snake["status"] = status
            camel["orderStatus"] = status

        filter = {"$or": [snake, camel]}
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
    
    def find_ready_for_delivery(self, statuses, store_id: str = None) -> List[Dict]:
        """Orders awaiting the counter's handover, in `statuses`: the caller's
        ONE definition (orders.lists.DELIVERABLE_STATUSES, read off the
        transition table), which its search over the queue filters on too."""
        filter = {"status": {"$in": list(statuses)}}
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
        """Open orders whose promised delivery day has passed (IST calendar).

        ``expected_delivery`` is an ISO STRING on every write path -- POST
        /orders stores ``datetime.isoformat()`` (a midnight stamp from an
        explicit delivery_date, or a full timestamp from the +N-days default)
        and PUT /orders/{id} stores ``date.isoformat()`` (bare YYYY-MM-DD);
        no writer stores a BSON date, whatever schemas.py declares (its
        validator is not applied). This used to bind a naive DATETIME against
        the column; BSON never compares a date with a string, so the overdue
        screen returned [] in production.

        The bound is therefore a STRING, and day-granular: a job promised for
        TODAY is not overdue until the IST day has rolled over (a stamped
        bound would flag it at 00:01, and the box clock is UTC, 5h30m behind).
        The window's last admitted day is yesterday, which iso_date_window --
        the ONE rule shared with the clinical date window and the workshop
        overdue list -- expresses as strictly-less-than the IST-today string,
        matching both stored shapes.
        """
        filter = {
            "status": {"$in": ["CONFIRMED", "PROCESSING", "READY"]},
            "expected_delivery": iso_date_window(
                to_day=ist_today() - timedelta(days=1)
            ),
        }
        if store_id:
            filter["store_id"] = store_id
        return self.find_many(filter, sort=[("expected_delivery", 1)])
    
    # =========================================================================
    # Order operations
    # =========================================================================
    
    def update_status(self, order_id: str, status: str, by_user: str = None) -> bool:
        """Update order status and add to status_history"""
        now = utc_now().replace(tzinfo=None)
        update_data = {
            "status": status,
            "status_updated_at": now
        }
        if by_user:
            update_data["status_updated_by"] = by_user
        
        if status == "DELIVERED":
            update_data["delivered_at"] = now
        
        # Add to status_history array
        status_history_entry = {
            "status": status,
            "timestamp": utc_event_iso(now),
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
    
    def add_payment(self, order_id: str, payment: Dict, *, expected_order=None) -> bool:
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
            for attempt in range(12):
                order = expected_order if expected_order is not None else self.find_by_id(order_id)
                if not order or order.get("status") == "CANCELLED":
                    return False
                key = payment.get("idempotency_key")
                if key and any(p.get("idempotency_key") == key for p in order.get("payments") or []):
                    return True

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

                # Tender and resulting ladder land in ONE compare-and-set.
                # Concurrent settlement retries from the new list; a CREDIT
                # approval must retain its exact reserved snapshot instead.
                from api.services.credit_limit import _snapshot
                result = self.collection.update_one(
                    {"order_id": order_id, **_snapshot(order)},
                    {"$push": {"payments": payment}, "$set": {
                    "amount_paid": amount_paid,
                    "balance_due": max(0.0, balance_due),
                    "payment_status": payment_status,
                    # Owner ruling 2026-08-30: bill type follows the money.
                    "bill_type": derive_bill_type(payment_status),
                    "credit_sale": credit_sale,
                    "updated_at": datetime.now(),
                }})
                if result.matched_count:
                    return True
                if expected_order is not None:
                    return False
            return False
        except ValueError:
            # Re-raise so the POS layer can surface it as 400 -- not a silent
            # False that the caller can't tell from a real DB error.
            raise
        except Exception as e:
            print(f"Error adding payment: {e}")
            return False
    
    def set_invoice(self, order_id: str, invoice_number: str) -> str:
        """Record the order's tax invoice number ONCE; return the number the
        order now carries. Compare-and-set on "no number yet": of two taps on
        one sale the loser gets the WINNER's number back -- one sale, one
        number. The loser's own serial is a gap (logged for the CA), never a
        second printed number."""
        if self.update_if(
            order_id,
            {"invoice_number": {"$in": [None, ""]}},
            {"invoice_number": invoice_number, "invoice_date": datetime.now()},
        ):
            return invoice_number
        stored = (self.find_by_id(order_id) or {}).get("invoice_number")
        if not stored:
            raise InvoiceNumberError("the invoice number could not be saved -- try again")
        logger.warning(
            "invoice serial %s unused: order %s already carries %s",
            invoice_number, order_id, stored,
        )
        return stored

    # =========================================================================
    # GST invoice numbering (consecutive serial per store + financial year)
    # =========================================================================

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
        """Allocate the next GST tax invoice number for ``store_id``
        (``SHOPCODE/FYFY/NNNNN``, e.g. ``BOK01/2627/00001``) -- the shop's
        tax-invoice series of the ONE mint, ``next_document_number``, which
        documents the format and every refusal (InvoiceNumberError). Callers
        mint only for an order with no stored number (``set_invoice`` keeps
        the first); an issued number (old format included) is never
        rewritten."""
        try:
            db = getattr(self.collection, "database", None)
        except Exception:  # noqa: BLE001
            db = None
        return next_document_number(db, store_id, "", when, store_doc)

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
                "status": {"$nin": ["CANCELLED", "DRAFT", "HISTORICAL"]}
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
                "status": {"$nin": ["CANCELLED", "DRAFT", "HISTORICAL"]}
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
                "status": {"$nin": ["CANCELLED", "DRAFT", "HISTORICAL"]}
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
