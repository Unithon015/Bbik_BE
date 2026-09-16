from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID, uuid4


class AssetType(StrEnum):
    VIDEO = "VIDEO"
    IMAGE = "IMAGE"


class AnalysisStatus(StrEnum):
    QUEUED = "QUEUED"
    ANALYZING = "ANALYZING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ReviewPriority(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class EvidenceLayer(StrEnum):
    RULE = "RULE"
    MEMORY = "MEMORY"
    NOW = "NOW"


class FindingStatus(StrEnum):
    PENDING = "PENDING"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"


@dataclass(frozen=True)
class StoredAsset:
    original_filename: str
    mime_type: str
    byte_size: int
    storage_key: str
    content_type: AssetType
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class FindingEvidence:
    layer: EvidenceLayer
    title: str
    source_url: str
    excerpt: str | None = None
    provider: str | None = None
    id: UUID = field(default_factory=uuid4)


@dataclass(frozen=True)
class ReviewFinding:
    category_code: str
    priority: ReviewPriority
    signal_type: str
    reason: str
    excerpt: str | None = None
    asset_id: UUID | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    evidences: list[FindingEvidence] = field(default_factory=list)
    media_types: list[str] = field(default_factory=list)
    status: FindingStatus = FindingStatus.PENDING
    confidence_score: float = 0.0
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class AnalysisRun:
    status: AnalysisStatus = AnalysisStatus.QUEUED
    current_step: str | None = "QUEUED"
    progress_percent: int = 0
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    findings: list[ReviewFinding] = field(default_factory=list)
    review_context_snapshot: dict[str, object] = field(default_factory=dict)
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class ContentSubmission:
    title: str
    caption_text: str | None
    assets: list[StoredAsset]
    analysis_runs: list[AnalysisRun]
    owner_id: UUID | None = None
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def latest_analysis(self) -> AnalysisRun:
        return max(self.analysis_runs, key=lambda run: run.created_at)

    @property
    def status(self) -> AnalysisStatus:
        return self.latest_analysis.status
