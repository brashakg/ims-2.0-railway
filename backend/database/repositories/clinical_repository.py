"""
IMS 2.0 - Clinical Repository
==============================
Repositories for eye test queue and clinical examination data
"""
from typing import List, Optional, Dict
from datetime import datetime, date
from .base_repository import BaseRepository


class EyeTestQueueRepository(BaseRepository):
    """
    Repository for eye test queue management
    """

    @property
    def entity_name(self) -> str:
        return "EyeTestQueue"

    @property
    def id_field(self) -> str:
        return "queue_id"

    def add_to_queue(
        self,
        store_id: str,
        patient_name: str,
        customer_phone: str,
        age: Optional[int] = None,
        reason: Optional[str] = None,
        customer_id: Optional[str] = None
    ) -> Optional[Dict]:
        """Add patient to eye test queue"""
        # Get today's token count for this store
        today = date.today().isoformat()
        token_count = self.count({
            "store_id": store_id,
            "created_date": today
        })

        queue_item = {
            "store_id": store_id,
            "token_number": f"T{token_count + 1:03d}",
            "patient_name": patient_name,
            "customer_phone": customer_phone,
            "age": age,
            "reason": reason,
            "customer_id": customer_id,
            "status": "WAITING",
            "created_date": today,
            "wait_time": 0
        }

        return self.create(queue_item)

    def get_store_queue(self, store_id: str, status: Optional[str] = None) -> List[Dict]:
        """Get queue items for a store"""
        today = date.today().isoformat()
        filter_dict = {
            "store_id": store_id,
            "created_date": today
        }
        if status:
            filter_dict["status"] = status

        return self.find_many(
            filter_dict,
            sort=[("created_at", 1)]
        )

    def get_waiting_queue(self, store_id: str) -> List[Dict]:
        """Get waiting patients for a store"""
        return self.get_store_queue(store_id, status="WAITING")

    def update_status(self, queue_id: str, status: str) -> bool:
        """Update queue item status"""
        valid_statuses = ["WAITING", "IN_PROGRESS", "COMPLETED", "CANCELLED", "NO_SHOW"]
        if status not in valid_statuses:
            return False

        update_data = {"status": status}
        if status == "IN_PROGRESS":
            update_data["started_at"] = datetime.now().isoformat()
        elif status == "COMPLETED":
            update_data["completed_at"] = datetime.now().isoformat()

        return self.update(queue_id, update_data)

    def remove_from_queue(self, queue_id: str) -> bool:
        """Remove item from queue (soft delete)"""
        return self.update(queue_id, {"status": "CANCELLED"})

    def get_today_stats(self, store_id: str) -> Dict:
        """Get today's queue statistics"""
        today = date.today().isoformat()
        filter_base = {"store_id": store_id, "created_date": today}

        return {
            "total": self.count(filter_base),
            "waiting": self.count({**filter_base, "status": "WAITING"}),
            "in_progress": self.count({**filter_base, "status": "IN_PROGRESS"}),
            "completed": self.count({**filter_base, "status": "COMPLETED"}),
            "no_show": self.count({**filter_base, "status": "NO_SHOW"})
        }


class EyeTestRepository(BaseRepository):
    """
    Repository for eye test/examination records
    """

    @property
    def entity_name(self) -> str:
        return "EyeTest"

    @property
    def id_field(self) -> str:
        return "test_id"

    def create_test(
        self,
        queue_id: str,
        patient_name: str,
        customer_phone: str,
        store_id: str,
        optometrist_id: str,
        optometrist_name: str,
        customer_id: Optional[str] = None
    ) -> Optional[Dict]:
        """Create a new eye test record when test starts"""
        test_data = {
            "queue_id": queue_id,
            "patient_name": patient_name,
            "customer_phone": customer_phone,
            "customer_id": customer_id,
            "store_id": store_id,
            "optometrist_id": optometrist_id,
            "optometrist_name": optometrist_name,
            "status": "IN_PROGRESS",
            "started_at": datetime.now().isoformat(),
            "test_date": date.today().isoformat()
        }
        return self.create(test_data)

    def complete_test(
        self,
        test_id: str,
        right_eye: Dict,
        left_eye: Dict,
        pd: Optional[float] = None,
        notes: Optional[str] = None,
        lens_recommendation: Optional[str] = None,
        coating_recommendation: Optional[str] = None
    ) -> bool:
        """Complete eye test with prescription data"""
        update_data = {
            "status": "COMPLETED",
            "completed_at": datetime.now().isoformat(),
            "prescription": {
                "right_eye": right_eye,
                "left_eye": left_eye,
                "pd": pd,
                "notes": notes,
                "lens_recommendation": lens_recommendation,
                "coating_recommendation": coating_recommendation
            }
        }
        return self.update(test_id, update_data)

    def get_store_tests(
        self,
        store_id: str,
        test_date: Optional[str] = None,
        status: Optional[str] = None
    ) -> List[Dict]:
        """Get eye tests for a store"""
        filter_dict = {"store_id": store_id}
        if test_date:
            filter_dict["test_date"] = test_date
        if status:
            filter_dict["status"] = status

        return self.find_many(
            filter_dict,
            sort=[("created_at", -1)]
        )

    def get_today_completed_tests(self, store_id: str) -> List[Dict]:
        """Get today's completed tests for a store"""
        today = date.today().isoformat()
        return self.get_store_tests(store_id, test_date=today, status="COMPLETED")

    def get_optometrist_tests(
        self,
        optometrist_id: str,
        from_date: Optional[date] = None,
        to_date: Optional[date] = None
    ) -> List[Dict]:
        """Get tests by optometrist within date range"""
        filter_dict = {"optometrist_id": optometrist_id}

        if from_date and to_date:
            filter_dict["test_date"] = {
                "$gte": from_date.isoformat(),
                "$lte": to_date.isoformat()
            }
        elif from_date:
            filter_dict["test_date"] = {"$gte": from_date.isoformat()}
        elif to_date:
            filter_dict["test_date"] = {"$lte": to_date.isoformat()}

        return self.find_many(filter_dict, sort=[("created_at", -1)])

    def get_patient_tests(self, customer_phone: str) -> List[Dict]:
        """Get all tests for a patient by phone"""
        return self.find_many(
            {"customer_phone": customer_phone},
            sort=[("created_at", -1)]
        )

    def get_customer_tests(self, customer_id: str) -> List[Dict]:
        """Get all tests for a customer by ID"""
        return self.find_many(
            {"customer_id": customer_id},
            sort=[("created_at", -1)]
        )

    def get_test_by_queue_id(self, queue_id: str) -> Optional[Dict]:
        """Get test record by queue ID"""
        return self.find_one({"queue_id": queue_id})

    def get_optometrist_stats(
        self,
        optometrist_id: str,
        from_date: date,
        to_date: date
    ) -> Dict:
        """Get statistics for an optometrist"""
        filter_dict = {
            "optometrist_id": optometrist_id,
            "test_date": {
                "$gte": from_date.isoformat(),
                "$lte": to_date.isoformat()
            }
        }

        all_tests = self.find_many(filter_dict)
        completed_tests = [t for t in all_tests if t.get("status") == "COMPLETED"]

        return {
            "total_tests": len(all_tests),
            "completed_tests": len(completed_tests),
            "completion_rate": round(len(completed_tests) / len(all_tests) * 100, 1) if all_tests else 0
        }
