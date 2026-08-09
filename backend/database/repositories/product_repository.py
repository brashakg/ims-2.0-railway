"""
IMS 2.0 - Product Repository
=============================
Product and Stock data access operations
"""

import logging
import re
from typing import List, NamedTuple, Optional, Dict
from datetime import datetime, date, timedelta

from api.utils.ist import ist_today

from .base_repository import BaseRepository

logger = logging.getLogger(__name__)

# Sentinel for "caller did not pass is_active" so the legacy active_only
# behaviour of find_by_category is preserved byte-for-byte while new callers
# (the Catalog Manager list) can request an explicit tri-state filter
# (True = active only, False = inactive only, None = everything).
_LEGACY = object()

# Case variants of the sellable status. stock_units is written with "AVAILABLE"
# everywhere in current code, but legacy / imported rows carry lowercase forms
# and inventory.py + oracle.py already treat those as on-hand. Used by the
# guarded mark_sold so tightening it from an unguarded update cannot make a
# legacy row silently unsellable.
AVAILABLE_STATUS_VALUES = ["AVAILABLE", "available", "Available"]


class StockReleaseResult(NamedTuple):
    """Outcome of a stock release (order cancel / DRAFT line removal).

    `incomplete` is the half that used to be missing: a mid-loop write failure
    returned a PARTIAL list that was indistinguishable from a clean run, so the
    caller reported success while units stayed SOLD against a CANCELLED order.
    Callers must OR `incomplete` into whatever failure flag they persist.
    """

    released: List[str]
    incomplete: bool


class ProductRepository(BaseRepository):
    """Repository for Product operations"""

    # Tokenized-search fields. `barcode` is ADDITIVE (Catalog Manager scanner
    # passthrough): it can only ADD matches for existing callers, never remove.
    SEARCH_FIELDS = ("brand", "model", "sku", "variant", "barcode")

    @property
    def entity_name(self) -> str:
        return "Product"

    @property
    def id_field(self) -> str:
        return "product_id"

    def find_by_sku(self, sku: str) -> Optional[Dict]:
        return self.find_one({"sku": sku})

    def find_by_identity_key(self, identity_key: str) -> Optional[Dict]:
        """Find a product by its brand+model+colour identity (Hub Phase 1
        duplicate guard). Returns None for a blank key."""
        if not identity_key:
            return None
        return self.find_one({"identity_key": identity_key})

    def find_by_barcode(self, barcode: str) -> Optional[Dict]:
        """Find a product by scan-to-sell barcode (Hub Phase 1 duplicate guard).
        Makes the create-path barcode arm functional whenever a barcode rides
        along (e.g. a bulk/import row); returns None for a blank value."""
        if not barcode:
            return None
        return self.find_one({"barcode": barcode})

    def _category_filter(
        self,
        category: str,
        is_active: Optional[bool],
        created_by: Optional[str] = None,
    ) -> Dict:
        filter = {"category": category}
        if is_active is not None:
            filter["is_active"] = is_active
        if created_by:
            filter["created_by"] = created_by
        return filter

    def find_by_category(
        self,
        category: str,
        active_only: bool = True,
        *,
        is_active=_LEGACY,
        created_by: Optional[str] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> List[Dict]:
        # Legacy contract preserved: active_only=True -> is_active True filter,
        # active_only=False -> no filter. The keyword-only `is_active` tri-state
        # (True/False/None) wins when passed explicitly (Catalog Manager).
        # `created_by` (cataloguer attribution) is an additive equality filter;
        # None (the default) preserves the pre-existing query byte-for-byte.
        if is_active is _LEGACY:
            is_active = True if active_only else None
        return self.find_many(
            self._category_filter(category, is_active, created_by),
            sort=[("brand", 1), ("model", 1)],
            skip=skip,
            limit=limit,
        )

    def count_by_category(
        self,
        category: str,
        *,
        is_active: Optional[bool] = True,
        created_by: Optional[str] = None,
    ) -> int:
        return self.count(self._category_filter(category, is_active, created_by))

    def _brand_filter(
        self,
        brand: str,
        category: Optional[str],
        is_active: Optional[bool],
        created_by: Optional[str] = None,
    ) -> Dict:
        filter: Dict = {"brand": brand}
        if is_active is not None:
            filter["is_active"] = is_active
        if category:
            filter["category"] = category
        if created_by:
            filter["created_by"] = created_by
        return filter

    def find_by_brand(
        self,
        brand: str,
        category: str = None,
        *,
        is_active: Optional[bool] = True,
        created_by: Optional[str] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> List[Dict]:
        return self.find_many(
            self._brand_filter(brand, category, is_active, created_by),
            sort=[("model", 1)],
            skip=skip,
            limit=limit,
        )

    def count_by_brand(
        self,
        brand: str,
        category: str = None,
        *,
        is_active: Optional[bool] = True,
        created_by: Optional[str] = None,
    ) -> int:
        return self.count(self._brand_filter(brand, category, is_active, created_by))

    def _search_extra_filter(
        self,
        category: Optional[str],
        is_active: Optional[bool],
        created_by: Optional[str] = None,
    ) -> Dict:
        filter: Dict = {}
        if is_active is not None:
            filter["is_active"] = is_active
        if category:
            filter["category"] = category
        if created_by:
            filter["created_by"] = created_by
        return filter

    def search_products(
        self,
        query: str,
        category: str = None,
        *,
        is_active: Optional[bool] = True,
        created_by: Optional[str] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> List[Dict]:
        return self.search(
            query,
            list(self.SEARCH_FIELDS),
            self._search_extra_filter(category, is_active, created_by),
            skip=skip,
            limit=limit,
        )

    def count_search_products(
        self,
        query: str,
        category: str = None,
        *,
        is_active: Optional[bool] = True,
        created_by: Optional[str] = None,
    ) -> int:
        return self.search_count(
            query,
            list(self.SEARCH_FIELDS),
            self._search_extra_filter(category, is_active, created_by),
        )

    def cataloguer_stats(self) -> List[Dict]:
        """Per-creator cataloguing rollup (attribution feature).

        Groups the products collection by `created_by`, excluding rows with no
        creator (legacy/system-seeded docs). Returns one row per cataloguer:
          {_id: user_id, name: best created_by_name seen (or None),
           created_count, last_created_at}
        sorted by created_count desc. Fail-soft [] via aggregate()."""
        pipeline = [
            {"$match": {"created_by": {"$nin": [None, "", "system", "SYSTEM"]}}},
            {
                "$group": {
                    "_id": "$created_by",
                    # $max ignores missing values, so any doc that carries the
                    # display name wins over older docs that lack it.
                    "name": {"$max": "$created_by_name"},
                    "created_count": {"$sum": 1},
                    "last_created_at": {"$max": "$created_at"},
                }
            },
            {"$sort": {"created_count": -1, "_id": 1}},
        ]
        return self.aggregate(pipeline)

    def update_price(
        self, product_id: str, mrp: float, offer_price: float, updated_by: str
    ) -> bool:
        if offer_price > mrp:
            raise ValueError("Offer price cannot exceed MRP")
        return self.update(
            product_id,
            {
                "mrp": mrp,
                "offer_price": offer_price,
                "price_updated_at": datetime.now(),
                "price_updated_by": updated_by,
            },
        )

    def get_brands(self, category: str = None) -> List[str]:
        filter = {"is_active": True}
        if category:
            filter["category"] = category
        pipeline = [
            {"$match": filter},
            {"$group": {"_id": "$brand"}},
            {"$sort": {"_id": 1}},
        ]
        return [r["_id"] for r in self.aggregate(pipeline)]

    def get_tags(self, prefix: str = None, limit: int = 200) -> List[str]:
        """Distinct normalised product tags (step-12 autocomplete backbone).

        Optional case-insensitive `prefix` narrows for typeahead. Tags are
        already stored lowercased; we unwind the `tags` array and group."""
        match: Dict = {"is_active": True, "tags": {"$exists": True, "$ne": []}}
        pipeline: List[Dict] = [
            {"$match": match},
            {"$unwind": "$tags"},
        ]
        if prefix:
            safe = re.escape(str(prefix).strip().lower())
            pipeline.append({"$match": {"tags": {"$regex": f"^{safe}"}}})
        pipeline += [
            {"$group": {"_id": "$tags", "count": {"$sum": 1}}},
            {"$sort": {"count": -1, "_id": 1}},
            {"$limit": int(limit)},
        ]
        return [r["_id"] for r in self.aggregate(pipeline)]


class StockRepository(BaseRepository):
    """Repository for Stock Unit operations"""

    @property
    def entity_name(self) -> str:
        return "StockUnit"

    @property
    def id_field(self) -> str:
        return "stock_id"

    def find_by_barcode(self, barcode: str) -> Optional[Dict]:
        return self.find_one({"barcode": barcode})

    def find_by_product_store(self, product_id: str, store_id: str) -> List[Dict]:
        return self.find_many(
            {"product_id": product_id, "store_id": store_id, "status": "AVAILABLE"}
        )

    # E3 item-event ledger: statuses that are explicitly NOT sellable on-hand
    # (mirrors api.services.item_events.EXCLUDED_STATUSES). A unit in any of
    # these states can never be counted as sellable POS stock.
    EXCLUDED_STATUSES = [
        "QUARANTINED",
        "UNDER_AUDIT",
        "BLIND_COUNT",
        "TRANSFERRED",
        "SOLD",
        "VOID",
        "DAMAGED",
        "RTV",
    ]

    # ======================================================================
    # F2 (patient-safety / compliance): the EXPIRY FLOOR
    # ======================================================================
    # Contact lenses / solutions carry `expiry_date`, stamped at GRN and
    # persisted by add_stock as an ISO date STRING (date.isoformat(), e.g.
    # "2026-05-30") -- see find_expiring below. EVERY other unit (frames,
    # sunglasses, accessories, watches...) carries NO expiry_date at all.
    #
    # Before this guard a past-dated unit was fully sellable AND, because the
    # FEFO claim sorted expiry ASCENDING with no floor, it was dispensed FIRST:
    # POS handed out the MOST-expired lens on the shelf, silently.
    #
    # THE ONE INVARIANT THAT MATTERS HERE: a unit that does not carry an
    # expiry_date must be COMPLETELY UNAFFECTED. Hence every filter below is
    # built as an $or whose FIRST branch is "undated" -- a frame matches that
    # branch and is never hidden, never re-sorted, never re-counted.
    #
    # ONLY a value we can actually PARSE may take stock off the shelf. A raw
    # string $gte is not a date comparison, it is a lexicographic one, and on the
    # real data shapes it is WRONG IN BOTH DIRECTIONS (panel must-fix 8):
    #   "15-08-2027" (a valid FUTURE date, DD-MM-YYYY) sorts BELOW "2026-08-09",
    #       so real in-date stock went dark while the counter was told it was
    #       "PAST THEIR EXPIRY DATE";
    #   "31/12/2025" (genuinely EXPIRED) sorts ABOVE it, so it stayed sellable.
    # The GRN door (vendors.py) validates the field with nothing but a whitespace
    # strip, so both shapes are accepted today.
    #
    # So the floor now only bites on a CANONICAL ISO value (^YYYY-MM-DD). Every
    # other shape -- missing, null, blank, malformed string, or a legacy BSON
    # datetime -- passes through as SELLABLE with a warning at the scan door.
    # That makes the fail-soft direction CONSISTENT: when we cannot interpret the
    # value we never false-block the counter. The real fix for the malformed
    # shapes is normalisation AT INGEST (see the note on _ISO_DATE_RE below).
    _ISO_DATE_RE = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}"

    @staticmethod
    def _expiry_floor_iso() -> str:
        """IST calendar 'today' as an ISO date string.

        IST (not UTC): the box runs UTC, so between 00:00-05:30 IST a naive
        date.today() is still YESTERDAY -- a lens would stay sellable for the
        first 5.5 hours of the day it expired.
        """
        try:
            return ist_today().isoformat()
        except Exception:  # noqa: BLE001 -- clock helper must never break a sale
            return date.today().isoformat()

    @classmethod
    def _not_expired_or(cls) -> List[Dict]:
        """$or branches selecting every unit that is NOT past its expiry.

        Branch order is deliberate (mock/`$or` matchers short-circuit on the
        first true branch, and the undated branch is the hot path):
          1. undated -- field missing, null, or blank -> ALWAYS sellable
          2. CANONICAL ISO string on/after IST today  -> still in date
          3. anything that is not a canonical ISO date string (malformed string
             OR a non-string legacy value) -> UNINTERPRETABLE, so it is allowed
             through rather than silently hidden

        Branch 3 subsumes the old "non-string" branch: `$not: {$regex: ISO}`
        matches a BSON datetime and a missing field as well as a malformed
        string. The ONLY thing this floor removes from sale is a value we can
        read with certainty and that is genuinely in the past.
        """
        return [
            {"expiry_date": {"$in": [None, ""]}},
            {
                "expiry_date": {
                    "$gte": cls._expiry_floor_iso(),
                    "$regex": cls._ISO_DATE_RE,
                }
            },
            {"expiry_date": {"$not": {"$regex": cls._ISO_DATE_RE}}},
        ]

    @classmethod
    def is_iso_expiry(cls, value) -> bool:
        """True when `value` is a canonical ^YYYY-MM-DD string -- i.e. the only
        shape the expiry floor is allowed to act on."""
        return bool(isinstance(value, str) and re.match(cls._ISO_DATE_RE, value))

    def sellable_filter(self, product_id: str, store_id: str) -> Dict:
        """Canonical 'what POS may actually sell' query for one product+store:
        AVAILABLE status AND not past its expiry date."""
        return {
            "product_id": product_id,
            "store_id": store_id,
            "status": "AVAILABLE",
            "$or": self._not_expired_or(),
        }

    def find_available(self, product_id: str, store_id: str) -> int:
        # The sellable-stock count for the POS path. The positive AVAILABLE match
        # already excludes every E3 non-sellable status (QUARANTINED /
        # UNDER_AUDIT / BLIND_COUNT / TRANSFERRED / SOLD / VOID / DAMAGED / RTV),
        # since none of those equals "AVAILABLE" -- this is the E3 rollup-
        # exclusion guarantee (intent #4 / #12). A quarantined or under-audit
        # unit therefore drops out of POS sellable on-hand immediately.
        #
        # F2: an EXPIRED dated unit is likewise not sellable on-hand. Units with
        # no expiry_date (frames etc.) match the undated $or branch and are
        # counted exactly as before.
        return self.count(self.sellable_filter(product_id, store_id))

    def count_expired(self, product_id: str, store_id: str) -> int:
        """How many AVAILABLE units for this product+store the expiry floor is
        holding back (F2's visible bucket -- expired stock is QUARANTINED FROM
        SALE, never silently deleted).

        Derived as (AVAILABLE total) - (sellable) rather than as its own query,
        so the two buckets ALWAYS reconcile: any AVAILABLE unit that find_available
        does not count shows up here. A product with no dated units always
        returns 0, and so does one whose dates are unreadable -- those are
        SELLABLE now (see _not_expired_or branch 3), so counting them here would
        be a lie in the other direction.
        """
        try:
            total = self.count(
                {"product_id": product_id, "store_id": store_id, "status": "AVAILABLE"}
            )
        except Exception:  # noqa: BLE001
            return 0
        return max(total - self.find_available(product_id, store_id), 0)

    def find_low_stock(self, store_id: str, threshold: int = 5) -> List[Dict]:
        # One stock_units row == one physical unit. Legacy rows have no
        # `quantity` field, so summing `$quantity` raw yields 0 and every
        # product looks out-of-stock. $ifNull treats a missing quantity as 1.
        pipeline = [
            {"$match": {"store_id": store_id, "status": "AVAILABLE"}},
            {
                "$group": {
                    "_id": "$product_id",
                    "quantity": {"$sum": {"$ifNull": ["$quantity", 1]}},
                }
            },
            {"$match": {"quantity": {"$lte": threshold}}},
            {"$sort": {"quantity": 1}},
        ]
        return self.aggregate(pipeline)

    def find_expiring(self, store_id: str, days: int = 30) -> List[Dict]:
        # expiry_date is persisted as an ISO date string (date.isoformat(),
        # e.g. "2026-05-30") by add_stock, NOT a BSON datetime. Datetime $gte/
        # $lte bounds never match a string field in Mongo (BSON type-bracketing),
        # which is why this returned 0. Compare string-vs-string with date-only
        # ISO bounds, mirroring how /contact-lenses/expiry-status parses these
        # values. ISO date strings sort lexicographically the same as
        # chronologically, so the window is correct: today (not yet expired)
        # through today + N days inclusive.
        now = datetime.now()
        lower = now.date().isoformat()
        upper = (now + timedelta(days=days)).date().isoformat()
        return self.find_many(
            {
                "store_id": store_id,
                "expiry_date": {"$lte": upper, "$gte": lower},
                "status": "AVAILABLE",
            }
        )

    def reserve_stock(self, stock_id: str) -> bool:
        return self.update(
            stock_id, {"status": "RESERVED", "reserved_at": datetime.now()}
        )

    def release_stock(self, stock_id: str) -> bool:
        return self.update(stock_id, {"status": "AVAILABLE", "reserved_at": None})

    def mark_sold(self, stock_id: str, order_id: str) -> bool:
        """Flip ONE SPECIFIC unit AVAILABLE -> SOLD (barcode-scan POS path).

        F7: this used to be an UNGUARDED update() -- it happily re-sold a unit
        that was already SOLD / TRANSFERRED / QUARANTINED / DAMAGED / RTV / VOID
        and OVERWROTE its prior sale lineage (order_id / sold_at), so the earlier
        sale's unit could never be traced or returned. It is now an ATOMIC
        GUARDED update keyed on status (same pattern as claim_for_transfer):
        the unit must still be AVAILABLE **and** not past its expiry date, else
        NOTHING is written and False is returned. The caller (POS) must surface
        that as a real error -- never a silent success.

        The status match accepts the CASE VARIANTS of AVAILABLE. Turning an
        unguarded update into an exact-equality guard would otherwise make a
        legacy lowercase `available` row silently unsellable through this door
        (inventory.py / oracle.py already treat those as on-hand). Deliberately
        NOT widened to `in_stock`: that is a different status token which neither
        this method nor claim_one_available ever sold before, so accepting it
        here would be a NEW behaviour change rather than a regression fix.
        """
        flt: Dict = {
            "stock_id": stock_id,
            "status": {"$in": AVAILABLE_STATUS_VALUES},
            "$or": self._not_expired_or(),
        }
        try:
            doc = self.collection.find_one_and_update(
                flt,
                {
                    "$set": {
                        "status": "SOLD",
                        "sold_at": datetime.now(),
                        "order_id": order_id,
                    }
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[STOCK] mark_sold(%s) failed: %s", stock_id, exc)
            return False
        return doc is not None

    def release_sold_units_for_order(
        self,
        order_id: str,
        *,
        product_id: Optional[str] = None,
        stock_id: Optional[str] = None,
        limit: Optional[int] = None,
        reason: str = "ORDER_CANCELLED",
    ) -> "StockReleaseResult":
        """Flip units still SOLD against `order_id` back to AVAILABLE. Returns
        ``StockReleaseResult(released=[stock_ids], incomplete=bool)`` -- the
        stock-side UNDO of a sale that never happened (order cancelled / a DRAFT
        line removed).

        IDEMPOTENT BY CONSTRUCTION: each unit is claimed with an ATOMIC
        find_one_and_update whose filter requires status=="SOLD" AND
        order_id==this order. The very same write clears `order_id`, so the
        unit can never match again -- a retried / double cancel reactivates
        NOTHING (and a concurrent return-restock can never double-count it).
        That property is what makes a RE-RUN safe after a partial failure.

        `incomplete` is TRUE when a write failed mid-loop (panel must-fix 4).
        This used to `break` and return the partial list indistinguishably from
        a clean run, so the caller reported the cancel as a success while units
        stayed SOLD against a CANCELLED order -- silent, permanent stock loss.
        The caller MUST surface `incomplete` (see orders.cancel_order, which
        flags the order and allows a re-run).

        Lineage is preserved, not overwritten: the stale sale attribution
        (order_id / sold_at / sold_to_customer_id) is CLEARED so an AVAILABLE
        unit no longer "answers to" a cancelled order, while
        prior_sold_order_id / released_from_order_id keep the audit trail
        (mirrors returns._reactivate_original_unit).

        Targeting (panel must-fix 5):
          * `stock_id`  -- release THAT EXACT unit. Required for a removed DRAFT
            line that named its own serial: releasing an arbitrary unit of the
            same product leaves the customer holding a serial the system shows
            AVAILABLE (double-sellable) while a frame sitting on the shelf reads
            SOLD. Never guess when the line told us the answer.
          * `product_id` + `limit` -- the FIFO fallback for a line that never
            named a unit.
          * neither -- the whole order (cancel).
        """
        released: List[str] = []
        if not order_id:
            return StockReleaseResult(released, False)
        flt: Dict = {"order_id": order_id, "status": "SOLD"}
        if stock_id:
            flt["stock_id"] = stock_id
        if product_id:
            flt["product_id"] = product_id
        # Hard bound so a misbehaving collection can never spin forever; no real
        # order has 500 serialized units. An explicit stock_id is exactly one.
        if stock_id:
            max_units = 1
        elif limit is None:
            max_units = 500
        else:
            max_units = max(int(limit), 0)
        incomplete = False
        for _ in range(min(max_units, 500)):
            try:
                doc = self.collection.find_one_and_update(
                    dict(flt),
                    {
                        "$set": {
                            "status": "AVAILABLE",
                            "order_id": None,
                            "sold_at": None,
                            "sold_to_customer_id": None,
                            "reserved_at": None,
                            "prior_sold_order_id": order_id,
                            "released_from_order_id": order_id,
                            "release_reason": reason,
                            "released_at": datetime.now().isoformat(),
                        }
                    },
                )
            except Exception as exc:  # noqa: BLE001
                # A write failed: units may still be SOLD against a cancelled
                # order. Report it -- do NOT return a partial list as success.
                logger.error(
                    "[STOCK] release_sold_units_for_order(%s) write failed after "
                    "%d unit(s): %s -- remaining units may be STRANDED SOLD",
                    order_id,
                    len(released),
                    exc,
                )
                incomplete = True
                break
            if not doc:
                break
            sid = doc.get("stock_id") or doc.get("_id")
            if sid:
                released.append(str(sid))
        return StockReleaseResult(released, incomplete)

    def count_sold_units_for_order(self, order_id: str) -> int:
        """How many units are STILL SOLD against this order. Lets the cancel
        path tell "nothing to release" apart from "release did not finish", and
        makes a re-run after a partial failure verifiable."""
        if not order_id:
            return 0
        try:
            return self.count({"order_id": order_id, "status": "SOLD"})
        except Exception:  # noqa: BLE001
            return 0

    def claim_one_available(
        self,
        product_id: str,
        store_id: str,
        order_id: str,
        exclude_ids=None,
    ) -> Optional[str]:
        """Atomically claim one AVAILABLE unit for product+store and flip it
        SOLD; return its stock_id, or None when none is available.

        Concurrency-safe: a single find_one_and_update with a status="AVAILABLE"
        filter means two racing sales can NEVER claim the same physical unit (the
        loser gets the next unit or None). Replaces the old find_by_product_store
        + mark_sold check-then-act FIFO path, which let two concurrent last-unit
        sales both mark the SAME unit SOLD.

        FEFO (First-Expiry-First-Out): expirable stock (contact lenses, solutions)
        carries expiry_date stamped at GRN. The claim is TWO-PHASE:
          1. Among DATED units that are STILL IN DATE (expiry_date >= IST today)
             claim the EARLIEST expiry first (sort ascending). Dispensing
             near-expiry units first is a clinical/inventory-correctness
             requirement -- but ONLY down to today.
          2. Only when no in-date dated unit is available, fall back to the
             original unsorted claim, now restricted to units the expiry floor
             allows (undated units -- frames, sunglasses -- plus any legacy
             non-string expiry). Plain undated products behave EXACTLY as before:
             they match the first $or branch, in natural order, unsorted.
        A naive single ascending sort would pick null/undated units FIRST under
        BSON ordering (null sorts before dates), hence the two phases. Each phase
        is still one atomic find_one_and_update, so the no-double-claim contract
        is unchanged.

        F2 (patient safety): phase 1 previously had NO date floor, so the claim
        sorted expiry ASCENDING across ALL dated units and handed POS the
        MOST-EXPIRED unit first. A past-dated unit is now claimable by NEITHER
        phase -- it stays AVAILABLE-but-unsellable and is reported by
        count_expired() so staff can quarantine it.
        """
        flt = {
            "product_id": product_id,
            "store_id": store_id,
            "status": "AVAILABLE",
        }
        if exclude_ids:
            flt["stock_id"] = {"$nin": list(exclude_ids)}
        update = {
            "$set": {
                "status": "SOLD",
                "sold_at": datetime.now(),
                "order_id": order_id,
            }
        }
        # Phase 1 (FEFO): earliest-expiring IN-DATE unit first.
        dated_flt = dict(flt)
        dated_flt["expiry_date"] = {"$gte": self._expiry_floor_iso()}
        try:
            doc = self.collection.find_one_and_update(
                dated_flt, update, sort=[("expiry_date", 1)]
            )
        except Exception:
            doc = None
        if not doc:
            # Phase 2: no in-date dated unit available -> claim any unit the
            # expiry floor still permits (undated / legacy-typed).
            undated_flt = dict(flt)
            undated_flt["$or"] = self._not_expired_or()
            try:
                doc = self.collection.find_one_and_update(undated_flt, update)
            except Exception:
                return None
        if not doc:
            return None
        return doc.get("stock_id") or doc.get("_id")

    def mark_barcode_printed(self, stock_id: str) -> bool:
        return self.update(
            stock_id, {"barcode_printed": True, "barcode_printed_at": datetime.now()}
        )

    def claim_for_transfer(
        self, stock_id: str, transfer_id, to_store_id
    ) -> bool:
        """Atomically flip a SPECIFIC unit AVAILABLE -> TRANSFERRED, only if it is
        still AVAILABLE. Returns False when another concurrent ship already
        claimed it. Concurrency-safe replacement for update() in the transfer-ship
        loop, which previously let two concurrent ships of the same product double-
        claim the same physical unit (find_many + update = check-then-act)."""
        try:
            doc = self.collection.find_one_and_update(
                {"stock_id": stock_id, "status": "AVAILABLE"},
                {
                    "$set": {
                        "status": "TRANSFERRED",
                        "transfer_id": transfer_id,
                        "transferred_at": datetime.now().isoformat(),
                        "transfer_to_store_id": to_store_id,
                    }
                },
            )
        except Exception:
            return False
        return doc is not None
