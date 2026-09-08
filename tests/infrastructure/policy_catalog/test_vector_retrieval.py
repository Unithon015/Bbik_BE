import unittest
from unittest.mock import AsyncMock, patch

from src.infrastructure.policy_catalog.context import IncidentPromptContext
from src.infrastructure.policy_catalog.vector import (
    MAX_COSINE_DISTANCE,
    enrich_incident_context,
    merge_incident_context,
    search_semantic_incidents,
    vector_search_ready,
)


class VectorMergeTest(unittest.TestCase):
    def entry(self, title, url):
        return IncidentPromptContext(title=title, year=2026, source_url=url, source_type="NAMU_WIKI", risk_categories=())

    def test_keyword_priority_dedupes_by_source_url_and_limits_to_five(self):
        keyword = [self.entry("A", "a"), self.entry("B", "b"), self.entry("C", "c")]
        vector = [self.entry("B2", "b"), self.entry("D", "d"), self.entry("E", "e")]
        self.assertEqual([item.title for item in merge_incident_context(keyword, vector)], ["A", "B", "C", "D", "E"])


class VectorReadinessTest(unittest.IsolatedAsyncioTestCase):
    async def test_missing_embedding_model_column_is_not_vector_ready(self):
        class Result:
            def __init__(self, value):
                self.value = value
            def scalar(self):
                return self.value
        class Session:
            bind = type("Bind", (), {"dialect": type("Dialect", (), {"name": "postgresql"})()})()
            def __init__(self):
                self.values = iter([True, True, False])
            async def execute(self, *_args):
                return Result(next(self.values))

        self.assertFalse(await vector_search_ready(Session()))

    async def test_extension_and_both_columns_are_required_for_vector_readiness(self):
        class Result:
            def __init__(self, value):
                self.value = value
            def scalar(self):
                return self.value
        class Session:
            bind = type("Bind", (), {"dialect": type("Dialect", (), {"name": "postgresql"})()})()
            def __init__(self):
                self.calls = 0
                self.values = iter([True, True, True])
            async def execute(self, *_args):
                self.calls += 1
                return Result(next(self.values))
        session = Session()

        self.assertTrue(await vector_search_ready(session))
        self.assertEqual(session.calls, 3)
    async def test_non_postgresql_is_not_vector_ready(self):
        session = type("Session", (), {"bind": type("Bind", (), {"dialect": type("Dialect", (), {"name": "sqlite"})()})()})()
        self.assertFalse(await vector_search_ready(session))

    async def test_empty_semantic_query_skips_embedding(self):
        with patch("src.infrastructure.policy_catalog.vector.create_embeddings", new=AsyncMock()) as embed:
            result = await enrich_incident_context(object(), [], search_summary="", search_terms=(), original_text="", api_key="key")
        self.assertEqual(result, [])
        embed.assert_not_awaited()

    async def test_embedding_failure_keeps_keyword_incidents(self):
        keyword = [IncidentPromptContext("A", 2026, "a", "NAMU_WIKI", ())]
        with (
            patch("src.infrastructure.policy_catalog.vector.vector_search_ready", new=AsyncMock(return_value=True)),
            patch("src.infrastructure.policy_catalog.vector.create_embeddings", new=AsyncMock(side_effect=RuntimeError("failed"))),
        ):
            result = await enrich_incident_context(object(), keyword, search_summary="summary", search_terms=(), original_text=None, api_key="key")
        self.assertEqual(result, keyword)

    async def test_vector_results_are_added_after_keyword_results(self):
        keyword = [IncidentPromptContext("A", 2026, "a", "NAMU_WIKI", ())]
        vector = [IncidentPromptContext("B", 2026, "b", "NAMU_WIKI", ())]
        with (
            patch("src.infrastructure.policy_catalog.vector.vector_search_ready", new=AsyncMock(return_value=True)),
            patch("src.infrastructure.policy_catalog.vector.create_embeddings", new=AsyncMock(return_value=[[0.1]])),
            patch("src.infrastructure.policy_catalog.vector.search_semantic_incidents", new=AsyncMock(return_value=vector)),
        ):
            result = await enrich_incident_context(object(), keyword, search_summary="summary", search_terms=(), original_text=None, api_key="key")
        self.assertEqual(result, keyword + vector)

    async def test_semantic_sql_requires_current_model_non_null_embedding_and_threshold(self):
        class Result:
            def mappings(self):
                return self
            def all(self):
                return []
        class Session:
            bind = type("Bind", (), {"dialect": type("Dialect", (), {"name": "postgresql"})()})()
            async def execute(self, statement, params):
                self.statement = str(statement)
                self.params = params
                return Result()
        session = Session()

        self.assertEqual(await search_semantic_incidents(session, [0.1]), [])
        self.assertIn("embedding IS NOT NULL", session.statement)
        self.assertIn("embedding_model = :embedding_model", session.statement)
        self.assertIn("embedding <=> CAST(:embedding AS vector) <= :max_distance", session.statement)
        self.assertEqual(session.params["max_distance"], MAX_COSINE_DISTANCE)

    def test_threshold_is_named_and_matches_mvp_value(self):
        self.assertEqual(MAX_COSINE_DISTANCE, 0.50)