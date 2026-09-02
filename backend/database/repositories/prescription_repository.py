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
    
    def find_by_store(
        self,
        store_id: str,
        from_date: date = None,
        to_date: date = None,
        created_after: datetime = None,
    ) -> List[Dict]:
        """Prescriptions for a store, optionally windowed.

        TWO DIFFERENT DATE FIELDS, deliberately, because they are stored in two
        different TYPES:

        `prescription_date` is the CLINICAL date (it can be back-dated), and the
        create door writes it with `.isoformat()` -- a STRING. Bounding a string
        field with a datetime matches NOTHING in Mongo: BSON brackets by type
        and never compares the two. Verified against production: of 8 stored
        prescriptions, prescription_date is `str` on 3 and MISSING on 5, and not
        one is a datetime. So the caller's from/to window is matched in the
        frame it is actually stored in, which is also the bug fix for a filter
        that has silently returned nothing all along.

        `created_after` bounds `created_at`, which BaseRepository writes as a
        real BSON Date on 100% of rows. That is the field the 30-day browse
        horizon uses: a security clamp must never hang off a field that is
        absent on most documents, or it fails open on exactly the rows it was
        meant to hide -- and never off one whose type makes it match nothing,
        which would empty the screen instead.
        """
        filter: Dict = {"store_id": store_id}

        # Clinical window, compared as STRINGS (the stored frame). ISO-8601 is
        # lexicographically ordered, so a string compare is a correct date
        # compare for this format.
        if from_date:
            filter["prescription_date"] = {
                "$gte": datetime.combine(from_date, datetime.min.time()).isoformat()
            }
        if to_date:
            filter.setdefault("prescription_date", {})["$lte"] = datetime.combine(
                to_date, datetime.max.time()
            ).isoformat()

        # Security horizon, on the reliably-typed field.
        if created_after:
            filter["created_at"] = {"$gte": created_after}

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
