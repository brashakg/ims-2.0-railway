"""
IMS 2.0 - Vendor Repository
============================
Vendor, PO, and GRN data access operations
"""
from typing import List, Optional, Dict
from datetime import datetime, date, timedelta
from .base_repository import BaseRepository


class VendorRepository(BaseRepository):
    """Repository for Vendor operations"""
    
    @property
    def entity_name(self) -> str:
        return "Vendor"
    
    @property
    def id_field(self) -> str:
        return "vendor_id"
    
    def find_by_code(self, vendor_code: str) -> Optional[Dict]:
        return self.find_one({"vendor_code": vendor_code})
    
    def find_by_gstin(self, gstin: str) -> Optional[Dict]:
        return self.find_one({"gstin": gstin})
    
    def find_active(self, filter: Dict = None) -> List[Dict]:
        query = {"is_active": True}
        if filter:
            query.update(filter)
        return self.find_many(query, sort=[("trade_name", 1)])
    
    def search_vendors(self, query: str) -> List[Dict]:
        return self.search(query, ["legal_name", "trade_name", "gstin", "vendor_code"])
    
    def get_outstanding_balance(self, vendor_id: str) -> float:
        vendor = self.find_by_id(vendor_id)
        return float(vendor.get("current_balance", 0)) if vendor else 0
    
    def update_balance(self, vendor_id: str, amount: float, is_credit: bool = False) -> bool:
        """Update vendor balance (credit reduces, debit increases)"""
        try:
            change = -amount if is_credit else amount
            self.collection.update_one(
                {"vendor_id": vendor_id},
                {"$inc": {"current_balance": change}}
            )
            return True
        except:
            return False


class PurchaseOrderRepository(BaseRepository):
    """Repository for Purchase Order operations"""
    
    @property
    def entity_name(self) -> str:
        return "PurchaseOrder"
    
    @property
    def id_field(self) -> str:
        return "po_id"
    
    def find_by_number(self, po_number: str) -> Optional[Dict]:
        return self.find_one({"po_number": po_number})
    
    def find_by_vendor(self, vendor_id: str, status: str = None) -> List[Dict]:
        filter = {"vendor_id": vendor_id}
        if status:
            filter["status"] = status
        return self.find_many(filter, sort=[("created_at", -1)])
    
    def find_pending(self, vendor_id: str = None) -> List[Dict]:
        filter = {"status": {"$in": ["SENT", "ACKNOWLEDGED", "PARTIALLY_RECEIVED"]}}
        if vendor_id:
            filter["vendor_id"] = vendor_id
        return self.find_many(filter, sort=[("expected_date", 1)])
    
    def find_overdue(self) -> List[Dict]:
        return self.find_many({
            "status": {"$in": ["SENT", "ACKNOWLEDGED", "PARTIALLY_RECEIVED"]},
            "expected_date": {"$lt": datetime.now()}
        }, sort=[("expected_date", 1)])
    
    def update_status(self, po_id: str, status: str) -> bool:
        return self.update(po_id, {"status": status})
    
    def add_item(self, po_id: str, item: Dict) -> bool:
        try:
            self.collection.update_one(
                {"po_id": po_id},
                {"$push": {"items": item}}
            )
            return True
        except:
            return False


class GRNRepository(BaseRepository):
    """Repository for GRN operations"""
    
    @property
    def entity_name(self) -> str:
        return "GRN"
    
    @property
    def id_field(self) -> str:
        return "grn_id"
    
    def find_by_number(self, grn_number: str) -> Optional[Dict]:
        return self.find_one({"grn_number": grn_number})
    
    def find_by_po(self, po_id: str) -> List[Dict]:
        return self.find_many({"po_id": po_id}, sort=[("created_at", -1)])
    
    def find_by_vendor(self, vendor_id: str) -> List[Dict]:
        return self.find_many({"vendor_id": vendor_id}, sort=[("created_at", -1)])
    
    def find_by_store(self, store_id: str, status: str = None) -> List[Dict]:
        filter = {"store_id": store_id}
        if status:
            filter["status"] = status
        return self.find_many(filter, sort=[("created_at", -1)])
    
    def find_pending_acceptance(self, store_id: str) -> List[Dict]:
        return self.find_many({
            "store_id": store_id,
            "status": {"$in": ["DRAFT", "PENDING_QC", "QC_PASSED"]}
        })
    
    def find_disputed(self, store_id: str = None) -> List[Dict]:
        filter = {"status": "DISPUTED"}
        if store_id:
            filter["store_id"] = store_id
        return self.find_many(filter)
    
    def accept(self, grn_id: str, accepted_by: str) -> bool:
        return self.update(grn_id, {
            "status": "ACCEPTED",
            "accepted_by": accepted_by,
            "accepted_at": datetime.now()
        })
    
    def escalate_mismatch(self, grn_id: str, note: str) -> bool:
        return self.update(grn_id, {
            "status": "DISPUTED",
            "has_mismatch": True,
            "mismatch_escalated": True,
            "escalation_note": note
        })
