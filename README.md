# CHIPER — H.A.K.I.

**C**ontent **H**arvesting, **I**ntegration, **P**arsing, and **E**xtraction **R**outine

> *Headless Asynchronous Knowledge Integrator — v1.2.0*</new_text>

A Python/FastAPI middleware API that bridges AI agents with SearXNG for automated web research. Extracts clean Markdown content from static and JavaScript-heavy pages using a Two-Tier strategy with **auto JS garbage detection**. Now with **recursive multi-page crawl** — start from one URL and follow internal links depth-by-depth, with path filtering, configurable concurrency, and optional AI summarization of all crawled pages. Supports **synchronous** mode (all steps run inline, summary returned directly) and **full async/background** mode (entire pipeline runs in background, poll for results). Full observability + reliability: JSON logging, X-Request-ID tracing, Prometheus metrics, retry+backoff, circuit breaker, rate limiting, connection pool reuse, browser pool, configurable search categories, and domain filtering for non-scrapable media sites.

---

## 🏗️ Architecture

```mermaid
flowchart TD
    A[POST /api/v1/research] --> B[Tracing Middleware<br/>X-Request-ID]
    B --> C{run_async?}

    C -->|true| Z[Background Task<br/>Entire Pipeline]
    Z --> Z1["GET /research/{id}<br/>Poll for full result"]

    C -->|false| D[SearXNG<br/>Search Engine]
    D -->|circuit breaker| E{Two-Tier<br/>Scraping}

    E -->|Tier 1| F[httpx<br/>Static Fetch]
    F -->|retry 3x backoff| G{Content OK?}
    G -->|yes| H[Clean Markdown]
    G -->|no / JS garbage| I[Tier 2]
    E -->|force_js_render| I

    I[Playwright<br/>Dynamic Render] -->|retry 2x + browser pool| H

    H --> J{generate_summary?}
    J -->|yes| K[Sync AI Summarization<br/>DeepSeek]
    K --> L[Return Full Results]
    J -->|no| L
```

---

## 🛠️ Tech Stack

| Component | Technology |
|-----------|------------|
| **Framework** | FastAPI + Uvicorn |
| **Search Engine** | SearXNG (self-hosted, JSON API) |
| **Static Fetch** | `httpx` (async, shared connection pool) |
| **Dynamic Render** | `playwright` (headless Chromium, browser pool) |
| **Content Extractor** | `trafilatura` + `markdownify` |
| **AI Summarization** | `openai` SDK → OpenRouter → DeepSeek |
| **Reliability** | Retry+backoff, circuit breaker, rate limiting (`slowapi`), browser pool |
| **Background Tasks** | `asyncio.create_task` + in-memory task store |
| **Structured Logging** | JSON logs with trace context |
| **Metrics** | Prometheus |
| **Deployment** | Docker + Docker Compose |

---

## 📁 Project Structure

```
CHIPER/
├── app/
│   ├── main.py                  # FastAPI entry + lifespan (httpx pool, browser pool, circuit)
│   ├── config.py                # Environment-based settings
│   ├── middleware/
│   │   └── tracing.py           # X-Request-ID middleware
│   ├── models/
│   │   └── schemas.py           # Pydantic request/response validation
│   ├── api/
│   │   └── routes.py            # POST /research + GET /research/{task_id}
│   ├── services/
│   │   ├── searxng.py           # SearXNG JSON API integration
│   │   ├── scraper.py           # Two-Tier scraping + JS garbage detection + browser pool
│   │   ├── crawler.py           # BFS recursive crawl engine (NEW)
│   │   └── summarizer.py        # AI summarization (DeepSeek)
│   └── utils/
│       ├── helpers.py           # Re-export logging utilities
│       ├── logging.py           # Structured JSON logging + trace context
│       ├── metrics.py           # Prometheus metrics definitions
│       ├── circuit_breaker.py   # 3-state circuit breaker
│       ├── links.py             # URL normalization + internal link extraction (NEW)
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
git clone https://github.com/zeinhasan/CHIPER && cd CHIPER
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

Executes the research pipeline. Supports two modes:
- **Synchronous** (`run_async: false`, default): All steps run immediately. If `generate_summary: true`, the AI summary is returned directly in the response (no polling needed).
- **Async** (`run_async: true`): Entire pipeline (search + scrape + summary) runs as background task. Returns `task_id` immediately. Poll `GET /research/{task_id}` for full result.

#### Request

```json
{
  "query": "Latest financial reports from tech companies",
  "max_results": 5,
  "force_js_render": false,
  "generate_summary": true,
  "run_async": false
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `query` | string | — | Search query *(required)* |
| `max_results` | int | `5` | Maximum URLs to scrape (1–20) |
| `force_js_render` | bool | `false` | Skip Tier-1 for all URLs |
| `generate_summary` | bool | `false` | Generate AI summary. Runs synchronously when `run_async: false` (summary returned directly). |
| `run_async` | bool | `false` | Run entire pipeline as background task. Returns `task_id` immediately. Poll `GET /research/{task_id}` for full result. |

#### Response — With Summary (Sync Mode)

When `run_async: false` and `generate_summary: true`, the summary is returned directly:

```json
{
  "query": "DSSA Anjlok",
  "ai_summary": "PT Dian Swastatika Sentosa Tbk (DSSA) membukukan laba bersih...",
  "results": [{
    "url": "https://...",
    "title": "Saham DSSA Anjlok...",
    "fetch_method": "httpx",
    "markdown_content": "PT Dian Swastatika...",
    "content_length": 3116
  }],
  "total_results": 5
}
```

| Field | Description |
|-------|-------------|
| `query` | Original search query |
| `task_id` | Background task ID (only populated when `run_async: true`) |
| `ai_summary` | AI summary (populated when `generate_summary: true`; null otherwise) |
| `results[].url` | Scraped URL |
| `results[].title` | Title from SearXNG |
| `results[].fetch_method` | `httpx` or `playwright` |
| `results[].markdown_content` | Clean extracted Markdown |
| `total_results` | Total scraped results |

### `GET /api/v1/research/{task_id}`

Poll for the result of a background full-research task (used with `run_async: true`).

| `status` | Meaning |
|----------|---------|
| `processing` | Task is still being processed |
| `done` | Task is ready — `results`, `total_results`, and `ai_summary` are populated |
| `error` | Task failed (error message in `ai_summary`) |
| `not_found` | Task ID not found or expired |

### `POST /api/v1/crawl`

Recursively crawl a website, following same-domain internal links and extracting clean Markdown from every page. Built on the Two-Tier scraping engine.

Supports two modes:
- **Synchronous** (`run_async: false`, default): Crawl runs immediately, returns full results.
- **Async** (`run_async: true`): Crawl runs as background task. Returns `task_id` immediately. Poll `GET /crawl/{task_id}` for results.

#### Request

```json
{
  "url": "http://quotes.toscrape.com",
  "max_depth": 2,
  "max_pages": 10,
  "include_paths": ["/tag/.*"],
  "exclude_paths": ["/login.*"],
  "force_js_render": false,
  "generate_summary": false,
  "run_async": false
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `url` | string | — | Starting URL to crawl *(required)* |
| `max_depth` | int | `3` | Maximum crawl depth from base URL (1–10) |
| `max_pages` | int | `20` | Maximum total pages to crawl (1–100) |
| `include_paths` | list\[string\] | `[]` | Regex patterns for path whitelist |
| `exclude_paths` | list\[string\] | `[]` | Regex patterns for path blacklist |
| `force_js_render` | bool | `false` | Use Playwright for all pages (skip Tier-1) |
| `generate_summary` | bool | `false` | Generate AI summary of all crawled content |
| `run_async` | bool | `false` | Run crawl as background task |

#### Response (Sync Mode)

```json
{
  "base_url": "http://quotes.toscrape.com",
  "total_pages": 5,
  "results": [
    {
      "url": "http://quotes.toscrape.com",
      "title": "Quotes to Scrape",
      "depth": 0,
      "fetch_method": "httpx",
      "markdown_content": "# Quotes to Scrape\n\n...",
      "content_length": 2890,
      "links_found": 46
    }
  ],
  "task_id": null,
  "ai_summary": null
}
```

| Field | Description |
|-------|-------------|
| `base_url` | Starting URL (normalized) |
| `total_pages` | Number of successfully crawled pages |
| `results[].url` | Page URL |
| `results[].depth` | Depth level from seed (0 = seed) |
| `results[].fetch_method` | `httpx` or `playwright` |
| `results[].links_found` | Number of internal links discovered |
| `results[].markdown_content` | Clean extracted Markdown |
| `task_id` | Background task ID (only when `run_async: true`) |
| `ai_summary` | AI summary (only when `generate_summary: true`) |

### `GET /api/v1/crawl/{task_id}`

Poll for the result of a background crawl task (used with `run_async: true`).

| `status` | Meaning |
|----------|---------|
| `processing` | Task is still being processed |
| `done` | Task is ready — `base_url`, `total_pages`, `results`, `ai_summary` populated |
| `error` | Task failed (error message in `ai_summary`) |
| `not_found` | Task ID not found or expired |

### `GET /health`

```json
{ "status": "ok", "version": "1.2.0" }
```

### `GET /metrics`

Exposes Prometheus metrics (OpenMetrics format).

---

## ⚙️ Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SEARXNG_BASE_URL` | `http://localhost:8080` | SearXNG instance URL |
| `SEARXNG_CATEGORIES` | `web,news` | SearXNG search categories (comma-separated). Default excludes videos, images, and files. |
| `CMD_API_KEY` | — | API key for AI summarization *(required)* |
| `CMD_BASE_URL` | `https://openrouter.ai/api/v1` | OpenAI-compatible API base URL |
| `CMD_MODEL` | `deepseek/deepseek-chat` | Model name for summarization |
| `PLAYWRIGHT_BROWSER_PATH` | *(auto)* | Custom Chromium binary path |
| `BROWSER_POOL_SIZE` | `2` | Number of Chromium instances (round-robin) |
| `HOST` | `0.0.0.0` | Server host binding |
| `PORT` | `8000` | Server port |
| `LOG_FORMAT` | `json` | `json` (structured) or `console` |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `MIN_CONTENT_LENGTH` | `200` | Min chars for Tier-1 sufficiency |
| `FETCH_STATIC_RETRIES` | `3` | Max httpx retry attempts |
| `FETCH_DYNAMIC_RETRIES` | `2` | Max Playwright retry attempts |
| `FETCH_RETRY_BASE_DELAY` | `1.0` | Base delay for exponential backoff |
| `CIRCUIT_BREAKER_FAILURES` | `5` | Failures before opening SearXNG circuit |
| `CIRCUIT_BREAKER_TIMEOUT` | `30.0` | Seconds before half-opening circuit |
| `RATE_LIMIT` | `30/minute` | Max requests per IP |
| `CRAWL_RATE_LIMIT` | `5/minute` | Max crawl requests per IP |
| `CRAWL_MAX_CONCURRENT` | `5` | Max concurrent scrapes per crawl |
| `CRAWL_DELAY_MS` | `500` | Per-page delay in milliseconds |

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
                   +-- browser pool: N instances, round-robin
                   +-- block images/CSS/fonts
                   +-- wait network idle
                   +-- extract rendered HTML

HTML → trafilatura → Clean Markdown
```

---

## 🧪 Example cURL Requests

```bash
# Quick research (no summary, <5s)
curl -X POST http://localhost:8000/api/v1/research \
  -H "Content-Type: application/json" \
  -d '{"query": "DSSA Anjlok", "max_results": 3}'

# Research + summary (synchronous, returns summary directly)
curl -X POST http://localhost:8000/api/v1/research \
  -H "Content-Type: application/json" \
  -d '{"query": "DSSA Anjlok", "max_results": 5, "generate_summary": true}'

# Full async research (entire pipeline in background, instant return)
curl -X POST http://localhost:8000/api/v1/research \
  -H "Content-Type: application/json" \
  -d '{"query": "DSSA Anjlok", "max_results": 5, "run_async": true}'

# Poll for async research result
curl http://localhost:8000/api/v1/research/abc-123

# Force Playwright
curl -X POST http://localhost:8000/api/v1/research \
  -H "Content-Type: application/json" \
  -d '{"query": "React 19 features", "max_results": 3, "force_js_render": true}'

# ── Crawl ────────────────────────────────────────────

# Basic crawl (depth 1, max 3 pages)
curl -X POST http://localhost:8000/api/v1/crawl \
  -H "Content-Type: application/json" \
  -d '{"url": "http://quotes.toscrape.com", "max_depth": 1, "max_pages": 3}'

# Crawl with path whitelist (only /tag/ pages)
curl -X POST http://localhost:8000/api/v1/crawl \
  -H "Content-Type: application/json" \
  -d '{"url": "http://quotes.toscrape.com", "max_depth": 2, "max_pages": 15, "include_paths": ["/tag/.*"]}'

# Async crawl (returns task_id immediately)
curl -X POST http://localhost:8000/api/v1/crawl \
  -H "Content-Type: application/json" \
  -d '{"url": "http://quotes.toscrape.com", "max_depth": 1, "max_pages": 5, "run_async": true}'

# Poll for async crawl result
curl http://localhost:8000/api/v1/crawl/{task_id}

# Crawl + AI summary
curl -X POST http://localhost:8000/api/v1/crawl \
  -H "Content-Type: application/json" \
  -d '{"url": "http://quotes.toscrape.com", "max_depth": 1, "max_pages": 5, "generate_summary": true}'
```

---

## 📝 License

MIT License — see [LICENSE](LICENSE).
