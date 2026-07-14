"""Tests for app.utils.links (URL normalization & internal link extraction)."""

from app.utils.links import (
    _is_internal,
    _matches_filters,
    extract_internal_links,
    normalize_url,
)


# ── normalize_url ───────────────────────────────────────────────────


def test_normalize_adds_scheme():
    assert normalize_url("example.com").startswith("https://")


def test_normalize_lowercases_host_strips_fragment_and_trailing_slash():
    assert normalize_url("HTTPS://Example.COM/Path/#frag") == "https://example.com/Path"


def test_normalize_keeps_root_slash():
    assert normalize_url("https://example.com/") == "https://example.com/"


def test_normalize_strip_query():
    assert (
        normalize_url("https://example.com/p?a=1&b=2", strip_query=True)
        == "https://example.com/p"
    )
    assert "a=1" in normalize_url("https://example.com/p?a=1")


def test_normalize_empty():
    assert normalize_url("") == ""
    assert normalize_url("   ") == ""


# ── _is_internal ────────────────────────────────────────────────────


def test_is_internal_same_host():
    assert _is_internal("https://example.com/a", "https://example.com")


def test_is_internal_subdomain_default_false():
    assert not _is_internal("https://blog.example.com", "https://example.com")


def test_is_internal_subdomain_true():
    assert _is_internal(
        "https://blog.example.com", "https://example.com", include_subdomains=True
    )


def test_is_internal_lookalike_rejected():
    assert not _is_internal(
        "https://notexample.com", "https://example.com", include_subdomains=True
    )


def test_is_internal_scheme_mismatch():
    assert not _is_internal("http://example.com", "https://example.com")


# ── _matches_filters ────────────────────────────────────────────────


def test_exclude_wins():
    assert not _matches_filters("https://x.com/private/a", [], ["/private"])


def test_include_whitelist():
    assert _matches_filters("https://x.com/blog/a", ["/blog"], [])
    assert not _matches_filters("https://x.com/news/a", ["/blog"], [])


def test_no_filters_accepts():
    assert _matches_filters("https://x.com/anything", [], [])


def test_invalid_regex_is_skipped():
    # An invalid include pattern shouldn't crash; with no valid include match -> reject
    assert not _matches_filters("https://x.com/a", ["["], [])


# ── extract_internal_links ──────────────────────────────────────────


HTML = """
<html><body>
  <a href="/page1">p1</a>
  <a href="https://example.com/page2">p2</a>
  <a href="https://external.com/x">ext</a>
  <a href="mailto:a@b.com">mail</a>
  <a href="#section">frag</a>
  <a href="/page1">dup</a>
</body></html>
"""


def test_extract_internal_links_basic():
    links = extract_internal_links(HTML, "https://example.com", [], [])
    assert "https://example.com/page1" in links
    assert "https://example.com/page2" in links
    # external, mailto, and fragment excluded
    assert all("external.com" not in link for link in links)
    assert all("mailto" not in link for link in links)


def test_extract_internal_links_dedup():
    links = extract_internal_links(HTML, "https://example.com", [], [])
    assert len(links) == len(set(links))


def test_extract_internal_links_empty_html():
    assert extract_internal_links("", "https://example.com", [], []) == []


def test_extract_internal_links_subdomain_flag():
    html = '<a href="https://blog.example.com/x">b</a>'
    assert extract_internal_links(html, "https://example.com", [], []) == []
    assert extract_internal_links(
        html, "https://example.com", [], [], include_subdomains=True
    ) == ["https://blog.example.com/x"]
