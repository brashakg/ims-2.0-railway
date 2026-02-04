"""
IMS 2.0 - Store Repository
===========================
Store data access operations
"""
from typing import List, Optional, Dict
from .base_repository import BaseRepository


class StoreRepository(BaseRepository):
    """Repository for Store operations"""
    
    @property
    def entity_name(self) -> str:
        return "Store"
    
    @property
    def id_field(self) -> str:
        return "store_id"
    
    def find_by_code(self, store_code: str) -> Optional[Dict]:
        return self.find_one({"store_code": store_code})
    
    def find_by_brand(self, brand: str) -> List[Dict]:
        return self.find_many({"brand": brand, "is_active": True}, sort=[("store_name", 1)])
    
    def find_by_city(self, city: str) -> List[Dict]:
        return self.find_many({"city": city, "is_active": True})
    
    def find_hq(self) -> Optional[Dict]:
        return self.find_one({"is_hq": True})
    
    def find_active(self, filter: Dict = None) -> List[Dict]:
        query = {"is_active": True}
        if filter:
            query.update(filter)
        return self.find_many(query, sort=[("brand", 1), ("store_name", 1)])
    
    def has_category(self, store_id: str, category: str) -> bool:
        store = self.find_by_id(store_id)
        if store:
            return category in store.get("enabled_categories", [])
        return False
    
    def enable_category(self, store_id: str, category: str) -> bool:
        try:
            self.collection.update_one(
                {"store_id": store_id},
                {"$addToSet": {"enabled_categories": category}}
            )
            return True
        except:
            return False
    
    def disable_category(self, store_id: str, category: str) -> bool:
        try:
            self.collection.update_one(
                {"store_id": store_id},
                {"$pull": {"enabled_categories": category}}
            )
            return True
        except:
            return False
    
    def get_store_summary(self) -> Dict:
        """Get summary of all stores"""
        pipeline = [
            {"$match": {"is_active": True}},
            {"$group": {
                "_id": "$brand",
                "count": {"$sum": 1},
                "cities": {"$addToSet": "$city"}
            }}
        ]
        results = self.aggregate(pipeline)
        return {r["_id"]: {"count": r["count"], "cities": r["cities"]} for r in results}
