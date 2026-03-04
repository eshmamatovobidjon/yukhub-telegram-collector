from typing import Optional
from pydantic import BaseModel, Field


class ParsedCargoPost(BaseModel):
    """
    Structured output produced by the LLM extractor.

    All fields are Optional except confidence and is_cargo_request.
    Any field the LLM cannot extract is left as None.
    """

    # Origin
    origin_raw: Optional[str] = None        # as written in message
    origin_region: Optional[str] = None     # normalized English region name

    # Destination
    dest_raw: Optional[str] = None
    dest_region: Optional[str] = None
    dest_country: Optional[str] = None      # "Uzbekistan" for domestic

    # Cargo
    cargo_type: Optional[str] = None        # in English
    cargo_weight_kg: Optional[float] = None
    cargo_volume_m3: Optional[float] = None

    # Truck
    truck_type: Optional[str] = None        # tent / refrigerator / flatbed / box / tanker / container / other
    truck_tonnage: Optional[float] = None   # required capacity in tonnes

    # Dates (ISO 8601 strings — repository converts to datetime)
    pickup_date: Optional[str] = None
    delivery_date: Optional[str] = None

    # Contact
    contact_phone: Optional[str] = None
    contact_name: Optional[str] = None

    # Price
    price_raw: Optional[str] = None         # exactly as written
    price_usd: Optional[float] = None       # converted equivalent

    # Quality indicators
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    is_cargo_request: bool = False
