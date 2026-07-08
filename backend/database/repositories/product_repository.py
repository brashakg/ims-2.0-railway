"""
IMS 2.0 - Product Repository
=============================
Product and Stock data access operations
"""

import re
from typing import List, Optional, Dict
from datetime import datetime, date, timedelta
from .base_repository import BaseRepository

# Sentinel for "caller did not pass is_active" so the legacy active_only
# behaviour of find_by_category is preserved byte-for-byte while new callers
# (the Catalog Manager list) can request an explicit tri-state filter
# (True = active only, False = inactive only, None = everything).
_LEGACY = object()


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

    def _category_filter(self, category: str, is_active: Optional[bool]) -> Dict:
        filter = {"category": category}
        if is_active is not None:
            filter["is_active"] = is_active
        return filter

    def find_by_category(
        self,
        category: str,
        active_only: bool = True,
        *,
        is_active=_LEGACY,
        skip: int = 0,
        limit: int = 100,
    ) -> List[Dict]:
        # Legacy contract preserved: active_only=True -> is_active True filter,
        # active_only=False -> no filter. The keyword-only `is_active` tri-state
        # (True/False/None) wins when passed explicitly (Catalog Manager).
        if is_active is _LEGACY:
            is_active = True if active_only else None
        return self.find_many(
            self._category_filter(category, is_active),
            sort=[("brand", 1), ("model", 1)],
            skip=skip,
            limit=limit,
        )

    def count_by_category(self, category: str, *, is_active: Optional[bool] = True) -> int:
        return self.count(self._category_filter(category, is_active))

    def _brand_filter(
        self, brand: str, category: Optional[str], is_active: Optional[bool]
    ) -> Dict:
        filter: Dict = {"brand": brand}
        if is_active is not None:
            filter["is_active"] = is_active
        if category:
            filter["category"] = category
        return filter

    def find_by_brand(
        self,
        brand: str,
        category: str = None,
        *,
        is_active: Optional[bool] = True,
        skip: int = 0,
        limit: int = 100,
    ) -> List[Dict]:
        return self.find_many(
            self._brand_filter(brand, category, is_active),
            sort=[("model", 1)],
            skip=skip,
            limit=limit,
        )

    def count_by_brand(
        self, brand: str, category: str = None, *, is_active: Optional[bool] = True
    ) -> int:
        return self.count(self._brand_filter(brand, category, is_active))

    def _search_extra_filter(
        self, category: Optional[str], is_active: Optional[bool]
    ) -> Dict:
        filter: Dict = {}
        if is_active is not None:
            filter["is_active"] = is_active
        if category:
            filter["category"] = category
        return filter

    def search_products(
        self,
        query: str,
        category: str = None,
        *,
        is_active: Optional[bool] = True,
        skip: int = 0,
        limit: int = 100,
    ) -> List[Dict]:
        return self.search(
            query,
            list(self.SEARCH_FIELDS),
            self._search_extra_filter(category, is_active),
            skip=skip,
            limit=limit,
        )

    def count_search_products(
        self, query: str, category: str = None, *, is_active: Optional[bool] = True
    ) -> int:
        return self.search_count(
            query, list(self.SEARCH_FIELDS), self._search_extra_filter(category, is_active)
        )

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

    def find_available(self, product_id: str, store_id: str) -> int:
        # The sellable-stock count for the POS path. The positive AVAILABLE match
        # already excludes every E3 non-sellable status (QUARANTINED /
        # UNDER_AUDIT / BLIND_COUNT / TRANSFERRED / SOLD / VOID / DAMAGED / RTV),
        # since none of those equals "AVAILABLE" -- this is the E3 rollup-
        # exclusion guarantee (intent #4 / #12). A quarantined or under-audit
        # unit therefore drops out of POS sellable on-hand immediately.
        return self.count(
            {"product_id": product_id, "store_id": store_id, "status": "AVAILABLE"}
        )

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
        return self.update(
            stock_id,
            {"status": "SOLD", "sold_at": datetime.now(), "order_id": order_id},
        )

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
          1. Among DATED units (expiry_date exists and is not null) claim the
             EARLIEST expiry first (sort ascending). Dispensing near-expiry units
             first is a clinical/inventory-correctness requirement.
          2. Only when no dated unit is available, fall back to the original
             unsorted claim for undated units -- so plain products (frames,
             sunglasses) behave exactly as before.
        A naive single ascending sort would pick null/undated units FIRST under
        BSON ordering (null sorts before dates), hence the two phases. Each phase
        is still one atomic find_one_and_update, so the no-double-claim contract
        is unchanged.
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
        # Phase 1 (FEFO): earliest-expiring DATED unit first.
        dated_flt = dict(flt)
        dated_flt["expiry_date"] = {"$exists": True, "$ne": None}
        try:
            doc = self.collection.find_one_and_update(
                dated_flt, update, sort=[("expiry_date", 1)]
            )
        except Exception:
            doc = None
        if not doc:
            # Phase 2: no dated unit available -> claim any undated unit
            # (identical to the pre-FEFO behaviour).
            try:
                doc = self.collection.find_one_and_update(flt, update)
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
