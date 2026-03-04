"""
YukHub Telegram Collector — Entry Point

Startup sequence (matches requirements FR-03 / Service Lifecycle section):
  1.  Load config (pydantic-settings validates at import time)
  2.  Init DB (CREATE TABLE IF NOT EXISTS)
  3.  Create MemoryQueue
  4.  Create EventBus
  5.  Start parser worker tasks (background)
  6.  Start APScheduler cleanup job (background)
  7.  Start FastAPI/Uvicorn SSE server (background)
  8.  Connect Telethon, register real-time handler
  9.  Run historical backfill for all groups (background tasks, non-blocking)
  10. Enter Telethon run loop — service is now fully operational
"""
import asyncio
import logging
import signal

import uvicorn

from app.api.stream import app as fastapi_app, init_api
from app.config import settings
from app.db.session import close_db, init_db
from app.events.bus import EventBus
from app.parser.worker import ParserWorker
from app.queue.memory_queue import MemoryQueue
from app.scheduler.jobs import create_scheduler
from app.telegram.listener import TelegramListener

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("yukhub.main")


# ---------------------------------------------------------------------------
# Main coroutine
# ---------------------------------------------------------------------------

async def main() -> None:
    logger.info("=" * 60)
    logger.info("YukHub Telegram Collector starting up")
    logger.info(f"  LLM model   : {settings.LLM_MODEL}")
    logger.info(f"  LLM base_url: {settings.LLM_BASE_URL or 'OpenAI default'}")
    logger.info(f"  Retention   : {settings.MAX_POST_AGE_DAYS} days")
    logger.info(f"  Queue size  : {settings.QUEUE_MAX_SIZE}")
    logger.info(f"  Workers     : {settings.PARSER_WORKERS}")
    logger.info("=" * 60)

    # 1. Database
    await init_db()
    logger.info("Database tables ready")

    # 2. Shared components
    queue = MemoryQueue(maxsize=settings.QUEUE_MAX_SIZE)
    bus = EventBus()
    init_api(bus)

    # 3. Parser workers
    worker = ParserWorker(queue, bus)
    asyncio.create_task(worker.start(), name="parser-worker-pool")

    # 4. Scheduler
    scheduler = create_scheduler()
    scheduler.start()

    # 5. HTTP/SSE server
    uvi_config = uvicorn.Config(
        fastapi_app,
        host="0.0.0.0",
        port=8000,
        log_level="warning",
        loop="none",   # re-use existing asyncio loop
    )
    server = uvicorn.Server(uvi_config)
    asyncio.create_task(server.serve(), name="uvicorn-server")
    logger.info("SSE API server starting on :8000")

    # 6. Telegram
    listener = TelegramListener(queue, bus)
    await listener.start()  # connects, registers handler, launches backfill tasks

    # ---------------------------------------------------------------------------
    # Graceful shutdown
    # ---------------------------------------------------------------------------
    loop = asyncio.get_running_loop()

    def _on_shutdown() -> None:
        logger.info("Shutdown signal received — beginning graceful shutdown")
        worker.stop()
        try:
            scheduler.shutdown(wait=False)
        except Exception:
            pass
        server.should_exit = True
        asyncio.create_task(listener.stop())
        asyncio.create_task(close_db())

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _on_shutdown)
        except NotImplementedError:
            pass  # Windows — signal handlers not supported in asyncio loop

    logger.info("YukHub Collector fully operational — entering run loop")

    # 7. Block here until Telegram disconnects
    await listener.run()

    logger.info("YukHub Collector shut down cleanly")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    asyncio.run(main())
