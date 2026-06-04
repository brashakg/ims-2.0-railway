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
        # TechCherry-imported customers store the number under `phone`;
        # natively-created docs use `mobile`. Match either so a lookup by
        # number resolves both data sources.
        return self.find_one({"$or": [{"phone": mobile}, {"mobile": mobile}]})
    
    def find_by_email(self, email: str) -> Optional[Dict]:
        return self.find_one({"email": email})
    
    def find_by_gstin(self, gstin: str) -> Optional[Dict]:
        return self.find_one({"gstin": gstin})
    
    def search_customers(self, query: str, store_id: str = None) -> List[Dict]:
        # Match the account holder's name + both phone fields + email, AND any
        # family member under the account (patients[].name / .mobile) -- searching
        # a patient's name or number must surface their customer record (the bug:
        # patient data was never searched). Native docs store the number in
        # `mobile`; TechCherry-imported docs in `phone`. Store scope matches
        # home_store_id (native) or preferred_store_id (import).
        store_filter = None
        if store_id:
            store_filter = {
                "$or": [
                    {"home_store_id": store_id},
                    {"preferred_store_id": store_id},
                ]
            }
        return self.search(
            query,
            ["name", "mobile", "phone", "email", "patients.name", "patients.mobile"],
            store_filter,
        )
    
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
        except Exception:
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
        except Exception:
            return False
    
    def add_store_credit(self, customer_id: str, amount: float) -> bool:
        try:
            self.collection.update_one(
                {"customer_id": customer_id},
                {"$inc": {"store_credit": amount}}
            )
            return True
        except Exception:
            return False

    # Sentinel: the collection had no atomic-guard support (minimal/mock coll),
    # so the caller must fall back to the legacy snapshot path rather than treat
    # the result as "insufficient".
    DEBIT_NO_ATOMIC = "__no_atomic__"

    def try_debit_store_credit(self, customer_id: str, amount: float):
        """Atomically debit `amount` of store credit, guard-in-the-filter
        (mirrors the voucher redeem). The decrement runs ONLY when the filter
        still sees store_credit >= amount at modify time, so two concurrent
        redeems can never both succeed and drive the balance negative.

        Returns:
          * the POST-update customer doc (dict) on success -> read the fresh
            balance from doc["store_credit"], never a stale snapshot;
          * None when the credit was insufficient (no document matched) -> the
            caller surfaces a 400;
          * DEBIT_NO_ATOMIC when the bound collection cannot do a conditional
            update (a minimal stand-in) -> the caller falls back to the legacy
            read-modify-write path instead of wrongly rejecting.

        `amount` must be > 0. A conditional update_one (atomic in Mongo) is used
        rather than find_one_and_update so the guard works on any collection
        that honours a filtered update; the fresh doc is then re-read.
        """
        try:
            amt = round(float(amount), 2)
        except (TypeError, ValueError):
            return None
        if amt <= 0:
            return None

        updater = getattr(self.collection, "update_one", None)
        if not callable(updater):
            return self.DEBIT_NO_ATOMIC
        try:
            res = updater(
                {"customer_id": customer_id, "store_credit": {"$gte": amt}},
                {"$inc": {"store_credit": -amt}},
            )
        except Exception:
            # Driver/mock can't evaluate the conditional -> let caller fall back.
            return self.DEBIT_NO_ATOMIC
        matched = getattr(res, "matched_count", None)
        if matched is None:
            matched = getattr(res, "modified_count", 0)
        if not matched:
            return None
        return self.find_by_id(customer_id)

    def update_total_purchases(self, customer_id: str, amount: float) -> bool:
        try:
            self.collection.update_one(
                {"customer_id": customer_id},
                {"$inc": {"total_purchases": amount}}
            )
            return True
        except Exception:
            return False
