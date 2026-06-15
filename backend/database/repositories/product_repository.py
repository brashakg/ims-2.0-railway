"""
IMS 2.0 - Product Repository
=============================
Product and Stock data access operations
"""

import re
from typing import List, Optional, Dict
from datetime import datetime, date, timedelta
from .base_repository import BaseRepository


class ProductRepository(BaseRepository):
    """Repository for Product operations"""

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

    def find_by_category(self, category: str, active_only: bool = True) -> List[Dict]:
        filter = {"category": category}
        if active_only:
            filter["is_active"] = True
        return self.find_many(filter, sort=[("brand", 1), ("model", 1)])

    def find_by_brand(self, brand: str, category: str = None) -> List[Dict]:
        filter = {"brand": brand, "is_active": True}
        if category:
            filter["category"] = category
        return self.find_many(filter, sort=[("model", 1)])

    def search_products(self, query: str, category: str = None) -> List[Dict]:
        filter = {"is_active": True}
        if category:
            filter["category"] = category
        return self.search(query, ["brand", "model", "sku", "variant"], filter)

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

    def mark_barcode_printed(self, stock_id: str) -> bool:
        return self.update(
            stock_id, {"barcode_printed": True, "barcode_printed_at": datetime.now()}
        )
