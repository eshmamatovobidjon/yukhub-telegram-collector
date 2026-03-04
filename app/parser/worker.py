"""
Parser Worker — consumes the asyncio queue and enriches posts via the LLM.

Runs PARSER_WORKERS concurrent asyncio tasks (default: 3).
Each task independently pops from the shared queue and makes one LLM call.
Because LLM calls are pure async I/O, all tasks share the single event loop
without any threading overhead.
"""
import asyncio
import logging
from datetime import date

from app.config import settings
from app.db import repository
from app.events.bus import EventBus
from app.parser import extractor
from app.queue.memory_queue import MemoryQueue

logger = logging.getLogger(__name__)


class ParserWorker:
    def __init__(self, queue: MemoryQueue, bus: EventBus) -> None:
        self._queue = queue
        self._bus = bus
        self._running = False

    async def start(self) -> None:
        """Spawn PARSER_WORKERS concurrent loop tasks and wait for all of them."""
        self._running = True
        worker_tasks = [
            asyncio.create_task(self._loop(worker_id=i), name=f"parser-worker-{i}")
            for i in range(settings.PARSER_WORKERS)
        ]
        logger.info(f"ParserWorker: {settings.PARSER_WORKERS} concurrent tasks started")
        await asyncio.gather(*worker_tasks)

    def stop(self) -> None:
        """Signal all worker loops to exit after finishing their current message."""
        self._running = False
        logger.info("ParserWorker: stop signal sent")

    # ------------------------------------------------------------------
    # Worker loop
    # ------------------------------------------------------------------

    async def _loop(self, worker_id: int) -> None:
        logger.info(f"Parser worker-{worker_id} running")
        while self._running:
            payload = await self._queue.pop(timeout=5)
            if payload is None:
                continue  # timeout — check _running flag and loop again
            await self._process(payload, worker_id)
        logger.info(f"Parser worker-{worker_id} stopped")

    async def _process(self, payload: dict, worker_id: int) -> None:
        post_id: int = payload["db_post_id"]
        text: str = payload["text"]
        today: str = date.today().isoformat()

        try:
            parsed = await extractor.extract_cargo_info(text, today)

            if not parsed.is_cargo_request:
                # Not a cargo post — mark inactive, don't publish
                await repository.mark_inactive(post_id)
                logger.debug(f"[w{worker_id}] Post {post_id}: not cargo, marked inactive")
                return

            post = await repository.enrich_post(post_id, parsed)
            if post is None:
                logger.warning(f"[w{worker_id}] enrich_post returned None for post_id={post_id}")
                return

            # Build SSE payload — only serialisable types
            enriched_payload = {
                "id": post_id,
                "origin_region": parsed.origin_region,
                "dest_region": parsed.dest_region,
                "dest_country": parsed.dest_country,
                "cargo_type": parsed.cargo_type,
                "cargo_weight_kg": _f(parsed.cargo_weight_kg),
                "cargo_volume_m3": _f(parsed.cargo_volume_m3),
                "truck_type": parsed.truck_type,
                "truck_tonnage": _f(parsed.truck_tonnage),
                "pickup_date": parsed.pickup_date,
                "delivery_date": parsed.delivery_date,
                "contact_phone": parsed.contact_phone,
                "contact_name": parsed.contact_name,
                "price_raw": parsed.price_raw,
                "price_usd": _f(parsed.price_usd),
                "confidence": round(parsed.confidence, 4),
            }

            await self._bus.publish("post_enriched", enriched_payload)
            logger.info(
                f"[w{worker_id}] Post {post_id} enriched "
                f"(origin={parsed.origin_region} → dest={parsed.dest_region}, "
                f"conf={parsed.confidence:.2f})"
            )

        except Exception as exc:
            logger.error(
                f"[w{worker_id}] Unhandled exception processing post {post_id}: {exc!r}",
                exc_info=True,
            )
            try:
                await repository.mark_parse_error(post_id, str(exc))
            except Exception:
                pass  # log only — never crash the worker


def _f(value) -> float | None:
    """Safely convert Decimal/float to Python float for JSON serialisation."""
    return float(value) if value is not None else None
