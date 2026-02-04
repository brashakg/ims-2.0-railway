"""
IMS 2.0 - Product Repository
=============================
Product and Stock data access operations
"""
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
    
    def update_price(self, product_id: str, mrp: float, offer_price: float, 
                     updated_by: str) -> bool:
        if offer_price > mrp:
            raise ValueError("Offer price cannot exceed MRP")
        return self.update(product_id, {
            "mrp": mrp,
            "offer_price": offer_price,
            "price_updated_at": datetime.now(),
            "price_updated_by": updated_by
        })
    
    def get_brands(self, category: str = None) -> List[str]:
        filter = {"is_active": True}
        if category:
            filter["category"] = category
        pipeline = [
            {"$match": filter},
            {"$group": {"_id": "$brand"}},
            {"$sort": {"_id": 1}}
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
        return self.find_many({
            "product_id": product_id,
            "store_id": store_id,
            "status": "AVAILABLE"
        })
    
    def find_available(self, product_id: str, store_id: str) -> int:
        return self.count({
            "product_id": product_id,
            "store_id": store_id,
            "status": "AVAILABLE"
        })
    
    def find_low_stock(self, store_id: str, threshold: int = 5) -> List[Dict]:
        pipeline = [
            {"$match": {"store_id": store_id, "status": "AVAILABLE"}},
            {"$group": {
                "_id": "$product_id",
                "quantity": {"$sum": "$quantity"}
            }},
            {"$match": {"quantity": {"$lte": threshold}}},
            {"$sort": {"quantity": 1}}
        ]
        return self.aggregate(pipeline)
    
    def find_expiring(self, store_id: str, days: int = 30) -> List[Dict]:
        cutoff = datetime.now() + timedelta(days=days)
        return self.find_many({
            "store_id": store_id,
            "expiry_date": {"$lte": cutoff, "$gte": datetime.now()},
            "status": "AVAILABLE"
        })
    
    def reserve_stock(self, stock_id: str) -> bool:
        return self.update(stock_id, {"status": "RESERVED", "reserved_at": datetime.now()})
    
    def release_stock(self, stock_id: str) -> bool:
        return self.update(stock_id, {"status": "AVAILABLE", "reserved_at": None})
    
    def mark_sold(self, stock_id: str, order_id: str) -> bool:
        return self.update(stock_id, {"status": "SOLD", "sold_at": datetime.now(), "order_id": order_id})
    
    def mark_barcode_printed(self, stock_id: str) -> bool:
        return self.update(stock_id, {"barcode_printed": True, "barcode_printed_at": datetime.now()})
