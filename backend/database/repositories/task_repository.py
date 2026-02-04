"""
IMS 2.0 - Task Repository
==========================
Task and SOP data access operations
"""
from typing import List, Optional, Dict
from datetime import datetime, timedelta
from .base_repository import BaseRepository


class TaskRepository(BaseRepository):
    """Repository for Task operations"""
    
    @property
    def entity_name(self) -> str:
        return "Task"
    
    @property
    def id_field(self) -> str:
        return "task_id"
    
    def find_by_number(self, task_number: str) -> Optional[Dict]:
        return self.find_one({"task_number": task_number})
    
    def find_by_assignee(self, user_id: str, status: str = None, 
                         include_completed: bool = False) -> List[Dict]:
        filter = {"assigned_to": user_id}
        if status:
            filter["status"] = status
        elif not include_completed:
            filter["status"] = {"$nin": ["COMPLETED", "CANCELLED"]}
        return self.find_many(filter, sort=[("priority", 1), ("due_at", 1)])
    
    def find_by_store(self, store_id: str, status: str = None) -> List[Dict]:
        filter = {"store_id": store_id}
        if status:
            filter["status"] = status
        return self.find_many(filter, sort=[("priority", 1), ("due_at", 1)])
    
    def find_open(self, store_id: str = None, user_id: str = None) -> List[Dict]:
        filter = {"status": {"$in": ["OPEN", "IN_PROGRESS"]}}
        if store_id:
            filter["store_id"] = store_id
        if user_id:
            filter["assigned_to"] = user_id
        return self.find_many(filter, sort=[("priority", 1), ("due_at", 1)])
    
    def find_overdue(self, store_id: str = None) -> List[Dict]:
        filter = {
            "status": {"$in": ["OPEN", "IN_PROGRESS"]},
            "due_at": {"$lt": datetime.now()}
        }
        if store_id:
            filter["store_id"] = store_id
        return self.find_many(filter, sort=[("due_at", 1)])
    
    def find_escalated(self, user_id: str = None) -> List[Dict]:
        filter = {"status": "ESCALATED"}
        if user_id:
            filter["escalated_to"] = user_id
        return self.find_many(filter, sort=[("escalated_at", -1)])
    
    def find_by_priority(self, priority: str, store_id: str = None) -> List[Dict]:
        filter = {"priority": priority, "status": {"$nin": ["COMPLETED", "CANCELLED"]}}
        if store_id:
            filter["store_id"] = store_id
        return self.find_many(filter, sort=[("due_at", 1)])
    
    def find_by_entity(self, entity_type: str, entity_id: str) -> List[Dict]:
        return self.find_many({
            "linked_entity_type": entity_type,
            "linked_entity_id": entity_id
        })
    
    # Task operations
    def start_task(self, task_id: str) -> bool:
        return self.update(task_id, {
            "status": "IN_PROGRESS",
            "started_at": datetime.now()
        })
    
    def complete_task(self, task_id: str, notes: str = "") -> bool:
        return self.update(task_id, {
            "status": "COMPLETED",
            "completed_at": datetime.now(),
            "completion_notes": notes
        })
    
    def escalate_task(self, task_id: str, escalate_to: str, level: int) -> bool:
        return self.update(task_id, {
            "status": "ESCALATED",
            "escalated_to": escalate_to,
            "escalated_at": datetime.now(),
            "escalation_level": level
        })
    
    def reassign_task(self, task_id: str, new_assignee: str, reassigned_by: str) -> bool:
        return self.update(task_id, {
            "assigned_to": new_assignee,
            "reassigned_by": reassigned_by,
            "reassigned_at": datetime.now()
        })
    
    # Analytics
    def get_task_summary(self, store_id: str = None) -> Dict:
        filter = {"store_id": store_id} if store_id else {}
        
        pipeline = [
            {"$match": filter},
            {"$group": {
                "_id": "$status",
                "count": {"$sum": 1}
            }}
        ]
        
        results = self.aggregate(pipeline)
        return {r["_id"]: r["count"] for r in results}
    
    def get_overdue_count(self, store_id: str = None, user_id: str = None) -> int:
        filter = {
            "status": {"$in": ["OPEN", "IN_PROGRESS"]},
            "due_at": {"$lt": datetime.now()}
        }
        if store_id:
            filter["store_id"] = store_id
        if user_id:
            filter["assigned_to"] = user_id
        return self.count(filter)
