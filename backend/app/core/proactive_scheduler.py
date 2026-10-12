import asyncio
import structlog
from datetime import datetime, timedelta
from typing import Optional
import redis.asyncio as aioredis

from app.agents.morning_briefing import MorningBriefingOrchestrator
from app.core.model_router_kimi_deepseek import KimiDeepSeekRouter
from app.memory.cognee_setup import CogneeMemory

logger = structlog.get_logger("aether.core.proactive_scheduler")

REDIS_KEY_LAST_BRIEFING = "aether:proactive_scheduler:last_briefing"
REDIS_KEY_NEXT_BRIEFING = "aether:proactive_scheduler:next_briefing"


class ProactiveScheduler:
    """
    Schedules and triggers proactive agent tasks, such as the morning briefing.
    Persists schedule state in Redis so restarts don't lose track.
    """

    def __init__(
        self,
        model_router: KimiDeepSeekRouter,
        cognee_memory: CogneeMemory,
        redis_client: aioredis.Redis,
        briefing_time_str: str = "08:00",
        timezone_offset_hours: int = 2,  # Africa/Johannesburg is UTC+2
    ):
        self.model_router = model_router
        self.cognee_memory = cognee_memory
        self.redis_client = redis_client
        self.briefing_time_str = briefing_time_str
        self.timezone_offset = timedelta(hours=timezone_offset_hours)
        self._running = False
        self._task: Optional[asyncio.Task] = None
        logger.info("proactive_scheduler.initialized", briefing_time=briefing_time_str, tz_offset=timezone_offset_hours)

    async def start(self):
        """Starts the scheduler loop in a background task."""
        if self._running:
            logger.warning("proactive_scheduler.already_running")
            return
        
        self._running = True
        self._task = asyncio.create_task(self._run_schedule_loop())
        logger.info("proactive_scheduler.started")

    async def stop(self):
        """Stops the scheduler loop."""
        if not self._running:
            logger.warning("proactive_scheduler.not_running")
            return
        
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                logger.info("proactive_scheduler.task_cancelled")
            self._task = None
        logger.info("proactive_scheduler.stopped")

    def _local_now(self) -> datetime:
        """Return current time in the configured local timezone."""
        return datetime.utcnow() + self.timezone_offset

    def _next_briefing_datetime(self, after: datetime) -> datetime:
        """Calculate the next briefing datetime in local time."""
        briefing_hour, briefing_minute = map(int, self.briefing_time_str.split(":"))
        candidate = after.replace(hour=briefing_hour, minute=briefing_minute, second=0, microsecond=0)
        if after >= candidate:
            candidate += timedelta(days=1)
        return candidate

    async def _run_schedule_loop(self):
        """The main loop for checking and triggering scheduled tasks."""
        while self._running:
            now_local = self._local_now()

            # ── Restore state from Redis ──────────────────────────
            last_briefing_str = await self.redis_client.get(REDIS_KEY_LAST_BRIEFING)
            next_briefing_str = await self.redis_client.get(REDIS_KEY_NEXT_BRIEFING)

            if last_briefing_str:
                last_briefing = datetime.fromisoformat(last_briefing_str.decode("utf-8"))
            else:
                last_briefing = None

            if next_briefing_str:
                next_briefing = datetime.fromisoformat(next_briefing_str.decode("utf-8"))
            else:
                # First run — calculate from now
                next_briefing = self._next_briefing_datetime(now_local)
                await self.redis_client.set(REDIS_KEY_NEXT_BRIEFING, next_briefing.isoformat())
                logger.info("proactive_scheduler.first_run_calculated", next_briefing=next_briefing.isoformat())

            # ── Catch-up logic ────────────────────────────────────
            # If we missed the briefing (e.g. server was down), trigger immediately
            if now_local >= next_briefing:
                if last_briefing is None or last_briefing.date() < next_briefing.date():
                    logger.warning("proactive_scheduler.missed_briefing_detected", 
                                   now=now_local.isoformat(), 
                                   missed=next_briefing.isoformat())
                    await self._trigger_briefing()
                    # Recalculate next after triggering
                    next_briefing = self._next_briefing_datetime(now_local)
                    await self.redis_client.set(REDIS_KEY_NEXT_BRIEFING, next_briefing.isoformat())

            # ── Sleep until next briefing ─────────────────────────
            time_to_wait = (next_briefing - now_local).total_seconds()
            if time_to_wait > 0:
                logger.debug("proactive_scheduler.waiting_for_briefing", 
                             next_briefing=next_briefing.isoformat(), 
                             wait_seconds=time_to_wait)
                try:
                    await asyncio.wait_for(
                        asyncio.Event().wait(),
                        timeout=time_to_wait,
                    )
                except asyncio.TimeoutError:
                    pass  # Time to wake up
            else:
                # Should only happen if we just triggered and recalculated
                await asyncio.sleep(60)
                continue

            if not self._running:
                break

            # ── Trigger briefing ──────────────────────────────────
            await self._trigger_briefing()

            # Recalculate next
            now_local = self._local_now()
            next_briefing = self._next_briefing_datetime(now_local)
            await self.redis_client.set(REDIS_KEY_NEXT_BRIEFING, next_briefing.isoformat())
            logger.info("proactive_scheduler.rescheduled", next_briefing=next_briefing.isoformat())

            # Small buffer to prevent tight loop
            await asyncio.sleep(60)

    async def _trigger_briefing(self):
        """Generate and deliver the morning briefing."""
        logger.info("proactive_scheduler.triggering_morning_briefing")
        try:
            orchestrator = MorningBriefingOrchestrator(self.model_router, self.cognee_memory)
            briefing = await orchestrator.generate_briefing()
            logger.info("proactive_scheduler.morning_briefing_generated", briefing_length=len(briefing))
            
            # Persist last briefing time
            now_local = self._local_now()
            await self.redis_client.set(REDIS_KEY_LAST_BRIEFING, now_local.isoformat())
            
            # TODO: Integrate with notification service (email, webhook, websocket broadcast)
            print(f"\n--- MORNING BRIEFING ---\n{briefing}\n-----------------------\n")
        except Exception as e:
            logger.error("proactive_scheduler.briefing_failed", error=str(e), exc_info=True)