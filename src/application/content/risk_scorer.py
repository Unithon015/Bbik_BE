from dataclasses import replace

from src.domain.content.entity import ReviewFinding, ReviewPriority

_PRIORITY_BASE: dict[ReviewPriority, float] = {
    ReviewPriority.HIGH: 0.80,
    ReviewPriority.MEDIUM: 0.50,
    ReviewPriority.LOW: 0.25,
}

_CATEGORY_BOOST: dict[str, float] = {
    "R-03": 0.08,  # 혐오·차별
    "R-04": 0.08,  # 욕설·성적
    "R-07": 0.12,  # 개인정보 (preprocessor 생성분은 이미 1.0)
    "R-06": 0.06,  # 사건·재난
    "R-05": 0.04,  # 사실관계
}

_EVIDENCE_BOOST_PER = 0.10
_EVIDENCE_BOOST_MAX = 0.30

# 이 임계값 미만인 finding만 reference review로 재검증
RECHECK_THRESHOLD = 0.65


def score_findings(findings: list[ReviewFinding]) -> list[ReviewFinding]:
    """Rule-based confidence score 산정. 이미 confidence_score가 있으면 그대로."""
    return [
        f if f.confidence_score > 0.0 else replace(f, confidence_score=_score(f))
        for f in findings
    ]


def split_by_confidence(
    findings: list[ReviewFinding],
    threshold: float = RECHECK_THRESHOLD,
) -> tuple[list[ReviewFinding], list[ReviewFinding]]:
    high = [f for f in findings if f.confidence_score >= threshold]
    low = [f for f in findings if f.confidence_score < threshold]
    return high, low


def _score(finding: ReviewFinding) -> float:
    score = _PRIORITY_BASE.get(finding.priority, 0.25)
    score += _CATEGORY_BOOST.get(finding.category_code, 0.0)
    score += min(len(finding.evidences) * _EVIDENCE_BOOST_PER, _EVIDENCE_BOOST_MAX)
    return min(round(score, 3), 1.0)
