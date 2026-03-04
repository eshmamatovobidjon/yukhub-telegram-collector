"""
Thin wrapper around asyncio.Queue.

Keeps the listener and parser decoupled inside the same process.
No Redis, no external dependency — everything is in-process.
"""
import asyncio
from typing import Any, Optional


class MemoryQueue:
    def __init__(self, maxsize: int = 5000):
        self._q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)

    async def push(self, payload: Any) -> None:
        """Enqueue a payload. Blocks (backpressure) if queue is at maxsize."""
        await self._q.put(payload)

    async def pop(self, timeout: float = 5.0) -> Optional[Any]:
        """Dequeue with a timeout. Returns None on timeout (allows caller to check stop flag)."""
        try:
            return await asyncio.wait_for(self._q.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    def length(self) -> int:
        return self._q.qsize()
