import re
import time
from dataclasses import dataclass

from src.domain.content.entity import ReviewFinding, ReviewPriority

_PII_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("phone", re.compile(r"(?<!\d)01[016789]-?\d{3,4}-?\d{4}(?!\d)")),
    ("email", re.compile(r"[\w.+\-]+@[\w\-]+\.[\w.\-]+")),
    ("rrn", re.compile(r"(?<!\d)\d{6}-[1-4]\d{6}(?!\d)")),
    ("card", re.compile(r"(?<!\d)\d{4}[\s\-]\d{4}[\s\-]\d{4}[\s\-]\d{4}(?!\d)")),
    ("account", re.compile(r"(?<!\d)\d{3,6}-\d{2,6}-\d{4,8}(?!\d)")),
]

_PII_SIGNAL = {
    "phone": "개인 전화번호",
    "email": "개인 이메일 주소",
    "rrn": "주민등록번호",
    "card": "카드번호",
    "account": "계좌번호",
}


@dataclass(frozen=True)
class PreprocessResult:
    masked_text: str
    pii_findings: list[ReviewFinding]
    pii_counts: dict[str, int]
    elapsed_ms: int


def preprocess_text(text: str | None) -> PreprocessResult:
    t0 = time.monotonic()
    if not text:
        return PreprocessResult(masked_text="", pii_findings=[], pii_counts={}, elapsed_ms=0)

    masked = text
    counts: dict[str, int] = {}
    for pii_type, pattern in _PII_PATTERNS:
        matches = pattern.findall(masked)
        if matches:
            counts[pii_type] = len(matches)
            masked = pattern.sub(f"[{pii_type.upper()}]", masked)

    findings = [
        ReviewFinding(
            category_code="R-07",
            priority=ReviewPriority.HIGH,
            signal_type=_PII_SIGNAL[pii_type],
            reason=(
                f"텍스트에서 {count}건의 {_PII_SIGNAL[pii_type]}가 감지되어 마스킹 처리되었습니다. "
                "게시 전 포함 여부를 확인하세요."
            ),
            excerpt=None,
            confidence_score=1.0,
        )
        for pii_type, count in counts.items()
    ]

    return PreprocessResult(
        masked_text=masked,
        pii_findings=findings,
        pii_counts=counts,
        elapsed_ms=int((time.monotonic() - t0) * 1000),
    )


def extract_image_pii(
    search_summary: str,
    search_terms: tuple[str, ...],
    image_excerpts: list[str],
) -> list[ReviewFinding]:
    """GPT가 이미지에서 OCR한 텍스트(search_terms, excerpts)에 PII regex 적용.

    추가 API 호출 없이 기존 GPT 응답을 재활용한다.
    텍스트 전처리(confidence=1.0)보다 낮은 0.85 — GPT OCR 범위에 의존하기 때문.
    """
    combined = " ".join([search_summary, *search_terms, *image_excerpts])
    if not combined.strip():
        return []

    counts: dict[str, int] = {}
    for pii_type, pattern in _PII_PATTERNS:
        matches = pattern.findall(combined)
        if matches:
            counts[pii_type] = len(matches)

    return [
        ReviewFinding(
            category_code="R-07",
            priority=ReviewPriority.HIGH,
            signal_type=f"이미지 내 {_PII_SIGNAL[pii_type]}",
            reason=(
                f"이미지에서 {count}건의 {_PII_SIGNAL[pii_type]}가 감지되었습니다. "
                "게시 전 제거 여부를 확인하세요."
            ),
            excerpt=None,
            media_types=["image"],
            confidence_score=0.85,
        )
        for pii_type, count in counts.items()
    ]
