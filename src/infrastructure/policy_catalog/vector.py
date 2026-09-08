from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.openai.embedding import (
    INCIDENT_EMBEDDING_MODEL,
    build_incident_query_text,
    create_embeddings,
)
from src.infrastructure.policy_catalog.context import IncidentPromptContext

INCIDENT_VECTOR_LIMIT = 3
INCIDENT_CONTEXT_LIMIT = 5
MAX_COSINE_DISTANCE = 0.50
INCIDENT_TABLE_NAME = "namu_wiki_incident_index_entries"


async def vector_search_ready(session: AsyncSession) -> bool:
    bind = getattr(session, "bind", None)
    if bind is None or bind.dialect.name != "postgresql":
        return False
    try:
        extension = await session.execute(text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')"))
        embedding_column = await session.execute(text("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = :table_name AND column_name = 'embedding'
            )
        """), {"table_name": INCIDENT_TABLE_NAME})
        embedding_model_column = await session.execute(text("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = :table_name AND column_name = 'embedding_model'
            )
        """), {"table_name": INCIDENT_TABLE_NAME})
        return (
            bool(extension.scalar())
            and bool(embedding_column.scalar())
            and bool(embedding_model_column.scalar())
        )
    except Exception:
        return False


async def search_semantic_incidents(
    session: AsyncSession,
    embedding: list[float],
    *,
    limit: int = INCIDENT_VECTOR_LIMIT,
) -> list[IncidentPromptContext]:
    vector_literal = "[" + ",".join(str(value) for value in embedding) + "]"
    result = await session.execute(text(f"""
        SELECT title, year, source_url, source_type, risk_categories
        FROM {INCIDENT_TABLE_NAME}
        WHERE is_active = TRUE
          AND embedding IS NOT NULL
          AND embedding_model = :embedding_model
          AND embedding <=> CAST(:embedding AS vector) <= :max_distance
        ORDER BY embedding <=> CAST(:embedding AS vector)
        LIMIT :limit
    """), {
        "embedding_model": INCIDENT_EMBEDDING_MODEL,
        "embedding": vector_literal,
        "max_distance": MAX_COSINE_DISTANCE,
        "limit": limit,
    })
    rows = result.mappings().all()
    return [
        IncidentPromptContext(
            title=row["title"], year=row["year"], source_url=row["source_url"],
            source_type=row["source_type"], risk_categories=tuple(row["risk_categories"] or []),
        )
        for row in rows
    ]


def merge_incident_context(
    keyword_incidents: list[IncidentPromptContext],
    vector_incidents: list[IncidentPromptContext],
) -> list[IncidentPromptContext]:
    merged = []
    keys = set()
    for incident in [*keyword_incidents, *vector_incidents]:
        key = incident.source_url or f"{incident.source_type}:{incident.year}:{incident.title}"
        if key not in keys:
            keys.add(key)
            merged.append(incident)
    return merged[:INCIDENT_CONTEXT_LIMIT]


async def enrich_incident_context(
    session: AsyncSession,
    keyword_incidents: list[IncidentPromptContext],
    *,
    search_summary: str,
    search_terms: tuple[str, ...],
    original_text: str | None,
    api_key: str,
) -> list[IncidentPromptContext]:
    query = build_incident_query_text(search_summary, search_terms, original_text)
    if not query:
        return keyword_incidents
    try:
        if not await vector_search_ready(session):
            return keyword_incidents
        embeddings = await create_embeddings([query], api_key=api_key)
        vector_incidents = await search_semantic_incidents(session, embeddings[0]) if embeddings else []
    except Exception:
        vector_incidents = []
    return merge_incident_context(keyword_incidents, vector_incidents)