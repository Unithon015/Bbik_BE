from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.application.incident_index.scheduler import (
    build_daily_2026_scheduler,
    should_start_daily_sync,
    should_start_initial_sync,
    sync_initial_years_if_needed,
)
from src.interface.auth.router import router as auth_router
from src.interface.audience_profile.router import router as audience_profile_router
from src.interface.content.router import router as content_router
from src.database import engine
from src import config
from src.infrastructure.db import models  # noqa: F401 - registers application models
from src.infrastructure.db.base import Base as ApplicationBase
from src.infrastructure.db.migrations import upgrade_application_schema
from src.infrastructure.persistence.models import Base as IncidentIndexBase
from src.infrastructure.persistence.incident_index_migrations import upgrade_incident_index_schema
from src.infrastructure.persistence.policy_catalog_migrations import upgrade_policy_catalog_schema
from src.infrastructure.persistence.policy_catalog_repository import SqlAlchemyPolicyCatalogRepository
from src.infrastructure.persistence.database import build_session_factory
from src.infrastructure.policy_catalog.meta_community_standards import META_COMMUNITY_STANDARDS
from src.interface.namu_wiki.router import router as namu_wiki_router
from src.interface.user.router import router as user_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    config.validate_security_configuration()
    async with engine.begin() as connection:
        await connection.run_sync(ApplicationBase.metadata.create_all)
        await connection.run_sync(upgrade_application_schema)
        await connection.run_sync(IncidentIndexBase.metadata.create_all)
        await connection.run_sync(upgrade_incident_index_schema)
        await connection.run_sync(upgrade_policy_catalog_schema)

    policy_session_factory = build_session_factory()
    with policy_session_factory() as session:
        SqlAlchemyPolicyCatalogRepository().upsert_many(
            session, provider="META_COMMUNITY_STANDARDS", entries=META_COMMUNITY_STANDARDS
        )
        session.commit()

    if should_start_initial_sync():
        try:
            summaries = await sync_initial_years_if_needed()
            if summaries:
                logger.info(
                    "Completed initial Namu Wiki incident sync for years: %s",
                    ", ".join(str(summary.incident_year) for summary in summaries),
                )
        except Exception:
            logger.exception("Initial Namu Wiki incident sync failed; it will retry on restart.")

    scheduler = None
    if should_start_daily_sync():
        scheduler = build_daily_2026_scheduler()
        scheduler.start()
    try:
        yield
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)

app = FastAPI(title="baekend", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ALLOW_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(user_router)
app.include_router(audience_profile_router)
app.include_router(auth_router)
app.include_router(content_router)
app.include_router(namu_wiki_router)


@app.get("/health")
def health():
    return {"status": "ok"}
