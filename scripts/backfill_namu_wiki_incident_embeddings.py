from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import text

from src import config

from src.infrastructure.openai.embedding import (
    INCIDENT_EMBEDDING_MODEL,
    build_incident_embedding_text,
    create_embeddings,
)
from src.infrastructure.persistence.database import build_engine
from src.infrastructure.policy_catalog.vector import INCIDENT_TABLE_NAME

BATCH_SIZE = 64


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(str(value) for value in values) + "]"


def backfill_missing_embeddings(batch_size: int = BATCH_SIZE) -> int:
    api_key = config.OPEN_API_KEY
    if not api_key:
        raise RuntimeError("OPEN_API_KEY must be configured for embedding backfill")
    engine = build_engine()
    if engine.dialect.name != "postgresql":
        raise RuntimeError("Embedding backfill requires PostgreSQL with prepared pgvector schema")
    updated = 0
    with engine.begin() as connection:
        extension_ready = connection.execute(
            text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')")
        ).scalar()
        column_ready = connection.execute(text("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = :table_name AND column_name = 'embedding'
            )
        """), {"table_name": INCIDENT_TABLE_NAME}).scalar()
        if not extension_ready or not column_ready:
            raise RuntimeError("pgvector extension and incident embedding column must be prepared first")
        rows = connection.execute(text(f"""
            SELECT id, title, match_keywords, risk_categories
            FROM {INCIDENT_TABLE_NAME}
            WHERE is_active = TRUE
              AND (embedding IS NULL OR embedding_model IS DISTINCT FROM :embedding_model)
            ORDER BY year, normalized_title
            LIMIT :limit
        """), {"embedding_model": INCIDENT_EMBEDDING_MODEL, "limit": batch_size}).mappings().all()
        texts = [build_incident_embedding_text(row["title"], row["match_keywords"] or [], row["risk_categories"] or []) for row in rows]
        if not texts:
            return 0
        embeddings = asyncio.run(create_embeddings(texts, api_key=api_key))
        for row, embedding in zip(rows, embeddings, strict=True):
            connection.execute(text(f"""
                UPDATE {INCIDENT_TABLE_NAME}
                SET embedding = CAST(:embedding AS vector), embedding_model = :embedding_model
                WHERE id = :id
            """), {"embedding": _vector_literal(embedding), "embedding_model": INCIDENT_EMBEDDING_MODEL, "id": row["id"]})
            updated += 1
    return updated


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill missing NamuWiki incident embeddings.")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    arguments = parser.parse_args()
    print(f"updated={backfill_missing_embeddings(arguments.batch_size)}")