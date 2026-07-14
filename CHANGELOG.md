# Changelog

All notable changes to the CHIPER project.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.4.2] — 2026-07-14

### Security
- **SSRF via Redirect / Crawl Child URL / Sitemap (NB-1)** — SSRF was previously validated only once on the seed URL. Now every internally-derived URL is re-checked: crawl child URLs are validated before enqueueing (guards against same-domain links resolving to private IPs via DNS rebinding), post-redirect targets in discovery are re-validated (`follow_redirects=True` could land on `127.0.0.1`/`169.254.169.254`), and sitemap/sitemap-index fetches validate both the target and the redirected URL. New `is_safe_url()` helper centralizes the gate. (`security.py`, `crawler.py`, `discovery.py`, `sitemap.py`)
- **IPv6 SSRF Bypass (NB-6)** — DNS resolution in the SSRF gate was IPv4-only (`AF_INET`), so an IPv6-only host pointing at `::1` or a private ULA bypassed all checks. Resolution now uses `AF_UNSPEC` and validates every returned IPv4/IPv6 address against the blocklist (IPv6 zone-ids stripped). (`security.py`)
- **`/research` Endpoint Missing SSRF Check (NB-4)** — Unlike crawl/map/extract, the research pipeline did not validate URLs before scraping (Tier-2 Playwright follows redirects anywhere). SearXNG result URLs are now filtered through `validate_url_safe()` in both sync and async modes. (`routes.py`)
- **Non-Constant-Time API Key Comparison (NB-5)** — `ApiKeyMiddleware` now uses `secrets.compare_digest()` instead of `!=`, eliminating a timing side-channel. (`auth.py`)
- **Body-Size Limit Bypass (NB-7)** — The `SCRAPE_MAX_BODY_MB` cap was only enforced via the `Content-Length` header, so chunked/streaming responses without that header could be fully read into memory. Both `_fetch_static()` and `_fetch_and_extract_links()` now stream via `aiter_bytes()` and abort once the accumulated byte count exceeds the cap. (`scraper.py`, `discovery.py`)

### Fixed
- **Inflight Task Limit Race Condition (NB-2)** — The `max_inflight_tasks` check-then-create in `TaskStore` was not atomic (TOCTOU), allowing concurrent background tasks to exceed the limit. Wrapped in a `threading.Lock`. (`task_store.py`)
- **Background Tasks Without Timeout / Unreferenced (NB-3)** — Async-mode background workers ran the pipeline without any timeout (sync mode had one), and `asyncio.create_task(...)` results were not retained (risk of mid-flight GC). All background workers are now wrapped with `asyncio.wait_for(...)`, and a `_spawn_background()` helper keeps strong references until completion. (`routes.py`)

### Added
- `EXTRACT_TOTAL_TIMEOUT` env var — total timeout for extraction (sync + async), default `300s`. (`config.py`)
- `RESEARCH_TOTAL_TIMEOUT` env var — total timeout for the async research scraping phase, default `300s`. (`config.py`)

---

## [1.4.1] — 2026-07-02

### Security
- **SSRF Prevention** — All user-supplied URLs (`/crawl`, `/map`, `/extract`) are now validated against private/internal IP ranges before any outbound request. Blocks loopback (`127.0.0.0/8`, `::1`), private networks (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), link-local (`169.254.0.0/16`), multicast, and special-use (`0.0.0.0`, TEST-NET, CGNAT). Includes DNS resolution check for hostname-based attacks. (`security.py`, `routes.py`)
- **API Key Authentication** — New `ApiKeyMiddleware` validates `X-API-Key` header against `CHIPER_API_KEY` env var. Public endpoints (`/health`, `/metrics`) are exempt. Auth is disabled when `CHIPER_API_KEY` is empty (development mode). (`auth.py`, `main.py`, `config.py`)
- **CORS Configuration** — `CORSMiddleware` added with configurable origins via `CHIPER_CORS_ORIGINS` env var (comma-separated). Empty = CORS disabled. Logs warning if `*` wildcard is used. (`main.py`, `config.py`)
- **Error Information Disclosure** — All API error responses now use generic messages (`_ERR_INTERNAL`, `_ERR_SSRF`, `_ERR_TASK_LIMIT`) instead of raw exception strings (`str(exc)`). Internal details are preserved in server logs. (`routes.py`)
- **HTTP Response Body Size Limit** — Content-Length header check added to `_fetch_static()` (scraper) and `_fetch_and_extract_links()` (discovery). Responses exceeding `SCRAPE_MAX_BODY_MB` (default 50MB) are rejected before the body is read, preventing OOM via multi-GB responses. (`scraper.py`, `discovery.py`, `config.py`)
- **Max Concurrent Background Tasks** — `TaskStore` now tracks inflight task count. When `MAX_INFLIGHT_TASKS` > 0, `create()` raises `RuntimeError` if limit reached. Routes return HTTP 429 via `_create_task_safe()` wrapper. (`task_store.py`, `routes.py`, `config.py`)
- **Sitemap URL SSRF via robots.txt** — `find_sitemap_url()` now validates that sitemap URLs from `robots.txt` belong to the same domain AND do not resolve to internal/private IPs. (`sitemap.py`)
- **Operational Timeouts** — Sync crawl and map operations now wrapped with `asyncio.wait_for()`. Timeouts: `CRAWL_TOTAL_TIMEOUT` (default 300s), `MAP_TOTAL_TIMEOUT` (default 120s). Timeout → HTTP 504. (`routes.py`, `config.py`)
- **LLM Prompt Injection Protection** — User-supplied extraction prompts are now sanitized (XML-style tags removed) and wrapped in `<user_input>...</user_input>` delimiters. System prompt includes explicit boundary instruction to ignore instructions outside the delimiter block. (`extractor.py`)

### Changed
- **Config cleanup** — Removed unused `EXTRACT_MAX_PROMPT_LENGTH` from `config.py` (validated by Pydantic `Field(max_length=...)` instead).
- `.env` and `.env.example` updated with all new security-related env vars.

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

