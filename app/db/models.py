from sqlalchemy import (
    BigInteger, Boolean, Column, Index, Numeric,
    String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


class CargoPost(Base):
    __tablename__ = "cargo_posts"

    # --- Identity ---
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    tg_message_id = Column(BigInteger, nullable=False)
    tg_group_name = Column(String(128), nullable=False)
    tg_sender_id = Column(BigInteger, nullable=True)
    tg_sender_name = Column(String(256), nullable=True)

    # --- Raw content (never overwritten) ---
    original_text = Column(Text, nullable=False)
    posted_at = Column(TIMESTAMP(timezone=True), nullable=False)

    # --- Parsed fields (filled after LLM enrichment) ---
    origin_raw = Column(String(256), nullable=True)
    origin_region = Column(String(128), nullable=True)
    dest_raw = Column(String(256), nullable=True)
    dest_region = Column(String(128), nullable=True)
    dest_country = Column(String(64), nullable=True)
    cargo_type = Column(String(256), nullable=True)
    cargo_weight_kg = Column(Numeric(10, 2), nullable=True)
    cargo_volume_m3 = Column(Numeric(10, 2), nullable=True)
    truck_type = Column(String(128), nullable=True)
    truck_tonnage = Column(Numeric(6, 2), nullable=True)
    pickup_date = Column(TIMESTAMP(timezone=True), nullable=True)
    delivery_date = Column(TIMESTAMP(timezone=True), nullable=True)
    contact_phone = Column(String(64), nullable=True)
    contact_name = Column(String(256), nullable=True)
    price_raw = Column(Text, nullable=True)
    price_usd = Column(Numeric(10, 2), nullable=True)

    # --- Parse metadata ---
    parse_confidence = Column(Numeric(3, 2), nullable=True)
    parsed_fields = Column(JSONB, nullable=True)       # full raw LLM JSON
    parse_error = Column(Text, nullable=True)

    # --- Housekeeping ---
    collected_at = Column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    is_active = Column(Boolean, nullable=False, default=True)

    __table_args__ = (
        # Deduplication: same message in same group is rejected
        UniqueConstraint("tg_message_id", "tg_group_name", name="ix_cargo_tg_unique"),
        # Fast route-filtering queries from the UI
        Index("ix_cargo_origin_dest", "origin_region", "dest_region"),
        # Time-range queries and cleanup job
        Index("ix_cargo_posted_at", "posted_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<CargoPost id={self.id} group={self.tg_group_name!r} "
            f"tg_msg_id={self.tg_message_id} active={self.is_active}>"
        )
