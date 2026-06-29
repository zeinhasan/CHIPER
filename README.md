# CHIPER — H.A.K.I.

**C**ontent **H**arvesting, **I**ntegration, **P**arsing, and **E**xtraction **R**outine

> *Headless Asynchronous Knowledge Integrator — v2.5 Deep Fetch + AI Summary Edition*

Middleware API berbasis Python/FastAPI yang menjembatani AI Agent dengan SearXNG untuk riset web otomatis. Mengekstrak konten dari halaman statis maupun dinamis (JavaScript-heavy), lalu secara opsional merangkum semua hasil menggunakan DeepSeek AI. Dilengkapi **observability** penuh: structured JSON logging, request tracing (X-Request-ID), dan Prometheus metrics.

---

## 🏗️ Arsitektur

```
POST /api/v1/research
         │
    ┌────┴─────────────────────────────┐
    │  Tracing: X-Request-ID           │  ← Setiap request punya trace ID
    └────┬─────────────────────────────┘
         │
         ▼
   ┌──────────┐
   │ SearXNG  │  ← Self-hosted search engine (Docker)
   └────┬─────┘
        │ daftar URL hasil pencarian
        ▼
   ┌──────────────────────────────────────┐
   │         Two-Tier Scraping            │
   │                                      │
   │  Tier 1: httpx → trafilatura         │  ← Static HTML (cepat)
   │      │                               │
   │      └── konten < 200 chars? ──────┐ │
   │                                    │ │
   │  Tier 2: Playwright → trafilatura  │ │  ← Dynamic JS (fallback)
   │                                    │ │
   └────────────────────────────────────┘ │
         │                                │
         ▼                                │
   ┌──────────┐                           │
   │ Markdown │  ← Hasil bersih           │
   └────┬─────┘                           │
        │                                 │
        ▼                                 │
   ┌──────────┐  (opsional)               │
   │ DeepSeek │  ← AI Summary             │
   └────┬─────┘                           │
        │
        ▼
   ┌──────────────────────────────────────┐
   │         Observability                │
   │                                      │
   │  JSON Logs  +  X-Request-ID          │  ← Semua log terstruktur
   │  /metrics   +  Prometheus            │  ← Metrik siap Grafana
   └──────────────────────────────────────┘
```

---

## 🛠️ Tech Stack

| Komponen | Teknologi |
|----------|-----------|
| **Framework** | FastAPI + Uvicorn |
| **Search Engine** | SearXNG (self-hosted, JSON API) |
| **Static Fetch** | `httpx` (async) |
| **Dynamic Render** | `playwright` (headless Chromium) |
| **Content Extractor** | `trafilatura` + `markdownify` |
| **AI Summarization** | `openai` SDK → OpenAI-compatible API → DeepSeek |
| **Structured Logging** | JSON logs dengan trace context (`python-json-logger`) |
| **Metrics** | Prometheus (`prometheus-client` + `prometheus-fastapi-instrumentator`) |
| **Deployment** | Docker + Docker Compose |

---

## 📁 Struktur Project

```
CHIPER/
├── app/
│   ├── main.py                  # FastAPI entry point + Playwright lifespan
│   ├── config.py                # Settings dari environment variables
│   ├── middleware/
│   │   └── tracing.py           # X-Request-ID middleware
│   ├── models/
│   │   └── schemas.py           # Pydantic request/response validation
│   ├── api/
│   │   └── routes.py            # POST /api/v1/research endpoint
│   ├── services/
│   │   ├── searxng.py           # Integrasi SearXNG JSON API
│   │   ├── scraper.py           # Two-Tier scraping engine
│   │   └── summarizer.py        # AI summarization (DeepSeek)
│   └── utils/
│       ├── helpers.py           # Re-export logging utilities
│       ├── logging.py           # Structured JSON logging + trace context
│       └── metrics.py           # Prometheus metrics definitions
├── searxng/
│   └── settings.yml             # Konfigurasi SearXNG
├── Dockerfile                   # Build image CHIPER
├── docker-compose.yml           # All-in-one: SearXNG + CHIPER
├── requirements.txt             # Python dependencies
├── IMPROVEMENT.md               # Roadmap ide pengembangan
├── .env.example                 # Template environment variables
└── .env                         # Environment variables (private)
```

---

## 🚀 Quick Start (Docker — Direkomendasikan)

```bash
# 1. Clone repository
git clone <repo-url> && cd CHIPER

# 2. Buat .env dari template & isi API key
cp .env.example .env
# Edit .env → isi CMD_API_KEY=... dan CMD_BASE_URL=...

# 3. Jalankan semua service (SearXNG + CHIPER)
docker compose up -d --build
```

Setelah selesai:
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

# 3. Jalankan SearXNG via Docker
docker run -d --name searxng -p 8081:8080 \
  -v ./searxng:/etc/searxng \
  -e SEARXNG_BASE_URL=http://localhost:8081/ \
  searxng/searxng:latest

# 4. Setup environment
cp .env.example .env
# Edit .env → isi CMD_API_KEY=... dan CMD_BASE_URL=...

# 5. Jalankan server
python -m app.main
```

---

## 📡 API Reference

### `POST /api/v1/research`

Menjalankan pipeline riset penuh: SearXNG → Scraping → (opsional) AI Summary.

#### Request

```json
{
  "query": "Laporan keuangan terbaru perusahaan teknologi",
  "max_results": 5,
  "force_js_render": false,
  "generate_summary": true
}
```

| Field | Type | Default | Deskripsi |
|-------|------|---------|-----------|
| `query` | string | — | Kata kunci pencarian *(wajib)* |
| `max_results` | int | `5` | Maksimum URL yang di-scrape (1-20) |
| `force_js_render` | bool | `false` | Skip Tier-1 (httpx), langsung Playwright |
| `generate_summary` | bool | `false` | Generate AI summary via DeepSeek |

#### Response (`generate_summary: true`)

```json
{
  "query": "Laporan keuangan terbaru perusahaan teknologi",
  "ai_summary": "Berdasarkan ketiga sumber yang diekstrak, mayoritas...",
  "results": [
    {
      "url": "https://example.com/laporan",
      "title": "Laporan Keuangan Q1 2025",
      "fetch_method": "playwright",
      "markdown_content": "# Laporan Kuartal...\n\nData menunjukkan...",
      "content_length": 1542
    }
  ],
  "total_results": 3
}
```

| Field | Deskripsi |
|-------|-----------|
| `query` | Query pencarian original |
| `ai_summary` | Ringkasan AI (null jika `generate_summary: false`) |
| `results[].url` | URL hasil scraping |
| `results[].title` | Judul dari hasil SearXNG |
| `results[].fetch_method` | `httpx` atau `playwright` |
| `results[].markdown_content` | Konten bersih dalam format Markdown |
| `total_results` | Jumlah total hasil scraping |

### `GET /health`

```json
{ "status": "ok", "version": "2.5.0" }
```

### `GET /metrics`

Mengeluarkan metrik Prometheus (format OpenMetrics), meliputi:

| Metrik | Deskripsi |
|--------|-----------|
| `chiper_http_requests_total` | Total HTTP requests (method, status) |
| `chiper_searxng_duration_seconds` | Latency SearXNG API call |
| `chiper_searxng_requests_total` | Total SearXNG requests (success/error) |
| `chiper_searxng_results_total` | Total hasil pencarian |
| `chiper_scrape_duration_seconds` | Latency scraping per URL (per fetch_method) |
| `chiper_scrape_total` | Total scraping attempt (per fetch_method, status) |
| `chiper_tier_fallback_total` | Total fallback Tier-1 → Tier-2 |
| `chiper_summarize_duration_seconds` | Latency AI summarization |
| `chiper_summarize_total` | Total summarization attempt (success/error) |

---

## ⚙️ Environment Variables

| Variable | Default | Deskripsi |
|----------|---------|-----------|
| `SEARXNG_BASE_URL` | `http://localhost:8080` | URL SearXNG instance |
| `CMD_API_KEY` | — | API key untuk AI summarization *(wajib)* |
| `CMD_BASE_URL` | `http://localhost:20128/v1` | Base URL OpenAI-compatible API |
| `CMD_MODEL` | `cmc/deepseek/deepseek-v4-pro` | Nama model untuk summarization |
| `PLAYWRIGHT_BROWSER_PATH` | *(auto)* | Path ke binary Chromium kustom |
| `HOST` | `0.0.0.0` | Host binding server |
| `PORT` | `8000` | Port server |
| `LOG_FORMAT` | `json` | Format log: `json` (structured) atau `console` (human-readable) |
| `LOG_LEVEL` | `INFO` | Level log: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `MIN_CONTENT_LENGTH` | `200` | Minimal karakter agar Tier-1 dianggap cukup |

---

## 🔍 Observability

### Structured JSON Logging

Setiap log entry dalam format JSON, siap di-parse oleh Loki / ELK / Datadog:

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

Ubah ke format human-readable:
```bash
# di .env
LOG_FORMAT=console
```

### Request Tracing

Setiap request mendapat `X-Request-ID` unik (auto-generated atau dari header incoming). ID ini muncul di:
- Semua log entry selama request tersebut
- Response header `X-Request-ID`

```bash
curl -v http://localhost:8000/health
# < X-Request-ID: a1b2c3d4-5678-90ab-cdef-1234567890ab
```

### Prometheus Metrics

Endpoint `/metrics` siap di-scrape Prometheus. **Prometheus tidak wajib diinstall** — endpoint tetap berfungsi dan bisa diakses langsung via browser.

**Opsional:** Setup Prometheus + Grafana untuk dashboard visual:
```yaml
# prometheus.yml
scrape_configs:
  - job_name: "chiper"
    scrape_interval: 15s
    static_configs:
      - targets: ["chiper:8000"]
```

---

## 🔄 Flow Two-Tier Scraping

```
URL diberikan
    │
    ▼
force_js_render = false?
    │
    ├── YA ──► Tier 1: httpx (static fetch)
    │              │
    │              ├── HTML didapat & konten >= 200 chars ──► SELESAI
    │              │
    │              └── GAGAL / konten < 200 chars ──► Lanjut Tier 2
    │
    └── TIDAK ──► Tier 2: Playwright Chromium (headless)
                       │
                       ├── Buka halaman, tunggu network idle
                       ├── Block gambar/CSS/font (hemat bandwidth)
                       └── Ambil HTML penuh

HTML hasil Tier 1 atau Tier 2
    │
    ▼
trafilatura → ekstrak konten utama, buang boilerplate
    │
    ▼
Markdown bersih
```

Konkurensi diatur dengan `asyncio.gather` + `Semaphore` (max 3 Playwright context sekaligus).

---

## 🧪 Contoh cURL

```bash
# Research tanpa AI summary
curl -X POST http://localhost:8000/api/v1/research \
  -H "Content-Type: application/json" \
  -d '{"query": "Python FastAPI best practices", "max_results": 3}'

# Research + AI summary
curl -X POST http://localhost:8000/api/v1/research \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Laporan keuangan terbaru perusahaan teknologi",
    "max_results": 5,
    "generate_summary": true
  }'

# Paksa semua URL pakai Playwright (skip Tier-1)
curl -X POST http://localhost:8000/api/v1/research \
  -H "Content-Type: application/json" \
  -d '{
    "query": "React 19 new features",
    "max_results": 3,
    "force_js_render": true
  }'

# Lihat metrik Prometheus
curl http://localhost:8000/metrics

# Lihat trace ID di response header
curl -v http://localhost:8000/health
```

---

## 📝 License

MIT License — lihat file [LICENSE](LICENSE).
