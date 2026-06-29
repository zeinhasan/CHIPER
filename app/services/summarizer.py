"""
AI Summarization Service

Uses OpenAI-compatible SDK pointed at CommandCode's DeepSeek endpoint
to synthesize multiple scraped Markdown documents into a concise summary.

Instrumented with Prometheus metrics for observability.
"""

import time

from openai import AsyncOpenAI

from app.config import settings
from app.utils.helpers import get_logger
from app.utils.metrics import summarize_duration, summarize_total

logger = get_logger(__name__)


# System prompt that instructs the model how to synthesize the results.
SYSTEM_PROMPT = (
    "Kamu adalah asisten riset AI yang membantu menyusun ringkasan "
    "dari berbagai sumber hasil pencarian web. "
    "Tugasmu adalah membaca beberapa konten Markdown yang diekstrak dari halaman web, "
    "lalu membuat ringkasan yang koheren dan informatif dalam Bahasa Indonesia. "
    "Sebutkan poin-poin penting, temuan utama, dan bila relevan, bandingkan "
    "informasi dari sumber yang berbeda. "
    "Jangan membuat klaim yang tidak didukung oleh konten yang diberikan."
)


def _build_user_prompt(documents: list[str], query: str) -> str:
    """Build the user prompt containing the query and all scraped documents."""

    joined = "\n\n---\n\n".join(
        f"### Sumber #{i + 1}\n{doc}" for i, doc in enumerate(documents)
    )

    return (
        f"Pertanyaan riset: {query}\n\n"
        f"Berikut adalah {len(documents)} dokumen hasil scraping:\n\n"
        f"{joined}\n\n"
        f"Buatlah ringkasan sintesis dalam Bahasa Indonesia berdasarkan dokumen-dokumen di atas."
    )


async def summarize(documents: list[str], query: str) -> str:
    """
    Send all scraped Markdown documents to DeepSeek and return a synthesized summary.

    Args:
        documents: List of Markdown strings from scraped pages.
        query: The original user query for context.

    Returns:
        A synthesized summary string in Bahasa Indonesia.

    Raises:
        RuntimeError: If the API key is missing or the API call fails.
    """
    if not settings.cmd_api_key:
        raise RuntimeError(
            "CMD_API_KEY is not set. "
            "Please configure it via environment variable to enable AI summarization."
        )

    if not documents:
        return "Tidak ada konten yang dapat diringkas (semua scraping gagal)."

    client = AsyncOpenAI(
        base_url=settings.cmd_base_url,
        api_key=settings.cmd_api_key,
    )

    user_prompt = _build_user_prompt(documents, query)
    total_chars = sum(len(d) for d in documents)

    logger.info(
        "Sending summarization request",
        extra={
            "model": settings.cmd_model,
            "document_count": len(documents),
            "total_chars": total_chars,
        },
    )

    start = time.monotonic()

    try:
        response = await client.chat.completions.create(
            model=settings.cmd_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=2048,
        )
    except Exception as exc:
        elapsed = time.monotonic() - start
        summarize_duration.observe(elapsed)
        summarize_total.labels(status="error").inc()

        logger.error("AI summarization API call failed", extra={"error": str(exc)})
        raise RuntimeError(f"AI summarization failed: {exc}") from exc

    elapsed = time.monotonic() - start
    summarize_duration.observe(elapsed)
    summarize_total.labels(status="success").inc()

    summary = response.choices[0].message.content or ""

    logger.info(
        "Summarization complete",
        extra={
            "summary_length": len(summary),
            "duration_ms": int(elapsed * 1000),
        },
    )
    return summary.strip()
