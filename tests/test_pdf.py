"""Tests for PDF scraping helpers (feature 22).

Skips if scraper module dependencies (trafilatura/markdownify) or pymupdf are
not installed in the current environment.
"""

import pytest

# scraper.py imports trafilatura, markdownify, playwright, and the prometheus
# metrics stack at module load — skip cleanly if any are unavailable.
fitz = pytest.importorskip("fitz")
scraper = pytest.importorskip("app.services.scraper")
_extract_pdf = scraper._extract_pdf
_looks_like_pdf = scraper._looks_like_pdf


# ── detection ───────────────────────────────────────────────────────


def test_detect_by_content_type():
    assert _looks_like_pdf("https://x.com/doc", "application/pdf", b"")


def test_detect_by_extension():
    assert _looks_like_pdf("https://x.com/report.pdf", "text/html", b"")
    assert _looks_like_pdf("https://x.com/report.pdf?v=1", "", b"")


def test_detect_by_magic_bytes():
    assert _looks_like_pdf("https://x.com/unknown", "application/octet-stream", b"%PDF-1.7")


def test_not_pdf():
    assert not _looks_like_pdf("https://x.com/page", "text/html", b"<html>")


# ── extraction ──────────────────────────────────────────────────────


def _make_pdf(text: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def test_extract_pdf_text():
    data = _make_pdf("Hello CHIPER PDF")
    text, err = _extract_pdf(data)
    assert err is None
    assert "Hello CHIPER PDF" in text


def test_extract_pdf_invalid_bytes():
    text, err = _extract_pdf(b"not a real pdf")
    assert text is None
    assert err is not None
