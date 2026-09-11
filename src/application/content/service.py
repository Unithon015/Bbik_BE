from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from src import config
from src.domain.content.entity import (
    AnalysisRun,
    AssetType,
    ContentSubmission,
    FindingStatus,
    StoredAsset,
)
from src.domain.content.repository import ContentSubmissionRepository


class ContentSubmissionValidationError(ValueError):
    pass


class ContentSubmissionNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class UploadPayload:
    filename: str
    mime_type: str
    content: bytes


class ContentStorage:
    async def store(self, submission_id: UUID, payload: UploadPayload) -> str:
        raise NotImplementedError

    async def delete(self, storage_key: str) -> None:
        raise NotImplementedError

    async def read_bytes(self, storage_key: str) -> bytes:
        raise NotImplementedError

    async def get_download_url(self, storage_key: str) -> str | None:
        return None


class ContentSubmissionService:
    _allowed_mime_types = {
        "video/mp4": AssetType.VIDEO,
        "image/jpeg": AssetType.IMAGE,
        "image/png": AssetType.IMAGE,
        "image/webp": AssetType.IMAGE,
    }

    def __init__(self, repository: ContentSubmissionRepository, storage: ContentStorage):
        self._repository = repository
        self._storage = storage

    async def create(
        self,
        *,
        title: str | None,
        caption_text: str | None,
        files: list[UploadPayload],
        owner_id: UUID | None = None,
    ) -> ContentSubmission:
        clean_caption = (caption_text or "").strip() or None
        self._validate(files=files, caption_text=clean_caption)

        submission = ContentSubmission(
            title=self._resolve_title(title, clean_caption, files),
            caption_text=clean_caption,
            assets=[],
            analysis_runs=[AnalysisRun()],
            owner_id=owner_id,
        )

        stored_keys: list[str] = []
        try:
            for payload in files:
                storage_key = await self._storage.store(submission.id, payload)
                stored_keys.append(storage_key)
                submission.assets.append(
                    StoredAsset(
                        original_filename=payload.filename,
                        mime_type=payload.mime_type,
                        byte_size=len(payload.content),
                        storage_key=storage_key,
                        content_type=self._allowed_mime_types[payload.mime_type],
                    )
                )
            return await self._repository.save(submission)
        except Exception:
            for storage_key in stored_keys:
                await self._storage.delete(storage_key)
            raise

    async def get(self, submission_id: UUID) -> ContentSubmission:
        submission = await self._repository.find_by_id(submission_id)
        if not submission:
            raise ContentSubmissionNotFoundError
        return submission

    async def list_recent(self, limit: int = 20) -> list[ContentSubmission]:
        return await self._repository.list_recent(min(max(limit, 1), 50))

    async def list_by_owner(self, owner_id: UUID, limit: int = 10, page: int = 1) -> list[ContentSubmission]:
        safe_limit = min(max(limit, 1), 50)
        offset = (max(page, 1) - 1) * safe_limit
        return await self._repository.list_by_owner(owner_id, safe_limit, offset)

    async def update_finding_status(
        self, submission_id: UUID, finding_id: UUID, status: FindingStatus
    ) -> None:
        submission = await self._repository.find_by_id(submission_id)
        if not submission:
            raise ContentSubmissionNotFoundError
        exists = any(
            f.id == finding_id
            for run in submission.analysis_runs
            for f in run.findings
        )
        if not exists:
            raise LookupError("Finding not found")
        await self._repository.update_finding_status(finding_id, status)

    def _validate(self, *, files: list[UploadPayload], caption_text: str | None) -> None:
        if not files and not caption_text:
            raise ContentSubmissionValidationError("검수할 텍스트 또는 파일을 하나 이상 입력해 주세요.")
        if len(files) > config.MAX_UPLOAD_FILES:
            raise ContentSubmissionValidationError(
                f"파일은 한 번에 최대 {config.MAX_UPLOAD_FILES}개까지 업로드할 수 있습니다."
            )
        if caption_text and len(caption_text) > 10_000:
            raise ContentSubmissionValidationError("텍스트는 10,000자 이하로 입력해 주세요.")

        total_bytes = 0
        for payload in files:
            if not payload.filename:
                raise ContentSubmissionValidationError("파일 이름을 확인할 수 없습니다.")
            if payload.mime_type not in self._allowed_mime_types:
                raise ContentSubmissionValidationError(
                    "MP4 영상 또는 JPG, PNG, WEBP 이미지 파일만 업로드할 수 있습니다."
                )
            if not self._matches_declared_mime_type(payload):
                raise ContentSubmissionValidationError(
                    "The uploaded file does not match its declared media type."
                )
            file_size = len(payload.content)
            if file_size == 0:
                raise ContentSubmissionValidationError("빈 파일은 업로드할 수 없습니다.")
            if file_size > config.MAX_UPLOAD_FILE_BYTES:
                raise ContentSubmissionValidationError("파일 하나의 크기는 30MB 이하여야 합니다.")
            total_bytes += file_size

        if total_bytes > config.MAX_UPLOAD_TOTAL_BYTES:
            raise ContentSubmissionValidationError("한 번의 총 업로드 크기는 50MB 이하여야 합니다.")

    @staticmethod
    def _matches_declared_mime_type(payload: UploadPayload) -> bool:
        content = payload.content
        signatures = {
            "image/jpeg": lambda: content.startswith(b"\xff\xd8\xff"),
            "image/png": lambda: content.startswith(b"\x89PNG\r\n\x1a\n"),
            "image/webp": lambda: len(content) >= 12
            and content.startswith(b"RIFF")
            and content[8:12] == b"WEBP",
            "video/mp4": lambda: len(content) >= 12 and content[4:8] == b"ftyp",
        }
        return signatures[payload.mime_type]()

    @staticmethod
    def _resolve_title(
        requested_title: str | None, caption_text: str | None, files: list[UploadPayload]
    ) -> str:
        if requested_title and requested_title.strip():
            return requested_title.strip()[:120]
        if files:
            return Path(files[0].filename).stem[:120] or "콘텐츠 검수 요청"
        assert caption_text
        return caption_text.replace("\n", " ")[:40] or "텍스트 검수 요청"
