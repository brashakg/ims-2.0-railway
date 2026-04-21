"""
IMS 2.0 — Agent Config Manager
================================
MongoDB-backed agent configuration with ON/OFF toggle control.
Stores per-agent settings in the `agent_config` collection.
"""

from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
import logging

logger = logging.getLogger(__name__)


# Default agent configurations
DEFAULT_AGENT_CONFIGS = [
    {
        "agent_id": "jarvis",
        "agent_name": "JARVIS",
        "enabled": True,
        "toggleable": False,
        "schedule_type": "event",
        "schedule_value": "on_query",
        "description": "NLP & conversation core — the voice users talk to",
        "hero": "Iron Man's J.A.R.V.I.S.",
        "config_overrides": {},
    },
    {
        "agent_id": "cortex",
        "agent_name": "CORTEX",
        "enabled": True,
        "toggleable": False,
        "schedule_type": "event",
        "schedule_value": "on_query",
        "description": "Orchestrator — routes queries, coordinates multi-agent workflows",
        "hero": "Professor X",
        "config_overrides": {},
    },
    {
        "agent_id": "sentinel",
        "agent_name": "SENTINEL",
        "enabled": True,
        "toggleable": True,
        "schedule_type": "interval",
        "schedule_value": "60",
        "description": "Health & monitoring — API, DB, frontend, deployments",
        "hero": "The Sentinels",
        "config_overrides": {
            "alert_threshold_score": 70,
            "check_api": True,
            "check_db": True,
            "check_frontend": True,
        },
    },
    {
        "agent_id": "pixel",
        "agent_name": "PIXEL",
        "enabled": False,
        "toggleable": True,
        "schedule_type": "cron",
        "schedule_value": "0 2 * * *",
        "description": "UI/UX quality auditing — performance, accessibility, visual regression",
        "hero": "Batman",
        "config_overrides": {},
    },
    {
        "agent_id": "megaphone",
        "agent_name": "MEGAPHONE",
        "enabled": False,
        "toggleable": True,
        "schedule_type": "interval",
        "schedule_value": "1800",
        "description": "Marketing & engagement — WhatsApp campaigns, Rx reminders, birthday messages",
        "hero": "Black Canary",
        "config_overrides": {
            "dnd_start_hour": 21,
            "dnd_end_hour": 9,
        },
    },
    {
        "agent_id": "oracle",
        "agent_name": "ORACLE",
        "enabled": False,
        "toggleable": True,
        "schedule_type": "cron",
        "schedule_value": "0 * * * *",
        "description": "AI analysis — demand forecasting, discount abuse, fraud detection",
        "hero": "Oracle (Barbara Gordon)",
        "config_overrides": {},
    },
    {
        "agent_id": "taskmaster",
        "agent_name": "TASKMASTER",
        "enabled": False,
        "toggleable": True,
        "schedule_type": "interval",
        "schedule_value": "300",
        "description": "Task execution — auto-reorder, SOP enforcement, escalations",
        "hero": "Taskmaster",
        "config_overrides": {},
    },
    {
        "agent_id": "nexus",
        "agent_name": "NEXUS",
        "enabled": False,
        "toggleable": True,
        "schedule_type": "interval",
        "schedule_value": "3600",
        "description": "Data & integrations — Shopify, Razorpay, Shiprocket, Tally sync",
        "hero": "Cyborg",
        "config_overrides": {},
    },
]


class AgentConfigManager:
    """Manages agent configurations in MongoDB."""

    def __init__(self, db=None):
        self._db = db

    @property
    def collection(self):
        """Get the agent_config collection."""
        if self._db:
            try:
                return self._db.get_collection("agent_config")
            except Exception:
                return getattr(self._db, "agent_config", None)
        return None

    def seed_configs(self):
        """
        Seed default agent configs if they don't exist.
        Called during FastAPI startup.
        """
        col = self.collection
        if not col:
            logger.warning("[CONFIG] No DB available — cannot seed agent configs")
            return

        for config in DEFAULT_AGENT_CONFIGS:
            existing = col.find_one({"agent_id": config["agent_id"]})
            if not existing:
                config["created_at"] = datetime.now(timezone.utc)
                config["last_run"] = None
                config["last_status"] = None
                config["last_error"] = None
                config["run_count"] = 0
                config["error_count"] = 0
                config["avg_run_time_ms"] = 0
                config["toggled_by"] = "system"
                config["toggled_at"] = datetime.now(timezone.utc)
                col.insert_one(config)
                logger.info(f"[CONFIG] Seeded config for {config['agent_id']}")
            else:
                # Update description and hero if missing
                updates = {}
                if "hero" not in existing:
                    updates["hero"] = config.get("hero", "")
                if "toggleable" not in existing:
                    updates["toggleable"] = config.get("toggleable", True)
                if updates:
                    col.update_one({"agent_id": config["agent_id"]}, {"$set": updates})

    def get_all_configs(self) -> List[Dict[str, Any]]:
        """Get all agent configs."""
        col = self.collection
        if not col:
            return [c.copy() for c in DEFAULT_AGENT_CONFIGS]
        try:
            configs = list(col.find({}, {"_id": 0}))
            return configs if configs else [c.copy() for c in DEFAULT_AGENT_CONFIGS]
        except Exception as e:
            logger.error(f"[CONFIG] Failed to get configs: {e}")
            return [c.copy() for c in DEFAULT_AGENT_CONFIGS]

    def get_config(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Get a single agent's config."""
        col = self.collection
        if not col:
            for c in DEFAULT_AGENT_CONFIGS:
                if c["agent_id"] == agent_id:
                    return c.copy()
            return None
        try:
            return col.find_one({"agent_id": agent_id}, {"_id": 0})
        except Exception:
            return None

    def toggle_agent(self, agent_id: str, enabled: bool, toggled_by: str = "system") -> bool:
        """Toggle an agent ON/OFF."""
        col = self.collection
        if not col:
            return False

        # Check if agent is toggleable
        config = col.find_one({"agent_id": agent_id})
        if not config:
            return False
        if not config.get("toggleable", True):
            logger.warning(f"[CONFIG] Cannot toggle core agent: {agent_id}")
            return False

        result = col.update_one(
            {"agent_id": agent_id},
            {
                "$set": {
                    "enabled": enabled,
                    "toggled_by": toggled_by,
                    "toggled_at": datetime.now(timezone.utc),
                }
            },
        )
        return result.modified_count > 0

    def update_config(self, agent_id: str, updates: Dict[str, Any]) -> bool:
        """Update agent config fields (schedule, overrides, etc.)."""
        col = self.collection
        if not col:
            return False

        # Don't allow updating protected fields
        protected = {"agent_id", "agent_name", "_id", "created_at"}
        safe_updates = {k: v for k, v in updates.items() if k not in protected}

        if not safe_updates:
            return False

        result = col.update_one(
            {"agent_id": agent_id},
            {"$set": safe_updates},
        )
        return result.modified_count > 0
