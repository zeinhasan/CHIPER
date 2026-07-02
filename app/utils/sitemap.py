"""
Sitemap XML Parser

Parses sitemap.xml (and sitemap index files) to discover all listed URLs.
Supports:
- Standard sitemap (<urlset> with <url><loc> entries)
- Sitemap index (<sitemapindex> with <sitemap><loc> references)
- Gzip-compressed sitemaps
- robots.txt Sitemap: directive fallback
- XML namespace handling
"""

import asyncio
import gzip
import io
import re
import time
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse

import httpx

from app.config import settings
from app.utils.helpers import get_logger
from app.utils.links import _is_internal, _matches_filters, normalize_url
from app.utils.security import is_private_ip

logger = get_logger(__name__)

# XML namespace for standard sitemaps.
_SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"

# Max sitemap size in bytes before we abort (50 MB hard limit).
_MAX_SITEMAP_SIZE = 50 * 1024 * 1024

# Warning threshold — log a warning but still attempt to parse (10 MB).
_SITEMAP_SIZE_WARNING = 10 * 1024 * 1024

# Max recursion depth for sitemap index traversal.
_MAX_SITEMAP_INDEX_DEPTH = 3

# Regex to extract "Sitemap:" directive from robots.txt.
_SITEMAP_LINE_RE = re.compile(r"^\s*Sitemap:\s*(\S+)", re.IGNORECASE | re.MULTILINE)


async def find_sitemap_url(client: httpx.AsyncClient, base_url: str) -> str | None:
    """
    Auto-discover the sitemap URL for a site.

    Tries in order:
    1. GET <base_url>/sitemap.xml
    2. GET <base_url>/robots.txt → extract ``Sitemap: <url>`` directive

    Returns the sitemap URL on success, or None if not found.
    """
    parsed_base = urlparse(base_url)
    sitemap_candidate = f"{parsed_base.scheme}://{parsed_base.netloc}/sitemap.xml"

    # ── Try 1: /sitemap.xml ─────────────────────────────────────────
    try:
        resp = await client.get(sitemap_candidate, follow_redirects=True)
        if resp.status_code == 200 and "xml" in (resp.headers.get("content-type", "")):
            logger.info(
                "Sitemap found at /sitemap.xml", extra={"url": sitemap_candidate}
            )
            return sitemap_candidate
        elif resp.status_code == 200:
            # Could be sitemap with wrong content-type — peek at body
            text = (resp.text or "").strip()
            if (
                text.startswith("<?xml")
                or text.startswith("<urlset")
                or text.startswith("<sitemapindex")
            ):
                logger.info(
                    "Sitemap found at /sitemap.xml (content sniffed)",
                    extra={"url": sitemap_candidate},
                )
                return sitemap_candidate
    except Exception:
        logger.debug(
            "GET /sitemap.xml failed, falling back to robots.txt",
            extra={"url": sitemap_candidate},
        )

    # ── Try 2: robots.txt ───────────────────────────────────────────
    robots_url = f"{parsed_base.scheme}://{parsed_base.netloc}/robots.txt"
    try:
        resp = await client.get(robots_url, follow_redirects=True)
        if resp.status_code == 200:
            text = resp.text or ""
            match = _SITEMAP_LINE_RE.search(text)
            if match:
                sitemap_url = match.group(1).strip()
                # ── Validate: sitemap URL must be on the same domain ─
                if not _is_internal(sitemap_url, base_url, include_subdomains=False):
                    logger.warning(
                        "Sitemap URL from robots.txt points to external domain — ignored",
                        extra={"sitemap_url": sitemap_url, "base_url": base_url},
                    )
                    return None
                # ── Also validate: must not point to internal IP (SSRF) ─
                sitemap_host = urlparse(sitemap_url).hostname or ""
                if is_private_ip(sitemap_host):
                    logger.warning(
                        "Sitemap URL from robots.txt points to internal IP — blocked",
                        extra={"sitemap_url": sitemap_url},
                    )
                    return None
                logger.info(
                    "Sitemap found via robots.txt",
                    extra={"robots_url": robots_url, "sitemap_url": sitemap_url},
                )
                return sitemap_url
    except Exception:
        logger.debug("GET robots.txt failed", extra={"url": robots_url})

    logger.info("No sitemap found for site", extra={"base_url": base_url})
    return None


def _decompress_body(content: bytes, content_encoding: str | None) -> str:
    """
    Decompress response body if gzip-encoded, otherwise decode as UTF-8.
    """
    if content_encoding and "gzip" in content_encoding.lower():
        try:
            content = gzip.decompress(content)
        except Exception:
            logger.warning("Failed to decompress gzip sitemap body")
            return ""
    try:
        return content.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _strip_ns(tag: str) -> str:
    """Strip XML namespace from tag, e.g. '{http://...}url' → 'url'."""
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _parse_urlset(
    xml_text: str,
    include_paths: list[str],
    exclude_paths: list[str],
    include_subdomains: bool,
    ignore_query_params: bool,
    base_url: str,
    source_depth: int = 0,
) -> list[dict]:
    """
    Parse a <urlset> XML and return discovered URLs.

    Each entry is a dict with keys: url, path, depth, source, last_modified.
    """
    results: list[dict] = []

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.warning("Failed to parse sitemap XML", extra={"error": str(exc)})
        return results

    # Check if it's actually an index, not a urlset
    root_tag = _strip_ns(root.tag)
    if root_tag == "sitemapindex":
        logger.warning(
            "_parse_urlset called with sitemapindex — use _parse_sitemap_index instead"
        )
        return results

    for url_elem in root:
        if _strip_ns(url_elem.tag) != "url":
            continue

        loc_elem = url_elem.find(f"{{{_SITEMAP_NS}}}loc")
        if loc_elem is None:
            loc_elem = url_elem.find("loc")  # try without namespace
        if loc_elem is None or not (loc_elem.text or "").strip():
            continue

        raw_url = loc_elem.text.strip()

        # ── Resolve relative URLs ───────────────────────────────────
        full_url = urljoin(base_url, raw_url)

        # ── Parse path ──────────────────────────────────────────────
        parsed = urlparse(full_url)
        path = parsed.path or "/"

        # ── Apply path filters ──────────────────────────────────────
        if not _matches_filters(full_url, include_paths, exclude_paths):
            continue

        # ── Subdomain check ─────────────────────────────────────────
        if not _is_internal(full_url, base_url, include_subdomains=include_subdomains):
            continue

        # ── Normalize & dedup ───────────────────────────────────────
        normalized = normalize_url(full_url, strip_query=ignore_query_params)
        if not normalized:
            continue

        # ── Extract <lastmod> ───────────────────────────────────────
        last_modified: str | None = None
        lm_elem = url_elem.find(f"{{{_SITEMAP_NS}}}lastmod")
        if lm_elem is None:
            lm_elem = url_elem.find("lastmod")
        if lm_elem is not None and lm_elem.text:
            last_modified = lm_elem.text.strip()

        results.append(
            {
                "url": normalized,
                "path": path,
                "depth": source_depth,
                "source": "sitemap",
                "last_modified": last_modified,
            }
        )

    return results


async def _parse_sitemap_index(
    client: httpx.AsyncClient,
    index_url: str,
    include_paths: list[str],
    exclude_paths: list[str],
    include_subdomains: bool,
    ignore_query_params: bool,
    base_url: str,
    visited: set[str] | None = None,
    depth: int = 0,
) -> list[dict]:
    """
    Recursively follow a sitemap index via <sitemap><loc> references.
    """
    if visited is None:
        visited = set()

    norm_index = normalize_url(index_url)
    if norm_index in visited:
        return []
    visited.add(norm_index)

    if depth >= _MAX_SITEMAP_INDEX_DEPTH:
        logger.warning(
            "Sitemap index depth limit reached",
            extra={"index_url": index_url, "depth": depth},
        )
        return []

    # ── Fetch index ─────────────────────────────────────────────────
    try:
        resp = await client.get(index_url, follow_redirects=True)
        if resp.status_code != 200:
            logger.warning(
                "Sitemap index fetch failed",
                extra={"index_url": index_url, "status": resp.status_code},
            )
            return []
    except Exception as exc:
        logger.warning(
            "Sitemap index fetch error",
            extra={"index_url": index_url, "error": str(exc)},
        )
        return []

    content_length = len(resp.content)
    if content_length > _MAX_SITEMAP_SIZE:
        logger.warning(
            "Sitemap index too large — aborting",
            extra={"index_url": index_url, "size_bytes": content_length},
        )
        return []
    elif content_length > _SITEMAP_SIZE_WARNING:
        logger.warning(
            "Sitemap index exceeds warning threshold — attempting to parse",
            extra={"index_url": index_url, "size_bytes": content_length},
        )

    xml_text = _decompress_body(resp.content, resp.headers.get("content-encoding"))

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.warning(
            "Failed to parse sitemap index XML",
            extra={"index_url": index_url, "error": str(exc)},
        )
        return []

    all_urls: list[dict] = []
    root_tag = _strip_ns(root.tag)

    for child in root:
        child_tag = _strip_ns(child.tag)

        if child_tag == "sitemap" and root_tag == "sitemapindex":
            # ── Follow child sitemap ────────────────────────────────
            loc_elem = child.find(f"{{{_SITEMAP_NS}}}loc")
            if loc_elem is None:
                loc_elem = child.find("loc")
            if loc_elem is None or not (loc_elem.text or "").strip():
                continue

            child_url = loc_elem.text.strip()
            # Resolve relative
            child_full = urljoin(index_url, child_url)

            child_urls = await _parse_sitemap_index(
                client,
                child_full,
                include_paths,
                exclude_paths,
                include_subdomains,
                ignore_query_params,
                base_url,
                visited,
                depth + 1,
            )
            all_urls.extend(child_urls)

        elif child_tag == "url" and root_tag == "urlset":
            # ── This index actually contains URLs directly ───────────
            loc_elem = child.find(f"{{{_SITEMAP_NS}}}loc")
            if loc_elem is None:
                loc_elem = child.find("loc")
            if loc_elem is None or not (loc_elem.text or "").strip():
                continue

            raw_url = loc_elem.text.strip()
            full_url = urljoin(base_url, raw_url)
            parsed = urlparse(full_url)

            if not _matches_filters(full_url, include_paths, exclude_paths):
                continue
            if not _is_internal(
                full_url, base_url, include_subdomains=include_subdomains
            ):
                continue

            normalized = normalize_url(full_url, strip_query=ignore_query_params)
            if not normalized:
                continue

            last_modified: str | None = None
            lm_elem = child.find(f"{{{_SITEMAP_NS}}}lastmod")
            if lm_elem is None:
                lm_elem = child.find("lastmod")
            if lm_elem is not None and lm_elem.text:
                last_modified = lm_elem.text.strip()

            all_urls.append(
                {
                    "url": normalized,
                    "path": parsed.path or "/",
                    "depth": 0,
                    "source": "sitemap",
                    "last_modified": last_modified,
                }
            )

    return all_urls


async def parse_sitemap(
    client: httpx.AsyncClient,
    sitemap_url: str,
    include_paths: list[str],
    exclude_paths: list[str],
    include_subdomains: bool,
    ignore_query_params: bool,
    base_url: str,
) -> list[dict]:
    """
    Parse a sitemap (or sitemap index) and return all discovered URLs.

    Returns a list of dicts, each with keys:
        url, path, depth, source, last_modified

    Handles both <urlset> and <sitemapindex> root elements.
    """
    start = time.monotonic()

    try:
        resp = await client.get(sitemap_url, follow_redirects=True)
        if resp.status_code != 200:
            logger.warning(
                "Sitemap fetch failed",
                extra={"sitemap_url": sitemap_url, "status": resp.status_code},
            )
            return []
    except Exception as exc:
        logger.warning(
            "Sitemap fetch error",
            extra={"sitemap_url": sitemap_url, "error": str(exc)},
        )
        return []

    content_length = len(resp.content)
    if content_length > _MAX_SITEMAP_SIZE:
        logger.warning(
            "Sitemap too large — aborting",
            extra={"sitemap_url": sitemap_url, "size_bytes": content_length},
        )
        return []
    elif content_length > _SITEMAP_SIZE_WARNING:
        logger.warning(
            "Sitemap exceeds warning threshold — attempting to parse",
            extra={"sitemap_url": sitemap_url, "size_bytes": content_length},
        )

    xml_text = _decompress_body(resp.content, resp.headers.get("content-encoding"))
    if not xml_text:
        return []

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.warning(
            "Failed to parse sitemap XML",
            extra={"sitemap_url": sitemap_url, "error": str(exc)},
        )
        return []

    root_tag = _strip_ns(root.tag)
    results: list[dict] = []

    if root_tag == "sitemapindex":
        # ── Sitemap index — recurse into child sitemaps ─────────────
        results = await _parse_sitemap_index(
            client,
            sitemap_url,
            include_paths,
            exclude_paths,
            include_subdomains,
            ignore_query_params,
            base_url,
        )
    elif root_tag == "urlset":
        # ── Direct URL set ──────────────────────────────────────────
        results = _parse_urlset(
            xml_text,
            include_paths,
            exclude_paths,
            include_subdomains,
            ignore_query_params,
            base_url,
        )
    else:
        logger.warning("Unknown sitemap root element", extra={"root_tag": root_tag})
        return []

    elapsed = time.monotonic() - start
    logger.info(
        "Sitemap parsed",
        extra={
            "sitemap_url": sitemap_url,
            "urls_found": len(results),
            "duration_ms": int(elapsed * 1000),
        },
    )

    return results
