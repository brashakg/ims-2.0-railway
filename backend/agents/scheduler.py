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
import os
import uuid

from .base import JarvisAgent
from .config import AgentConfigManager
from api.utils.ist import IST

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


# ---------------------------------------------------------------------------
# Singleton / leader-election guard (multi-worker safety)
# ---------------------------------------------------------------------------
# Problem: the FastAPI lifespan starts an AgentScheduler in EVERY process.
# APScheduler's max_instances=1 only dedupes WITHIN one process; with >1
# uvicorn worker or Railway replica, every worker would tick every agent ->
# duplicate POs from TASKMASTER, duplicate WhatsApp from MEGAPHONE.
#
# Fix: only one process should own the schedule.
#   1) RUN_AGENT_SCHEDULER env gate (default "true"). Set "false" on workers
#      that must never schedule (e.g. a web-only replica).
#   2) If Redis is configured, additionally take a leader lock (SET NX EX) so
#      that across N processes that all have RUN_AGENT_SCHEDULER=true, exactly
#      ONE wins and runs the jobs; the lock is refreshed while we hold it.
#
# Fail-soft: no Redis / Redis error -> log a warning and RUN anyway. That
# preserves today's single-worker behavior (the common dev + small-deploy
# case) rather than silently scheduling nothing.

# Redis key the scheduler leader holds. Short, namespaced.
SCHEDULER_LEADER_KEY = os.getenv(
    "AGENT_SCHEDULER_LEADER_KEY", "ims:agents:scheduler:leader"
)
# Lock TTL in seconds. We refresh well within this so a live leader keeps it,
# but a crashed leader's lock expires and another worker can take over.
SCHEDULER_LEADER_TTL = int(os.getenv("AGENT_SCHEDULER_LEADER_TTL", "90"))
# How often the holder refreshes the TTL. Must be comfortably < TTL.
SCHEDULER_LEADER_REFRESH = int(os.getenv("AGENT_SCHEDULER_LEADER_REFRESH", "30"))


def _scheduler_enabled() -> bool:
    """RUN_AGENT_SCHEDULER env gate. Default true (preserve prior behavior)."""
    val = os.getenv("RUN_AGENT_SCHEDULER", "true").strip().lower()
    return val not in ("0", "false", "no", "off")


def _make_redis_client():
    """Build a sync Redis client using the SAME config convention as
    api/services/cache.py and agents/event_bus.py (REDIS_URL preferred, else
    REDIS_HOST/PORT/PASSWORD/DB). Returns a connected client or None.

    Sync (not async) on purpose: lock acquire/refresh are tiny + infrequent,
    and we don't want to entangle the leader lock with the asyncio Redis the
    event bus uses. Fail-soft: any error -> None.
    """
    try:
        import redis as _redis_lib  # redis-py, already a project dep
    except Exception as e:  # pragma: no cover - redis is a declared dep
        logger.warning(f"[SCHEDULER] redis library unavailable ({e})")
        return None

    url = os.getenv("REDIS_URL") or None
    host = os.getenv("REDIS_HOST")
    if not url and not host:
        return None  # Redis simply not configured -> caller falls back to run.

    try:
        if url:
            client = _redis_lib.from_url(
                url, decode_responses=True, socket_connect_timeout=2
            )
        else:
            client = _redis_lib.Redis(
                host=host,
                port=int(os.getenv("REDIS_PORT", "6379")),
                password=os.getenv("REDIS_PASSWORD") or None,
                db=int(os.getenv("REDIS_DB", "0")),
                decode_responses=True,
                socket_connect_timeout=2,
            )
        client.ping()  # surface connect errors here, not on first SET
        return client
    except Exception as e:
        logger.warning(f"[SCHEDULER] Redis connect failed ({e})")
        return None


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

        # Leader-election state (multi-worker singleton guard).
        self._instance_id = str(uuid.uuid4())      # unique per process
        self._redis = None                          # set if we hold the lock
        self._is_leader = False                     # did this process win?
        self._leader_refresh_task: Optional[asyncio.Task] = None

        if APSCHEDULER_AVAILABLE:
            self._scheduler = AsyncIOScheduler(
                timezone=IST,  # BUG-104: cron triggers (PIXEL 2 AM, EOD 10 PM, Tally 11 PM) must fire on IST wall-clock, not the UTC box clock
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

        Singleton guard: returns WITHOUT scheduling any jobs unless this
        process is allowed to be the scheduler (RUN_AGENT_SCHEDULER gate, plus
        a Redis leader lock when Redis is configured). This stops every worker
        in a multi-worker deploy from ticking every agent.
        """
        # --- Multi-worker singleton guard --------------------------------
        if not _scheduler_enabled():
            logger.info(
                "[SCHEDULER] RUN_AGENT_SCHEDULER is false -> not starting "
                "agent jobs in this process."
            )
            return
        if not await self._acquire_leadership():
            logger.info(
                "[SCHEDULER] Another process holds the scheduler leader lock "
                "-> not starting agent jobs here (worker %s).",
                self._instance_id[:8],
            )
            return
        # -----------------------------------------------------------------

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
            # Only a leader actually started the APScheduler; guard the call so
            # a non-leader shutdown doesn't raise SchedulerNotRunningError.
            try:
                if getattr(self._scheduler, "running", False):
                    self._scheduler.shutdown(wait=False)
                    logger.info("[SCHEDULER] APScheduler shutdown")
            except Exception as e:
                logger.warning(f"[SCHEDULER] APScheduler shutdown error: {e}")
        else:
            for task_id, task in self._fallback_tasks.items():
                task.cancel()
            self._fallback_tasks.clear()
            logger.info("[SCHEDULER] Fallback tasks cancelled")

        # Release the leader lock so another worker can take over promptly
        # instead of waiting for the TTL to lapse.
        await self._release_leadership()

    # --- Leader election (multi-worker singleton) ---

    async def _acquire_leadership(self) -> bool:
        """Decide whether THIS process should run the scheduler.

        - Redis not configured  -> True  (fail-soft: keep single-worker behavior)
        - Redis configured       -> try SET key uuid NX EX TTL.
            - acquired           -> True,  spawn the refresh task
            - not acquired       -> False (someone else is the leader)
            - Redis error         -> True  (fail-soft + warning)
        """
        client = _make_redis_client()
        if client is None:
            # Either Redis isn't set up at all, or we couldn't reach it. Both
            # cases fall back to running so a no-Redis deploy still schedules.
            logger.info(
                "[SCHEDULER] No Redis leader lock (not configured/unreachable) "
                "-> running scheduler in this process (single-worker assumption)."
            )
            self._is_leader = True
            return True

        try:
            # SET key <uuid> NX EX <ttl>: atomic acquire-if-absent with expiry.
            acquired = client.set(
                SCHEDULER_LEADER_KEY,
                self._instance_id,
                nx=True,
                ex=SCHEDULER_LEADER_TTL,
            )
        except Exception as e:
            logger.warning(
                f"[SCHEDULER] Leader lock SET failed ({e}) -> running anyway."
            )
            self._is_leader = True
            return True

        if not acquired:
            # Someone else holds it. Don't run jobs here. Keep no client ref.
            try:
                client.close()
            except Exception:
                pass
            self._is_leader = False
            return False

        # We are the leader. Hold the client + refresh the TTL while we run.
        self._redis = client
        self._is_leader = True
        logger.info(
            "[SCHEDULER] Acquired leader lock '%s' (worker %s, ttl=%ss).",
            SCHEDULER_LEADER_KEY, self._instance_id[:8], SCHEDULER_LEADER_TTL,
        )
        try:
            self._leader_refresh_task = asyncio.create_task(self._refresh_leadership_loop())
        except RuntimeError:
            # No running loop (e.g. called outside async context in a test) --
            # the lock is still held; we just won't auto-refresh it.
            self._leader_refresh_task = None
        return True

    async def _refresh_leadership_loop(self):
        """While we hold the lock, periodically re-assert it so the TTL never
        lapses under a live leader. Only refresh if WE still own the key
        (guards the edge case where our lock expired and another worker took
        over -- we must not clobber theirs).

        Loops on `self._redis` (set on acquire, cleared on release) rather than
        `self._running` so it doesn't depend on start()'s flag-set ordering.
        """
        while self._redis is not None:
            try:
                await asyncio.sleep(SCHEDULER_LEADER_REFRESH)
            except asyncio.CancelledError:
                break
            if self._redis is None:
                break
            try:
                current = self._redis.get(SCHEDULER_LEADER_KEY)
                if current == self._instance_id:
                    self._redis.expire(SCHEDULER_LEADER_KEY, SCHEDULER_LEADER_TTL)
                else:
                    # Lost the lock (expired + reclaimed). Stop refreshing; the
                    # process keeps its already-scheduled jobs until restart,
                    # but won't fight the new leader for the key.
                    logger.warning(
                        "[SCHEDULER] Leader lock no longer ours (worker %s) -- "
                        "stopping refresh.", self._instance_id[:8],
                    )
                    break
            except Exception as e:
                logger.warning(f"[SCHEDULER] Leader lock refresh error ({e}).")

    async def _release_leadership(self):
        """Cancel the refresh task and delete the lock if we own it."""
        if self._leader_refresh_task is not None:
            self._leader_refresh_task.cancel()
            try:
                await self._leader_refresh_task
            except (asyncio.CancelledError, Exception):
                pass
            self._leader_refresh_task = None

        if self._redis is not None:
            try:
                # Only delete if it's still our value -- never drop another
                # worker's freshly-acquired lock.
                if self._redis.get(SCHEDULER_LEADER_KEY) == self._instance_id:
                    self._redis.delete(SCHEDULER_LEADER_KEY)
                    logger.info(
                        "[SCHEDULER] Released leader lock (worker %s).",
                        self._instance_id[:8],
                    )
            except Exception as e:
                logger.warning(f"[SCHEDULER] Leader lock release error ({e}).")
            finally:
                try:
                    self._redis.close()
                except Exception:
                    pass
                self._redis = None
        self._is_leader = False

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
