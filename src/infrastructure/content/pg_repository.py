from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.domain.content.entity import (
    AnalysisRun,
    AnalysisStatus,
    AssetType,
    ContentSubmission,
    EvidenceLayer,
    FindingEvidence,
    FindingStatus,
    ReviewFinding,
    ReviewPriority,
    StoredAsset,
)
from src.domain.content.repository import ContentSubmissionRepository
from src.infrastructure.db.models import (
    AnalysisRunModel,
    ContentAssetModel,
    ContentSubmissionModel,
    FindingEvidenceModel,
    ReviewFindingModel,
)


class PostgresContentSubmissionRepository(ContentSubmissionRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def save(self, submission: ContentSubmission) -> ContentSubmission:
        model = ContentSubmissionModel(
            id=submission.id,
            owner_id=submission.owner_id,
            title=submission.title,
            caption_text=submission.caption_text,
            status=submission.status.value,
            created_at=submission.created_at,
            updated_at=submission.updated_at,
        )
        self._session.add(model)

        for asset in submission.assets:
            self._session.add(
                ContentAssetModel(
                    id=asset.id,
                    submission_id=submission.id,
                    content_type=asset.content_type.value,
                    original_filename=asset.original_filename,
                    mime_type=asset.mime_type,
                    byte_size=asset.byte_size,
                    storage_key=asset.storage_key,
                    created_at=asset.created_at,
                )
            )

        for analysis_run in submission.analysis_runs:
            self._add_analysis_run(submission.id, analysis_run)

        await self._session.commit()
        return await self._get_required(submission.id)

    async def find_by_id(self, submission_id: UUID) -> ContentSubmission | None:
        result = await self._session.execute(self._base_query().where(ContentSubmissionModel.id == submission_id))
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def list_recent(self, limit: int) -> list[ContentSubmission]:
        result = await self._session.execute(
            self._base_query()
            .order_by(ContentSubmissionModel.created_at.desc())
            .limit(limit)
        )
        return [self._to_entity(model) for model in result.scalars().all()]

    async def list_by_owner(self, owner_id: UUID, limit: int, offset: int = 0) -> list[ContentSubmission]:
        result = await self._session.execute(
            self._base_query()
            .where(ContentSubmissionModel.owner_id == owner_id)
            .order_by(ContentSubmissionModel.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return [self._to_entity(model) for model in result.scalars().all()]

    async def update_analysis_run(self, analysis_run: AnalysisRun) -> AnalysisRun:
        result = await self._session.execute(
            select(AnalysisRunModel)
            .options(
                selectinload(AnalysisRunModel.submission),
                selectinload(AnalysisRunModel.findings).selectinload(ReviewFindingModel.evidences),
            )
            .where(AnalysisRunModel.id == analysis_run.id)
        )
        model = result.scalar_one_or_none()
        if not model:
            raise LookupError("Analysis run not found")

        model.status = analysis_run.status.value
        model.current_step = analysis_run.current_step
        model.progress_percent = analysis_run.progress_percent
        model.error_message = analysis_run.error_message
        model.started_at = analysis_run.started_at
        model.completed_at = analysis_run.completed_at
        model.review_context_snapshot = analysis_run.review_context_snapshot
        model.submission.status = analysis_run.status.value
        model.submission.updated_at = datetime.now(timezone.utc)
        model.findings.clear()
        for finding in analysis_run.findings:
            self._add_finding(analysis_run.id, finding)

        await self._session.commit()
        return analysis_run

    def _add_analysis_run(self, submission_id: UUID, analysis_run: AnalysisRun) -> None:
        self._session.add(
            AnalysisRunModel(
                id=analysis_run.id,
                submission_id=submission_id,
                status=analysis_run.status.value,
                current_step=analysis_run.current_step,
                progress_percent=analysis_run.progress_percent,
                error_message=analysis_run.error_message,
                started_at=analysis_run.started_at,
                completed_at=analysis_run.completed_at,
                review_context_snapshot=analysis_run.review_context_snapshot,
                created_at=analysis_run.created_at,
            )
        )
        for finding in analysis_run.findings:
            self._add_finding(analysis_run.id, finding)

    async def update_title(self, submission_id: UUID, title: str) -> None:
        result = await self._session.execute(
            select(ContentSubmissionModel).where(ContentSubmissionModel.id == submission_id)
        )
        model = result.scalar_one_or_none()
        if model:
            model.title = title[:120]
            await self._session.commit()

    async def update_finding_status(self, finding_id: UUID, status: FindingStatus) -> None:
        result = await self._session.execute(
            select(ReviewFindingModel).where(ReviewFindingModel.id == finding_id)
        )
        model = result.scalar_one_or_none()
        if not model:
            raise LookupError("Finding not found")
        model.status = status.value
        await self._session.commit()

    def _add_finding(self, analysis_run_id: UUID, finding: ReviewFinding) -> None:
        self._session.add(
            ReviewFindingModel(
                id=finding.id,
                analysis_run_id=analysis_run_id,
                asset_id=finding.asset_id,
                category_code=finding.category_code,
                priority=finding.priority.value,
                status=finding.status.value,
                signal_type=finding.signal_type,
                excerpt=finding.excerpt,
                reason=finding.reason,
                start_ms=finding.start_ms,
                end_ms=finding.end_ms,
                media_types=finding.media_types,
                created_at=finding.created_at,
            )
        )
        for evidence in finding.evidences[:3]:
            self._session.add(
                FindingEvidenceModel(
                    id=evidence.id,
                    finding_id=finding.id,
                    layer=evidence.layer.value,
                    title=evidence.title,
                    source_url=evidence.source_url,
                    excerpt=evidence.excerpt,
                    provider=evidence.provider,
                )
            )

    async def _get_required(self, submission_id: UUID) -> ContentSubmission:
        result = await self._session.execute(self._base_query().where(ContentSubmissionModel.id == submission_id))
        return self._to_entity(result.scalar_one())

    @staticmethod
    def _base_query():
        return select(ContentSubmissionModel).options(
            selectinload(ContentSubmissionModel.assets),
            selectinload(ContentSubmissionModel.analysis_runs)
            .selectinload(AnalysisRunModel.findings)
            .selectinload(ReviewFindingModel.evidences),
        )

    @staticmethod
    def _to_entity(model: ContentSubmissionModel) -> ContentSubmission:
        assets = [
            StoredAsset(
                id=asset.id,
                original_filename=asset.original_filename,
                mime_type=asset.mime_type,
                byte_size=asset.byte_size,
                storage_key=asset.storage_key,
                content_type=AssetType(asset.content_type),
                created_at=asset.created_at,
            )
            for asset in model.assets
        ]
        analysis_runs = [
            AnalysisRun(
                id=analysis_run.id,
                status=AnalysisStatus(analysis_run.status),
                current_step=analysis_run.current_step,
                progress_percent=analysis_run.progress_percent,
                error_message=analysis_run.error_message,
                started_at=analysis_run.started_at,
                completed_at=analysis_run.completed_at,
                review_context_snapshot=analysis_run.review_context_snapshot or {},
                created_at=analysis_run.created_at,
                findings=[
                    ReviewFinding(
                        id=finding.id,
                        category_code=finding.category_code,
                        priority=ReviewPriority(finding.priority),
                        status=FindingStatus(finding.status),
                        signal_type=finding.signal_type,
                        reason=finding.reason,
                        excerpt=finding.excerpt,
                        asset_id=finding.asset_id,
                        start_ms=finding.start_ms,
                        end_ms=finding.end_ms,
                        media_types=finding.media_types or [],
                        created_at=finding.created_at,
                        evidences=[
                            FindingEvidence(
                                id=evidence.id,
                                layer=EvidenceLayer(evidence.layer),
                                title=evidence.title,
                                source_url=evidence.source_url,
                                excerpt=evidence.excerpt,
                                provider=evidence.provider,
                            )
                            for evidence in finding.evidences
                        ],
                    )
                    for finding in analysis_run.findings
                ],
            )
            for analysis_run in model.analysis_runs
        ]
        return ContentSubmission(
            id=model.id,
            owner_id=model.owner_id,
            title=model.title,
            caption_text=model.caption_text,
            assets=assets,
            analysis_runs=analysis_runs,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )
