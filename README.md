# CHIPER — H.A.K.I.

**C**ontent **H**arvesting, **I**ntegration, **P**arsing, and **E**xtraction **R**outine

> *Headless Asynchronous Knowledge Integrator — v2.5 Deep Fetch + AI Summary Edition*

A Python/FastAPI middleware API that bridges AI agents with SearXNG for automated web research. Extracts clean Markdown content from both static and JavaScript-heavy pages using a Two-Tier strategy, with optional AI summarization via DeepSeek. Comes with full observability: structured JSON logging, request tracing (X-Request-ID), and Prometheus metrics.

---

## 🏗️ Architecture

```
POST /api/v1/research
         │
    ┌────┴─────────────────────────────┐
    │  Tracing: X-Request-ID           │  ← Unique trace ID per request
    └────┬─────────────────────────────┘
         │
         ▼
   ┌──────────┐
   │ SearXNG  │  ← Self-hosted search engine (Docker)
   └────┬─────┘
        │ list of result URLs
        ▼
   ┌──────────────────────────────────────┐
   │         Two-Tier Scraping            │
   │                                      │
   │  Tier 1: httpx → trafilatura         │  ← Static HTML (fast)
   │      │                               │
   │      └── content < 200 chars? ─────┐ │
   │                                    │ │
   │  Tier 2: Playwright → trafilatura  │ │  ← Dynamic JS (fallback)
   │                                    │ │
   └────────────────────────────────────┘ │
         │                                │
         ▼                                │
   ┌──────────┐                           │
   │ Markdown │  ← Clean output           │
   └────┬─────┘                           │
        │                                 │
        ▼                                 │
   ┌──────────┐  (optional)               │
   │ DeepSeek │  ← AI Summary             │
   └────┬─────┘                           │
        │
        ▼
   ┌──────────────────────────────────────┐
   │         Observability                │
   │                                      │
   │  JSON Logs  +  X-Request-ID          │  ← All logs structured
   │  /metrics   +  Prometheus            │  ← Grafana-ready metrics
   └──────────────────────────────────────┘
```

---

## 🛠️ Tech Stack

| Component | Technology |
|-----------|------------|
| **Framework** | FastAPI + Uvicorn |
| **Search Engine** | SearXNG (self-hosted, JSON API) |
| **Static Fetch** | `httpx` (async) |
| **Dynamic Render** | `playwright` (headless Chromium) |
| **Content Extractor** | `trafilatura` + `markdownify` |
| **AI Summarization** | `openai` SDK → OpenAI-compatible API → DeepSeek |
| **Structured Logging** | JSON logs with trace context (`python-json-logger`) |
| **Metrics** | Prometheus (`prometheus-client` + `prometheus-fastapi-instrumentator`) |
| **Deployment** | Docker + Docker Compose |

---

## 📁 Project Structure

```
CHIPER/
├── app/
│   ├── main.py                  # FastAPI entry point + Playwright lifespan
│   ├── config.py                # Environment-based settings
│   ├── middleware/
│   │   └── tracing.py           # X-Request-ID middleware
│   ├── models/
│   │   └── schemas.py           # Pydantic request/response validation
│   ├── api/
│   │   └── routes.py            # POST /api/v1/research endpoint
│   ├── services/
│   │   ├── searxng.py           # SearXNG JSON API integration
│   │   ├── scraper.py           # Two-Tier scraping engine
│   │   └── summarizer.py        # AI summarization (DeepSeek)
│   └── utils/
│       ├── helpers.py           # Re-export logging utilities
│       ├── logging.py           # Structured JSON logging + trace context
│       └── metrics.py           # Prometheus metrics definitions
├── searxng/
│   └── settings.yml             # SearXNG configuration
├── Dockerfile                   # CHIPER image build
├── docker-compose.yml           # All-in-one: SearXNG + CHIPER
├── requirements.txt             # Python dependencies
├── IMPROVEMENT.md               # Improvement roadmap
├── .env.example                 # Environment variables template
└── .env                         # Environment variables (private)
```

---

## 🚀 Quick Start (Docker — Recommended)

```bash
# 1. Clone repository
git clone <repo-url> && cd CHIPER

# 2. Create .env from template and fill in your API key
cp .env.example .env
# Edit .env → set CMD_API_KEY=... and CMD_BASE_URL=...

# 3. Run all services (SearXNG + CHIPER)
docker compose up -d --build
```

Once running:
- **CHIPER API**: http://localhost:8000
- **SearXNG**: http://localhost:8081
- **Health Check**: http://localhost:8000/health
- **Swagger Docs**: http://localhost:8000/docs
- **Prometheus Metrics**: http://localhost:8000/metrics

---

## 🖥️ Quick Start (Manual / Development)

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Install Playwright browser
playwright install chromium

# 3. Run SearXNG via Docker
docker run -d --name searxng -p 8081:8080 \
  -v ./searxng:/etc/searxng \
  -e SEARXNG_BASE_URL=http://localhost:8081/ \
  searxng/searxng:latest

# 4. Setup environment
cp .env.example .env
# Edit .env → set CMD_API_KEY=... and CMD_BASE_URL=...

# 5. Run the server
python -m app.main
```

---

## 📡 API Reference

### `POST /api/v1/research`

Executes the full research pipeline: SearXNG → Scraping → (optional) AI Summary.

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
| `force_js_render` | bool | `false` | Skip Tier-1 (httpx), go straight to Playwright |
| `generate_summary` | bool | `false` | Generate AI summary via DeepSeek |

#### Response (`generate_summary: true`)

```json
{
  "query": "Latest financial reports from tech companies",
  "ai_summary": "Based on the three extracted sources, the majority of tech companies reported...",
  "results": [
    {
      "url": "https://example.com/report",
      "title": "Q1 2025 Financial Report",
      "fetch_method": "playwright",
      "markdown_content": "# Quarterly Report...\n\nData shows...",
      "content_length": 1542
    }
  ],
  "total_results": 3
}
```

| Field | Description |
|-------|-------------|
| `query` | Original search query |
| `ai_summary` | AI-generated summary (null if `generate_summary: false`) |
| `results[].url` | Scraped URL |
| `results[].title` | Title from SearXNG result |
| `results[].fetch_method` | `httpx` or `playwright` |
| `results[].markdown_content` | Clean extracted Markdown content |
| `total_results` | Total number of scraped results |

### `GET /health`

```json
{ "status": "ok", "version": "2.5.0" }
```

### `GET /metrics`

Exposes Prometheus metrics (OpenMetrics format), including:

| Metric | Description |
|--------|-------------|
| `chiper_http_requests_total` | Total HTTP requests (by method, status) |
| `chiper_searxng_duration_seconds` | SearXNG API call latency |
| `chiper_searxng_requests_total` | Total SearXNG requests (success/error) |
| `chiper_searxng_results_total` | Total search results returned |
| `chiper_scrape_duration_seconds` | Scraping latency per URL (by fetch_method) |
| `chiper_scrape_total` | Total scrape attempts (by fetch_method, status) |
| `chiper_tier_fallback_total` | Total Tier-1 → Tier-2 fallbacks |
| `chiper_summarize_duration_seconds` | AI summarization latency |
| `chiper_summarize_total` | Total summarization attempts (success/error) |

---

## ⚙️ Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SEARXNG_BASE_URL` | `http://localhost:8080` | SearXNG instance URL |
| `CMD_API_KEY` | — | API key for AI summarization *(required)* |
| `CMD_BASE_URL` | `http://localhost:20128/v1` | OpenAI-compatible API base URL |
| `CMD_MODEL` | `cmc/deepseek/deepseek-v4-pro` | Model name for summarization |
| `PLAYWRIGHT_BROWSER_PATH` | *(auto)* | Custom Chromium binary path |
| `HOST` | `0.0.0.0` | Server host binding |
| `PORT` | `8000` | Server port |
| `LOG_FORMAT` | `json` | Log format: `json` (structured) or `console` (human-readable) |
| `LOG_LEVEL` | `INFO` | Log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `MIN_CONTENT_LENGTH` | `200` | Minimum characters for Tier-1 to be considered sufficient |

---

## 🔍 Observability

### Structured JSON Logging

Every log entry is machine-parseable JSON, ready for Loki / ELK / Datadog:

```json
{
  "timestamp": "2026-06-29T12:30:00.123456+00:00",
  "level": "INFO",
  "logger": "app.services.scraper",
  "message": "Tier-1 (httpx) SUCCESS",
  "module": "scraper",
  "function": "fetch_and_extract",
  "line": 142,
  "trace_id": "a1b2c3d4-5678-90ab-cdef-1234567890ab",
  "url": "https://example.com/page",
  "content_length": 1542,
  "duration_ms": 320
}
```

Switch to human-readable format:
```bash
# in .env
LOG_FORMAT=console
```

### Request Tracing

Every request receives a unique `X-Request-ID` (auto-generated or passed from upstream). This ID appears in:
- All log entries during that request
- The response header `X-Request-ID`

```bash
curl -v http://localhost:8000/health
# < X-Request-ID: a1b2c3d4-5678-90ab-cdef-1234567890ab
```

### Prometheus Metrics

The `/metrics` endpoint is Prometheus-scrape ready. **Prometheus is not required** — the endpoint works standalone and can be viewed directly in a browser.

**Optional:** Set up Prometheus + Grafana for visual dashboards:
```yaml
# prometheus.yml
scrape_configs:
  - job_name: "chiper"
    scrape_interval: 15s
    static_configs:
      - targets: ["chiper:8000"]
```

---

## 🔄 Two-Tier Scraping Flow

```
URL received
    │
    ▼
force_js_render = false?
    │
    ├── YES ──► Tier 1: httpx (static fetch)
    │              │
    │              ├── HTML obtained & content >= 200 chars ──► DONE
    │              │
    │              └── FAILED / content < 200 chars ──► Continue to Tier 2
    │
    └── NO ────► Tier 2: Playwright Chromium (headless)
                       │
                       ├── Open page, wait for network idle
                       ├── Block images/CSS/fonts (save bandwidth)
                       └── Extract full rendered HTML

HTML from Tier 1 or Tier 2
    │
    ▼
trafilatura → extract main content, strip boilerplate
    │
    ▼
Clean Markdown
```

Concurrency is managed via `asyncio.gather` + `Semaphore` (max 3 simultaneous Playwright contexts).

---

## 🧪 Example cURL Requests

```bash
# Research without AI summary
curl -X POST http://localhost:8000/api/v1/research \
  -H "Content-Type: application/json" \
  -d '{"query": "Python FastAPI best practices", "max_results": 3}'

# Research + AI summary
curl -X POST http://localhost:8000/api/v1/research \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Latest financial reports from tech companies",
    "max_results": 5,
    "generate_summary": true
  }'

# Force Playwright for all URLs (skip Tier-1)
curl -X POST http://localhost:8000/api/v1/research \
  -H "Content-Type: application/json" \
  -d '{
    "query": "React 19 new features",
    "max_results": 3,
    "force_js_render": true
  }'

# View Prometheus metrics
curl http://localhost:8000/metrics

# Check trace ID in response headers
curl -v http://localhost:8000/health
```

---

## 📝 License

MIT License — see [LICENSE](LICENSE).
