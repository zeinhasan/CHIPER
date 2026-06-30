"""
URL Normalization & Internal Link Extraction

Utilities for the recursive crawl engine:
- normalize_url: canonical URL form for deduplication
- extract_internal_links: extract same-domain links from HTML via BeautifulSoup
"""

import re
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.utils.helpers import get_logger

logger = get_logger(__name__)

# Schemes that we don't want to follow during link extraction.
_SKIP_SCHEMES: frozenset[str] = frozenset(
    {"mailto", "tel", "javascript", "data", "ftp", "file"}
)


def normalize_url(url: str) -> str:
    """
    Produce a canonical, dedup-safe form of a URL.

    Normalizations applied:
    - Strips leading/trailing whitespace.
    - Adds ``https://`` if no scheme is present.
    - Lowercases the scheme and hostname (domain).
    - Strips the fragment (``#section``).
    - Strips trailing slash from the path (unless path is ``/``).

    Returns an empty string when input is falsy or whitespace-only.
    """
    if not url or not url.strip():
        return ""

    url = url.strip()

    # ── Add scheme if missing ──────────────────────────────────────
    if "://" not in url:
        url = "https://" + url

    parsed = urlparse(url)

    scheme = parsed.scheme.lower() if parsed.scheme else "https"
    netloc = parsed.netloc.lower()
    path = parsed.path or "/"

    # ── Strip fragment ─────────────────────────────────────────────
    # (already handled by setting fragment="" in urlunparse below)

    # ── Strip trailing slash (except root) ─────────────────────────
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    # ── Rebuild canonical URL ──────────────────────────────────────
    normalized = urlunparse((scheme, netloc, path, parsed.params, parsed.query, ""))

    return normalized


def _is_internal(url: str, base_url: str) -> bool:
    """
    Return True if *url* shares the same (scheme, hostname) as *base_url*.

    Handles both absolute and relative URLs (relative URLs are considered
    internal by default, but callers should resolve them first via urljoin).
    """
    try:
        target = urlparse(url)
        base = urlparse(base_url)
        # Subdomain is NOT considered internal (e.g. api.example.com != example.com).
        return target.scheme == base.scheme and target.hostname == base.hostname
    except Exception:
        return False


def _matches_filters(
    url: str,
    include_paths: list[str],
    exclude_paths: list[str],
) -> bool:
    """
    Determine whether a URL passes the include / exclude path filters.

    Rules (evaluated in order):
    1. If URL path matches any *exclude* pattern → **REJECT**.
    2. If *include_paths* is non-empty and URL path does **not** match
       any include pattern → **REJECT**.
    3. Otherwise → **ACCEPT**.

    Patterns are applied to ``urlparse(url).path`` only.
    """
    path = urlparse(url).path

    # ── Exclude check (blacklist) ──────────────────────────────────
    for pattern in exclude_paths:
        try:
            if re.search(pattern, path):
                return False
        except re.error:
            logger.warning(
                "Invalid exclude regex pattern skipped", extra={"pattern": pattern}
            )
            continue

    # ── Include check (whitelist) ──────────────────────────────────
    if include_paths:
        for pattern in include_paths:
            try:
                if re.search(pattern, path):
                    return True
            except re.error:
                logger.warning(
                    "Invalid include regex pattern skipped", extra={"pattern": pattern}
                )
                continue
        # No include pattern matched
        return False

    # ── No includes specified, and not excluded → accept ───────────
    return True


def extract_internal_links(
    html: str,
    base_url: str,
    include_paths: list[str],
    exclude_paths: list[str],
) -> list[str]:
    """
    Extract same-domain internal links from an HTML string.

    *html* is the full, raw HTML of the page.
    *base_url* is the URL of the current page (used to resolve relative links).

    Only ``<a href="...">`` tags are inspected.  Links with non-HTTP(S)
    schemes (``mailto:``, ``tel:``, ``javascript:``, ``data:``, etc.) and
    fragment-only links (``#``) are skipped.

    Returns a deduplicated, order-preserving list of normalized, filtered URLs.
    """
    if not html:
        return []

    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        logger.warning(
            "BeautifulSoup failed to parse HTML", extra={"base_url": base_url}
        )
        return []

    links: list[str] = []

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()

        # ── Skip empty, anchors, and non-navigable schemes ─────────
        if not href or href.startswith("#"):
            continue
        if ":" in href:
            scheme = href.split(":", 1)[0].lower()
            if scheme in _SKIP_SCHEMES:
                continue

        # ── Resolve relative → absolute ────────────────────────────
        try:
            full_url = urljoin(base_url, href)
        except ValueError:
            logger.debug(
                "urljoin failed for href", extra={"base_url": base_url, "href": href}
            )
            continue

        # ── Only follow same-domain links ──────────────────────────
        if not _is_internal(full_url, base_url):
            continue

        # ── Normalize and filter ───────────────────────────────────
        normalized = normalize_url(full_url)
        if not normalized:
            continue

        if _matches_filters(normalized, include_paths, exclude_paths):
            links.append(normalized)

    # ── Deduplicate while preserving order ─────────────────────────
    seen: set[str] = set()
    unique: list[str] = []
    for link in links:
        if link not in seen:
            seen.add(link)
            unique.append(link)

    logger.debug(
        "Link extraction complete",
        extra={"base_url": base_url, "total_found": len(links), "unique": len(unique)},
    )
    return unique
