"""
IMS 2.0 - Workshop Repository
==============================
Workshop job data access operations
"""
from typing import List, Optional, Dict
from datetime import datetime, date
from .base_repository import BaseRepository


class WorkshopJobRepository(BaseRepository):
    """Repository for Workshop Job operations"""
    
    @property
    def entity_name(self) -> str:
        return "WorkshopJob"
    
    @property
    def id_field(self) -> str:
        return "job_id"
    
    def find_by_number(self, job_number: str) -> Optional[Dict]:
        return self.find_one({"job_number": job_number})
    
    def find_by_order(self, order_id: str) -> List[Dict]:
        return self.find_many({"order_id": order_id})
    
    def find_by_store(self, store_id: str, status: str = None) -> List[Dict]:
        filter = {"store_id": store_id}
        if status:
            filter["status"] = status
        return self.find_many(filter, sort=[("created_at", -1)])
    
    def find_pending(self, store_id: str = None) -> List[Dict]:
        filter = {"status": {"$in": ["PENDING", "IN_PROGRESS"]}}
        if store_id:
            filter["store_id"] = store_id
        return self.find_many(filter, sort=[("expected_date", 1)])
    
    def find_ready(self, store_id: str = None) -> List[Dict]:
        filter = {"status": "READY"}
        if store_id:
            filter["store_id"] = store_id
        return self.find_many(filter, sort=[("completed_at", -1)])
    
    def find_overdue(self, store_id: str = None) -> List[Dict]:
        filter = {
            "status": {"$in": ["PENDING", "IN_PROGRESS"]},
            "expected_date": {"$lt": datetime.now()}
        }
        if store_id:
            filter["store_id"] = store_id
        return self.find_many(filter, sort=[("expected_date", 1)])
    
    def update_status(self, job_id: str, status: str, by_user: str = None) -> bool:
        update = {"status": status, "status_updated_at": datetime.now()}
        if by_user:
            update["status_updated_by"] = by_user
        if status == "COMPLETED":
            update["completed_at"] = datetime.now()
        return self.update(job_id, update)
    
    def assign_technician(self, job_id: str, technician_id: str) -> bool:
        return self.update(job_id, {
            "technician_id": technician_id,
            "assigned_at": datetime.now()
        })
    
    def add_qc_result(self, job_id: str, passed: bool, notes: str, by_user: str) -> bool:
        return self.update(job_id, {
            "qc_passed": passed,
            "qc_notes": notes,
            "qc_by": by_user,
            "qc_at": datetime.now(),
            "status": "READY" if passed else "QC_FAILED"
        })
    
    def get_technician_workload(self, store_id: str) -> List[Dict]:
        pipeline = [
            {"$match": {
                "store_id": store_id,
                "status": {"$in": ["PENDING", "IN_PROGRESS"]}
            }},
            {"$group": {
                "_id": "$technician_id",
                "job_count": {"$sum": 1},
                "oldest_job": {"$min": "$created_at"}
            }},
            {"$sort": {"job_count": -1}}
        ]
        return self.aggregate(pipeline)
