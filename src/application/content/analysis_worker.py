import time
from uuid import UUID

from src.application.content.analysis_service import ContentAnalysisService
from src.application.content.preprocessor import extract_image_pii, preprocess_text
from src.application.content.risk_scorer import RECHECK_THRESHOLD, score_findings, split_by_confidence
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
        t_start = time.monotonic()
        try:
            await service.start(submission_id, step="PREPROCESSING")
            submission = await repo.find_by_id(submission_id)
            assert submission

            # ① 자체 전처리: PII 탐지·마스킹
            preprocess_result = preprocess_text(submission.caption_text)
            text_for_gpt = preprocess_result.masked_text.strip() or None

            images = await _read_images(submission.assets, storage)
            audience_profile = None
            if submission.owner_id:
                audience_profile = await PostgresAudienceProfileRepository(db).find_by_user_id(
                    submission.owner_id
                )
            review_context = await DatabaseReviewContextResolver(db).resolve(audience_profile)
            audit_snapshot = snapshot_for_audit(audience_profile, review_context)

            # ② GPT 정형 분석 (마스킹된 텍스트 사용)
            await service.report_progress(submission_id, step="GENERAL_REVIEW", progress_percent=15)
            t_preprocess_done = time.monotonic()

            general_result = await analyze_general(
                text=text_for_gpt,
                images=images if images else None,
                audience_profile=audience_profile,
                review_context=review_context,
                api_key=api_key,
            )
            if general_result.title:
                await repo.update_title(submission_id, general_result.title)
            t_general_done = time.monotonic()

            # 이미지 PII: GPT OCR 결과(search_terms, image excerpts)에 regex 재적용
            image_pii_findings: list = []
            if images:
                image_excerpts = [
                    f.excerpt for f in general_result.findings
                    if "image" in (f.media_types or []) and f.excerpt
                ]
                image_pii_findings = extract_image_pii(
                    general_result.search_summary,
                    general_result.search_terms,
                    image_excerpts,
                )

            # ④ 자체 위험도 산정
            scored_gpt = score_findings(list(general_result.findings))
            high_conf, low_conf = split_by_confidence(scored_gpt)
            pii_findings = preprocess_result.pii_findings
            t_scoring_done = time.monotonic()

            # ③ pgvector 검색 + ⑤ 저신뢰 finding 재검증
            ref_ms = 0
            try:
                await service.report_progress(
                    submission_id, step="REFERENCE_SEARCH", progress_percent=55
                )
                query_text = general_result.retrieval_query(submission.caption_text)
                matched_policy_context, incident_context = await search_relevant_reference_context(
                    db, query_text, incident_limit=3,
                )
                policy_context = _merge_policy_context(
                    _profile_policy_context(review_context), matched_policy_context
                )
                try:
                    incident_context = await enrich_incident_context(
                        db,
                        incident_context,
                        search_summary=general_result.search_summary,
                        search_terms=general_result.search_terms,
                        original_text=submission.caption_text,
                        api_key=api_key,
                    )
                except Exception:
                    pass

                if scored_gpt and (policy_context or incident_context):
                    await service.report_progress(
                        submission_id, step="REFERENCE_REVIEW", progress_percent=75
                    )
                    t_ref_start = time.monotonic()
                    refined_all = await analyze_references(
                        text=text_for_gpt,
                        images=images if images else None,
                        api_key=api_key,
                        provisional_findings=scored_gpt,
                        policy_context=policy_context,
                        incident_context=incident_context,
                        audience_profile=audience_profile,
                        review_context=review_context,
                    )
                    ref_ms = int((time.monotonic() - t_ref_start) * 1000)
                    high_conf, low_conf = split_by_confidence(refined_all)
            except Exception:
                pass

            total_ms = int((time.monotonic() - t_start) * 1000)
            findings = pii_findings + image_pii_findings + high_conf + low_conf

            await service.complete(
                submission_id,
                findings=findings,
                review_context_snapshot={
                    **audit_snapshot,
                    "phase_timings_ms": {
                        "preprocessing": preprocess_result.elapsed_ms,
                        "general_review": int((t_general_done - t_preprocess_done) * 1000),
                        "risk_scoring": int((t_scoring_done - t_general_done) * 1000),
                        "reference_review": ref_ms,
                        "total": total_ms,
                    },
                    "preprocessing": {
                        "text_pii_counts": preprocess_result.pii_counts,
                        "text_pii_total": sum(preprocess_result.pii_counts.values()),
                        "image_pii_total": len(image_pii_findings),
                    },
                    "scoring": {
                        "high_confidence": len(high_conf),
                        "low_confidence": len(low_conf),
                        "threshold": RECHECK_THRESHOLD,
                        "pii_auto_detected": len(pii_findings) + len(image_pii_findings),
                    },
                },
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
