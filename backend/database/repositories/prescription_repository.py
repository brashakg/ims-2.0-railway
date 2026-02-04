"""
IMS 2.0 - Prescription Repository
==================================
Prescription data access operations
"""
from typing import List, Optional, Dict
from datetime import datetime, date, timedelta
from .base_repository import BaseRepository


class PrescriptionRepository(BaseRepository):
    """Repository for Prescription operations"""
    
    @property
    def entity_name(self) -> str:
        return "Prescription"
    
    @property
    def id_field(self) -> str:
        return "prescription_id"
    
    def find_by_number(self, prescription_number: str) -> Optional[Dict]:
        return self.find_one({"prescription_number": prescription_number})
    
    def find_by_patient(self, patient_id: str, limit: int = 10) -> List[Dict]:
        return self.find_many(
            {"patient_id": patient_id},
            sort=[("prescription_date", -1)],
            limit=limit
        )
    
    def find_by_customer(self, customer_id: str) -> List[Dict]:
        return self.find_many(
            {"customer_id": customer_id},
            sort=[("prescription_date", -1)]
        )
    
    def find_by_optometrist(self, optometrist_id: str, from_date: date = None, 
                            to_date: date = None) -> List[Dict]:
        filter = {"optometrist_id": optometrist_id}
        if from_date:
            filter["prescription_date"] = {"$gte": datetime.combine(from_date, datetime.min.time())}
        if to_date:
            filter.setdefault("prescription_date", {})["$lte"] = datetime.combine(to_date, datetime.max.time())
        return self.find_many(filter, sort=[("prescription_date", -1)])
    
    def find_by_store(self, store_id: str, from_date: date = None, to_date: date = None) -> List[Dict]:
        filter = {"store_id": store_id}
        if from_date:
            filter["prescription_date"] = {"$gte": datetime.combine(from_date, datetime.min.time())}
        if to_date:
            filter.setdefault("prescription_date", {})["$lte"] = datetime.combine(to_date, datetime.max.time())
        return self.find_many(filter, sort=[("prescription_date", -1)])
    
    def find_valid(self, patient_id: str) -> List[Dict]:
        """Find prescriptions still within validity"""
        return self.find_many({
            "patient_id": patient_id,
            "expiry_date": {"$gte": datetime.now()}
        }, sort=[("prescription_date", -1)])
    
    def find_expiring_soon(self, days: int = 30) -> List[Dict]:
        """Find prescriptions expiring soon"""
        cutoff = datetime.now() + timedelta(days=days)
        return self.find_many({
            "expiry_date": {"$gte": datetime.now(), "$lte": cutoff}
        })
    
    def get_optometrist_stats(self, optometrist_id: str, from_date: date, to_date: date) -> Dict:
        """Get optometrist prescription statistics"""
        pipeline = [
            {"$match": {
                "optometrist_id": optometrist_id,
                "prescription_date": {
                    "$gte": datetime.combine(from_date, datetime.min.time()),
                    "$lte": datetime.combine(to_date, datetime.max.time())
                }
            }},
            {"$group": {
                "_id": None,
                "total": {"$sum": 1},
                "tested_at_store": {"$sum": {"$cond": [{"$eq": ["$source", "TESTED_AT_STORE"]}, 1, 0]}}
            }}
        ]
        results = self.aggregate(pipeline)
        return results[0] if results else {"total": 0, "tested_at_store": 0}
