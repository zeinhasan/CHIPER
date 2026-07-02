"""
Structured Data Extraction Service

Extracts specific fields from web page content using an OpenAI-compatible LLM.
Supports two modes:
- **Prompt-only**: Natural language description → LLM returns structured JSON.
- **Schema mode**: JSON Schema → LLM returns JSON validated against the schema.

Supports two extraction strategies:
- **combined** (default): All pages scraped, content combined, one LLM call.
- **per_page**: Each page scraped and extracted independently.

Reuses the Two-Tier scraper for content fetching and the same OpenAI-compatible
client configuration as the summarizer (CMD_API_KEY, CMD_BASE_URL, CMD_MODEL).
"""

import json
import re
import time
from typing import Any

import httpx
import jsonschema
from openai import AsyncOpenAI
from playwright.async_api import Browser

from app.config import settings
from app.models.schemas import ExtractData
from app.utils.helpers import get_logger
from app.utils.metrics import extract_duration, extract_total

logger = get_logger(__name__)


# ── Prompt Templates ────────────────────────────────────────────

SYSTEM_PROMPT_BASE = (
    "You are a precise data extraction assistant. Your task is to extract "
    "the requested information from the provided web page content."
)

SYSTEM_PROMPT_NO_SCHEMA = (
    SYSTEM_PROMPT_BASE
    + """

Rules:
1. Only extract information explicitly present in the content.
2. Do NOT fabricate, infer, or make up information.
3. Return the data in a clean, structured JSON format.
4. If the requested information is not found, return an empty object.
5. Be concise — extract exactly what was asked, nothing more.
6. Maintain original values — do not paraphrase or summarize numbers.
7. Return ONLY valid JSON — no explanation, no markdown formatting.
"""
)

SYSTEM_PROMPT_WITH_SCHEMA = (
    SYSTEM_PROMPT_BASE
    + """

Rules:
1. Only extract information explicitly present in the content.
2. Do NOT fabricate, infer, or make up information.
3. Return ONLY valid JSON that strictly follows the provided JSON Schema.
4. The JSON MUST validate against the provided schema.
5. If a required field cannot be found, use null.
6. Maintain original values — do not paraphrase or summarize numbers.
7. Return raw JSON only — NO markdown code fences.
"""
)

RETRY_USER_PROMPT = (
    "\n\nYour previous response was not valid JSON. "
    "Please return ONLY valid JSON. No explanation, no markdown formatting."
)


# ── Helpers ─────────────────────────────────────────────────────


def _build_system_prompt(schema: dict | None) -> str:
    """Select the appropriate system prompt based on schema presence."""
    return SYSTEM_PROMPT_WITH_SCHEMA if schema else SYSTEM_PROMPT_NO_SCHEMA


def _build_user_prompt(
    content: str,
    prompt: str,
    url: str | None = None,
    schema: dict | None = None,
) -> str:
    """Build the user prompt with content, extraction request, and optional schema."""
    parts: list[str] = []

    if url:
        parts.append(f"URL: {url}\n")

    parts.append(f"Content to extract from:\n---\n{content}\n---\n")
    parts.append(f"Extraction request: {prompt}")

    if schema:
        schema_json = json.dumps(schema, indent=2)
        parts.append(f"\n\nRequired JSON Schema:\n```json\n{schema_json}\n```")

    return "\n".join(parts)


def _extract_json_from_response(raw: str) -> str | None:
    """
    Extract JSON from an LLM response, handling various wrapping formats.

    Attempts (in order):
    1. Direct JSON parse.
    2. JSON inside markdown code fences (```json ... ```).
    3. JSON object or array at the root level via brace/bracket matching.
    """
    text = raw.strip()

    # ── Try 1: Direct parse ─────────────────────────────────────
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        pass

    # ── Try 2: Markdown code fences ─────────────────────────────
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        candidate = match.group(1).strip()
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            pass

    # ── Try 3: Brace/bracket matching ───────────────────────────
    for start_char, end_char in [("{", "}"), ("[", "]")]:
        start_idx = text.find(start_char)
        if start_idx == -1:
            continue
        depth = 0
        for i in range(start_idx, len(text)):
            if text[i] == start_char:
                depth += 1
            elif text[i] == end_char:
                depth -= 1
                if depth == 0:
                    candidate = text[start_idx : i + 1].strip()
                    try:
                        json.loads(candidate)
                        return candidate
                    except json.JSONDecodeError:
                        break

    return None


def _validate_against_schema(data: Any, schema: dict | None) -> tuple[bool, str | None]:
    """Validate extracted data against a JSON Schema. Returns (valid, error)."""
    if schema is None:
        return True, None
    try:
        jsonschema.validate(data, schema)
        return True, None
    except jsonschema.ValidationError as exc:
        return False, str(exc)


# ── LLM Call ────────────────────────────────────────────────────


async def _call_llm(
    system_prompt: str,
    user_prompt: str,
    previous_response: str | None = None,
) -> str:
    """
    Call the OpenAI-compatible LLM and return the raw response text.

    If *previous_response* is provided, the assistant's previous (invalid)
    response is included in the conversation so the model can correct itself.
    """
    if not settings.cmd_api_key:
        raise RuntimeError("CMD_API_KEY is not set. Cannot perform AI extraction.")

    client = AsyncOpenAI(
        base_url=settings.cmd_base_url,
        api_key=settings.cmd_api_key,
    )

    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    if previous_response:
        messages.append({"role": "assistant", "content": previous_response})
        messages.append({"role": "user", "content": RETRY_USER_PROMPT})

    response = await client.chat.completions.create(
        model=settings.cmd_model,
        messages=messages,
        temperature=settings.extract_temperature,
        max_tokens=4096,
    )

    return response.choices[0].message.content or ""


# ── Single Extraction (one content → one result) ────────────────


async def _extract_from_content(
    content: str,
    prompt: str,
    schema: dict | None,
    url: str | None = None,
) -> dict | list | str | None:
    """
    Send content to the LLM and extract structured data.

    In schema mode, performs retry + schema validation.
    Returns the extracted data, or None on total failure.
    """
    system_prompt = _build_system_prompt(schema)
    user_prompt = _build_user_prompt(content, prompt, url, schema)

    previous: str | None = None

    for attempt in range(settings.extract_retries + 1):
        try:
            raw = await _call_llm(system_prompt, user_prompt, previous)
        except Exception as exc:
            logger.error(
                "LLM extraction call failed",
                extra={"url": url, "attempt": attempt + 1, "error": str(exc)},
            )
            if attempt < settings.extract_retries:
                previous = None  # API error, not a JSON parse error
                continue
            return None

        # ── Parse JSON from LLM response ────────────────────────
        json_str = _extract_json_from_response(raw)
        if not json_str:
            logger.warning(
                "LLM returned no JSON content",
                extra={"url": url, "attempt": attempt + 1},
            )
            if attempt < settings.extract_retries:
                previous = raw
                continue
            # Last resort: return raw text
            return raw[:2000] if len(raw) > 2000 else raw

        try:
            parsed = json.loads(json_str)
        except json.JSONDecodeError:
            logger.warning(
                "LLM returned invalid JSON",
                extra={"url": url, "attempt": attempt + 1},
            )
            if attempt < settings.extract_retries:
                previous = raw
                continue
            return json_str  # Return raw string as last resort

        # ── Validate against schema (schema mode only) ──────────
        if schema:
            valid, error_msg = _validate_against_schema(parsed, schema)
            if not valid:
                logger.warning(
                    "LLM response failed schema validation",
                    extra={"url": url, "attempt": attempt + 1, "error": error_msg},
                )
                if attempt < settings.extract_retries:
                    previous = raw
                    continue
                # Return parsed data even if schema invalid (graceful degradation)
                return parsed

        return parsed

    return None


# ── Orchestrator ────────────────────────────────────────────────


async def run_extraction(
    urls: list[str],
    prompt: str,
    schema: dict | None,
    extract_mode: str,
    force_js_render: bool,
    http_client: httpx.AsyncClient,
    browsers: list[Browser],
) -> tuple[list[ExtractData], int]:
    """
    Run the full extraction pipeline: scrape URLs → extract via LLM.

    Args:
        urls: List of URLs to extract data from.
        prompt: Natural-language description of what to extract.
        schema: Optional JSON Schema for validation.
        extract_mode: "combined" or "per_page".
        force_js_render: Skip Tier-1 if True.
        http_client: Shared httpx client.
        browsers: Shared browser pool.

    Returns:
        Tuple of (data list, count of failed URLs).
    """
    start = time.monotonic()
    total_urls = len(urls)
    results: list[ExtractData] = []

    # ── Step 1: Scrape all URLs concurrently ────────────────────
    import asyncio
    import itertools

    from app.services.scraper import fetch_and_extract

    semaphore = asyncio.Semaphore(3)  # Limit concurrent Playwright contexts
    pool = itertools.cycle(browsers)

    scrape_tasks = [
        fetch_and_extract(
            url,
            next(pool),
            http_client,
            force_js_render=force_js_render,
            semaphore=semaphore,
        )
        for url in urls
    ]
    scrape_results = await asyncio.gather(*scrape_tasks, return_exceptions=True)

    # ── Step 2: Extract ─────────────────────────────────────────
    failed = 0

    if extract_mode == "combined":
        # Combine all successful content into one LLM call
        combined_parts: list[str] = []
        successful_urls: list[str] = []

        for i, r in enumerate(scrape_results):
            if isinstance(r, BaseException):
                logger.warning(
                    "Scrape failed for URL", extra={"url": urls[i], "error": str(r)}
                )
                results.append(ExtractData(url=urls[i], extraction=None, error=str(r)))
                failed += 1
                continue

            if r.get("error"):
                results.append(
                    ExtractData(url=urls[i], extraction=None, error=r["error"])
                )
                failed += 1
                continue

            content = r.get("markdown_content", "")
            if len(content) > settings.extract_max_content_chars:
                content = content[: settings.extract_max_content_chars] + (
                    "\n\n[... content truncated ...]"
                )

            combined_parts.append(f"--- Page {i + 1}: {urls[i]} ---\n{content}")
            successful_urls.append(urls[i])

        if not combined_parts:
            logger.warning("All URLs failed to scrape — no content to extract")
            # Ensure all URLs have a result entry
            for url in urls:
                if not any(d.url == url for d in results):
                    results.append(
                        ExtractData(
                            url=url, extraction=None, error="No content to extract"
                        )
                    )
        else:
            combined_content = "\n\n".join(combined_parts)

            extraction_result = await _extract_from_content(
                content=combined_content,
                prompt=prompt,
                schema=schema,
            )

            for url in successful_urls:
                results.append(
                    ExtractData(
                        url=url,
                        extraction=extraction_result,
                        error=None
                        if extraction_result is not None
                        else "Extraction failed",
                    )
                )

    else:  # per_page
        for i, r in enumerate(scrape_results):
            if isinstance(r, BaseException):
                results.append(ExtractData(url=urls[i], extraction=None, error=str(r)))
                failed += 1
                continue

            if r.get("error"):
                results.append(
                    ExtractData(url=urls[i], extraction=None, error=r["error"])
                )
                failed += 1
                continue

            content = r.get("markdown_content", "")
            if len(content) > settings.extract_max_content_chars:
                content = content[: settings.extract_max_content_chars] + (
                    "\n\n[... content truncated ...]"
                )

            if not content.strip():
                results.append(
                    ExtractData(url=urls[i], extraction=None, error="Empty content")
                )
                failed += 1
                continue

            extraction_result = await _extract_from_content(
                content=content,
                prompt=prompt,
                schema=schema,
                url=urls[i],
            )

            results.append(
                ExtractData(
                    url=urls[i],
                    extraction=extraction_result,
                    error=None
                    if extraction_result is not None
                    else "Extraction failed",
                )
            )

    # ── Determine overall status ────────────────────────────────
    elapsed = time.monotonic() - start

    if failed == 0:
        status = "success"
    elif failed < total_urls:
        status = "partial"
    else:
        status = "error"

    extract_duration.observe(elapsed)
    extract_total.labels(
        status=status,
        mode=extract_mode,
        has_schema=str(schema is not None),
    ).inc()

    logger.info(
        "Extraction complete",
        extra={
            "total_urls": total_urls,
            "failed": failed,
            "mode": extract_mode,
            "has_schema": schema is not None,
            "status": status,
            "duration_ms": int(elapsed * 1000),
        },
    )

    return results, failed
