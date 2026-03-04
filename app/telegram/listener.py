"""
Telegram Listener — connects via Telethon (MTProto user account).

Two responsibilities:
  1. Historical backfill — on startup, fetch the last MAX_POST_AGE_DAYS days
     from every group the account is a member of.
  2. Real-time handler — NewMessage events from all groups simultaneously.

The NewMessage handler is registered BEFORE backfill starts so that messages
posted while backfill is running are never missed.  The DB unique constraint
(ON CONFLICT DO NOTHING) makes any overlap between backfill and real-time
messages safe.
"""
import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from telethon import TelegramClient, events
from telethon.tl.types import Channel, Chat, Message, User

from app.config import settings
from app.db import repository
from app.events.bus import EventBus
from app.queue.memory_queue import MemoryQueue

logger = logging.getLogger(__name__)


class TelegramListener:
    def __init__(self, queue: MemoryQueue, bus: EventBus) -> None:
        self._queue = queue
        self._bus = bus

        # Ensure session directory exists (first-run safety)
        session_dir = os.path.dirname(settings.TELEGRAM_SESSION_NAME)
        if session_dir:
            os.makedirs(session_dir, exist_ok=True)

        self._client = TelegramClient(
            settings.TELEGRAM_SESSION_NAME,
            settings.TELEGRAM_API_ID,
            settings.TELEGRAM_API_HASH,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """
        Connect to Telegram, register the event handler, then launch backfill.
        Handler is registered first — no messages are missed during backfill.
        """
        await self._client.start(phone=settings.TELEGRAM_PHONE)
        logger.info("Telethon authenticated and connected to Telegram")

        self._client.add_event_handler(
            self._on_new_message,
            events.NewMessage(incoming=True),
        )
        logger.info("Real-time NewMessage handler registered")

        await self._start_backfill()

    async def run(self) -> None:
        """Block until the client disconnects. This is the main run loop."""
        await self._client.run_until_disconnected()

    async def stop(self) -> None:
        """Graceful disconnect."""
        if self._client.is_connected():
            await self._client.disconnect()
            logger.info("Telethon disconnected")

    # ------------------------------------------------------------------
    # Backfill
    # ------------------------------------------------------------------

    async def _start_backfill(self) -> None:
        """Launch one background task per group for historical backfill."""
        days = settings.MAX_POST_AGE_DAYS
        group_count = 0

        async for dialog in self._client.iter_dialogs():
            entity = dialog.entity
            if not isinstance(entity, (Chat, Channel)):
                continue  # skip private conversations
            group_name = _group_identifier(entity)
            asyncio.create_task(
                self._backfill_group(entity, group_name, days),
                name=f"backfill:{group_name}",
            )
            group_count += 1

        logger.info(f"Backfill launched for {group_count} groups (last {days} days)")

    async def _backfill_group(self, entity, group_name: str, days: int) -> None:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        count = 0
        try:
            async for msg in self._client.iter_messages(
                entity, reverse=True, offset_date=since
            ):
                if not isinstance(msg, Message):
                    continue
                text = (msg.text or "").strip()
                if len(text) < 10:
                    continue
                await self._persist_and_enqueue(msg, group_name)
                count += 1

            logger.info(f"Backfill done — {group_name}: {count} messages queued")
        except Exception as exc:
            logger.error(f"Backfill failed for {group_name}: {exc!r}")

    # ------------------------------------------------------------------
    # Real-time handler
    # ------------------------------------------------------------------

    async def _on_new_message(self, event: events.NewMessage.Event) -> None:
        if not (event.is_group or event.is_channel):
            return  # ignore private DMs

        msg: Message = event.message
        text = (msg.text or "").strip()
        if len(text) < 10:
            return  # FR-02: ignore short messages

        try:
            chat = await event.get_chat()
            group_name = _group_identifier(chat)
        except Exception:
            group_name = str(event.chat_id)

        await self._persist_and_enqueue(msg, group_name)

    # ------------------------------------------------------------------
    # Core: persist raw + push to queue + publish SSE event
    # ------------------------------------------------------------------

    async def _persist_and_enqueue(self, msg: Message, group_name: str) -> None:
        sender_id, sender_name = await _get_sender_info(msg)

        posted_at: datetime = msg.date
        if posted_at.tzinfo is None:
            posted_at = posted_at.replace(tzinfo=timezone.utc)

        # 1. Raw INSERT — returns None on duplicate (idempotent)
        post = await repository.insert_raw(
            tg_message_id=msg.id,
            tg_group_name=group_name,
            tg_sender_id=sender_id,
            tg_sender_name=sender_name,
            original_text=msg.text,
            posted_at=posted_at,
        )

        if post is None:
            return  # duplicate — already processed

        raw_payload = {
            "id": post.id,
            "group": group_name,
            "sender": sender_name,
            "text": msg.text,
            "posted_at": posted_at.isoformat(),
        }

        # 2. Push SSE new_post_raw to all connected clients
        await self._bus.publish("new_post_raw", raw_payload)

        # 3. Enqueue for LLM parsing
        await self._queue.push({"db_post_id": post.id, "text": msg.text})

        logger.debug(f"Raw saved & queued: id={post.id} group={group_name}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _group_identifier(entity) -> str:
    """Return a stable string key for the group (username or numeric ID)."""
    username = getattr(entity, "username", None)
    if username:
        return username
    return str(getattr(entity, "id", "unknown"))


async def _get_sender_info(msg: Message):
    """Safely extract sender ID and display name from a message."""
    sender_id: Optional[int] = None
    sender_name: Optional[str] = None
    try:
        sender = await msg.get_sender()
        if sender:
            sender_id = getattr(sender, "id", None)
            first = getattr(sender, "first_name", "") or ""
            last = getattr(sender, "last_name", "") or ""
            sender_name = f"{first} {last}".strip() or None
    except Exception:
        pass
    return sender_id, sender_name
