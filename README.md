# YukHub Telegram Collector

Real-time cargo post collector from Telegram groups.
Parses Uzbek/Russian freight messages with an LLM and streams structured data to the UI via Server-Sent Events.

---

## Architecture at a glance

```
Telegram Groups
      │ MTProto (Telethon user account)
      ▼
 TelegramListener ──► asyncio.Queue ──► ParserWorker (3×) ──► PostgreSQL
      │                                        │
      │ raw save                    LLM enrich │
      ▼                                        ▼
 PostgreSQL                              EventBus
      │                                        │
      └──────────────────────────────────────► SSE API (FastAPI :8000)
                                               │
                                          UI / Backend
```

Single Python process — one asyncio event loop handles everything.

---

## Prerequisites

| Tool | Version |
|---|---|
| Docker | 24+ |
| Docker Compose | v2 |
| Telegram account | Any personal number |
| OpenAI API key | (or self-hosted LLM — see below) |

---

## Step 1 — Get Telegram API credentials

1. Go to **https://my.telegram.org** and log in with the phone number you want to use.
2. Click **"API development tools"**.
3. Create an app (name/description don't matter).
4. Copy **App api_id** (integer) and **App api_hash** (string).

> **Important:** Use a real phone number that is already a member of the cargo Telegram groups you want to monitor.
> The service reads messages as this user — it sees exactly what this account sees.

---

## Step 2 — Configure environment

```bash
cp .env.example .env
```

Edit `.env` and fill in at minimum:

```dotenv
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=abcdef1234567890abcdef1234567890
TELEGRAM_PHONE=+998901234567

OPENAI_API_KEY=sk-...
```

Leave `DATABASE_URL` as-is — it points to the Compose postgres service automatically.

### Using a self-hosted LLM instead of OpenAI

The extractor uses the OpenAI-compatible API interface. Point it at any local server:

**Ollama (e.g. your Llama 3.1 8B setup):**
```dotenv
LLM_BASE_URL=http://host.docker.internal:11434/v1
LLM_MODEL=llama3.1:8b
OPENAI_API_KEY=ollama   # any non-empty string, Ollama ignores it
```

**vLLM / llama.cpp server:**
```dotenv
LLM_BASE_URL=http://your-gpu-server:8080/v1
LLM_MODEL=Meta-Llama-3.1-8B-Instruct
OPENAI_API_KEY=any_string
```

---

## Step 3 — First run (Telegram authentication)

On the very first start you must authenticate the Telegram session interactively.
The session file is then persisted in `./sessions/` and reused on all subsequent starts.

```bash
# Build the image
docker compose build

# First-run: keep the terminal open — you will be prompted for your SMS code
docker compose run --rm -it collector
```

When prompted:
```
Please enter the code you received: _
```
Enter the 5-digit SMS/app code Telegram sends to your phone.

If 2FA is enabled on the account, you will also be prompted for your password.

After successful login the terminal will show:
```
YukHub Collector fully operational — entering run loop
```

Press **Ctrl+C** to stop. The session is now saved in `./sessions/yukhub_session.session`.

---

## Step 4 — Normal start

```bash
docker compose up -d
```

View logs:
```bash
docker compose logs -f collector
```

---

## Day-to-day operations

### Adding a new group to monitor
Log into Telegram on any device with the account and **join the group**.
The Telethon `NewMessage` handler picks it up within seconds — no code change, no restart needed.

### Removing a group
**Leave the group** on Telegram. The listener stops receiving its messages immediately.

### Stopping the service
```bash
docker compose down
```
The Postgres volume and session file are preserved. Next `up` continues where it left off.

### Full reset (wipe DB + session)
```bash
docker compose down -v          # removes volumes
rm -f sessions/yukhub_session.session
```

### Checking service health
```bash
curl http://localhost:8000/health
# {"status":"ok","subscribers":2}
```

### Viewing real-time SSE stream (debugging)
```bash
curl -N http://localhost:8000/stream
```

You will see events like:
```
event: new_post_raw
data: {"type":"new_post_raw","data":{"id":4821,"group":"cargo_uz","sender":"Jasur","text":"...","posted_at":"2025-06-15T09:42:11+00:00"}}

event: post_enriched
data: {"type":"post_enriched","data":{"id":4821,"origin_region":"Tashkent","dest_region":"Samarkand","cargo_type":"vegetables","confidence":0.97,...}}

event: heartbeat
data: {}
```

---

## Consuming the SSE stream from the UI

```javascript
const es = new EventSource('http://localhost:8000/stream');

es.addEventListener('new_post_raw', (e) => {
  const post = JSON.parse(e.data);
  // post.data = { id, group, sender, text, posted_at }
  // Show card immediately with raw text
  renderCard(post.data);
});

es.addEventListener('post_enriched', (e) => {
  const post = JSON.parse(e.data);
  // post.data = { id, origin_region, dest_region, cargo_type, confidence, ... }
  // Update the card with structured data
  updateCard(post.data.id, post.data);
});

es.addEventListener('heartbeat', () => {
  // Connection is alive — optionally update a "last seen" indicator
});

es.onerror = () => {
  // EventSource reconnects automatically — no manual retry logic needed
};
```

---

## Switching LLM mid-operation

1. Edit `.env` — change `LLM_MODEL` and/or `LLM_BASE_URL`.
2. `docker compose restart collector`
3. Already-parsed posts are unaffected. Raw-only posts in the DB can be re-parsed
   once you improve the prompt in `app/parser/extractor.py`.

---

## Configuration reference

| Variable | Default | Description |
|---|---|---|
| `TELEGRAM_API_ID` | required | From my.telegram.org |
| `TELEGRAM_API_HASH` | required | From my.telegram.org |
| `TELEGRAM_PHONE` | required | Account phone with country code |
| `TELEGRAM_SESSION_NAME` | `sessions/yukhub_session` | Session file path |
| `DATABASE_URL` | required | `postgresql+asyncpg://...` |
| `OPENAI_API_KEY` | required | OpenAI key (or dummy string for local LLMs) |
| `LLM_MODEL` | `gpt-4o-mini` | Model name passed to API |
| `LLM_BASE_URL` | unset | Custom base URL for self-hosted models |
| `MAX_POST_AGE_DAYS` | `15` | Retention window (days) |
| `CLEANUP_INTERVAL_HOURS` | `6` | Cleanup job frequency |
| `QUEUE_MAX_SIZE` | `5000` | asyncio.Queue bound |
| `PARSER_WORKERS` | `3` | Concurrent LLM tasks |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |

---

## Failure behaviour

| Scenario | What happens |
|---|---|
| Telegram network drop | Telethon auto-reconnects with exponential backoff |
| LLM API timeout / error | Post stays in DB as raw text; parse_error logged; worker continues |
| LLM API completely down | Queue fills; raw posts saved and pushed to UI; parsing resumes automatically |
| Postgres connection lost | SQLAlchemy pool retries; errors logged |
| SSE client drops | Its queue removed from EventBus; zero impact on other clients |
| Service crash / restart | Backfill re-runs on next start; DB unique constraint rejects duplicates safely |

---

## Project structure

```
yukhub-telegram-collector/
├── app/
│   ├── main.py              # Entry point — wires all components
│   ├── config.py            # All config via pydantic-settings
│   ├── telegram/
│   │   └── listener.py      # Telethon client, backfill, real-time handler
│   ├── parser/
│   │   ├── worker.py        # asyncio worker pool (PARSER_WORKERS tasks)
│   │   ├── extractor.py     # OpenAI-compatible LLM client + prompt
│   │   └── schema.py        # ParsedCargoPost Pydantic model
│   ├── db/
│   │   ├── models.py        # SQLAlchemy ORM: CargoPost
│   │   ├── session.py       # Async engine, session factory, init_db()
│   │   └── repository.py    # insert_raw, enrich_post, mark_inactive, delete_older_than
│   ├── queue/
│   │   └── memory_queue.py  # asyncio.Queue wrapper
│   ├── events/
│   │   └── bus.py           # Per-subscriber pub/sub EventBus
│   ├── api/
│   │   └── stream.py        # FastAPI: GET /stream, GET /health
│   └── scheduler/
│       └── jobs.py          # APScheduler cleanup job
├── sessions/                # Telegram session file (git-ignored)
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
└── README.md
```
