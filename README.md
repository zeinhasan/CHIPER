# CHIPER — H.A.K.I.

**C**ontent **H**arvesting, **I**ntegration, **P**arsing, and **E**xtraction **R**outine

> *Headless Asynchronous Knowledge Integrator — v2.5 Deep Fetch + AI Summary Edition*

A Python/FastAPI middleware API that bridges AI agents with SearXNG for automated web research. Extracts clean Markdown content from static and JavaScript-heavy pages using a Two-Tier strategy with **auto JS garbage detection**. AI summarization runs as a **background task** — the API returns scraped results in <5 seconds while the summary is generated asynchronously. Full observability: JSON logging, X-Request-ID tracing, Prometheus metrics.

---

## 🏗️ Architecture

```
POST /api/v1/research
         |
    +----+-----------------------------+
    |  Tracing: X-Request-ID           |  <- Unique trace ID
    +----+-----------------------------+
         |
         v
   +----------+
   | SearXNG  |  <- Self-hosted search engine (Docker)
   +----+-----+
        |
        v
   +--------------------------------------+
   |         Two-Tier Scraping            |
   |  httpx (static) -> Playwright (SPA)  |
   |  + retry + backoff + circuit breaker |
   +----+-----+
        |
        v
   +----------+
   | Markdown |  <- Clean output
   +----+-----+
        |
        +----- generate_summary = true?
        |           |
        |           v
        |    +----------------+
        |    | Background Task |  <- AI Summary (async, non-blocking)
        |    +-------+--------+
        |            |
        |            v
        |    GET /research/{id}  <- Poll for result
        |
        +----- return immediately (<5s)
                    with results + task_id
```

---

## 🛠️ Tech Stack

| Component | Technology |
|-----------|------------|
| **Framework** | FastAPI + Uvicorn |
| **Search Engine** | SearXNG (self-hosted, JSON API) |
| **Static Fetch** | `httpx` (async, shared connection pool) |
| **Dynamic Render** | `playwright` (headless Chromium) |
| **Content Extractor** | `trafilatura` + `markdownify` |
| **AI Summarization** | `openai` SDK → OpenRouter → DeepSeek |
| **Reliability** | Retry+backoff, circuit breaker, rate limiting (`slowapi`) |
| **Background Tasks** | `asyncio.create_task` + in-memory task store |
| **Structured Logging** | JSON logs with trace context |
| **Metrics** | Prometheus |
| **Deployment** | Docker + Docker Compose |

---

## 📁 Project Structure

```
CHIPER/
├── app/
│   ├── main.py                  # FastAPI entry + lifespan (httpx pool, Playwright, circuit)
│   ├── config.py                # Environment-based settings
│   ├── middleware/
│   │   └── tracing.py           # X-Request-ID middleware
│   ├── models/
│   │   └── schemas.py           # Pydantic request/response validation
│   ├── api/
│   │   └── routes.py            # POST /research + GET /research/{task_id}
│   ├── services/
│   │   ├── searxng.py           # SearXNG JSON API integration
│   │   ├── scraper.py           # Two-Tier scraping + JS garbage detection
│   │   └── summarizer.py        # AI summarization (DeepSeek)
│   └── utils/
│       ├── helpers.py           # Re-export logging utilities
│       ├── logging.py           # Structured JSON logging + trace context
│       ├── metrics.py           # Prometheus metrics definitions
│       ├── circuit_breaker.py   # 3-state circuit breaker
│       └── task_store.py        # In-memory background task store
├── searxng/
│   └── settings.yml             # SearXNG configuration
├── Dockerfile                   # CHIPER image build
├── docker-compose.yml           # All-in-one: DoH proxy + SearXNG + CHIPER
├── requirements.txt             # Python dependencies
├── IMPROVEMENT.md               # Improvement roadmap
├── .env.example                 # Environment variables template
└── .env                         # Environment variables (private)
```

---

## 🚀 Quick Start (Docker — Recommended)

```bash
git clone <repo-url> && cd CHIPER
cp .env.example .env    # fill in CMD_API_KEY and CMD_BASE_URL
docker compose up -d --build
```

Once running:
- **CHIPER API**: http://localhost:8000
- **SearXNG**: http://localhost:8081
- **Swagger Docs**: http://localhost:8000/docs
- **Health**: http://localhost:8000/health
- **Metrics**: http://localhost:8000/metrics

---

## 📡 API Reference

### `POST /api/v1/research`

Executes the research pipeline. Scraping runs synchronously; AI summarization runs as a background task (non-blocking).

#### Request

```json
{
  "query": "Latest financial reports from tech companies",
  "max_results": 5,
  "force_js_render": false,
  "generate_summary": true
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `query` | string | — | Search query *(required)* |
| `max_results` | int | `5` | Maximum URLs to scrape (1–20) |
| `force_js_render` | bool | `false` | Skip Tier-1 for all URLs |
| `generate_summary` | bool | `false` | Generate AI summary (background task) |

#### Response — Without Summary

```json
{
  "query": "DSSA Anjlok",
  "task_id": null,
  "ai_summary": null,
  "results": [{ "url": "...", "title": "...", "fetch_method": "httpx", "markdown_content": "...", "content_length": 3116 }],
  "total_results": 5
}
```

#### Response — With Summary (Background Task)

```json
{
  "query": "DSSA Anjlok",
  "task_id": "a1b2c3d4-...",
  "ai_summary": null,
  "results": [{ "url": "...", "title": "...", "fetch_method": "httpx", "markdown_content": "...", "content_length": 3116 }],
  "total_results": 5
}
```

| Field | Description |
|-------|-------------|
| `query` | Original search query |
| `task_id` | Background task ID (null if `generate_summary: false`) |
| `ai_summary` | AI summary (always null in initial response — poll to get it) |
| `results[].url` | Scraped URL |
| `results[].title` | Title from SearXNG |
| `results[].fetch_method` | `httpx` or `playwright` |
| `results[].markdown_content` | Clean extracted Markdown |
| `total_results` | Total scraped results |

### `GET /api/v1/research/{task_id}`

Poll for the result of a background summarization task.

#### Response — Still Processing

```json
{ "task_id": "abc-123", "status": "processing", "query": null, "ai_summary": null }
```

#### Response — Done

```json
{ "task_id": "abc-123", "status": "done", "query": null, "ai_summary": "Based on the five sources..." }
```

#### Response — Error

```json
{ "task_id": "abc-123", "status": "error", "query": null, "ai_summary": "Summarization failed: ..." }
```

### `GET /health`

```json
{ "status": "ok", "version": "2.5.0" }
```

### `GET /metrics`

Exposes Prometheus metrics (OpenMetrics format).

---

## ⚙️ Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SEARXNG_BASE_URL` | `http://localhost:8080` | SearXNG instance URL |
| `CMD_API_KEY` | — | API key for AI summarization *(required)* |
| `CMD_BASE_URL` | `https://openrouter.ai/api/v1` | OpenAI-compatible API base URL |
| `CMD_MODEL` | `deepseek/deepseek-chat` | Model name for summarization |
| `PLAYWRIGHT_BROWSER_PATH` | *(auto)* | Custom Chromium binary path |
| `HOST` | `0.0.0.0` | Server host binding |
| `PORT` | `8000` | Server port |
| `LOG_FORMAT` | `json` | `json` (structured) or `console` |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `MIN_CONTENT_LENGTH` | `200` | Min chars for Tier-1 sufficiency |
| `FETCH_STATIC_RETRIES` | `3` | Max httpx retry attempts |
| `FETCH_DYNAMIC_RETRIES` | `2` | Max Playwright retry attempts |
| `FETCH_RETRY_BASE_DELAY` | `1.0` | Base delay for exponential backoff (seconds) |
| `CIRCUIT_BREAKER_FAILURES` | `5` | Failures before opening SearXNG circuit |
| `CIRCUIT_BREAKER_TIMEOUT` | `30.0` | Seconds before half-opening circuit |
| `RATE_LIMIT` | `30/minute` | Max requests per IP |

---

## 🔍 Observability

### Structured JSON Logging

```json
{
  "timestamp": "2026-06-29T12:30:00.123+00:00",
  "level": "INFO",
  "logger": "app.api.routes",
  "message": "Starting background summarization",
  "module": "routes",
  "function": "research",
  "line": 118,
  "trace_id": "a1b2c3d4-...",
  "task_id": "abc-123",
  "document_count": 5
}
```

Switch format: `LOG_FORMAT=console` in `.env`.

### Request Tracing

All requests get a unique `X-Request-ID` (auto-generated or passed via header). Appears in all logs and response headers.

### Prometheus Metrics

`/metrics` endpoint — scrape-ready. **Prometheus is optional.**

---

## 🔄 Two-Tier Scraping Flow

```
URL received → force_js_render?
    |
    +-- false → Tier 1: httpx (retry 3x, exponential backoff)
    |              +-- content >= 200 chars AND not JS garbage → DONE
    |              +-- otherwise → fallback to Tier 2
    |
    +-- true  → Tier 2: Playwright (retry 2x, exponential backoff)
                   +-- block images/CSS/fonts
                   +-- wait network idle
                   +-- extract rendered HTML

HTML → trafilatura → Clean Markdown
```

Concurrency: `asyncio.gather` + `Semaphore` (max 3 Playwright contexts).

---

## 🧪 Example cURL Requests

```bash
# Quick research (no summary, <5s)
curl -X POST http://localhost:8000/api/v1/research \
  -H "Content-Type: application/json" \
  -d '{"query": "DSSA Anjlok", "max_results": 3}'

# Research + summary (background, <5s return)
curl -X POST http://localhost:8000/api/v1/research \
  -H "Content-Type: application/json" \
  -d '{"query": "DSSA Anjlok", "max_results": 5, "generate_summary": true}'
# → {"task_id": "abc-123", "results": [...], "ai_summary": null}

# Poll for summary
curl http://localhost:8000/api/v1/research/abc-123
# → {"status": "processing"} ... repeat until "done"

# Force Playwright
curl -X POST http://localhost:8000/api/v1/research \
  -H "Content-Type: application/json" \
  -d '{"query": "React 19 features", "max_results": 3, "force_js_render": true}'

# Metrics
curl http://localhost:8000/metrics
```

---

## 📝 License

MIT License — see [LICENSE](LICENSE).
