import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from src.application.content.analysis_worker import run_analysis
from src.application.content.review_context import ReviewContext
from src.domain.content.entity import AnalysisRun, ContentSubmission, ReviewFinding, ReviewPriority
from src.infrastructure.openai.analyzer import GeneralAnalysisResult
from src.infrastructure.policy_catalog.context import IncidentPromptContext, PolicyPromptContext


class _AsyncSessionContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class ContextWorkerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.general_finding = ReviewFinding(
            category_code="R-04", priority=ReviewPriority.HIGH,
            signal_type="직접 공격 표현", reason="직접적인 공격 표현입니다.",
            excerpt="공격 표현", media_types=["text"],
        )
        self.submission = ContentSubmission(
            title="검수 요청", caption_text="테스트 사건을 언급한 공격 표현",
            assets=[], analysis_runs=[AnalysisRun()],
        )
        self.general_result = GeneralAnalysisResult(
            findings=[self.general_finding], search_summary="테스트 사건",
            search_terms=("테스트 사건",), title=None,
        )
        self.policy = PolicyPromptContext(
            policy_code="META_TEST", title="테스트 정책", review_category="TEST_CATEGORY",
            source_url="https://example.com/policy", policy_summary="테스트 요약",
            detection_hints=("테스트 신호",), applicable_media_types=("text",),
        )
        self.incident = IncidentPromptContext(
            title="테스트 사건", year=2026, source_url="https://namu.wiki/w/test",
            source_type="NAMU_WIKI", risk_categories=(),
        )

    async def _run_worker(
        self, *, search_result=None, search_error=None, context_result=None,
        context_error=None, general_error=None,
    ):
        repo = MagicMock()
        repo.find_by_id = AsyncMock(return_value=self.submission)
        repo.update_title = AsyncMock()
        service = MagicMock()
        service.start = AsyncMock()
        service.report_progress = AsyncMock()
        service.complete = AsyncMock()
        service.fail = AsyncMock()
        resolver = MagicMock()
        resolver.return_value.resolve = AsyncMock(return_value=ReviewContext())
        general = AsyncMock(return_value=self.general_result)
        if general_error is not None:
            general.side_effect = general_error
        search = AsyncMock(return_value=search_result)
        if search_error is not None:
            search.side_effect = search_error
        context = AsyncMock(return_value=context_result)
        if context_error is not None:
            context.side_effect = context_error

        with (
            patch("src.database.AsyncSessionLocal", new=lambda: _AsyncSessionContext()),
            patch("src.application.content.analysis_worker.PostgresContentSubmissionRepository", return_value=repo),
            patch("src.application.content.analysis_worker.ContentAnalysisService", return_value=service),
            patch("src.application.content.analysis_worker.DatabaseReviewContextResolver", resolver),
            patch("src.application.content.analysis_worker.analyze_general", general),
            patch("src.application.content.analysis_worker.search_relevant_reference_context", search),
            patch("src.application.content.analysis_worker.analyze_references", context),
        ):
            await run_analysis(self.submission.id, api_key="test-key", storage=MagicMock())

        return service, search, context

    async def test_general_findings_continue_to_context_review_and_pass_matched_incidents(self) -> None:
        final_finding = ReviewFinding(
            category_code="R-04", priority=ReviewPriority.MEDIUM,
            signal_type="맥락 반영 공격 표현", reason="맥락을 반영했습니다.",
        )
        service, search, context = await self._run_worker(
            search_result=([self.policy], [self.incident]), context_result=[final_finding]
        )

        search.assert_awaited_once()
        self.assertEqual(search.await_args.kwargs["incident_limit"], 3)
        self.assertEqual(context.await_args.kwargs["provisional_findings"], [self.general_finding])
        self.assertEqual(context.await_args.kwargs["incident_context"], [self.incident])
        self.assertEqual(service.complete.await_args.kwargs["findings"], [final_finding])

    async def test_incident_only_candidate_triggers_context_review(self) -> None:
        service, _, context = await self._run_worker(
            search_result=([], [self.incident]),
            context_result=[self.general_finding],
        )

        context.assert_awaited_once()
        self.assertEqual(context.await_args.kwargs["policy_context"], [])
        self.assertEqual(context.await_args.kwargs["incident_context"], [self.incident])
        self.assertEqual(service.complete.await_args.kwargs["findings"], [self.general_finding])
    async def test_uses_general_findings_without_context_call_when_no_candidates_match(self) -> None:
        service, search, context = await self._run_worker(search_result=([], []), context_result=[])

        search.assert_awaited_once()
        self.assertEqual(search.await_args.kwargs["incident_limit"], 3)
        context.assert_not_awaited()
        self.assertEqual(service.complete.await_args.kwargs["findings"], [self.general_finding])

    async def test_falls_back_to_general_findings_when_context_retrieval_fails(self) -> None:
        service, search, context = await self._run_worker(search_error=RuntimeError("retrieval unavailable"))

        search.assert_awaited_once()
        self.assertEqual(search.await_args.kwargs["incident_limit"], 3)
        context.assert_not_awaited()
        self.assertEqual(service.complete.await_args.kwargs["findings"], [self.general_finding])
        service.fail.assert_not_awaited()

    async def test_falls_back_to_general_findings_when_context_review_fails(self) -> None:
        service, search, context = await self._run_worker(
            search_result=([self.policy], [self.incident]), context_error=RuntimeError("context unavailable")
        )

        search.assert_awaited_once()
        context.assert_awaited_once()
        self.assertEqual(context.await_args.kwargs["provisional_findings"], [self.general_finding])
        self.assertEqual(service.complete.await_args.kwargs["findings"], [self.general_finding])
        service.fail.assert_not_awaited()

    async def test_vector_failure_keeps_keyword_incident_for_context_review(self) -> None:
        with patch(
            "src.application.content.analysis_worker.enrich_incident_context",
            new=AsyncMock(side_effect=RuntimeError("vector unavailable")),
        ) as enrich:
            service, _, context = await self._run_worker(
                search_result=([], [self.incident]),
                context_result=[self.general_finding],
            )

        enrich.assert_awaited_once()
        context.assert_awaited_once()
        self.assertEqual(context.await_args.kwargs["incident_context"], [self.incident])
        self.assertEqual(service.complete.await_args.kwargs["findings"], [self.general_finding])
        service.fail.assert_not_awaited()
    async def test_marks_analysis_failed_when_general_review_fails(self) -> None:
        service, search, context = await self._run_worker(general_error=RuntimeError("general unavailable"))

        search.assert_not_awaited()
        context.assert_not_awaited()
        service.complete.assert_not_awaited()
        service.fail.assert_awaited_once()
