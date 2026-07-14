"""Tests for app.models.schemas (Pydantic validation, per-page config, new fields)."""

import pytest
from pydantic import ValidationError

from app.models.schemas import (
    CrawlRequest,
    ExtractRequest,
    MapRequest,
    ResearchRequest,
    UrlSpec,
)


# ── ResearchRequest ─────────────────────────────────────────────────


def test_research_defaults():
    r = ResearchRequest(query="hello world")
    assert r.max_results == 5
    assert r.summary_prompt is None
    assert r.allow_domains == []
    assert r.block_domains == []


def test_research_max_results_bounds():
    with pytest.raises(ValidationError):
        ResearchRequest(query="x", max_results=0)
    with pytest.raises(ValidationError):
        ResearchRequest(query="x", max_results=21)


def test_research_summary_prompt_length():
    with pytest.raises(ValidationError):
        ResearchRequest(query="x", summary_prompt="p" * 2001)


# ── CrawlRequest / MapRequest URL normalization (L-2) ───────────────


def test_crawl_url_adds_scheme():
    assert CrawlRequest(url="example.com").url == "https://example.com"


def test_crawl_url_rejects_bad_scheme():
    with pytest.raises(ValidationError):
        CrawlRequest(url="ftp://example.com")


def test_crawl_domain_fields():
    r = CrawlRequest(url="example.com", allow_domains=["a.com"], block_domains=["b.com"])
    assert r.allow_domains == ["a.com"]
    assert r.block_domains == ["b.com"]


def test_map_url_normalized():
    assert MapRequest(url="example.com").url == "https://example.com"


def test_map_discovery_method_validated():
    with pytest.raises(ValidationError):
        MapRequest(url="example.com", discovery_method="invalid")


# ── ExtractRequest: per-page config (feature 21) ────────────────────


def test_extract_urls_plain_strings():
    r = ExtractRequest(urls=["example.com", "https://b.com"], prompt="extract prices")
    assert all(isinstance(u, UrlSpec) for u in r.urls)
    assert r.urls[0].url == "https://example.com"
    assert r.urls[0].force_js_render is None


def test_extract_urls_object_form():
    r = ExtractRequest(
        urls=[{"url": "b.com", "force_js_render": True}], prompt="extract prices"
    )
    assert r.urls[0].url == "https://b.com"
    assert r.urls[0].force_js_render is True


def test_extract_urls_mixed_forms():
    r = ExtractRequest(
        urls=["a.com", {"url": "b.com", "force_js_render": False}],
        prompt="extract prices",
    )
    assert r.urls[0].force_js_render is None
    assert r.urls[1].force_js_render is False


def test_extract_urls_object_missing_url():
    with pytest.raises(ValidationError):
        ExtractRequest(urls=[{"force_js_render": True}], prompt="extract prices")


def test_extract_urls_bad_scheme():
    with pytest.raises(ValidationError):
        ExtractRequest(urls=["ftp://x.com"], prompt="extract prices")


def test_extract_prompt_min_length():
    with pytest.raises(ValidationError):
        ExtractRequest(urls=["a.com"], prompt="short")


def test_extract_url_count_bounds():
    with pytest.raises(ValidationError):
        ExtractRequest(urls=[], prompt="extract prices")
    with pytest.raises(ValidationError):
        ExtractRequest(urls=[f"site{i}.com" for i in range(21)], prompt="extract prices")


def test_extract_domain_fields():
    r = ExtractRequest(
        urls=["a.com"], prompt="extract prices", block_domains=["bad.com"]
    )
    assert r.block_domains == ["bad.com"]
