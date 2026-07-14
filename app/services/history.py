"""
Persistent History Service

Records every completed request (research/crawl/map/extract) to a persistent
database. Defaults to SQLite (zero-config, file-based); set DATABASE_URL to a
PostgreSQL async URL (postgresql+asyncpg://...) to use Postgres instead.

Design notes:
- SQLAlchemy 2.x async Core (no ORM models) — one code path for SQLite & PG.
- Best-effort: DB failures are logged but never propagate to the request.
- Full content is only stored when HISTORY_STORE_FULL_CONTENT=true; otherwise
  only a compact summary/counts are kept to save space.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    delete,
    insert,
    select,
)
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import settings
from app.utils.helpers import get_logger

logger = get_logger(__name__)

_metadata = MetaData()

history_table = Table(
    "history",
    _metadata,
    Column("id", String(64), primary_key=True),
    Column("kind", String(16), nullable=False, index=True),
    Column("query_or_url", Text, nullable=False),
    Column("params", Text, nullable=True),
    Column("status", String(16), nullable=False),
    Column("result", Text, nullable=True),
    Column("result_size", Integer, nullable=False, default=0),
    Column("error", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False, index=True),
)

_engine: AsyncEngine | None = None


async def init_db() -> None:
    """Create the async engine and ensure the history table exists."""
    global _engine
    if not settings.history_enabled:
        logger.info("History disabled (HISTORY_ENABLED=false).")
        return

    engine = create_async_engine(settings.database_url, future=True)
    _engine = engine

    # SQLite: enable WAL for better concurrent read/write behavior.
    if settings.database_url.startswith("sqlite"):
        from sqlalchemy import text

        async with engine.begin() as conn:
            await conn.execute(text("PRAGMA journal_mode=WAL"))

    async with engine.begin() as conn:
        await conn.run_sync(_metadata.create_all)

    logger.info("History DB initialized: %s", settings.database_url.split("://")[0])


async def close() -> None:
    """Dispose of the engine on shutdown."""
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None


def _row_to_dict(row: Any) -> dict:
    d = dict(row._mapping)
    for key in ("params", "result"):
        if d.get(key):
            try:
                d[key] = json.loads(d[key])
            except (json.JSONDecodeError, TypeError):
                pass
    if isinstance(d.get("created_at"), datetime):
        d["created_at"] = d["created_at"].isoformat()
    return d


async def save(
    *,
    id: str | None,
    kind: str,
    query_or_url: str,
    params: dict | None,
    status: str,
    result: Any,
    result_size: int,
    error: str | None = None,
) -> None:
    """Persist one history entry. Best-effort — never raises to the caller."""
    if _engine is None:
        return

    record_id = id or str(uuid.uuid4())

    # When not storing full content, keep only a compact summary.
    stored_result: Any = result
    if not settings.history_store_full_content and isinstance(result, dict):
        stored_result = {
            k: v
            for k, v in result.items()
            if k in ("ai_summary", "total_results", "total_pages", "total_urls",
                     "failed_urls", "success", "extract_mode", "discovery_method")
        }

    try:
        async with _engine.begin() as conn:
            await conn.execute(
                insert(history_table).values(
                    id=record_id,
                    kind=kind,
                    query_or_url=query_or_url[:2048],
                    params=json.dumps(params) if params else None,
                    status=status,
                    result=json.dumps(stored_result, default=str)
                    if stored_result is not None
                    else None,
                    result_size=result_size,
                    error=error,
                    created_at=datetime.now(timezone.utc),
                )
            )
    except Exception as exc:  # best-effort: log & swallow
        logger.warning(
            "Failed to save history entry",
            extra={"id": record_id, "kind": kind, "error": str(exc)},
        )


async def get(entry_id: str) -> dict | None:
    """Fetch a single history entry by id."""
    if _engine is None:
        return None
    try:
        async with _engine.connect() as conn:
            row = (
                await conn.execute(
                    select(history_table).where(history_table.c.id == entry_id)
                )
            ).first()
        return _row_to_dict(row) if row else None
    except Exception as exc:
        logger.warning("Failed to read history entry", extra={"error": str(exc)})
        return None


async def list_history(
    kind: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """List history entries (newest first), optionally filtered by kind."""
    if _engine is None:
        return []
    try:
        stmt = select(history_table)
        if kind:
            stmt = stmt.where(history_table.c.kind == kind)
        stmt = stmt.order_by(history_table.c.created_at.desc()).limit(limit).offset(
            offset
        )
        async with _engine.connect() as conn:
            rows = (await conn.execute(stmt)).fetchall()
        return [_row_to_dict(r) for r in rows]
    except Exception as exc:
        logger.warning("Failed to list history", extra={"error": str(exc)})
        return []


async def purge_expired() -> int:
    """Delete entries older than HISTORY_RETENTION_DAYS (0 = keep forever)."""
    if _engine is None or settings.history_retention_days <= 0:
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(
        days=settings.history_retention_days
    )
    try:
        async with _engine.begin() as conn:
            res = await conn.execute(
                delete(history_table).where(history_table.c.created_at < cutoff)
            )
        return res.rowcount or 0
    except Exception as exc:
        logger.warning("Failed to purge history", extra={"error": str(exc)})
        return 0
