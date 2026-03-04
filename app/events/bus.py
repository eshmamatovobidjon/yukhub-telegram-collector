"""
In-process pub/sub event bus.

Each SSE client gets its own asyncio.Queue (maxsize=256).
A slow or disconnected client is silently dropped (QueueFull) with
zero impact on other subscribers.
"""
import asyncio
import logging
from typing import List

logger = logging.getLogger(__name__)


class EventBus:
    def __init__(self) -> None:
        self._subscribers: List[asyncio.Queue] = []

    # ------------------------------------------------------------------
    # Subscription management
    # ------------------------------------------------------------------

    def subscribe(self, maxsize: int = 256) -> asyncio.Queue:
        """Register a new SSE client. Returns its personal event queue."""
        q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._subscribers.append(q)
        logger.debug(f"EventBus: subscriber added (total={len(self._subscribers)})")
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        """Remove a subscriber queue. Safe to call even if already removed."""
        try:
            self._subscribers.remove(q)
            logger.debug(f"EventBus: subscriber removed (total={len(self._subscribers)})")
        except ValueError:
            pass  # already removed — no-op

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------

    async def publish(self, event_type: str, data: dict) -> None:
        """
        Deliver a typed event to every subscriber.
        Slow clients whose queue is full are silently dropped.
        """
        payload = {"type": event_type, "data": data}
        dead: List[asyncio.Queue] = []

        for q in self._subscribers:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                dead.append(q)
                logger.warning("EventBus: slow client dropped (per-subscriber queue full)")

        for q in dead:
            self.unsubscribe(q)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)
