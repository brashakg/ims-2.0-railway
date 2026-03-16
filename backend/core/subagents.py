"""
IMS 2.0 — Subagent System
Autonomous task agents that monitor, analyze, and alert.
All READ-ONLY. No auto-execution. Superadmin visibility only.
"""
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from motor.motor_asyncio import AsyncIOMotorDatabase
import logging

logger = logging.getLogger(__name__)


class BaseAgent:
    """Base class for all IMS 2.0 subagents"""
    agent_id: str = "base"
    agent_name: str = "Base Agent"
    description: str = "Base agent"
    schedule: str = "daily"  # daily, hourly, realtime

    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db

    async def run(self) -> Dict[str, Any]:
        raise NotImplementedError

    async def log_run(self, result: Dict):
        """Log agent execution for audit trail"""
        await self.db.agent_runs.insert_one({
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "ran_at": datetime.utcnow(),
            "result_summary": result.get("summary", ""),
            "alerts_count": result.get("alerts_count", 0),
            "data": result,
        })


class StockHealthAgent(BaseAgent):
    """Monitors stock levels, aging, non-moving inventory, and reorder points"""
    agent_id = "stock_health"
    agent_name = "Stock Health Monitor"
    description = "Analyzes stock levels, identifies non-moving inventory, detects aging stock, and recommends inter-store transfers"
    schedule = "daily"

    async def run(self) -> Dict[str, Any]:
        alerts = []
        recommendations = []

        # 1. Low stock detection
        low_stock = await self.db.stock_units.find({
            "quantity": {"$lte": 2}, "status": "ACTIVE"
        }).to_list(100)
        for item in low_stock:
            alerts.append({
                "type": "LOW_STOCK",
                "severity": "HIGH" if item.get("quantity", 0) == 0 else "MEDIUM",
                "product": item.get("product_name", "Unknown"),
                "store": item.get("store_id", ""),
                "quantity": item.get("quantity", 0),
                "message": f"{item.get('product_name')} has only {item.get('quantity', 0)} units at {item.get('store_id')}",
            })

        # 2. Non-moving stock (no sales in 90 days)
        ninety_days_ago = datetime.utcnow() - timedelta(days=90)
        non_moving = await self.db.stock_units.find({
            "last_sold_at": {"$lt": ninety_days_ago},
            "quantity": {"$gt": 0},
        }).to_list(200)
        for item in non_moving:
            recommendations.append({
                "type": "NON_MOVING",
                "action": "CONSIDER_TRANSFER_OR_DISCOUNT",
                "product": item.get("product_name", "Unknown"),
                "store": item.get("store_id", ""),
                "days_since_sale": (datetime.utcnow() - item.get("last_sold_at", datetime.utcnow())).days,
                "mrp": item.get("mrp", 0),
            })

        # 3. Expiring stock (contact lenses, solutions)
        thirty_days = datetime.utcnow() + timedelta(days=30)
        expiring = await self.db.stock_units.find({
            "expiry_date": {"$lt": thirty_days, "$gt": datetime.utcnow()},
            "quantity": {"$gt": 0},
        }).to_list(50)
        for item in expiring:
            alerts.append({
                "type": "EXPIRING_SOON",
                "severity": "HIGH",
                "product": item.get("product_name", "Unknown"),
                "expiry": str(item.get("expiry_date", "")),
                "quantity": item.get("quantity", 0),
            })

        result = {
            "summary": f"{len(alerts)} alerts, {len(recommendations)} recommendations",
            "alerts_count": len(alerts),
            "alerts": alerts[:50],
            "recommendations": recommendations[:50],
            "low_stock_count": len(low_stock),
            "non_moving_count": len(non_moving),
            "expiring_count": len(expiring),
        }
        await self.log_run(result)
        return result


class PaymentCollectionAgent(BaseAgent):
    """Tracks outstanding payments, overdue balances, and collection patterns"""
    agent_id = "payment_collection"
    agent_name = "Payment Collection Tracker"
    description = "Identifies overdue payments, tracks collection rates, flags at-risk accounts"
    schedule = "daily"

    async def run(self) -> Dict[str, Any]:
        alerts = []

        # Orders with outstanding balance
        outstanding = await self.db.orders.find({
            "balance_due": {"$gt": 0},
            "order_status": {"$ne": "CANCELLED"},
        }).to_list(200)

        total_outstanding = 0
        overdue_7 = []
        overdue_30 = []
        now = datetime.utcnow()

        for order in outstanding:
            amt = order.get("balance_due", 0)
            total_outstanding += amt
            created = order.get("created_at", now)
            if isinstance(created, str):
                try:
                    created = datetime.fromisoformat(created.replace("Z", "+00:00"))
                except:
                    created = now

            days_old = (now - created).days
            entry = {
                "order_number": order.get("order_number", ""),
                "customer_name": order.get("customer_name", "Unknown"),
                "balance_due": amt,
                "days_old": days_old,
                "store_id": order.get("store_id", ""),
            }
            if days_old > 30:
                overdue_30.append(entry)
            elif days_old > 7:
                overdue_7.append(entry)

        for item in overdue_30:
            alerts.append({
                "type": "OVERDUE_30",
                "severity": "HIGH",
                "message": f"{item['customer_name']} — ₹{round(item['balance_due'])} overdue {item['days_old']} days",
                **item,
            })

        result = {
            "summary": f"₹{round(total_outstanding)} total outstanding, {len(overdue_30)} orders >30 days overdue",
            "alerts_count": len(alerts),
            "total_outstanding": round(total_outstanding),
            "orders_with_balance": len(outstanding),
            "overdue_7_days": overdue_7[:20],
            "overdue_30_days": overdue_30[:20],
            "alerts": alerts[:30],
        }
        await self.log_run(result)
        return result


class SOPComplianceAgent(BaseAgent):
    """Checks daily SOP compliance: till closing, stock count, task completion"""
    agent_id = "sop_compliance"
    agent_name = "SOP Compliance Monitor"
    description = "Verifies daily operational SOPs are followed: till closing, stock counts, checklists"
    schedule = "daily"

    async def run(self) -> Dict[str, Any]:
        alerts = []
        now = datetime.utcnow()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)

        # Check if stores closed their till yesterday
        yesterday = today - timedelta(days=1)
        stores = await self.db.stores.find({"status": "ACTIVE"}).to_list(20)

        for store in stores:
            store_id = str(store.get("_id", store.get("store_id", "")))
            store_name = store.get("name", store_id)

            # Check till closing
            till = await self.db.till_closings.find_one({
                "store_id": store_id,
                "date": {"$gte": yesterday, "$lt": today},
            })
            if not till:
                alerts.append({
                    "type": "TILL_NOT_CLOSED",
                    "severity": "HIGH",
                    "store": store_name,
                    "message": f"{store_name} did not close till yesterday",
                })

            # Check stock count submission
            stock_count = await self.db.stock_counts.find_one({
                "store_id": store_id,
                "date": {"$gte": yesterday, "$lt": today},
            })
            if not stock_count:
                alerts.append({
                    "type": "STOCK_COUNT_MISSING",
                    "severity": "MEDIUM",
                    "store": store_name,
                    "message": f"{store_name} did not submit stock count yesterday",
                })

        # Check overdue tasks
        overdue_tasks = await self.db.tasks.count_documents({
            "due_date": {"$lt": now},
            "status": {"$nin": ["COMPLETED", "CANCELLED"]},
        })

        if overdue_tasks > 0:
            alerts.append({
                "type": "OVERDUE_TASKS",
                "severity": "MEDIUM",
                "count": overdue_tasks,
                "message": f"{overdue_tasks} tasks are overdue across all stores",
            })

        result = {
            "summary": f"{len(alerts)} compliance issues detected",
            "alerts_count": len(alerts),
            "alerts": alerts,
            "stores_checked": len(stores),
            "overdue_tasks": overdue_tasks,
        }
        await self.log_run(result)
        return result


class IncentiveTrackingAgent(BaseAgent):
    """Tracks incentive-qualifying sales against monthly targets"""
    agent_id = "incentive_tracking"
    agent_name = "Incentive & Target Tracker"
    description = "Monitors staff incentive progress, kicker achievements, and target completion rates"
    schedule = "daily"

    async def run(self) -> Dict[str, Any]:
        now = datetime.utcnow()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        # Get incentive-tagged items from this month's orders
        orders = await self.db.orders.find({
            "created_at": {"$gte": month_start},
            "order_status": {"$ne": "CANCELLED"},
        }).to_list(1000)

        staff_incentives: Dict[str, Dict] = {}
        for order in orders:
            for item in order.get("items", []):
                tag = item.get("incentive_tag")
                if not tag:
                    continue
                sp = tag.get("salesperson", order.get("salesperson_id", "unknown"))
                if sp not in staff_incentives:
                    staff_incentives[sp] = {
                        "name": tag.get("salesperson", sp),
                        "zeiss_count": 0, "safilo_count": 0,
                        "total_incentive_value": 0,
                        "kickers": {},
                    }
                entry = staff_incentives[sp]
                kicker = tag.get("kicker_type", "")
                if "ZEISS" in kicker:
                    entry["zeiss_count"] += 1
                if "SAFILO" in kicker:
                    entry["safilo_count"] += 1
                entry["total_incentive_value"] += tag.get("item_value", 0)
                entry["kickers"][kicker] = entry["kickers"].get(kicker, 0) + 1

        result = {
            "summary": f"{len(staff_incentives)} staff with incentive-qualifying sales this month",
            "alerts_count": 0,
            "staff_progress": list(staff_incentives.values()),
            "month": now.strftime("%B %Y"),
        }
        await self.log_run(result)
        return result


class DiscountAbuseAgent(BaseAgent):
    """Detects potential discount abuse patterns (READ-ONLY, no auto-action)"""
    agent_id = "discount_abuse"
    agent_name = "Discount Pattern Analyzer"
    description = "Identifies near-limit discounts, repeated patterns, time-based anomalies"
    schedule = "daily"

    async def run(self) -> Dict[str, Any]:
        now = datetime.utcnow()
        week_ago = now - timedelta(days=7)
        flags = []

        orders = await self.db.orders.find({
            "created_at": {"$gte": week_ago},
            "total_discount": {"$gt": 0},
        }).to_list(500)

        # Pattern 1: Near-limit discounts (9-10% when cap is 10%)
        staff_discount_counts: Dict[str, int] = {}
        for order in orders:
            for item in order.get("items", []):
                pct = item.get("discount_percent", 0)
                if 8 <= pct <= 10:
                    sp = order.get("salesperson_id", "unknown")
                    staff_discount_counts[sp] = staff_discount_counts.get(sp, 0) + 1

        for sp, count in staff_discount_counts.items():
            if count >= 5:
                flags.append({
                    "type": "NEAR_LIMIT_PATTERN",
                    "staff": sp,
                    "count": count,
                    "period": "7 days",
                    "message": f"Staff {sp} gave near-cap discounts {count} times in 7 days",
                })

        result = {
            "summary": f"{len(flags)} potential patterns detected",
            "alerts_count": len(flags),
            "flags": flags,
            "orders_analyzed": len(orders),
        }
        await self.log_run(result)
        return result


# ============================================================================
# AGENT REGISTRY
# ============================================================================

AGENT_REGISTRY = {
    "stock_health": StockHealthAgent,
    "payment_collection": PaymentCollectionAgent,
    "sop_compliance": SOPComplianceAgent,
    "incentive_tracking": IncentiveTrackingAgent,
    "discount_abuse": DiscountAbuseAgent,
}


async def run_all_agents(db: AsyncIOMotorDatabase) -> Dict[str, Any]:
    """Run all registered agents and return combined results"""
    results = {}
    for agent_id, agent_class in AGENT_REGISTRY.items():
        try:
            agent = agent_class(db)
            results[agent_id] = await agent.run()
        except Exception as e:
            logger.error(f"Agent {agent_id} failed: {e}")
            results[agent_id] = {"error": str(e), "alerts_count": 0}
    return results


async def run_agent(db: AsyncIOMotorDatabase, agent_id: str) -> Dict[str, Any]:
    """Run a single agent by ID"""
    agent_class = AGENT_REGISTRY.get(agent_id)
    if not agent_class:
        return {"error": f"Unknown agent: {agent_id}"}
    agent = agent_class(db)
    return await agent.run()
