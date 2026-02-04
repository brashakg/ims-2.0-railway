"""
IMS 2.0 - Audit Repository
===========================
Audit log data access operations
"""
from typing import List, Optional, Dict
from datetime import datetime, date, timedelta
from .base_repository import BaseRepository


class AuditRepository(BaseRepository):
    """Repository for Audit Log operations"""
    
    @property
    def entity_name(self) -> str:
        return "AuditLog"
    
    @property
    def id_field(self) -> str:
        return "log_id"
    
    def find_by_user(self, user_id: str, limit: int = 100) -> List[Dict]:
        return self.find_many(
            {"user_id": user_id},
            sort=[("timestamp", -1)],
            limit=limit
        )
    
    def find_by_store(self, store_id: str, from_date: date = None, 
                      to_date: date = None, limit: int = 100) -> List[Dict]:
        filter = {"store_id": store_id}
        if from_date:
            filter["timestamp"] = {"$gte": datetime.combine(from_date, datetime.min.time())}
        if to_date:
            filter.setdefault("timestamp", {})["$lte"] = datetime.combine(to_date, datetime.max.time())
        return self.find_many(filter, sort=[("timestamp", -1)], limit=limit)
    
    def find_by_entity(self, entity_type: str, entity_id: str) -> List[Dict]:
        return self.find_many(
            {"entity_type": entity_type, "entity_id": entity_id},
            sort=[("timestamp", -1)]
        )
    
    def find_by_action(self, action: str, store_id: str = None, limit: int = 100) -> List[Dict]:
        filter = {"action": action}
        if store_id:
            filter["store_id"] = store_id
        return self.find_many(filter, sort=[("timestamp", -1)], limit=limit)
    
    def find_by_severity(self, severity: str, store_id: str = None) -> List[Dict]:
        filter = {"severity": severity}
        if store_id:
            filter["store_id"] = store_id
        return self.find_many(filter, sort=[("timestamp", -1)])
    
    def find_warnings_and_critical(self, store_id: str = None, days: int = 7) -> List[Dict]:
        cutoff = datetime.now() - timedelta(days=days)
        filter = {
            "severity": {"$in": ["WARNING", "CRITICAL"]},
            "timestamp": {"$gte": cutoff}
        }
        if store_id:
            filter["store_id"] = store_id
        return self.find_many(filter, sort=[("timestamp", -1)])
    
    def get_activity_summary(self, store_id: str, dt: date) -> Dict:
        start = datetime.combine(dt, datetime.min.time())
        end = datetime.combine(dt, datetime.max.time())
        
        pipeline = [
            {"$match": {
                "store_id": store_id,
                "timestamp": {"$gte": start, "$lte": end}
            }},
            {"$group": {
                "_id": "$action",
                "count": {"$sum": 1}
            }}
        ]
        results = self.aggregate(pipeline)
        return {r["_id"]: r["count"] for r in results}
    
    def get_user_activity_count(self, user_id: str, days: int = 7) -> int:
        cutoff = datetime.now() - timedelta(days=days)
        return self.count({"user_id": user_id, "timestamp": {"$gte": cutoff}})


class NotificationRepository(BaseRepository):
    """Repository for Notification operations"""
    
    @property
    def entity_name(self) -> str:
        return "Notification"
    
    @property
    def id_field(self) -> str:
        return "notification_id"
    
    def find_by_user(self, user_id: str, unread_only: bool = False, limit: int = 50) -> List[Dict]:
        filter = {"user_id": user_id}
        if unread_only:
            filter["status"] = {"$ne": "READ"}
        return self.find_many(filter, sort=[("created_at", -1)], limit=limit)
    
    def find_unread(self, user_id: str) -> List[Dict]:
        return self.find_many(
            {"user_id": user_id, "status": {"$ne": "READ"}},
            sort=[("created_at", -1)]
        )
    
    def count_unread(self, user_id: str) -> int:
        return self.count({"user_id": user_id, "status": {"$ne": "READ"}})
    
    def mark_read(self, notification_id: str) -> bool:
        return self.update(notification_id, {
            "status": "READ",
            "read_at": datetime.now()
        })
    
    def mark_all_read(self, user_id: str) -> int:
        return self.update_many(
            {"user_id": user_id, "status": {"$ne": "READ"}},
            {"status": "READ", "read_at": datetime.now()}
        )
    
    def find_pending(self) -> List[Dict]:
        return self.find_many({"status": "PENDING"}, sort=[("created_at", 1)])
    
    def mark_sent(self, notification_id: str) -> bool:
        return self.update(notification_id, {
            "status": "SENT",
            "sent_at": datetime.now()
        })
