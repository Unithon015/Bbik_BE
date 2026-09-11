from __future__ import annotations

import os
from collections.abc import Callable
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from src.infrastructure.persistence.database import build_session_factory
from src.domain.incident_index.entity import IncidentSyncSummary

from .embedding_backfill import backfill_missing_incident_embeddings
from .service import SyncNamuWikiIncidentIndexService

SEOUL = ZoneInfo("Asia/Seoul")
INITIAL_SYNC_YEARS = (2024, 2025, 2026)


def should_start_daily_sync() -> bool:
    enabled = os.getenv("NAMU_WIKI_2026_SYNC_ENABLED", "true").lower()
    return enabled in {"1", "true", "yes"} and bool(os.getenv("DATABASE_URL"))


def should_start_initial_sync() -> bool:
    enabled = os.getenv("NAMU_WIKI_INITIAL_SYNC_ENABLED", "true").lower()
    return enabled in {"1", "true", "yes"} and bool(os.getenv("DATABASE_URL"))


async def sync_initial_years_if_needed(
    service_factory: Callable[[], SyncNamuWikiIncidentIndexService] | None = None,
) -> list[IncidentSyncSummary]:
    """Synchronize 2024--2026 once when the application first starts."""
    factory = service_factory or (lambda: SyncNamuWikiIncidentIndexService(build_session_factory()))
    return await factory().sync_initial_years_if_needed(list(INITIAL_SYNC_YEARS))


def build_daily_2026_scheduler(
    service_factory: Callable[[], SyncNamuWikiIncidentIndexService] | None = None,
) -> AsyncIOScheduler:
    factory = service_factory or (lambda: SyncNamuWikiIncidentIndexService(build_session_factory()))
    scheduler = AsyncIOScheduler(timezone=SEOUL)

    async def sync_current_year() -> None:
        await factory().sync_years([2026])
        await backfill_missing_incident_embeddings()

    scheduler.add_job(
        sync_current_year,
        trigger=CronTrigger(hour=0, minute=0, timezone=SEOUL),
        id="sync-namu-wiki-incidents-2026",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    return scheduler
