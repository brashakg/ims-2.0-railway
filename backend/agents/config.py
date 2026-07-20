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
        "enabled": True,
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
        "enabled": True,
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
        "enabled": True,
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
        "enabled": True,
        "toggleable": True,
        "schedule_type": "interval",
        "schedule_value": "3600",
        "description": "Data & integrations — Shopify, Razorpay, Shiprocket, Tally sync",
        "hero": "Cyborg",
        "config_overrides": {},
    },
]


# Deterministic read order. Until the dedupe migration + the unique index have
# run on an older deployment, agent_config may still hold DUPLICATE docs per
# agent_id with CONFLICTING enabled values; a plain find_one() would return a
# nondeterministic one (which is exactly how the live fleet went dark). Sorting
# enabled DESC makes an enabled=True doc win over its False/None twin, and _id
# ASC breaks any remaining tie, so a stray leftover duplicate can never silently
# keep an agent paused.
_STABLE_SORT = [("enabled", -1), ("_id", 1)]


class AgentConfigManager:
    """Manages agent configurations in MongoDB."""

    def __init__(self, db=None):
        self._db = db

    @property
    def collection(self):
        """Get the agent_config collection."""
        if self._db is not None:
            try:
                return self._db.get_collection("agent_config")
            except Exception:
                return getattr(self._db, "agent_config", None)
        return None

    def ensure_indexes(self):
        """
        Create the UNIQUE index on agent_id so seeding is race-safe and
        duplicate docs can never accumulate again.

        Root cause of the live duplicate storm: seed_configs() used to do a
        find_one() + insert_one() with no unique constraint. Railway boots 4
        uvicorn workers that all run the lifespan concurrently -> each find_one
        returns None before the others insert -> up to 4 identical docs per
        agent_id. find_one() then returns a nondeterministic one, and if it
        carried enabled=None/False the agent's tick was silently skipped.

        Fail-soft: if duplicates ALREADY exist the unique-index build raises.
        We log LOUD (pointing at the dedupe script) and continue so startup is
        never blocked. Idempotent.
        """
        col = self.collection
        if col is None:
            return
        try:
            col.create_index("agent_id", unique=True, name="uq_agent_id")
        except Exception as e:
            logger.error(
                "[CONFIG] Could NOT create unique index on agent_config.agent_id "
                "(%s). This almost always means DUPLICATE agent_config docs "
                "already exist -> run scripts/dedupe_agent_config.py --apply to "
                "collapse them, then redeploy. Seeding continues best-effort.",
                e,
            )

    def seed_configs(self):
        """
        Seed default agent configs, idempotently and race-safely.
        Called during FastAPI startup (once per worker).

        Uses a single upsert per agent keyed by agent_id:
          - $setOnInsert seeds the full default ONLY when the doc is absent,
          - $set backfills the display fields (name/description/hero/toggleable)
            on already-existing docs WITHOUT clobbering operator-set
            enabled/schedule state.
        Combined with the unique index from ensure_indexes(), this guarantees
        exactly one doc per agent_id no matter how many workers boot at once.
        """
        col = self.collection
        if col is None:
            logger.warning("[CONFIG] No DB available — cannot seed agent configs")
            return

        # Unique index FIRST so concurrent upserts can't each insert a twin.
        self.ensure_indexes()

        for config in DEFAULT_AGENT_CONFIGS:
            agent_id = config["agent_id"]

            # Fields refreshed on every boot to track the code defaults. Must be
            # disjoint from $setOnInsert (Mongo rejects the same path in both).
            backfill = {
                "agent_name": config["agent_name"],
                "description": config.get("description", ""),
                "hero": config.get("hero", ""),
                "toggleable": config.get("toggleable", True),
            }

            set_on_insert = {k: v for k, v in config.items() if k not in backfill}
            set_on_insert.update(
                {
                    "created_at": datetime.now(timezone.utc),
                    "last_run": None,
                    "last_status": None,
                    "last_error": None,
                    "run_count": 0,
                    "error_count": 0,
                    "avg_run_time_ms": 0,
                    "toggled_by": "system",
                    "toggled_at": datetime.now(timezone.utc),
                }
            )

            try:
                col.update_one(
                    {"agent_id": agent_id},
                    {"$setOnInsert": set_on_insert, "$set": backfill},
                    upsert=True,
                )
            except Exception as e:
                # DuplicateKeyError under a boot race is benign (another worker
                # won the insert); log anything else but never abort the loop.
                logger.warning("[CONFIG] seed upsert for %s: %s", agent_id, e)

    def get_all_configs(self) -> List[Dict[str, Any]]:
        """Get all agent configs — exactly one row per agent_id.

        Collapses any residual duplicate docs deterministically: the stable sort
        puts the enabled=True twin first, so the first row seen per agent_id
        wins. This keeps the scheduler + /jarvis/agents from ever showing two
        cards (or a paused one) for the same agent while the dedupe migration is
        still pending on an older deployment.
        """
        col = self.collection
        if col is None:
            return [c.copy() for c in DEFAULT_AGENT_CONFIGS]
        try:
            rows = list(col.find({}, {"_id": 0}).sort(_STABLE_SORT))
            seen: Dict[str, Dict[str, Any]] = {}
            for r in rows:
                aid = r.get("agent_id")
                if aid and aid not in seen:
                    seen[aid] = r
            configs = list(seen.values())
            return configs if configs else [c.copy() for c in DEFAULT_AGENT_CONFIGS]
        except Exception as e:
            logger.error(f"[CONFIG] Failed to get configs: {e}")
            return [c.copy() for c in DEFAULT_AGENT_CONFIGS]

    def get_config(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Get a single agent's config (deterministic under residual dupes)."""
        col = self.collection
        if col is None:
            for c in DEFAULT_AGENT_CONFIGS:
                if c["agent_id"] == agent_id:
                    return c.copy()
            return None
        try:
            return col.find_one({"agent_id": agent_id}, {"_id": 0}, sort=_STABLE_SORT)
        except Exception:
            return None

    def toggle_agent(self, agent_id: str, enabled: bool, toggled_by: str = "system") -> bool:
        """Toggle an agent ON/OFF."""
        col = self.collection
        if col is None:
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
        if col is None:
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
