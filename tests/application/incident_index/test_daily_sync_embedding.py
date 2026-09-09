import unittest
from unittest.mock import AsyncMock, patch

from src.application.incident_index.scheduler import build_daily_2026_scheduler


class FakeSyncService:
    def __init__(self):
        self.received_years = None

    async def sync_years(self, years):
        self.received_years = years
        return []


class DailySyncEmbeddingTest(unittest.IsolatedAsyncioTestCase):
    async def test_daily_sync_backfills_missing_embeddings_after_sync(self):
        service = FakeSyncService()
        scheduler = build_daily_2026_scheduler(lambda: service)
        job = scheduler.get_job("sync-namu-wiki-incidents-2026")
        self.assertIsNotNone(job)

        with patch(
            "src.application.incident_index.scheduler.backfill_missing_incident_embeddings",
            new=AsyncMock(return_value=3),
        ) as backfill:
            await job.func()

        self.assertEqual(service.received_years, [2026])
        backfill.assert_awaited_once_with()
