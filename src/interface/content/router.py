from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import RedirectResponse
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession

from src import config
from src.application.content.dto import (
    AnalysisStatusResponse,
    ContentAssetResponse,
    ContentSubmissionResponse,
    FindingEvidenceResponse,
    FindingStatusUpdateRequest,
    MySubmissionItemResponse,
    MySubmissionListResponse,
    RecentContentResponse,
    ReviewFindingResponse,
)
from src.application.content.service import (
    ContentSubmissionNotFoundError,
    ContentSubmissionService,
    ContentSubmissionValidationError,
    ContentStorage,
    UploadPayload,
)
from src.domain.content.entity import FindingStatus
from src.application.content.analysis_worker import run_analysis
from src.database import get_db
from src.domain.content.entity import ContentSubmission
from src.infrastructure.content.pg_repository import PostgresContentSubmissionRepository
from src.interface.deps import get_current_user_id

router = APIRouter(prefix="/contents", tags=["contents"])


def _storage() -> ContentStorage:
    # Lazy import keeps local/unit-test environments independent from boto3.
    from src.infrastructure.content.s3_storage import S3ContentStorage

    return S3ContentStorage(
        bucket=config.S3_BUCKET_NAME,
        region=config.AWS_REGION,
        access_key=config.AWS_ACCESS_KEY_ID,
        secret_key=config.AWS_SECRET_ACCESS_KEY,
    )


def _service(db: AsyncSession = Depends(get_db)) -> ContentSubmissionService:
    return ContentSubmissionService(PostgresContentSubmissionRepository(db), _storage())


@router.post("", response_model=ContentSubmissionResponse, status_code=201)
async def create_content(
    background_tasks: BackgroundTasks,
    file: Annotated[UploadFile | None, File(description="이미지 파일 (선택)")] = None,
    files: Annotated[list[UploadFile] | None, File()] = None,
    text: Annotated[str | None, Form()] = None,
    current_user_id: UUID = Depends(get_current_user_id),
    service: ContentSubmissionService = Depends(_service),
    storage: ContentStorage = Depends(_storage),
):
    if not config.OPEN_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="Content analysis is temporarily unavailable",
        )
    uploads = [item for item in [file, *(files or [])] if item and item.filename]
    payloads = await _read_uploads(uploads)
    try:
        submission = await service.create(
            title=None,
            caption_text=text,
            files=payloads,
            owner_id=current_user_id,
        )
    except ContentSubmissionValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    background_tasks.add_task(
        run_analysis,
        submission.id,
        api_key=config.OPEN_API_KEY,
        storage=storage,
    )

    return _submission_response(submission)


@router.get("/me", response_model=MySubmissionListResponse)
async def list_my_contents(
    page: Annotated[int | None, Query(ge=1)] = None,
    service: ContentSubmissionService = Depends(_service),
    owner_id: UUID = Depends(get_current_user_id),
):
    submissions = await service.list_by_owner(owner_id, limit=10, page=page or 1)
    items = []
    for submission in submissions:
        run = submission.latest_analysis
        pending_count = sum(
            1 for f in run.findings
            if f.status == FindingStatus.PENDING
        )
        items.append(MySubmissionItemResponse(
            id=submission.id,
            title=submission.title,
            status=submission.status,
            pending_findings_count=pending_count,
            completed_at=run.completed_at,
            created_at=submission.created_at,
        ))
    return MySubmissionListResponse(items=items)


@router.get("", response_model=RecentContentResponse)
async def list_recent_contents(
    page: Annotated[int | None, Query(ge=1)] = None,
    current_user_id: UUID = Depends(get_current_user_id),
    service: ContentSubmissionService = Depends(_service),
):
    submissions = await service.list_by_owner(current_user_id, limit=10, page=page or 1)
    return RecentContentResponse(items=[_submission_response(submission) for submission in submissions])


@router.get("/{submission_id}", response_model=ContentSubmissionResponse)
async def get_content(
    submission_id: UUID,
    current_user_id: UUID = Depends(get_current_user_id),
    service: ContentSubmissionService = Depends(_service),
):
    return _submission_response(await _get_submission(service, submission_id, current_user_id))


@router.get("/{submission_id}/analysis", response_model=AnalysisStatusResponse)
async def get_analysis_status(
    submission_id: UUID,
    current_user_id: UUID = Depends(get_current_user_id),
    service: ContentSubmissionService = Depends(_service),
):
    submission = await _get_submission(service, submission_id, current_user_id)
    run = submission.latest_analysis

    content_types: list[str] = []
    if submission.caption_text:
        content_types.append("text")
    for asset in submission.assets:
        label = asset.content_type.value.lower()  # "image" or "video"
        if label not in content_types:
            content_types.append(label)

    return AnalysisStatusResponse(
        id=run.id,
        type=content_types,
        status=run.status,
        current_step=run.current_step,
        progress_percent=run.progress_percent,
        error_message=run.error_message,
        started_at=run.started_at,
        completed_at=run.completed_at,
        findings=[
            ReviewFindingResponse(
                id=finding.id,
                type=finding.media_types[0] if finding.media_types else "text",
                category_code=finding.category_code,
                priority=finding.priority,
                status=finding.status,
                signal_type=finding.signal_type,
                reason=finding.reason,
                excerpt=finding.excerpt,
                asset_id=finding.asset_id,
                start_ms=finding.start_ms,
                end_ms=finding.end_ms,
                evidences=[
                    FindingEvidenceResponse(
                        id=evidence.id,
                        layer=evidence.layer,
                        title=evidence.title,
                        source_url=evidence.source_url,
                        excerpt=evidence.excerpt,
                        provider=evidence.provider,
                    )
                    for evidence in finding.evidences[:3]
                ],
            )
            for finding in run.findings
            if finding.status != FindingStatus.DISMISSED
        ],
    )


@router.patch("/{submission_id}/findings/{finding_id}", status_code=204)
async def update_finding_status(
    submission_id: UUID,
    finding_id: UUID,
    body: FindingStatusUpdateRequest,
    service: ContentSubmissionService = Depends(_service),
    owner_id: UUID = Depends(get_current_user_id),
):
    submission = await _get_submission(service, submission_id, owner_id)
    if submission.owner_id != owner_id:
        raise HTTPException(status_code=403, detail="접근 권한이 없습니다.")
    try:
        await service.update_finding_status(submission_id, finding_id, body.status)
    except LookupError:
        raise HTTPException(status_code=404, detail="Finding not found")


@router.get("/{submission_id}/assets/{asset_id}")
async def download_asset(
    submission_id: UUID,
    asset_id: UUID,
    token: str = Query(),
    service: ContentSubmissionService = Depends(_service),
    storage: ContentStorage = Depends(_storage),
):
    _verify_asset_download_token(token, submission_id, asset_id)
    submission = await _get_submission_by_id(service, submission_id)
    asset = next((asset for asset in submission.assets if asset.id == asset_id), None)
    if not asset:
        raise HTTPException(status_code=404, detail="Content asset not found")

    url = await storage.get_download_url(asset.storage_key)
    if not url:
        raise HTTPException(status_code=410, detail="The original content is no longer available")
    return RedirectResponse(url)


async def _get_submission(
    service: ContentSubmissionService, submission_id: UUID, owner_id: UUID
) -> ContentSubmission:
    submission = await _get_submission_by_id(service, submission_id)
    if submission.owner_id != owner_id:
        raise HTTPException(status_code=404, detail="Content submission not found")
    return submission


async def _get_submission_by_id(
    service: ContentSubmissionService, submission_id: UUID
) -> ContentSubmission:
    try:
        return await service.get(submission_id)
    except ContentSubmissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Content submission not found") from exc


def _submission_response(submission: ContentSubmission) -> ContentSubmissionResponse:
    content_types: list[str] = []
    if submission.caption_text:
        content_types.append("text")
    for asset in submission.assets:
        label = asset.content_type.value.lower()
        if label not in content_types:
            content_types.append(label)

    return ContentSubmissionResponse(
        id=submission.id,
        title=submission.title,
        caption_text=submission.caption_text,
        status=submission.status,
        type=content_types,
        created_at=submission.created_at,
        assets=[
            ContentAssetResponse(
                id=asset.id,
                content_type=asset.content_type,
                original_filename=asset.original_filename,
                mime_type=asset.mime_type,
                byte_size=asset.byte_size,
                download_url=(
                    f"/contents/{submission.id}/assets/{asset.id}"
                    f"?token={_asset_download_token(submission.id, asset.id)}"
                ),
            )
            for asset in submission.assets
        ],
    )


async def _read_uploads(uploads: list[UploadFile]) -> list[UploadPayload]:
    payloads: list[UploadPayload] = []
    total_bytes = 0
    for upload in uploads:
        content = await upload.read(config.MAX_UPLOAD_FILE_BYTES + 1)
        if len(content) > config.MAX_UPLOAD_FILE_BYTES:
            raise HTTPException(status_code=422, detail="A file exceeds the upload size limit")
        total_bytes += len(content)
        if total_bytes > config.MAX_UPLOAD_TOTAL_BYTES:
            raise HTTPException(status_code=422, detail="Total upload size exceeds the limit")
        payloads.append(
            UploadPayload(
                filename=upload.filename or "",
                mime_type=upload.content_type or "application/octet-stream",
                content=content,
            )
        )
    return payloads


def _asset_download_token(submission_id: UUID, asset_id: UUID) -> str:
    return jwt.encode(
        {
            "sub": str(submission_id),
            "asset_id": str(asset_id),
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        },
        config.JWT_SECRET,
        algorithm=config.JWT_ALGORITHM,
    )


def _verify_asset_download_token(token: str, submission_id: UUID, asset_id: UUID) -> None:
    try:
        payload = jwt.decode(token, config.JWT_SECRET, algorithms=[config.JWT_ALGORITHM])
        if payload.get("sub") != str(submission_id) or payload.get("asset_id") != str(asset_id):
            raise ValueError("Asset token target mismatch")
    except (JWTError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="Content asset not found") from exc
