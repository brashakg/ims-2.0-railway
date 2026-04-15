"""
IMS 2.0 — Agent Scheduler
===========================
APScheduler-based background daemon that runs agents on their configured schedules.
Integrates with FastAPI lifespan for clean startup/shutdown.

Schedule types:
  - "interval": Run every N seconds (e.g., SENTINEL every 60s)
  - "cron": Run on cron schedule (e.g., PIXEL at 2 AM daily)
  - "event": Event-driven only, no scheduled ticks (e.g., CORTEX)
"""

from typing import Dict, Optional, Any
import asyncio
import logging

from .base import JarvisAgent
from .config import AgentConfigManager

logger = logging.getLogger(__name__)

# Try importing APScheduler, fall back to simple asyncio loop
try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.interval import IntervalTrigger
    from apscheduler.triggers.cron import CronTrigger
    APSCHEDULER_AVAILABLE = True
except ImportError:
    APSCHEDULER_AVAILABLE = False
    logger.warning("[SCHEDULER] APScheduler not installed — using simple asyncio fallback")


class AgentScheduler:
    """
    Manages background scheduling for all agents.
    Uses APScheduler if available, falls back to asyncio tasks.
    """

    def __init__(self, db=None):
        self._db = db
        self._config_manager = AgentConfigManager(db=db)
        self._scheduler = None
        self._fallback_tasks: Dict[str, asyncio.Task] = {}
        self._running = False

        if APSCHEDULER_AVAILABLE:
            self._scheduler = AsyncIOScheduler(
                job_defaults={
                    "coalesce": True,  # If missed, run once instead of catching up
                    "max_instances": 1,  # Only one instance per agent
                    "misfire_grace_time": 60,  # Allow 60s late execution
                }
            )

    async def start(self, agents: Dict[str, JarvisAgent]):
        """
        Start the scheduler with all registered agents.
        Called during FastAPI startup.
        """
        self._running = True
        configs = self._config_manager.get_all_configs()

        for config in configs:
            agent_id = config["agent_id"]
            agent = agents.get(agent_id)
            if not agent:
                continue

            schedule_type = config.get("schedule_type", "interval")
            schedule_value = config.get("schedule_value", "60")
            enabled = config.get("enabled", False)

            if schedule_type == "event":
                # Event-driven agents don't need scheduled ticks
                logger.info(f"[SCHEDULER] {agent_id}: event-driven (no scheduled tick)")
                continue

            if APSCHEDULER_AVAILABLE and self._scheduler:
                self._add_apscheduler_job(agent, schedule_type, schedule_value, enabled)
            else:
                if enabled:
                    self._add_fallback_task(agent, schedule_type, schedule_value)

        if APSCHEDULER_AVAILABLE and self._scheduler:
            self._scheduler.start()
            logger.info(f"[SCHEDULER] APScheduler started with {len(self._scheduler.get_jobs())} jobs")
        else:
            logger.info(f"[SCHEDULER] Fallback scheduler started with {len(self._fallback_tasks)} tasks")

    async def shutdown(self):
        """Gracefully shutdown the scheduler."""
        self._running = False

        if APSCHEDULER_AVAILABLE and self._scheduler:
            self._scheduler.shutdown(wait=False)
            logger.info("[SCHEDULER] APScheduler shutdown")
        else:
            for task_id, task in self._fallback_tasks.items():
                task.cancel()
            self._fallback_tasks.clear()
            logger.info("[SCHEDULER] Fallback tasks cancelled")

    def pause_agent(self, agent_id: str):
        """Pause an agent's scheduled job."""
        if APSCHEDULER_AVAILABLE and self._scheduler:
            try:
                self._scheduler.pause_job(agent_id)
                logger.info(f"[SCHEDULER] Paused job: {agent_id}")
            except Exception as e:
                logger.warning(f"[SCHEDULER] Could not pause {agent_id}: {e}")
        else:
            task = self._fallback_tasks.get(agent_id)
            if task:
                task.cancel()
                del self._fallback_tasks[agent_id]

    def resume_agent(self, agent_id: str, agent: JarvisAgent = None):
        """Resume an agent's scheduled job."""
        if APSCHEDULER_AVAILABLE and self._scheduler:
            try:
                self._scheduler.resume_job(agent_id)
                logger.info(f"[SCHEDULER] Resumed job: {agent_id}")
            except Exception as e:
                logger.warning(f"[SCHEDULER] Could not resume {agent_id}: {e}")
        else:
            if agent and agent_id not in self._fallback_tasks:
                config = self._config_manager.get_config(agent_id)
                if config:
                    self._add_fallback_task(
                        agent,
                        config.get("schedule_type", "interval"),
                        config.get("schedule_value", "60"),
                    )

    async def run_agent_now(self, agent: JarvisAgent):
        """Force an immediate background tick for an agent."""
        logger.info(f"[SCHEDULER] Force-running: {agent.agent_id}")
        await agent.background_tick()

    # --- APScheduler helpers ---

    def _add_apscheduler_job(self, agent: JarvisAgent, schedule_type: str,
                              schedule_value: str, enabled: bool):
        """Add a job to APScheduler for this agent."""
        trigger = self._make_trigger(schedule_type, schedule_value)
        if not trigger:
            return

        self._scheduler.add_job(
            agent.background_tick,
            trigger=trigger,
            id=agent.agent_id,
            name=f"{agent.agent_name} tick",
            replace_existing=True,
        )

        if not enabled:
            self._scheduler.pause_job(agent.agent_id)
            logger.info(f"[SCHEDULER] {agent.agent_id}: scheduled but PAUSED (disabled)")
        else:
            logger.info(f"[SCHEDULER] {agent.agent_id}: scheduled ({schedule_type}={schedule_value})")

    def _make_trigger(self, schedule_type: str, schedule_value: str):
        """Create an APScheduler trigger from config."""
        try:
            if schedule_type == "interval":
                seconds = int(schedule_value)
                return IntervalTrigger(seconds=seconds)
            elif schedule_type == "cron":
                # Parse cron string: "minute hour day month day_of_week"
                parts = schedule_value.split()
                if len(parts) == 5:
                    return CronTrigger(
                        minute=parts[0], hour=parts[1],
                        day=parts[2], month=parts[3],
                        day_of_week=parts[4],
                    )
            return None
        except Exception as e:
            logger.error(f"[SCHEDULER] Invalid trigger: {schedule_type}={schedule_value}: {e}")
            return None

    # --- Fallback asyncio loop ---

    def _add_fallback_task(self, agent: JarvisAgent, schedule_type: str, schedule_value: str):
        """Create a simple asyncio loop as fallback when APScheduler is not available."""
        if schedule_type == "interval":
            seconds = int(schedule_value)
        elif schedule_type == "cron":
            seconds = 3600  # Default to hourly for cron agents in fallback mode
        else:
            return

        async def _loop():
            while self._running:
                try:
                    await agent.background_tick()
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"[SCHEDULER] Fallback loop error for {agent.agent_id}: {e}")
                await asyncio.sleep(seconds)

        task = asyncio.create_task(_loop())
        self._fallback_tasks[agent.agent_id] = task
        logger.info(f"[SCHEDULER] {agent.agent_id}: fallback loop every {seconds}s")
