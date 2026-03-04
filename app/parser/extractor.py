"""
LLM Extractor — uses the OpenAI-compatible chat completions API.

Point LLM_BASE_URL at any compatible server to swap models:
  - Leave unset        → OpenAI (gpt-4o-mini by default)
  - http://host:11434/v1  → Ollama (set LLM_MODEL=llama3.1:8b)
  - http://host:8080/v1   → vLLM / llama.cpp server
  - http://host:1234/v1   → LM Studio
"""
import json
import logging
from typing import Optional

from openai import AsyncOpenAI

from app.config import settings
from app.parser.schema import ParsedCargoPost

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are a freight logistics data extraction assistant specialising in Uzbekistan and Central Asia.
Your job is to parse Telegram messages written in Uzbek, Russian, or a mix of both, and extract structured cargo delivery request data.

## City / Region Normalisation Table
Map local spellings to standard English names:
Toshkent | Ташкент | Toshkend            → Tashkent
Samarqand | Самарканд                    → Samarkand
Buxoro | Бухара                          → Bukhara
Namangan | Наманган                      → Namangan
Andijon | Андижан                        → Andijan
Farg'ona | Фергана                       → Fergana
Qashqadaryo | Кашкадарья                 → Kashkadarya
Surxondaryo | Сурхандарья                → Surkhandarya
Navoiy | Навои                           → Navoiy
Xorazm | Хорезм                         → Khorezm
Jizzax | Джизак                          → Jizzakh
Sirdaryo | Сырдарья                      → Syrdarya
Qoraqalpog'iston | Каракалпакстан        → Karakalpakstan
Termiz | Термез                          → Termez
Nukus | Нукус                            → Nukus
Qarshi | Карши                           → Karshi
G'uzor | Гузар                           → Guzar
Moskva | Москва                          → Moscow
Sankt-Peterburg | Питер                  → Saint Petersburg
Almaty | Алматы                          → Almaty
Nur-Sultan | Astana | Астана             → Astana
Bishkek | Бишкек                         → Bishkek
Dushanbe | Душанбе                       → Dushanbe
Ashgabat | Ашхабад | Ашгабад             → Ashgabat
Istanbul | Стамбул                       → Istanbul
Moskva → Moscow

## Freight Glossary (Uzbek/Russian slang → meaning)
yuk | груз | mal | tovar       → cargo/goods
mashina | avto | фура | avto   → truck
t | тонна | tonna              → tonnes weight
kub | куб | m3                 → cubic metres
tent | тент                    → curtainsider truck
ref | рефрижератор | holodalnik → refrigerated truck
bort | бортовой                → flatbed truck
konteyner | контейнер          → container
tsisterna | цистерна           → tanker
sabzavot | овощи               → vegetables
meva | фрукты                  → fruit
qurilish | стройматериалы      → construction materials
don | зерно | bug'doy          → grain
paxta | хлопок                 → cotton
un | мука                      → flour
yog' | масло                   → oil/fat
narx | цена | summa | narxi    → price
so'm | сум | UZS               → Uzbek Som (UZS)
bugun | сегодня                → today
ertaga | завтра                → tomorrow
tel | тел | aloqa              → phone/contact
ism | имя                      → name

## Truck type normalisation
Map any truck body description to one of:
  tent | refrigerator | flatbed | box | tanker | container | other

## Currency conversion (approximate)
1 USD ≈ 12 700 UZS
1 USD ≈ 90 RUB
1 USD ≈ 450 KZT
If price is already in USD, use as-is.

## Output rules
- Respond ONLY with a single valid JSON object. No markdown fences, no prose, no explanation.
- Use null for any field you cannot extract from the message.
- Dates: ISO 8601 format YYYY-MM-DD. Use today's year if only day/month is mentioned.
- confidence: 0.0–1.0 — how certain you are that all extracted fields are correct.
- is_cargo_request: true ONLY for genuine cargo transport requests. Set false for ads, greetings, news, spam, unrelated chat.

## JSON schema (strict)
{
  "origin_raw": string|null,
  "origin_region": string|null,
  "dest_raw": string|null,
  "dest_region": string|null,
  "dest_country": string|null,
  "cargo_type": string|null,
  "cargo_weight_kg": number|null,
  "cargo_volume_m3": number|null,
  "truck_type": string|null,
  "truck_tonnage": number|null,
  "pickup_date": string|null,
  "delivery_date": string|null,
  "contact_phone": string|null,
  "contact_name": string|null,
  "price_raw": string|null,
  "price_usd": number|null,
  "confidence": number,
  "is_cargo_request": boolean
}"""

# Returned when parsing fails — safe to store, won't pollute DB with garbage
_SAFE_DEFAULT = ParsedCargoPost(is_cargo_request=False, confidence=0.0)

# ---------------------------------------------------------------------------
# Client (singleton, lazy-initialised)
# ---------------------------------------------------------------------------

_client: Optional[AsyncOpenAI] = None


def _build_client() -> AsyncOpenAI:
    kwargs: dict = {"api_key": settings.OPENAI_API_KEY}
    if settings.LLM_BASE_URL:
        kwargs["base_url"] = settings.LLM_BASE_URL
        logger.info(f"LLM client pointing at custom base_url={settings.LLM_BASE_URL!r}")
    return AsyncOpenAI(**kwargs)


def get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = _build_client()
    return _client


# ---------------------------------------------------------------------------
# Main extraction function
# ---------------------------------------------------------------------------

async def extract_cargo_info(text: str, today: str) -> ParsedCargoPost:
    """
    Call the LLM and return a ParsedCargoPost.
    Never raises — on any failure returns the safe default with is_cargo_request=False.
    """
    user_prompt = f"Today's date: {today}\n\nMessage:\n{text}"
    try:
        response = await get_client().chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0,
            max_tokens=512,
        )

        raw: str = (response.choices[0].message.content or "").strip()

        # Strip accidental markdown fences (```json ... ```)
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1]
            raw = raw.rsplit("```", 1)[0].strip()

        data = json.loads(raw)
        return ParsedCargoPost(**data)

    except json.JSONDecodeError as exc:
        logger.warning(f"LLM returned non-JSON: {exc!r} | snippet={text[:80]!r}")
    except Exception as exc:
        logger.error(f"LLM extraction failed ({type(exc).__name__}): {exc!r} | snippet={text[:80]!r}")

    return _SAFE_DEFAULT
