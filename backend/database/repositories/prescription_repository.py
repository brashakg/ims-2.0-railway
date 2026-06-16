"""
IMS 2.0 - Prescription Repository
==================================
Prescription data access operations
"""
from typing import List, Optional, Dict
from datetime import datetime, date, timedelta
from api.utils.ist import now_ist_naive
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

    def find_by_eye_test(self, eye_test_id: str) -> Optional[Dict]:
        """Return the prescription auto-created for a given eye test, if any.

        The clinical completion flow stamps ``eye_test_id`` onto the Rx it
        auto-creates. Looking it up lets ``complete_test`` stay idempotent: a
        retried / double-clicked completion must not mint a SECOND prescription
        for the same test.
        """
        if not eye_test_id:
            return None
        return self.find_one({"eye_test_id": eye_test_id})

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
        """Find prescriptions still within validity.

        IST (TZ-P3): Rx validity is a business-calendar boundary; the server
        clock is UTC, so between 00:00-05:30 IST a plain datetime.now() is
        still on YESTERDAY and would mis-judge expiry by a day.
        """
        # expiry_date is stored as an ISO STRING (prescriptions.py / clinical.py
        # write .isoformat()). A datetime $gte bound never matches a string field
        # in BSON (type bracketing) -> this silently returned []. Compare as ISO
        # strings (lexicographic == chronological for fixed-format ISO), mirroring
        # product_repository.find_expiring and megaphone.py which already do this.
        return self.find_many({
            "patient_id": patient_id,
            "expiry_date": {"$gte": now_ist_naive().isoformat()}
        }, sort=[("prescription_date", -1)])

    def find_expiring_soon(self, days: int = 30) -> List[Dict]:
        """Find prescriptions expiring soon (IST business clock, see find_valid)"""
        # ISO-string comparison: expiry_date is stored as a string (see find_valid).
        now = now_ist_naive().isoformat()
        cutoff = (now_ist_naive() + timedelta(days=days)).isoformat()
        return self.find_many({
            "expiry_date": {"$gte": now, "$lte": cutoff}
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
