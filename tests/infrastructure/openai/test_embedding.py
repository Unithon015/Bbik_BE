import unittest
from unittest.mock import AsyncMock, patch

from src.infrastructure.openai.embedding import (
    INCIDENT_EMBEDDING_DIMENSIONS,
    INCIDENT_EMBEDDING_MODEL,
    build_incident_embedding_text,
    build_incident_query_text,
    create_embeddings,
)


class EmbeddingTest(unittest.IsolatedAsyncioTestCase):
    def test_incident_embedding_text_uses_catalog_fields_deterministically(self):
        self.assertEqual(
            build_incident_embedding_text("사건 A", ["키워드", "별칭"], ["R-06"]),
            "제목: 사건 A\n키워드: 키워드, 별칭\n위험 분류: R-06",
        )

    def test_query_prefers_general_summary_and_terms(self):
        self.assertEqual(
            build_incident_query_text("요약", ("용어 A", "용어 B"), "긴 원문"),
            "요약\n용어: 용어 A, 용어 B",
        )

    def test_query_uses_truncated_original_only_when_general_context_is_empty(self):
        self.assertEqual(build_incident_query_text("", (), "원문" * 1000), ("원문" * 1000)[:1000])

    async def test_embedding_wrapper_uses_required_model_and_dimensions(self):
        response = type("Response", (), {"data": [type("Data", (), {"embedding": [0.1]})()]})()
        with patch("src.infrastructure.openai.embedding.AsyncOpenAI") as client_type:
            client_type.return_value.embeddings.create = AsyncMock(return_value=response)
            result = await create_embeddings(["query"], api_key="test-key")
        self.assertEqual(result, [[0.1]])
        client_type.return_value.embeddings.create.assert_awaited_once_with(
            model=INCIDENT_EMBEDDING_MODEL,
            input=["query"],
            dimensions=INCIDENT_EMBEDDING_DIMENSIONS,
            encoding_format="float",
        )