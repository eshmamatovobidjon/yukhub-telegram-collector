"""
SSE API — FastAPI application serving:
  GET /stream  — Server-Sent Events stream (new_post_raw, post_enriched, heartbeat)
  GET /health  — Service health check
"""
import asyncio
import json
import logging

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from app.events.bus import EventBus

logger = logging.getLogger(__name__)

app = FastAPI(title="YukHub Collector", version="1.0.0", docs_url="/docs")

# The EventBus instance is injected at startup by main.py via init_api()
_bus: EventBus | None = None


def init_api(bus: EventBus) -> None:
    """Called once from main.py before the uvicorn server starts."""
    global _bus
    _bus = bus


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "subscribers": _bus.subscriber_count if _bus else 0,
    }


# ---------------------------------------------------------------------------
# /stream
# ---------------------------------------------------------------------------

@app.get("/stream")
async def stream(request: Request):
    """
    Server-Sent Events endpoint.

    Wire format per event:
      event: <type>
      data: <json>

    Event types:
      new_post_raw   — raw message saved to DB (~100 ms after Telegram post)
      post_enriched  — LLM-parsed fields ready (~1-4 s after Telegram post)
      heartbeat      — sent every 30 s to keep connection alive
    """

    async def event_generator():
        q = _bus.subscribe()
        logger.info("SSE client connected")
        try:
            while True:
                # Check for client disconnect on each iteration
                if await request.is_disconnected():
                    logger.info("SSE client disconnected (detected by request check)")
                    break

                try:
                    payload = await asyncio.wait_for(q.get(), timeout=30.0)
                    event_type = payload["type"]
                    data = json.dumps(payload, default=str)
                    yield f"event: {event_type}\ndata: {data}\n\n"
                except asyncio.TimeoutError:
                    # 30-second heartbeat
                    yield "event: heartbeat\ndata: {}\n\n"

        except asyncio.CancelledError:
            logger.info("SSE generator cancelled (client dropped)")
        finally:
            _bus.unsubscribe(q)
            logger.info("SSE client cleanup done")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # disable nginx response buffering
            "Connection": "keep-alive",
        },
    )
