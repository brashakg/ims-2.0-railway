"""
IMS 2.0 - Expense Repository
=============================
Expense and Advance data access operations
"""
from typing import List, Optional, Dict
from datetime import datetime, date
from .base_repository import BaseRepository


class ExpenseRepository(BaseRepository):
    """Repository for Expense operations"""
    
    @property
    def entity_name(self) -> str:
        return "Expense"
    
    @property
    def id_field(self) -> str:
        return "expense_id"
    
    def find_by_number(self, expense_number: str) -> Optional[Dict]:
        return self.find_one({"expense_number": expense_number})
    
    def find_by_employee(self, employee_id: str, status: str = None) -> List[Dict]:
        filter = {"employee_id": employee_id}
        if status:
            filter["status"] = status
        return self.find_many(filter, sort=[("expense_date", -1)])
    
    def find_by_store(self, store_id: str, status: str = None) -> List[Dict]:
        filter = {"store_id": store_id}
        if status:
            filter["status"] = status
        return self.find_many(filter, sort=[("expense_date", -1)])
    
    def find_pending_approval(self, store_id: str = None) -> List[Dict]:
        filter = {"status": "SUBMITTED"}
        if store_id:
            filter["store_id"] = store_id
        return self.find_many(filter, sort=[("created_at", 1)])
    
    def find_by_category(self, category: str, store_id: str = None) -> List[Dict]:
        filter = {"category": category}
        if store_id:
            filter["store_id"] = store_id
        return self.find_many(filter, sort=[("expense_date", -1)])
    
    def approve(self, expense_id: str, approved_by: str) -> bool:
        return self.update(expense_id, {
            "status": "APPROVED",
            "approved_by": approved_by,
            "approved_at": datetime.now()
        })
    
    def reject(self, expense_id: str, rejected_by: str, reason: str) -> bool:
        return self.update(expense_id, {
            "status": "REJECTED",
            "rejected_by": rejected_by,
            "rejection_reason": reason
        })
    
    def mark_paid(self, expense_id: str, payment_reference: str) -> bool:
        return self.update(expense_id, {
            "status": "PAID",
            "paid_at": datetime.now(),
            "payment_reference": payment_reference
        })
    
    def get_summary_by_category(self, store_id: str, from_date: date, to_date: date) -> List[Dict]:
        pipeline = [
            {"$match": {
                "store_id": store_id,
                "expense_date": {
                    "$gte": datetime.combine(from_date, datetime.min.time()),
                    "$lte": datetime.combine(to_date, datetime.max.time())
                },
                "status": {"$in": ["APPROVED", "PAID"]}
            }},
            {"$group": {
                "_id": "$category",
                "total": {"$sum": "$amount"},
                "count": {"$sum": 1}
            }},
            {"$sort": {"total": -1}}
        ]
        return self.aggregate(pipeline)


class AdvanceRepository(BaseRepository):
    """Repository for Advance operations"""
    
    @property
    def entity_name(self) -> str:
        return "Advance"
    
    @property
    def id_field(self) -> str:
        return "advance_id"
    
    def find_by_number(self, advance_number: str) -> Optional[Dict]:
        return self.find_one({"advance_number": advance_number})
    
    def find_by_employee(self, employee_id: str) -> List[Dict]:
        return self.find_many({"employee_id": employee_id}, sort=[("requested_date", -1)])
    
    def find_outstanding(self, employee_id: str = None) -> List[Dict]:
        filter = {"status": {"$in": ["DISBURSED", "PARTIALLY_SETTLED"]}}
        if employee_id:
            filter["employee_id"] = employee_id
        return self.find_many(filter)
    
    def find_pending_approval(self) -> List[Dict]:
        return self.find_many({"status": "REQUESTED"}, sort=[("created_at", 1)])
    
    def approve(self, advance_id: str, approved_by: str) -> bool:
        return self.update(advance_id, {
            "status": "APPROVED",
            "approved_by": approved_by,
            "approved_at": datetime.now()
        })
    
    def disburse(self, advance_id: str, reference: str) -> bool:
        return self.update(advance_id, {
            "status": "DISBURSED",
            "disbursed_at": datetime.now(),
            "disbursement_reference": reference
        })
    
    def add_settlement(self, advance_id: str, expense_id: str, amount: float) -> bool:
        try:
            self.collection.update_one(
                {"advance_id": advance_id},
                {
                    "$push": {"settlement_expenses": expense_id},
                    "$inc": {"settled_amount": amount}
                }
            )
            return True
        except:
            return False
