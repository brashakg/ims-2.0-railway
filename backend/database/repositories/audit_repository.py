"""
IMS 2.0 - Audit Repository
===========================
Audit log data access operations.

APPEND-ONLY (SYSTEM_INTENT 10): the `audit_logs` collection is the canonical
human-action audit trail and MUST be immutable, even for Superadmin. This repo
deliberately exposes NO update/delete path for audit rows -- the inherited
update()/delete()/soft_delete() helpers from BaseRepository must never be
called on audit data, and no API route mutates this collection (see
backend/api/routers/audit.py). Rows are created and read only.

Every create() funnels through the tamper-evident hash-chain (audit_chain.py),
which stamps seq / prev_hash / entry_hash so any post-hoc edit is detectable
via GET /api/v1/audit/verify. Chaining is fail-soft: if the chain head can't be
reached the row is still written best-effort (the business action never blocks).
"""
from typing import List, Optional, Dict
from datetime import datetime, date, timedelta
from .base_repository import BaseRepository
from .audit_chain import append_audit_entry


class AuditRepository(BaseRepository):
    """Repository for Audit Log operations"""

    @property
    def entity_name(self) -> str:
        return "AuditLog"

    @property
    def id_field(self) -> str:
        return "log_id"

    def create(self, data: Dict) -> Optional[Dict]:
        """Append a hash-chained audit row.

        Overrides BaseRepository.create so EVERY existing caller
        (audit_repo.create({...}) across walkouts/workshop/returns/payout/
        products/etc.) is chained automatically with no call-site changes.

        We replicate BaseRepository's id/timestamp/_id stamping, then hand the
        finished doc to append_audit_entry which claims the next seq + prev_hash
        from the atomic `audit_chain_head` and computes entry_hash before the
        actual insert. Fail-soft: any chain/insert error returns None (or an
        unchained row) and never raises, so an audit failure can't undo the
        business write that triggered it.
        """
        try:
            if self.id_field not in data:
                data[self.id_field] = self._generate_id()
            data = self._add_timestamps(data)
            data["_id"] = data[self.id_field]
            db = getattr(self.collection, "database", None)
            return append_audit_entry(self.collection, data, db=db)
        except Exception as e:  # noqa: BLE001
            print(f"Error creating {self.entity_name}: {e}")
            return None

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
