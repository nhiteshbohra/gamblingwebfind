import asyncio
import logging
import subprocess
import aiohttp

logger = logging.getLogger(__name__)


def alert(message: str):
    # ponytail: stub — wire to email/webhook when needed
    logger.warning(f"[ALERT] {message}")


async def check_searxng_health(searxng_url="http://127.0.0.1:8080") -> bool:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(searxng_url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                return resp.status < 500
    except Exception:
        return False


async def _check_stall(db, stall_minutes: int):
    """Warn if no new URL of any status has been discovered in the last stall_minutes."""
    import aiosqlite
    threshold = f"-{stall_minutes} minutes"
    async with aiosqlite.connect(db.db_path) as conn:
        async with conn.execute(
            "SELECT COUNT(*) FROM urls WHERE first_seen_at >= datetime('now', ?)",
            (threshold,)
        ) as cursor:
            count = (await cursor.fetchone())[0]
    if count == 0:
        alert(f"Stall detected: no new URLs discovered in the last {stall_minutes} minutes.")


async def start_watchdog(db, settings, scheduler_factory=None):
    """
    Monitors:
    1. Stall detection — no new pending URLs in stall_alert_minutes.
    2. SearXNG health — restarts container if unreachable.
    3. Scheduler restart — if scheduler_factory is provided and the task dies,
       watchdog logs the exception and restarts the scheduler loop.
    scheduler_factory: a zero-arg callable that returns a fresh coroutine each call.
    """
    searxng_url = settings.get('searxng_base_url', 'http://127.0.0.1:8080')
    stall_minutes = settings.get('stall_alert_minutes', 30)

    # Coroutines are single-use; we need a factory so each restart gets a fresh one.
    scheduler_task = None
    if scheduler_factory is not None:
        scheduler_task = asyncio.create_task(scheduler_factory())

    while True:
        try:
            await asyncio.sleep(60)

            # 1. Stall check
            if db is not None:
                await _check_stall(db, stall_minutes)

            # 2. SearXNG health
            is_healthy = await check_searxng_health(searxng_url)
            if not is_healthy:
                logger.warning(f"[Watchdog] SearXNG at {searxng_url} is unreachable. Restarting container...")
                alert(f"SearXNG health check failed. Attempting docker compose restart.")
                subprocess.run(["docker", "compose", "restart", "searxng"], check=False)

            # 3. Scheduler crash detection + restart
            if scheduler_task is not None and scheduler_task.done():
                exc = scheduler_task.exception() if not scheduler_task.cancelled() else None
                if exc:
                    logger.error(f"[Watchdog] Scheduler crashed: {exc!r}. Restarting...")
                    alert(f"Scheduler crashed: {exc!r}")
                if scheduler_factory is not None:
                    scheduler_task = asyncio.create_task(scheduler_factory())

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[Watchdog Error] {e}")
            await asyncio.sleep(60)
