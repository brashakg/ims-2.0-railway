"""
IMS 2.0 - Order Repository
===========================
Order data access operations
"""
from typing import List, Optional, Dict, Tuple
from datetime import datetime, date, timedelta
from decimal import Decimal
from .base_repository import BaseRepository


class OrderRepository(BaseRepository):
    """Repository for Order operations"""
    
    @property
    def entity_name(self) -> str:
        return "Order"
    
    @property
    def id_field(self) -> str:
        return "order_id"
    
    # =========================================================================
    # Order-specific queries
    # =========================================================================
    
    def find_by_order_number(self, order_number: str) -> Optional[Dict]:
        """Find order by order number"""
        return self.find_one({"order_number": order_number})
    
    def find_by_customer(self, customer_id: str, limit: int = 50) -> List[Dict]:
        """Find orders for customer"""
        return self.find_many(
            {"customer_id": customer_id},
            sort=[("created_at", -1)],
            limit=limit
        )
    
    def find_by_store(self, store_id: str, from_date: date = None, 
                      to_date: date = None, status: str = None) -> List[Dict]:
        """Find orders for store with optional filters"""
        filter = {"store_id": store_id}
        
        if from_date:
            filter["created_at"] = {"$gte": datetime.combine(from_date, datetime.min.time())}
        if to_date:
            filter.setdefault("created_at", {})["$lte"] = datetime.combine(to_date, datetime.max.time())
        if status:
            filter["status"] = status
        
        return self.find_many(filter, sort=[("created_at", -1)])
    
    def find_by_salesperson(self, user_id: str, from_date: date = None, 
                            to_date: date = None) -> List[Dict]:
        """Find orders by salesperson"""
        filter = {"salesperson_id": user_id}
        
        if from_date:
            filter["created_at"] = {"$gte": datetime.combine(from_date, datetime.min.time())}
        if to_date:
            filter.setdefault("created_at", {})["$lte"] = datetime.combine(to_date, datetime.max.time())
        
        return self.find_many(filter, sort=[("created_at", -1)])
    
    # =========================================================================
    # Status-based queries
    # =========================================================================
    
    def find_pending(self, store_id: str = None) -> List[Dict]:
        """Find pending orders"""
        filter = {"status": {"$in": ["CONFIRMED", "PROCESSING"]}}
        if store_id:
            filter["store_id"] = store_id
        return self.find_many(filter, sort=[("created_at", 1)])
    
    def find_ready_for_delivery(self, store_id: str = None) -> List[Dict]:
        """Find orders ready for delivery"""
        filter = {"status": "READY"}
        if store_id:
            filter["store_id"] = store_id
        return self.find_many(filter, sort=[("created_at", 1)])
    
    def find_unpaid(self, store_id: str = None) -> List[Dict]:
        """Find unpaid orders"""
        filter = {"payment_status": {"$in": ["UNPAID", "PARTIAL"]}}
        if store_id:
            filter["store_id"] = store_id
        return self.find_many(filter, sort=[("created_at", -1)])
    
    def find_overdue(self, store_id: str = None) -> List[Dict]:
        """Find overdue orders (past expected delivery)"""
        filter = {
            "status": {"$in": ["CONFIRMED", "PROCESSING", "READY"]},
            "expected_delivery": {"$lt": datetime.now()}
        }
        if store_id:
            filter["store_id"] = store_id
        return self.find_many(filter, sort=[("expected_delivery", 1)])
    
    # =========================================================================
    # Order operations
    # =========================================================================
    
    def update_status(self, order_id: str, status: str, by_user: str = None) -> bool:
        """Update order status"""
        update_data = {
            "status": status,
            "status_updated_at": datetime.now()
        }
        if by_user:
            update_data["status_updated_by"] = by_user
        
        if status == "DELIVERED":
            update_data["delivered_at"] = datetime.now()
        
        return self.update(order_id, update_data)
    
    def add_payment(self, order_id: str, payment: Dict) -> bool:
        """Add payment to order"""
        try:
            order = self.find_by_id(order_id)
            if not order:
                return False
            
            # Add payment
            self.collection.update_one(
                {"order_id": order_id},
                {"$push": {"payments": payment}}
            )
            
            # Update totals
            amount_paid = float(order.get("amount_paid", 0)) + float(payment["amount"])
            grand_total = float(order.get("grand_total", 0))
            balance_due = grand_total - amount_paid
            
            payment_status = "PAID" if balance_due <= 0 else "PARTIAL" if amount_paid > 0 else "UNPAID"
            
            return self.update(order_id, {
                "amount_paid": amount_paid,
                "balance_due": max(0, balance_due),
                "payment_status": payment_status
            })
        except Exception as e:
            print(f"Error adding payment: {e}")
            return False
    
    def set_invoice(self, order_id: str, invoice_number: str) -> bool:
        """Set invoice number"""
        return self.update(order_id, {
            "invoice_number": invoice_number,
            "invoice_date": datetime.now()
        })
    
    # =========================================================================
    # Analytics
    # =========================================================================
    
    def get_sales_summary(self, store_id: str, from_date: date, to_date: date) -> Dict:
        """Get sales summary for period"""
        pipeline = [
            {"$match": {
                "store_id": store_id,
                "created_at": {
                    "$gte": datetime.combine(from_date, datetime.min.time()),
                    "$lte": datetime.combine(to_date, datetime.max.time())
                },
                "status": {"$nin": ["CANCELLED", "DRAFT"]}
            }},
            {"$group": {
                "_id": None,
                "total_orders": {"$sum": 1},
                "total_revenue": {"$sum": "$grand_total"},
                "total_paid": {"$sum": "$amount_paid"},
                "avg_order_value": {"$avg": "$grand_total"},
                "total_items": {"$sum": {"$size": "$items"}}
            }}
        ]
        
        results = self.aggregate(pipeline)
        if results:
            return results[0]
        return {
            "total_orders": 0,
            "total_revenue": 0,
            "total_paid": 0,
            "avg_order_value": 0,
            "total_items": 0
        }
    
    def get_salesperson_performance(self, store_id: str, from_date: date, to_date: date) -> List[Dict]:
        """Get salesperson performance"""
        pipeline = [
            {"$match": {
                "store_id": store_id,
                "created_at": {
                    "$gte": datetime.combine(from_date, datetime.min.time()),
                    "$lte": datetime.combine(to_date, datetime.max.time())
                },
                "status": {"$nin": ["CANCELLED", "DRAFT"]}
            }},
            {"$group": {
                "_id": "$salesperson_id",
                "order_count": {"$sum": 1},
                "total_sales": {"$sum": "$grand_total"},
                "avg_order": {"$avg": "$grand_total"}
            }},
            {"$sort": {"total_sales": -1}}
        ]
        
        return self.aggregate(pipeline)
    
    def get_daily_sales(self, store_id: str, days: int = 30) -> List[Dict]:
        """Get daily sales for last N days"""
        start_date = datetime.now() - timedelta(days=days)
        
        pipeline = [
            {"$match": {
                "store_id": store_id,
                "created_at": {"$gte": start_date},
                "status": {"$nin": ["CANCELLED", "DRAFT"]}
            }},
            {"$group": {
                "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}},
                "order_count": {"$sum": 1},
                "total_sales": {"$sum": "$grand_total"}
            }},
            {"$sort": {"_id": 1}}
        ]
        
        return self.aggregate(pipeline)
    
    def get_status_counts(self, store_id: str = None) -> Dict:
        """Get order counts by status"""
        pipeline = [
            {"$match": {"store_id": store_id} if store_id else {}},
            {"$group": {
                "_id": "$status",
                "count": {"$sum": 1}
            }}
        ]
        
        results = self.aggregate(pipeline)
        return {r["_id"]: r["count"] for r in results}
    
    # =========================================================================
    # Search
    # =========================================================================
    
    def search_orders(self, query: str, store_id: str = None) -> List[Dict]:
        """Search orders by number, customer name, or phone"""
        return self.search(query, ["order_number", "customer_name", "customer_phone"],
                          {"store_id": store_id} if store_id else None)
