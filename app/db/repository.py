"""
Repository layer — all SQL in one place.

All methods open their own session via the get_session() context manager.
They never accept a session argument so callers don't have to manage sessions.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import delete, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models import CargoPost
from app.db.session import get_session
from app.parser.schema import ParsedCargoPost

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

async def insert_raw(
    tg_message_id: int,
    tg_group_name: str,
    tg_sender_id: Optional[int],
    tg_sender_name: Optional[str],
    original_text: str,
    posted_at: datetime,
) -> Optional[CargoPost]:
    """
    Insert a raw (pre-parse) row.
    Returns the new CargoPost, or None if the row already exists (duplicate).
    Uses ON CONFLICT DO NOTHING so backfill and crash-recovery are idempotent.
    """
    async with get_session() as session:
        stmt = (
            pg_insert(CargoPost)
            .values(
                tg_message_id=tg_message_id,
                tg_group_name=tg_group_name,
                tg_sender_id=tg_sender_id,
                tg_sender_name=tg_sender_name,
                original_text=original_text,
                posted_at=posted_at,
                collected_at=datetime.now(timezone.utc),
                is_active=True,
            )
            .on_conflict_do_nothing(constraint="ix_cargo_tg_unique")
            .returning(CargoPost)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


async def enrich_post(post_id: int, parsed: ParsedCargoPost) -> Optional[CargoPost]:
    """
    Update an existing row with LLM-extracted fields.
    Returns the updated CargoPost, or None if the row somehow no longer exists.
    """
    async with get_session() as session:
        stmt = (
            update(CargoPost)
            .where(CargoPost.id == post_id)
            .values(
                origin_raw=parsed.origin_raw,
                origin_region=parsed.origin_region,
                dest_raw=parsed.dest_raw,
                dest_region=parsed.dest_region,
                dest_country=parsed.dest_country,
                cargo_type=parsed.cargo_type,
                cargo_weight_kg=parsed.cargo_weight_kg,
                cargo_volume_m3=parsed.cargo_volume_m3,
                truck_type=parsed.truck_type,
                truck_tonnage=parsed.truck_tonnage,
                pickup_date=_parse_date(parsed.pickup_date),
                delivery_date=_parse_date(parsed.delivery_date),
                contact_phone=parsed.contact_phone,
                contact_name=parsed.contact_name,
                price_raw=parsed.price_raw,
                price_usd=parsed.price_usd,
                parse_confidence=parsed.confidence,
                parsed_fields=parsed.model_dump(mode="json"),
                parse_error=None,
                is_active=True,
            )
            .returning(CargoPost)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


async def mark_inactive(post_id: int) -> None:
    """Mark a post as non-cargo. Called when LLM says is_cargo_request=False."""
    async with get_session() as session:
        stmt = (
            update(CargoPost)
            .where(CargoPost.id == post_id)
            .values(is_active=False)
        )
        await session.execute(stmt)


async def mark_parse_error(post_id: int, error: str) -> None:
    """Record a parse failure on the row (raw text preserved)."""
    async with get_session() as session:
        stmt = (
            update(CargoPost)
            .where(CargoPost.id == post_id)
            .values(parse_error=error[:2000])  # cap length
        )
        await session.execute(stmt)


async def delete_older_than(days: int) -> int:
    """
    Hard-delete rows whose posted_at is older than `days` days.
    Returns the number of rows deleted.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    async with get_session() as session:
        stmt = (
            delete(CargoPost)
            .where(CargoPost.posted_at < cutoff)
            .returning(CargoPost.id)
        )
        result = await session.execute(stmt)
        rows = result.fetchall()
        count = len(rows)
        if count:
            logger.info(f"Cleanup: deleted {count} posts older than {days} days (cutoff={cutoff.date()})")
        return count


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_date(value: Optional[str]) -> Optional[datetime]:
    """Convert ISO 8601 date string from LLM to a timezone-aware datetime, or None."""
    if not value:
        return None
    try:
        from datetime import date
        d = date.fromisoformat(value[:10])  # take YYYY-MM-DD part only
        return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    except Exception:
        return None
