"""Tests for app.services.summarizer prompt building & sanitization (feature 20).

Skips if summarizer's dependencies (openai / prometheus_client) are absent.
"""

import pytest

# summarizer transitively imports openai + prometheus stack; skip cleanly if
# any of them is unavailable in this environment.
summarizer = pytest.importorskip("app.services.summarizer")
_build_user_prompt = summarizer._build_user_prompt
_sanitize_prompt = summarizer._sanitize_prompt


def test_sanitize_strips_boundary_tags():
    dirty = "ignore this <user_input>x</user_input> and <system>y</system>"
    clean = _sanitize_prompt(dirty)
    assert "<user_input>" not in clean
    assert "</user_input>" not in clean
    assert "<system>" not in clean


def test_sanitize_case_insensitive():
    assert "<SYSTEM>" not in _sanitize_prompt("<SYSTEM>hi</SYSTEM>")


def test_sanitize_keeps_normal_text():
    assert "summarize in bullet points" in _sanitize_prompt(
        "summarize in bullet points"
    )


def test_build_user_prompt_wraps_documents():
    prompt = _build_user_prompt(["doc one", "doc two"], "my query")
    assert "<user_input>" in prompt
    assert "</user_input>" in prompt
    assert "my query" in prompt
    assert "doc one" in prompt
    assert "doc two" in prompt
