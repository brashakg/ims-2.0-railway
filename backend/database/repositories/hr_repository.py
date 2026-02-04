"""
IMS 2.0 - HR Repository
========================
HR, Attendance, and Payroll data access operations
"""
from typing import List, Optional, Dict
from datetime import datetime, date, timedelta
from .base_repository import BaseRepository


class AttendanceRepository(BaseRepository):
    """Repository for Attendance operations"""
    
    @property
    def entity_name(self) -> str:
        return "Attendance"
    
    @property
    def id_field(self) -> str:
        return "attendance_id"
    
    def find_by_employee_date(self, employee_id: str, dt: date) -> Optional[Dict]:
        return self.find_one({
            "employee_id": employee_id,
            "date": datetime.combine(dt, datetime.min.time())
        })
    
    def find_by_employee_range(self, employee_id: str, from_date: date, to_date: date) -> List[Dict]:
        return self.find_many({
            "employee_id": employee_id,
            "date": {
                "$gte": datetime.combine(from_date, datetime.min.time()),
                "$lte": datetime.combine(to_date, datetime.max.time())
            }
        }, sort=[("date", 1)])
    
    def find_by_store_date(self, store_id: str, dt: date) -> List[Dict]:
        return self.find_many({
            "store_id": store_id,
            "date": datetime.combine(dt, datetime.min.time())
        })
    
    def mark_check_in(self, employee_id: str, store_id: str, dt: date, time: datetime) -> bool:
        existing = self.find_by_employee_date(employee_id, dt)
        if existing:
            return self.update(existing["attendance_id"], {"check_in": time})
        return self.create({
            "employee_id": employee_id,
            "store_id": store_id,
            "date": datetime.combine(dt, datetime.min.time()),
            "check_in": time,
            "status": "PRESENT"
        }) is not None
    
    def mark_check_out(self, employee_id: str, dt: date, time: datetime) -> bool:
        existing = self.find_by_employee_date(employee_id, dt)
        if existing:
            return self.update(existing["attendance_id"], {"check_out": time})
        return False
    
    def get_monthly_summary(self, employee_id: str, year: int, month: int) -> Dict:
        from calendar import monthrange
        start = date(year, month, 1)
        end = date(year, month, monthrange(year, month)[1])
        
        records = self.find_by_employee_range(employee_id, start, end)
        
        return {
            "total_days": len(records),
            "present": len([r for r in records if r.get("status") == "PRESENT"]),
            "absent": len([r for r in records if r.get("status") == "ABSENT"]),
            "late": len([r for r in records if r.get("is_late")]),
            "half_day": len([r for r in records if r.get("status") == "HALF_DAY"])
        }


class LeaveRepository(BaseRepository):
    """Repository for Leave operations"""
    
    @property
    def entity_name(self) -> str:
        return "Leave"
    
    @property
    def id_field(self) -> str:
        return "leave_id"
    
    def find_by_employee(self, employee_id: str, year: int = None) -> List[Dict]:
        filter = {"employee_id": employee_id}
        if year:
            filter["from_date"] = {
                "$gte": datetime(year, 1, 1),
                "$lt": datetime(year + 1, 1, 1)
            }
        return self.find_many(filter, sort=[("from_date", -1)])
    
    def find_pending_approval(self, store_id: str = None) -> List[Dict]:
        filter = {"status": "PENDING"}
        if store_id:
            filter["store_id"] = store_id
        return self.find_many(filter, sort=[("created_at", 1)])
    
    def approve(self, leave_id: str, approved_by: str) -> bool:
        return self.update(leave_id, {
            "status": "APPROVED",
            "approved_by": approved_by,
            "approved_at": datetime.now()
        })
    
    def reject(self, leave_id: str, rejected_by: str, reason: str) -> bool:
        return self.update(leave_id, {
            "status": "REJECTED",
            "rejected_by": rejected_by,
            "rejection_reason": reason
        })
    
    def get_leave_balance(self, employee_id: str, year: int) -> Dict:
        pipeline = [
            {"$match": {
                "employee_id": employee_id,
                "status": "APPROVED",
                "from_date": {
                    "$gte": datetime(year, 1, 1),
                    "$lt": datetime(year + 1, 1, 1)
                }
            }},
            {"$group": {
                "_id": "$leave_type",
                "days_taken": {"$sum": "$days"}
            }}
        ]
        results = self.aggregate(pipeline)
        return {r["_id"]: r["days_taken"] for r in results}


class PayrollRepository(BaseRepository):
    """Repository for Payroll operations"""
    
    @property
    def entity_name(self) -> str:
        return "Payroll"
    
    @property
    def id_field(self) -> str:
        return "payroll_id"
    
    def find_by_employee_period(self, employee_id: str, year: int, month: int) -> Optional[Dict]:
        return self.find_one({
            "employee_id": employee_id,
            "year": year,
            "month": month
        })
    
    def find_by_period(self, year: int, month: int, store_id: str = None) -> List[Dict]:
        filter = {"year": year, "month": month}
        if store_id:
            filter["store_id"] = store_id
        return self.find_many(filter)
    
    def find_pending_approval(self, year: int, month: int) -> List[Dict]:
        return self.find_many({
            "year": year,
            "month": month,
            "status": "PENDING"
        })
    
    def approve(self, payroll_id: str, approved_by: str) -> bool:
        return self.update(payroll_id, {
            "status": "APPROVED",
            "approved_by": approved_by,
            "approved_at": datetime.now()
        })
    
    def mark_paid(self, payroll_id: str, payment_reference: str) -> bool:
        return self.update(payroll_id, {
            "status": "PAID",
            "paid_at": datetime.now(),
            "payment_reference": payment_reference
        })
