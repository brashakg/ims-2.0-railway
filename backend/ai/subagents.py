"""
IMS 2.0 — AI Subagent Architecture
====================================
Specialized agents that Jarvis dispatches to for domain-specific intelligence.
All agents are READ-ONLY and SUPERADMIN-ONLY.

Architecture:
  Jarvis (Orchestrator) → routes query to → SubAgent → reads DB → returns insight

SubAgents:
  1. InventoryAgent — stock levels, non-moving, reorder, transfer suggestions
  2. SalesAgent — revenue trends, discount patterns, staff performance
  3. HRAgent — attendance patterns, incentive calculations, payroll insights
  4. FinanceAgent — expense tracking, GST compliance, outstanding collections
  5. ClinicalAgent — prescription patterns, optometrist performance, redo rates
  6. MarketAgent — purchase suggestions at trade fairs (photo + context → recommendation)
"""

from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class AgentType(str, Enum):
    INVENTORY = "inventory"
    SALES = "sales"
    HR = "hr"
    FINANCE = "finance"
    CLINICAL = "clinical"
    MARKET = "market"


class SubAgentResult:
    """Standardized result from any subagent."""
    def __init__(
        self,
        agent: AgentType,
        summary: str,
        data: Dict[str, Any],
        recommendations: List[str],
        confidence: float = 0.8,
        alerts: List[str] = None,
    ):
        self.agent = agent
        self.summary = summary
        self.data = data
        self.recommendations = recommendations
        self.confidence = confidence
        self.alerts = alerts or []
        self.timestamp = datetime.utcnow().isoformat()

    def to_dict(self):
        return {
            "agent": self.agent.value,
            "summary": self.summary,
            "data": self.data,
            "recommendations": self.recommendations,
            "confidence": self.confidence,
            "alerts": self.alerts,
            "timestamp": self.timestamp,
        }


class InventoryAgent:
    """Stock analysis, non-moving detection, reorder suggestions, transfer optimization."""

    @staticmethod
    async def analyze(db, store_id: Optional[str] = None, query: Optional[str] = None) -> SubAgentResult:
        try:
            stock_collection = db["stock_units"]
            products_collection = db["products"]

            pipeline = []
            if store_id:
                pipeline.append({"$match": {"store_id": store_id}})

            pipeline.extend([
                {"$group": {
                    "_id": "$store_id",
                    "total_units": {"$sum": "$quantity"},
                    "total_value": {"$sum": {"$multiply": ["$quantity", {"$ifNull": ["$mrp", 0]}]}},
                    "zero_stock": {"$sum": {"$cond": [{"$lte": ["$quantity", 0]}, 1, 0]}},
                    "low_stock": {"$sum": {"$cond": [{"$and": [{"$gt": ["$quantity", 0]}, {"$lte": ["$quantity", 3]}]}, 1, 0]}},
                    "sku_count": {"$sum": 1},
                }}
            ])

            results = list(stock_collection.aggregate(pipeline))

            # Non-moving stock (no sales in 90 days)
            ninety_days_ago = datetime.utcnow() - timedelta(days=90)
            # This would join with orders in production
            non_moving_estimate = 0

            data = {
                "stores": [{
                    "store_id": r["_id"],
                    "total_units": r["total_units"],
                    "total_value": round(r["total_value"]),
                    "zero_stock_skus": r["zero_stock"],
                    "low_stock_skus": r["low_stock"],
                    "active_skus": r["sku_count"],
                } for r in results],
                "non_moving_estimate": non_moving_estimate,
            }

            total_value = sum(r.get("total_value", 0) for r in results)
            total_zero = sum(r.get("zero_stock", 0) for r in results)

            recommendations = []
            alerts = []

            if total_zero > 10:
                alerts.append(f"{total_zero} SKUs at zero stock across stores")
                recommendations.append("Run reorder analysis for zero-stock items")

            low_total = sum(r.get("low_stock", 0) for r in results)
            if low_total > 20:
                recommendations.append(f"{low_total} SKUs at critically low levels — consider emergency restock")

            if total_value > 5000000:
                recommendations.append(f"Total inventory value ₹{total_value:,.0f} — consider clearing slow movers to free capital")

            return SubAgentResult(
                agent=AgentType.INVENTORY,
                summary=f"Inventory across {len(results)} store(s): {sum(r['total_units'] for r in results)} units, ₹{total_value:,.0f} value, {total_zero} zero-stock SKUs",
                data=data,
                recommendations=recommendations,
                alerts=alerts,
            )
        except Exception as e:
            logger.error(f"InventoryAgent error: {e}")
            return SubAgentResult(
                agent=AgentType.INVENTORY,
                summary="Could not analyze inventory — database error",
                data={}, recommendations=[], alerts=[str(e)],
            )


class SalesAgent:
    """Revenue analysis, discount pattern detection, staff attribution."""

    @staticmethod
    async def analyze(db, store_id: Optional[str] = None, days: int = 30, query: Optional[str] = None) -> SubAgentResult:
        try:
            orders_col = db["orders"]
            since = datetime.utcnow() - timedelta(days=days)

            match_filter: Dict[str, Any] = {"created_at": {"$gte": since}}
            if store_id:
                match_filter["store_id"] = store_id

            pipeline = [
                {"$match": match_filter},
                {"$group": {
                    "_id": None,
                    "total_revenue": {"$sum": {"$ifNull": ["$grand_total", 0]}},
                    "total_orders": {"$sum": 1},
                    "total_discount": {"$sum": {"$ifNull": ["$total_discount", 0]}},
                    "total_tax": {"$sum": {"$ifNull": ["$tax_amount", 0]}},
                    "outstanding": {"$sum": {"$ifNull": ["$balance_due", 0]}},
                    "avg_bill": {"$avg": {"$ifNull": ["$grand_total", 0]}},
                }}
            ]

            results = list(orders_col.aggregate(pipeline))
            stats = results[0] if results else {}

            # Staff performance
            staff_pipeline = [
                {"$match": match_filter},
                {"$group": {
                    "_id": "$salesperson_id",
                    "name": {"$first": "$salesperson_name"},
                    "orders": {"$sum": 1},
                    "revenue": {"$sum": {"$ifNull": ["$grand_total", 0]}},
                    "discount_given": {"$sum": {"$ifNull": ["$total_discount", 0]}},
                }},
                {"$sort": {"revenue": -1}},
                {"$limit": 10},
            ]
            staff_results = list(orders_col.aggregate(staff_pipeline))

            data = {
                "period_days": days,
                "total_revenue": round(stats.get("total_revenue", 0)),
                "total_orders": stats.get("total_orders", 0),
                "avg_bill_value": round(stats.get("avg_bill", 0)),
                "total_discount": round(stats.get("total_discount", 0)),
                "outstanding": round(stats.get("outstanding", 0)),
                "staff_performance": [{
                    "name": s.get("name", "Unknown"),
                    "orders": s.get("orders", 0),
                    "revenue": round(s.get("revenue", 0)),
                    "avg_discount_pct": round(s.get("discount_given", 0) / max(s.get("revenue", 1), 1) * 100, 1),
                } for s in staff_results],
            }

            recommendations = []
            alerts = []

            if stats.get("outstanding", 0) > 50000:
                alerts.append(f"₹{stats['outstanding']:,.0f} outstanding — follow up on pending collections")

            discount_pct = stats.get("total_discount", 0) / max(stats.get("total_revenue", 1), 1) * 100
            if discount_pct > 8:
                alerts.append(f"Discount average {discount_pct:.1f}% — above 8% threshold")
                recommendations.append("Review discount approval patterns — potential over-discounting")

            return SubAgentResult(
                agent=AgentType.SALES,
                summary=f"Last {days}d: ₹{stats.get('total_revenue', 0):,.0f} revenue, {stats.get('total_orders', 0)} orders, ₹{stats.get('avg_bill', 0):,.0f} avg bill",
                data=data,
                recommendations=recommendations,
                alerts=alerts,
            )
        except Exception as e:
            logger.error(f"SalesAgent error: {e}")
            return SubAgentResult(
                agent=AgentType.SALES,
                summary="Could not analyze sales — database error",
                data={}, recommendations=[], alerts=[str(e)],
            )


class HRAgent:
    """Attendance patterns, late trends, incentive projections."""

    @staticmethod
    async def analyze(db, store_id: Optional[str] = None, query: Optional[str] = None) -> SubAgentResult:
        try:
            users_col = db["users"]

            total_staff = users_col.count_documents({"is_active": True})

            # In production: query attendance collection
            data = {
                "total_active_staff": total_staff,
                "attendance_today": {"present": 0, "absent": 0, "late": 0, "leave": 0},
                "monthly_trends": {"avg_attendance_pct": 0, "avg_late_pct": 0},
                "note": "Attendance data wiring in progress — check-in/check-out widget is live",
            }

            return SubAgentResult(
                agent=AgentType.HR,
                summary=f"{total_staff} active staff members",
                data=data,
                recommendations=["Enable geo-fenced check-in for store staff", "Set up shift swap approval workflow"],
                alerts=[],
            )
        except Exception as e:
            return SubAgentResult(agent=AgentType.HR, summary="HR analysis unavailable", data={}, recommendations=[], alerts=[str(e)])


class FinanceAgent:
    """Expense analysis, GST compliance check, outstanding tracking."""

    @staticmethod
    async def analyze(db, store_id: Optional[str] = None, query: Optional[str] = None) -> SubAgentResult:
        try:
            orders_col = db["orders"]

            # Outstanding amount
            outstanding_pipeline = [
                {"$match": {"balance_due": {"$gt": 0}}},
                {"$group": {
                    "_id": None,
                    "total_outstanding": {"$sum": "$balance_due"},
                    "count": {"$sum": 1},
                }}
            ]
            result = list(orders_col.aggregate(outstanding_pipeline))
            stats = result[0] if result else {"total_outstanding": 0, "count": 0}

            data = {
                "outstanding_amount": round(stats.get("total_outstanding", 0)),
                "outstanding_orders": stats.get("count", 0),
                "gst_status": "Pending manual reconciliation",
            }

            alerts = []
            if stats.get("total_outstanding", 0) > 100000:
                alerts.append(f"₹{stats['total_outstanding']:,.0f} outstanding across {stats['count']} orders — critical collection needed")

            return SubAgentResult(
                agent=AgentType.FINANCE,
                summary=f"₹{stats.get('total_outstanding', 0):,.0f} outstanding across {stats.get('count', 0)} orders",
                data=data,
                recommendations=["Generate aging report for outstanding >30 days", "Schedule GST reconciliation before filing date"],
                alerts=alerts,
            )
        except Exception as e:
            return SubAgentResult(agent=AgentType.FINANCE, summary="Finance analysis unavailable", data={}, recommendations=[], alerts=[str(e)])


class ClinicalAgent:
    """Prescription pattern analysis, optometrist performance, redo tracking."""

    @staticmethod
    async def analyze(db, store_id: Optional[str] = None, query: Optional[str] = None) -> SubAgentResult:
        try:
            rx_col = db["prescriptions"]

            total_rx = rx_col.count_documents({})

            data = {
                "total_prescriptions": total_rx,
                "note": "Deep clinical analysis requires prescription + order correlation — building",
            }

            return SubAgentResult(
                agent=AgentType.CLINICAL,
                summary=f"{total_rx} prescriptions in system",
                data=data,
                recommendations=["Track Rx-to-conversion ratio", "Monitor optometrist redo rates"],
                alerts=[],
            )
        except Exception as e:
            return SubAgentResult(agent=AgentType.CLINICAL, summary="Clinical analysis unavailable", data={}, recommendations=[], alerts=[str(e)])


# ============================================================================
# ORCHESTRATOR — Dispatches queries to the right subagent
# ============================================================================

class SubAgentOrchestrator:
    """Routes queries to specialized subagents based on intent."""

    INTENT_KEYWORDS = {
        AgentType.INVENTORY: ["stock", "inventory", "reorder", "non-moving", "transfer", "warehouse", "sku", "barcode"],
        AgentType.SALES: ["sale", "revenue", "order", "discount", "bill", "payment", "collection", "target"],
        AgentType.HR: ["staff", "attendance", "leave", "salary", "incentive", "employee", "shift", "performance"],
        AgentType.FINANCE: ["expense", "gst", "tax", "outstanding", "profit", "loss", "tally", "accounting"],
        AgentType.CLINICAL: ["prescription", "eye test", "optom", "lens", "power", "redo", "clinical"],
        AgentType.MARKET: ["buy", "purchase", "vendor", "trade fair", "should i buy", "recommend"],
    }

    @classmethod
    def detect_intent(cls, query: str) -> List[AgentType]:
        """Detect which agents should handle this query."""
        query_lower = query.lower()
        matched = []
        for agent_type, keywords in cls.INTENT_KEYWORDS.items():
            for kw in keywords:
                if kw in query_lower:
                    matched.append(agent_type)
                    break
        return matched or [AgentType.SALES]  # Default to sales

    @classmethod
    async def process(cls, db, query: str, store_id: Optional[str] = None) -> Dict[str, Any]:
        """Process a natural language query through the appropriate subagents."""
        agents = cls.detect_intent(query)

        results = []
        for agent_type in agents:
            if agent_type == AgentType.INVENTORY:
                r = await InventoryAgent.analyze(db, store_id, query)
            elif agent_type == AgentType.SALES:
                r = await SalesAgent.analyze(db, store_id, query=query)
            elif agent_type == AgentType.HR:
                r = await HRAgent.analyze(db, store_id, query)
            elif agent_type == AgentType.FINANCE:
                r = await FinanceAgent.analyze(db, store_id, query)
            elif agent_type == AgentType.CLINICAL:
                r = await ClinicalAgent.analyze(db, store_id, query)
            else:
                continue
            results.append(r.to_dict())

        all_alerts = []
        all_recommendations = []
        for r in results:
            all_alerts.extend(r.get("alerts", []))
            all_recommendations.extend(r.get("recommendations", []))

        return {
            "query": query,
            "agents_used": [a.value for a in agents],
            "results": results,
            "combined_alerts": all_alerts,
            "combined_recommendations": all_recommendations,
            "timestamp": datetime.utcnow().isoformat(),
        }
