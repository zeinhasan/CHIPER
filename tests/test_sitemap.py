"""Tests for app.utils.sitemap pure-parsing helpers."""

import gzip

from app.utils.sitemap import (
    _SITEMAP_LINE_RE,
    _decompress_body,
    _parse_urlset,
    _strip_ns,
)


def test_strip_ns():
    assert _strip_ns("{http://www.sitemaps.org/schemas/sitemap/0.9}url") == "url"
    assert _strip_ns("url") == "url"


def test_decompress_plain():
    assert _decompress_body(b"<xml/>", None) == "<xml/>"


def test_decompress_gzip():
    raw = gzip.compress(b"<urlset/>")
    assert _decompress_body(raw, "gzip") == "<urlset/>"


def test_decompress_bad_gzip_returns_empty():
    assert _decompress_body(b"not gzip", "gzip") == ""


def test_robots_sitemap_regex():
    text = "User-agent: *\nSitemap: https://example.com/sitemap.xml\n"
    m = _SITEMAP_LINE_RE.search(text)
    assert m and m.group(1) == "https://example.com/sitemap.xml"


SITEMAP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/a</loc><lastmod>2024-01-01</lastmod></url>
  <url><loc>https://example.com/b</loc></url>
  <url><loc>https://external.com/x</loc></url>
</urlset>
"""


def test_parse_urlset_filters_external():
    urls = _parse_urlset(
        SITEMAP_XML,
        include_paths=[],
        exclude_paths=[],
        include_subdomains=False,
        ignore_query_params=False,
        base_url="https://example.com",
    )
    locs = {u["url"] for u in urls}
    assert "https://example.com/a" in locs
    assert "https://example.com/b" in locs
    assert all("external.com" not in u for u in locs)


def test_parse_urlset_lastmod():
    urls = _parse_urlset(
        SITEMAP_XML, [], [], False, False, "https://example.com"
    )
    a = next(u for u in urls if u["url"].endswith("/a"))
    assert a["last_modified"] == "2024-01-01"
    assert a["source"] == "sitemap"


def test_parse_urlset_exclude_filter():
    urls = _parse_urlset(
        SITEMAP_XML, [], ["/b"], False, False, "https://example.com"
    )
    locs = {u["url"] for u in urls}
    assert "https://example.com/a" in locs
    assert "https://example.com/b" not in locs


def test_parse_urlset_invalid_xml():
    assert _parse_urlset("<not-valid", [], [], False, False, "https://example.com") == []
