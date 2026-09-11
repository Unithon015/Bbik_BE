import unittest
from unittest.mock import AsyncMock, patch

from src.domain.content.entity import EvidenceLayer, ReviewFinding, ReviewPriority
from src.infrastructure.openai.analyzer import analyze_references
from src.infrastructure.policy_catalog.context import IncidentPromptContext, PolicyPromptContext


class ContextReviewTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.policy = PolicyPromptContext(
            policy_code="META_TEST", title="테스트 정책", review_category="TEST_CATEGORY",
            source_url="https://example.com/policy", policy_summary="테스트 요약",
            detection_hints=("테스트 신호",), applicable_media_types=("text",),
        )
        self.incident = IncidentPromptContext(
            title="테스트 사건", year=2026, source_url="https://namu.wiki/w/test",
            source_type="NAMU_WIKI", risk_categories=(),
        )
        self.slur_finding = ReviewFinding(
            category_code="R-04", priority=ReviewPriority.HIGH,
            signal_type="직접 공격 표현", reason="직접적인 공격 표현을 포함합니다.",
            excerpt="공격 표현", media_types=["text"],
        )
        self.privacy_finding = ReviewFinding(
            category_code="R-07", priority=ReviewPriority.HIGH,
            signal_type="개인정보 노출", reason="전화번호가 노출됩니다.",
            excerpt="010-0000-0000", media_types=["text"],
        )

    async def _review(self, provisional_findings, response):
        with patch("src.infrastructure.openai.analyzer._request_json", new=AsyncMock(return_value=response)):
            return await analyze_references(
                text="검수 대상 콘텐츠", api_key="test-key",
                provisional_findings=provisional_findings,
                policy_context=[self.policy], incident_context=[self.incident],
            )

    async def test_keeps_general_findings_when_context_actions_are_missing(self) -> None:
        final_findings = await self._review(
            [self.slur_finding, self.privacy_finding], {"reviews": [], "new_findings": []}
        )

        self.assertEqual(final_findings, [self.slur_finding, self.privacy_finding])

    async def test_revises_and_drops_only_explicitly_reviewed_general_findings(self) -> None:
        final_findings = await self._review(
            [self.slur_finding, self.privacy_finding],
            {
                "reviews": [
                    {
                        "finding_index": 0, "action": "REVISE", "reason": "인용 맥락을 반영합니다.",
                        "finding": {
                            "type": ["text"], "category_code": "R-04", "priority": "LOW",
                            "signal_type": "인용된 공격 표현", "reason": "비판을 위한 인용입니다.",
                            "excerpt": "공격 표현", "evidences": [],
                        },
                    },
                    {"finding_index": 1, "action": "DROP", "reason": "개인정보가 아닙니다."},
                ],
                "new_findings": [],
            },
        )

        self.assertEqual(len(final_findings), 1)
        self.assertEqual(final_findings[0].priority, ReviewPriority.LOW)
        self.assertEqual(final_findings[0].signal_type, "인용된 공격 표현")

    async def test_ignores_invalid_actions_and_indexes_without_removing_general_findings(self) -> None:
        final_findings = await self._review(
            [self.slur_finding, self.privacy_finding],
            {
                "reviews": [
                    {"finding_index": 10, "action": "DROP", "reason": "잘못된 인덱스"},
                    {"finding_index": 0, "action": "UNKNOWN", "reason": "잘못된 action"},
                ],
                "new_findings": [],
            },
        )

        self.assertEqual(final_findings, [self.slur_finding, self.privacy_finding])

    async def test_adds_only_context_findings_with_whitelisted_evidence(self) -> None:
        final_findings = await self._review(
            [self.slur_finding],
            {
                "reviews": [],
                "new_findings": [
                    {
                        "type": ["text"], "category_code": "R-06", "priority": "MEDIUM",
                        "signal_type": "사건 맥락", "reason": "사건 후보와 직접 관련됩니다.",
                        "excerpt": "테스트 사건",
                        "evidences": [{
                            "layer": "MEMORY", "title": "테스트 사건",
                            "source_url": "https://namu.wiki/w/test", "provider": "NAMU_WIKI",
                        }],
                    },
                    {
                        "type": ["text"], "category_code": "R-06", "priority": "MEDIUM",
                        "signal_type": "환각 근거", "reason": "제공되지 않은 근거입니다.",
                        "evidences": [{
                            "layer": "RULE", "title": "없는 정책",
                            "source_url": "https://example.com/fake", "provider": "META_COMMUNITY_STANDARDS",
                        }],
                    },
                ],
            },
        )

        self.assertEqual(len(final_findings), 2)
        self.assertEqual(final_findings[0], self.slur_finding)
        self.assertEqual(final_findings[1].evidences[0].layer, EvidenceLayer.MEMORY)
        self.assertEqual(final_findings[1].evidences[0].title, "테스트 사건")

    async def test_revised_general_finding_survives_after_hallucinated_evidence_is_removed(self) -> None:
        final_findings = await self._review(
            [self.privacy_finding],
            {
                "reviews": [{
                    "finding_index": 0, "action": "REVISE", "reason": "표현을 보완합니다.",
                    "finding": {
                        "type": ["text"], "category_code": "R-07", "priority": "MEDIUM",
                        "signal_type": "개인정보 노출", "reason": "개인정보로 재검토가 필요합니다.",
                        "excerpt": "010-0000-0000",
                        "evidences": [{
                            "layer": "RULE", "title": "없는 정책",
                            "source_url": "https://example.com/fake", "provider": "META_COMMUNITY_STANDARDS",
                        }],
                    },
                }],
                "new_findings": [],
            },
        )

        self.assertEqual(len(final_findings), 1)
        self.assertEqual(final_findings[0].category_code, "R-07")
        self.assertEqual(final_findings[0].evidences, [])
