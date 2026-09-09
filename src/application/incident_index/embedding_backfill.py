from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from src import config
from src.infrastructure.openai.embedding import (
    INCIDENT_EMBEDDING_MODEL,
    build_incident_embedding_text,
    create_embeddings,
)
from src.infrastructure.persistence.database import build_engine
from src.infrastructure.policy_catalog.vector import INCIDENT_TABLE_NAME

DEFAULT_BATCH_SIZE = 64


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(str(value) for value in values) + "]"


def _schema_ready(engine: Engine) -> bool:
    if engine.dialect.name != "postgresql":
        return False
    try:
        with engine.connect() as connection:
            extension_ready = connection.execute(
                text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')")
            ).scalar()
            columns = connection.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = :table_name
                      AND column_name IN ('embedding', 'embedding_model')
                    """
                ),
                {"table_name": INCIDENT_TABLE_NAME},
            ).scalars().all()
        return bool(extension_ready) and {"embedding", "embedding_model"}.issubset(set(columns))
    except Exception:
        return False


def _fetch_missing_batch(engine: Engine, batch_size: int) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        return list(
            connection.execute(
                text(
                    f"""
                    SELECT id, title, match_keywords, risk_categories
                    FROM {INCIDENT_TABLE_NAME}
                    WHERE is_active = TRUE
                      AND (embedding IS NULL OR embedding_model IS DISTINCT FROM :embedding_model)
                    ORDER BY year, normalized_title
                    LIMIT :limit
                    """
                ),
                {"embedding_model": INCIDENT_EMBEDDING_MODEL, "limit": batch_size},
            ).mappings().all()
        )


def _persist_batch(
    engine: Engine,
    rows: list[dict[str, Any]],
    embeddings: list[list[float]],
) -> None:
    if len(rows) != len(embeddings):
        raise RuntimeError("Embedding result count did not match selected incident rows")
    with engine.begin() as connection:
        for row, embedding in zip(rows, embeddings, strict=True):
            connection.execute(
                text(
                    f"""
                    UPDATE {INCIDENT_TABLE_NAME}
                    SET embedding = CAST(:embedding AS vector),
                        embedding_model = :embedding_model
                    WHERE id = :id
                    """
                ),
                {
                    "embedding": _vector_literal(embedding),
                    "embedding_model": INCIDENT_EMBEDDING_MODEL,
                    "id": row["id"],
                },
            )


async def backfill_missing_incident_embeddings(
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    api_key: str | None = None,
) -> int:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    engine = build_engine()
    try:
        if not await asyncio.to_thread(_schema_ready, engine):
            return 0

        resolved_api_key = api_key if api_key is not None else config.OPEN_API_KEY
        if not resolved_api_key:
            raise RuntimeError("OPEN_API_KEY must be configured for embedding backfill")

        updated = 0
        while True:
            rows = await asyncio.to_thread(_fetch_missing_batch, engine, batch_size)
            if not rows:
                return updated

            inputs = [
                build_incident_embedding_text(
                    row["title"],
                    row.get("match_keywords") or [],
                    row.get("risk_categories") or [],
                )
                for row in rows
            ]
            embeddings = await create_embeddings(inputs, api_key=resolved_api_key)
            await asyncio.to_thread(_persist_batch, engine, rows, embeddings)
            updated += len(rows)
    finally:
        engine.dispose()
