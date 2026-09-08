import unittest
from unittest.mock import AsyncMock, patch

from src.domain.content.entity import EvidenceLayer
from src.infrastructure.openai.analyzer import _build_reference_system_prompt, analyze_references
from src.infrastructure.policy_catalog.context import IncidentPromptContext, PolicyPromptContext


class TestPolicyPrompt(unittest.IsolatedAsyncioTestCase):
    def test_includes_only_catalogued_policy_values_as_evidence_context(self) -> None:
        policy = PolicyPromptContext(
            policy_code="META_TEST",
            title="테스트 정책",
            review_category="TEST_CATEGORY",
            source_url="https://example.com/policy",
            policy_summary="테스트 요약",
            detection_hints=("테스트 신호",),
            applicable_media_types=("text",),
        )
        incident = IncidentPromptContext(
            title="테스트 사건",
            year=2026,
            source_url="https://namu.wiki/w/test",
            source_type="NAMU_WIKI",
            risk_categories=(),
        )

        prompt = _build_reference_system_prompt([policy], [incident], [])

        self.assertIn('"테스트 정책"', prompt)
        self.assertIn("https://example.com/policy", prompt)
        self.assertIn("META_COMMUNITY_STANDARDS", prompt)
        self.assertIn('"테스트 사건"', prompt)
        self.assertIn("https://namu.wiki/w/test", prompt)

    async def test_rejects_hallucinated_evidence_not_present_in_db_candidates(self) -> None:
        policy = PolicyPromptContext(
            policy_code="META_TEST",
            title="테스트 정책",
            review_category="TEST_CATEGORY",
            source_url="https://example.com/policy",
            policy_summary="테스트 요약",
            detection_hints=("테스트 신호",),
            applicable_media_types=("text",),
        )

        response = {
            "reviews": [],
            "new_findings": [{
                "type": ["text"],
                "category_code": "R-04",
                "priority": "MEDIUM",
                "signal_type": "테스트",
                "reason": "테스트",
                "evidences": [
                    {
                        "layer": "RULE",
                        "title": "테스트 정책",
                        "source_url": "https://example.com/policy",
                        "provider": "META_COMMUNITY_STANDARDS",
                    },
                    {
                        "layer": "RULE",
                        "title": "없는 정책",
                        "source_url": "https://example.com/fake",
                        "provider": "META_COMMUNITY_STANDARDS",
                    },
                ],
            }],
        }

        with patch("src.infrastructure.openai.analyzer._request_json", new=AsyncMock(return_value=response)):
            findings = await analyze_references(
                text="검수 대상 콘텐츠",
                api_key="test-key",
                provisional_findings=[],
                policy_context=[policy],
                incident_context=[],
            )

        self.assertEqual(len(findings), 1)
        self.assertEqual(len(findings[0].evidences), 1)
        evidence = findings[0].evidences[0]
        self.assertEqual(evidence.layer, EvidenceLayer.RULE)
        self.assertEqual(evidence.title, "테스트 정책")
        self.assertEqual(evidence.source_url, "https://example.com/policy")
        self.assertEqual(evidence.provider, "META_COMMUNITY_STANDARDS")