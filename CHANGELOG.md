# Changelog

All notable changes to the CHIPER project.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

