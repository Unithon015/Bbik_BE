from uuid import UUID

from src.application.content.analysis_service import ContentAnalysisService
from src.application.content.service import ContentStorage
from src.application.content.review_context import ReviewContext, snapshot_for_audit
from src.domain.content.entity import AssetType
from src.infrastructure.audience_profile.pg_repository import PostgresAudienceProfileRepository
from src.infrastructure.content.pg_repository import PostgresContentSubmissionRepository
from src.infrastructure.openai.analyzer import analyze_general, analyze_references
from src.infrastructure.policy_catalog.vector import enrich_incident_context
from src.infrastructure.policy_catalog.context import (
    PolicyPromptContext,
    search_relevant_reference_context,
)
from src.infrastructure.review_context.resolver import DatabaseReviewContextResolver


async def run_analysis(
    submission_id: UUID,
    *,
    api_key: str,
    storage: ContentStorage,
) -> None:
    from src.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        repo = PostgresContentSubmissionRepository(db)
        service = ContentAnalysisService(repo)
        try:
            await service.start(submission_id, step="GENERAL_REVIEW")
            submission = await repo.find_by_id(submission_id)
            assert submission

            images = await _read_images(submission.assets, storage)
            audience_profile = None
            if submission.owner_id:
                audience_profile = await PostgresAudienceProfileRepository(db).find_by_user_id(
                    submission.owner_id
                )
            review_context = await DatabaseReviewContextResolver(db).resolve(audience_profile)
            audit_snapshot = snapshot_for_audit(audience_profile, review_context)
            general_result = await analyze_general(
                text=submission.caption_text or None,
                images=images if images else None,
                audience_profile=audience_profile,
                review_context=review_context,
                api_key=api_key,
            )
            if general_result.title:
                await repo.update_title(submission_id, general_result.title)
            findings = list(general_result.findings)
            try:
                await service.report_progress(
                    submission_id,
                    step="REFERENCE_SEARCH",
                    progress_percent=55,
                )
                query_text = general_result.retrieval_query(submission.caption_text)
                matched_policy_context, incident_context = await search_relevant_reference_context(
                    db,
                    query_text,
                    incident_limit=3,
                )
                policy_context = _merge_policy_context(
                    _profile_policy_context(review_context),
                    matched_policy_context,
                )
                keyword_incidents = incident_context
                try:
                    incident_context = await enrich_incident_context(
                        db,
                        keyword_incidents,
                        search_summary=general_result.search_summary,
                        search_terms=general_result.search_terms,
                        original_text=submission.caption_text,
                        api_key=api_key,
                    )
                except Exception:
                    incident_context = keyword_incidents
                if policy_context or incident_context:
                    await service.report_progress(
                        submission_id,
                        step="REFERENCE_REVIEW",
                        progress_percent=75,
                    )
                    findings = await analyze_references(
                        text=submission.caption_text or None,
                        images=images if images else None,
                        api_key=api_key,
                        provisional_findings=general_result.findings,
                        policy_context=policy_context,
                        incident_context=incident_context,
                        audience_profile=audience_profile,
                        review_context=review_context,
                    )
            except Exception:
                findings = list(general_result.findings)

            await service.complete(
                submission_id,
                findings=findings,
                review_context_snapshot=audit_snapshot,
            )
        except Exception as exc:
            await service.fail(submission_id, message=str(exc))


async def _read_images(assets, storage: ContentStorage) -> list[tuple[bytes, str]]:
    result = []
    for asset in assets:
        if asset.content_type != AssetType.IMAGE:
            continue
        try:
            data = await storage.read_bytes(asset.storage_key)
            result.append((data, asset.mime_type))
        except Exception:
            pass
    return result


def _profile_policy_context(review_context: ReviewContext) -> list[PolicyPromptContext]:
    return [
        PolicyPromptContext(
            policy_code=item.policy_code,
            title=item.title,
            review_category=item.review_category,
            source_url=item.source_url,
            policy_summary=item.policy_summary,
            detection_hints=item.detection_hints,
            applicable_media_types=item.applicable_media_types,
        )
        for item in review_context.policy_candidates
    ]


def _merge_policy_context(
    preferred: list[PolicyPromptContext],
    matched: list[PolicyPromptContext],
    limit: int = 6,
) -> list[PolicyPromptContext]:
    merged: dict[str, PolicyPromptContext] = {}
    for item in [*preferred, *matched]:
        merged.setdefault(item.policy_code, item)
    return list(merged.values())[:limit]
