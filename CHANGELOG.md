# Changelog

All notable changes to the CHIPER project.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.4.0] — 2026-07-02

### Added
- **Structured Data Extraction** — `POST /api/v1/extract` endpoint that extracts specific data fields from web pages using an OpenAI-compatible LLM (DeepSeek, GPT, Claude, etc.). Scrapes URLs via the existing Two-Tier engine, then uses AI to return structured JSON matching the user's prompt or JSON Schema. Supports two extraction modes: `combined` (default — all page content combined into one LLM call) and `per_page` (each URL extracted independently). Async background mode with polling (`GET /api/v1/extract/{task_id}`). (`extractor.py`, `routes.py`, `schemas.py`)
- `ExtractRequest` model — accepts `urls`, `prompt`, `json_schema` (optional), `extract_mode`, `force_js_render`, `run_async`. (`schemas.py`)
- `ExtractData` / `ExtractResponse` / `ExtractTaskStatusResponse` models — structured response with per-URL extraction results, success/failure counts, and async polling support. (`schemas.py`)
- `EXTRACT_MAX_URLS` env var — max URLs per extraction request (default `20`). (`config.py`)
- `EXTRACT_MAX_PROMPT_LENGTH` env var — max prompt characters (default `2000`). (`config.py`)
- `EXTRACT_MAX_CONTENT_CHARS` env var — max content chars per URL sent to LLM (default `8000`). (`config.py`)
- `EXTRACT_TEMPERATURE` env var — LLM temperature for extraction, lower = more deterministic (default `0.1`). (`config.py`)
- `EXTRACT_RETRIES` env var — retry count if LLM returns invalid JSON in schema mode (default `1`). (`config.py`)
- `chiper_extract_duration_seconds` Prometheus histogram — tracks extraction latency. (`metrics.py`)
- `chiper_extract_total` Prometheus counter — tracks extraction attempts with labels: status (`success`/`partial`/`error`), mode (`combined`/`per_page`), has_schema. (`metrics.py`)
- `jsonschema` dependency — validates LLM output against user-provided JSON Schema. (`requirements.txt`)

### Changed
- Arch diagram in README now includes Extract pipeline subgraph (scrape → combined/per_page → LLM → schema validation). (`README.md`)
- `.env.example` updated with extraction config vars (commented out with defaults). (`.env.example`)

---

## [1.3.0] — 2026-06-30

### Added
- **Site Map Discovery** — `POST /api/v1/map` endpoint that discovers all URLs on a website without scraping content. Supports three modes: `sitemap` (parse sitemap.xml + sitemap index + robots.txt), `crawl` (lightweight BFS link-follower — no Playwright, no content extraction), and `hybrid` (sitemap first, then crawl to fill gaps). Includes path filtering (include/exclude regex), subdomain inclusion toggle, query param stripping, configurable depth/URL limits, async background mode with polling (`GET /api/v1/map/{task_id}`). (`discovery.py`, `sitemap.py`, `routes.py`, `schemas.py`)
- `MAP_MAX_CONCURRENT` env var — max concurrent requests during map discovery (default `10`). (`config.py`)
- `MAP_DELAY_MS` env var — per-page delay during map discovery (default `200`). (`config.py`)

### Changed
- `_is_internal()` in `links.py` extended with optional `include_subdomains` parameter — when True, subdomains like `blog.example.com` are considered internal to `example.com`. Backward compatible (defaults to `False`). (`links.py`)
- `normalize_url()` in `links.py` extended with optional `strip_query` parameter — when True, query parameters are stripped before building the canonical URL. Backward compatible (defaults to `False`). (`links.py`)
- Arch diagram in README now includes Map Discovery pipeline subgraph. (`README.md`)

---

## [1.2.0] — 2026-06-30

### Added
- **Web Crawl (Recursive)** — `POST /api/v1/crawl` endpoint that performs BFS multi-page crawl from a seed URL, following same-domain internal links and extracting Markdown from every page. Supports configurable depth, page limits, path filtering (include/exclude regex), async background mode with polling (`GET /api/v1/crawl/{task_id}`), and optional AI summarization of all crawled content. Built on top of the existing Two-Tier scraping engine. (`crawler.py`, `links.py`, `routes.py`, `schemas.py`)
- `CRAWL_RATE_LIMIT` env var — separate rate limit for crawl endpoint (default `5/minute`). (`config.py`)
- `CRAWL_MAX_CONCURRENT` env var — max concurrent scrapes per crawl (default `5`). (`config.py`)
- `CRAWL_DELAY_MS` env var — per-page delay for rate limiting (default `500`). (`config.py`)
- `beautifulsoup4` dependency — HTML parsing for internal link extraction. (`requirements.txt`)

### Changed
- `CrawlPage` model extends `ScrapeResult` — avoids field duplication. (`schemas.py`)
- Crawl endpoint uses separate rate limit (`crawl_rate_limit`) from research endpoint. (`routes.py`)

---

## [1.1.2] — 2026-06-30

### Added
- `SEARXNG_CATEGORIES` env var — configurable SearXNG search categories (default `"web,news"`). Allows narrowing search scope to text-only content, excluding videos/images/files. (`config.py`, `searxng.py`)
- Domain blocklist filtering — automatically skips video/media domains (YouTube, Vimeo, TikTok, Instagram, Twitch, etc.) before scraping, preventing wasted resources on non-scrapable URLs. (`routes.py`)

### Changed
- SearXNG search categories narrowed from `"general"` (all categories including videos) to `"web,news"` by default, preventing YouTube/video results from entering the research pipeline. (`searxng.py`)

### Fixed
- YouTube results appearing in search results for non-video queries (e.g., "Macbook neo tokopedia") — caused by SearXNG `"general"` category including video engines.

### Security
- Disabled video engines (youtube, vimeo, dailymotion, rumble, odysee, bilibili) and image engines (google images, duckduckgo images, bing images) in SearXNG config to prevent unnecessary engine usage. (`searxng/settings.yml`)

---

## [1.1.1] — 2026-06-30

### Added
- `run_async` payload field — run the entire research pipeline (search + scrape + summary) as a background task. Returns `task_id` immediately; poll `GET /api/v1/research/{task_id}` for the complete result.
- `TaskStatusResponse` now includes `results`, `total_results`, and `query` fields for full-research polling.
- `TaskStore` now stores the original query alongside each task entry.

### Changed
- Summarization now runs **synchronously** when `run_async: false` — the AI summary is returned directly in the response, no polling needed.
- `GET /api/v1/research/{task_id}` endpoint simplified to only handle full-research background tasks (from `run_async: true`).
- Architecture diagram updated in README to reflect sync/async dual-mode.

### Removed
- `_run_summarization()` function — replaced by direct `await summarizer.summarize()` in sync mode.

### Fixed
- `RecursionError` in `markdownify` HTML-to-Markdown conversion — added try/except fallback to `trafilatura` output for problematic pages. (`scraper.py`)
- AI summarization failures due to large documents — added per-document content truncation (`MAX_DOC_CHARS = 4000`) to prevent model token limit errors. (`summarizer.py`)

