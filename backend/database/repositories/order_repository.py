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
        """Find orders for store with optional filters.
        Handles both camelCase (storeId) and snake_case (store_id) field names."""
        filter = {"$or": [{"store_id": store_id}, {"storeId": store_id}]}
        
        if from_date:
            dt = datetime.combine(from_date, datetime.min.time()).isoformat()
            filter["$or"] = [
                {"store_id": store_id, "created_at": {"$gte": dt}},
                {"storeId": store_id, "createdAt": {"$gte": dt}},
            ]
        if status:
            filter["$or"] = [
                {"store_id": store_id, "status": status},
                {"storeId": store_id, "orderStatus": status},
            ]
        
        return self.find_many(filter, sort=[("created_at", -1)], limit=500)
    
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
        """Update order status and add to status_history"""
        update_data = {
            "status": status,
            "status_updated_at": datetime.now()
        }
        if by_user:
            update_data["status_updated_by"] = by_user
        
        if status == "DELIVERED":
            update_data["delivered_at"] = datetime.now()
        
        # Add to status_history array
        status_history_entry = {
            "status": status,
            "timestamp": datetime.now().isoformat(),
            "changed_by": by_user or "system"
        }
        
        try:
            self.collection.update_one(
                {self.id_field: order_id},
                {
                    "$set": update_data,
                    "$push": {"status_history": status_history_entry}
                }
            )
            return True
        except Exception as e:
            print(f"Error updating status: {e}")
            return False
    
    def add_payment(self, order_id: str, payment: Dict) -> bool:
        """Add a tender to an order and recompute its AR (receivable) status.

        A CREDIT tender is a pay-later promise, NOT cash received: it is excluded
        from amount_paid and instead flags the order as a credit sale
        (payment_status 'CREDIT') so it surfaces as a receivable in finance
        /outstanding (which treats CREDIT as unpaid). Real money tenders
        (cash/card/UPI/cheque/etc.) reduce the balance as before.

        Invariants (council Branch A residuals on top of PR #256):
          * Over-tender protection: actual cash collected (non-CREDIT) must
            not exceed grand_total. Refunds offset (negative tenders allowed).
            CREDIT tenders are a separate promise stream, not cash, so they
            don't count toward over-tender (an unsettled CREDIT can coexist
            with cash that later pays it off).
          * Sticky credit_sale flag: once an order ever takes a CREDIT tender,
            the flag stays True even after the customer settles it. Auditors
            need to know it was sold on credit, not just whether it's paid.
          * Multiple CREDIT rows: summed correctly. Each one adds to the
            credit-extended count but never to amount_paid.
          * Refund (negative amount): re-aggregated like any other tender;
            balance_due recomputes; status may flip back from PAID.

        Raises ValueError on over-tender so the POS layer surfaces it cleanly.
        """
        try:
            order = self.find_by_id(order_id)
            if not order:
                return False

            def _is_credit(p):
                return str((p or {}).get("method", "")).upper() == "CREDIT"

            def _amt(p):
                try:
                    return float((p or {}).get("amount", 0) or 0)
                except (TypeError, ValueError):
                    return 0.0

            # Pre-validate against over-tender BEFORE recording. Cash collected
            # (everything that is NOT a CREDIT promise) must not exceed
            # grand_total. CREDIT rows don't count -- they're a pay-later flag,
            # and adding "CASH 5000" to settle an earlier "CREDIT 5000" is a
            # legitimate flow (not double-payment).
            existing_payments = list(order.get("payments") or [])
            all_payments = existing_payments + [payment]
            grand_total = float(order.get("grand_total", 0) or 0)
            cash_collected = round(
                sum(_amt(p) for p in all_payments if not _is_credit(p)), 2
            )
            # 1-paisa rounding tolerance to absorb 5+5+0.01 float noise.
            if cash_collected - grand_total > 0.01:
                raise ValueError(
                    f"Over-tender: cash collected {cash_collected} exceeds "
                    f"grand_total {grand_total} on order {order_id}"
                )

            # Record the tender now that we know it's valid.
            self.collection.update_one(
                {"order_id": order_id},
                {"$push": {"payments": payment}}
            )

            # Recompute from the full tender list. CREDIT tenders never count
            # toward cash received; cash tenders include refunds (negative
            # amounts subtract from amount_paid).
            amount_paid = round(
                sum(_amt(p) for p in all_payments if not _is_credit(p)),
                2,
            )
            has_credit_now = any(_is_credit(p) for p in all_payments)
            # Sticky audit marker: once a credit sale, always flagged as one.
            credit_sale = bool(order.get("credit_sale")) or has_credit_now

            balance_due = round(grand_total - amount_paid, 2)

            if balance_due <= 0.01 and not has_credit_now:
                # Fully settled with cash/card/etc.
                payment_status = "PAID"
            elif balance_due <= 0.01 and has_credit_now:
                # Has a CREDIT promise + balance cleared by cash -> PAID.
                # Treat the order as settled. credit_sale flag still True.
                payment_status = "PAID"
            elif has_credit_now:
                payment_status = "CREDIT"
            elif amount_paid > 0:
                payment_status = "PARTIAL"
            else:
                payment_status = "UNPAID"

            return self.update(order_id, {
                "amount_paid": amount_paid,
                "balance_due": max(0.0, balance_due),
                "payment_status": payment_status,
                "credit_sale": credit_sale,
            })
        except ValueError:
            # Re-raise so the POS layer can surface it as 400 -- not a silent
            # False that the caller can't tell from a real DB error.
            raise
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
