import unittest
from unittest.mock import AsyncMock, patch

from src.application.incident_index import embedding_backfill


class FakeEngine:
    def __init__(self):
        self.disposed = False

    def dispose(self):
        self.disposed = True


class IncidentEmbeddingBackfillTest(unittest.IsolatedAsyncioTestCase):
    async def test_processes_missing_rows_until_none_remain(self):
        engine = FakeEngine()
        batches = [
            [
                {"id": 1, "title": "A", "match_keywords": ["a"], "risk_categories": []},
                {"id": 2, "title": "B", "match_keywords": ["b"], "risk_categories": ["R-03"]},
            ],
            [],
        ]
        persisted = []

        def fetch_batch(_engine, batch_size):
            self.assertEqual(batch_size, 64)
            return batches.pop(0)

        def persist_batch(_engine, rows, embeddings):
            persisted.append((rows, embeddings))

        with (
            patch.object(embedding_backfill, "build_engine", return_value=engine),
            patch.object(embedding_backfill, "_schema_ready", return_value=True),
            patch.object(embedding_backfill, "_fetch_missing_batch", side_effect=fetch_batch),
            patch.object(embedding_backfill, "_persist_batch", side_effect=persist_batch),
            patch.object(
                embedding_backfill,
                "create_embeddings",
                new=AsyncMock(return_value=[[0.1, 0.2], [0.3, 0.4]]),
            ) as create_embeddings,
        ):
            updated = await embedding_backfill.backfill_missing_incident_embeddings(api_key="key")

        self.assertEqual(updated, 2)
        self.assertEqual(len(persisted), 1)
        self.assertTrue(engine.disposed)
        create_embeddings.assert_awaited_once()

    async def test_skips_cleanly_when_vector_schema_is_not_ready(self):
        engine = FakeEngine()

        with (
            patch.object(embedding_backfill, "build_engine", return_value=engine),
            patch.object(embedding_backfill, "_schema_ready", return_value=False),
            patch.object(embedding_backfill, "_fetch_missing_batch") as fetch_batch,
        ):
            updated = await embedding_backfill.backfill_missing_incident_embeddings(api_key="key")

        self.assertEqual(updated, 0)
        fetch_batch.assert_not_called()
        self.assertTrue(engine.disposed)
