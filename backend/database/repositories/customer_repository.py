"""
IMS 2.0 - Customer Repository
==============================
Customer and Patient data access operations
"""
from typing import List, Optional, Dict
from datetime import datetime
from .base_repository import BaseRepository


class CustomerRepository(BaseRepository):
    """Repository for Customer operations"""
    
    @property
    def entity_name(self) -> str:
        return "Customer"
    
    @property
    def id_field(self) -> str:
        return "customer_id"
    
    def find_by_mobile(self, mobile: str) -> Optional[Dict]:
        return self.find_one({"mobile": mobile})
    
    def find_by_email(self, email: str) -> Optional[Dict]:
        return self.find_one({"email": email})
    
    def find_by_gstin(self, gstin: str) -> Optional[Dict]:
        return self.find_one({"gstin": gstin})
    
    def search_customers(self, query: str, store_id: str = None) -> List[Dict]:
        filter = {"home_store_id": store_id} if store_id else None
        return self.search(query, ["name", "mobile", "email"], filter)
    
    def find_b2b_customers(self, store_id: str = None) -> List[Dict]:
        filter = {"customer_type": "B2B"}
        if store_id:
            filter["home_store_id"] = store_id
        return self.find_many(filter, sort=[("name", 1)])
    
    def find_recent(self, store_id: str, limit: int = 20) -> List[Dict]:
        filter = {"home_store_id": store_id} if store_id else {}
        return self.find_many(filter, sort=[("created_at", -1)], limit=limit)
    
    # Patient operations
    def add_patient(self, customer_id: str, patient: Dict) -> bool:
        try:
            self.collection.update_one(
                {"customer_id": customer_id},
                {"$push": {"patients": patient}}
            )
            return True
        except:
            return False
    
    def find_patient(self, customer_id: str, patient_id: str) -> Optional[Dict]:
        customer = self.find_by_id(customer_id)
        if customer:
            for patient in customer.get("patients", []):
                if patient.get("patient_id") == patient_id:
                    return patient
        return None
    
    # Loyalty
    def add_loyalty_points(self, customer_id: str, points: int) -> bool:
        try:
            self.collection.update_one(
                {"customer_id": customer_id},
                {"$inc": {"loyalty_points": points}}
            )
            return True
        except:
            return False
    
    def add_store_credit(self, customer_id: str, amount: float) -> bool:
        try:
            self.collection.update_one(
                {"customer_id": customer_id},
                {"$inc": {"store_credit": amount}}
            )
            return True
        except:
            return False
    
    def update_total_purchases(self, customer_id: str, amount: float) -> bool:
        try:
            self.collection.update_one(
                {"customer_id": customer_id},
                {"$inc": {"total_purchases": amount}}
            )
            return True
        except:
            return False
