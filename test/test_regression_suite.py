"""Memory Regression Test Suite.

Validates agent answer quality is maintained after memory pruning and consolidation.
Uses AgentCore Evaluations to compute quality scores before and after lifecycle runs.

Requirements: 9.1, 9.2, 9.3, 9.4
"""

import logging
from dataclasses import dataclass
from typing import Optional

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class RegressionTestCase:
    """A single regression test case for memory quality validation."""

    question: str
    expected_criteria: str
    min_quality_score: float
    baseline_score: Optional[float] = None
    post_lifecycle_score: Optional[float] = None
    passed: Optional[bool] = None


# ---------------------------------------------------------------------------
# JSON fixtures – predefined test cases
# ---------------------------------------------------------------------------

DEFAULT_TEST_FIXTURES: list[dict] = [
    {
        "question": "What are the user's preferred programming languages?",
        "expected_criteria": "Response mentions specific languages previously discussed with the user",
        "min_quality_score": 0.7,
    },
    {
        "question": "Summarize the last project we worked on together.",
        "expected_criteria": "Response includes project name, key milestones, and outcome",
        "min_quality_score": 0.6,
    },
    {
        "question": "What deployment configuration does the user prefer?",
        "expected_criteria": "Response references specific cloud provider, region, or infrastructure preferences",
        "min_quality_score": 0.65,
    },
    {
        "question": "What recurring issues has the user reported?",
        "expected_criteria": "Response lists previously reported issues with context",
        "min_quality_score": 0.5,
    },
]


# ---------------------------------------------------------------------------
# Helper: load test cases from fixtures
# ---------------------------------------------------------------------------

def load_test_cases(fixtures: list[dict] | None = None) -> list[RegressionTestCase]:
    """Parse JSON fixture dicts into RegressionTestCase instances.

    Args:
        fixtures: List of fixture dicts.  Falls back to DEFAULT_TEST_FIXTURES.

    Returns:
        List of RegressionTestCase objects ready for execution.
    """
    raw = fixtures if fixtures is not None else DEFAULT_TEST_FIXTURES
    return [
        RegressionTestCase(
            question=tc["question"],
            expected_criteria=tc["expected_criteria"],
            min_quality_score=tc["min_quality_score"],
        )
        for tc in raw
    ]


# ---------------------------------------------------------------------------
# AgentCore Evaluations client wrapper
# ---------------------------------------------------------------------------

class AgentCoreEvaluationsClient:
    """Thin wrapper around the AgentCore Evaluations API (boto3).

    The client calls ``evaluate_response`` to obtain a quality score for a
    given agent response measured against expected criteria.
    """

    def __init__(self, region_name: str = "us-east-1"):
        self._client = boto3.client(
            "agentcore-evaluations",
            region_name=region_name,
        )

    def evaluate_response(
        self,
        agent_response: str,
        expected_criteria: str,
    ) -> float:
        """Score an agent response against expected criteria.

        Args:
            agent_response: The text response produced by the agent.
            expected_criteria: Human-readable criteria the response should satisfy.

        Returns:
            A quality score in [0.0, 1.0].
        """
        result = self._client.evaluate_response(
            agentResponse=agent_response,
            expectedCriteria=expected_criteria,
        )
        return float(result["qualityScore"])


# ---------------------------------------------------------------------------
# AgentCore runtime client wrapper (for querying the agent)
# ---------------------------------------------------------------------------

class AgentCoreRuntimeClient:
    """Thin wrapper for querying an AgentCore agent."""

    def __init__(self, agent_id: str, region_name: str = "us-east-1"):
        self.agent_id = agent_id
        self._client = boto3.client(
            "agentcore-runtime",
            region_name=region_name,
        )

    def query(self, question: str) -> str:
        """Send a question to the agent and return the text response."""
        result = self._client.invoke_agent(
            agentId=self.agent_id,
            inputText=question,
        )
        return result["outputText"]


# ---------------------------------------------------------------------------
# Core regression logic
# ---------------------------------------------------------------------------

def evaluate_test_case(
    test_case: RegressionTestCase,
    agent_response: str,
    evaluations_client: AgentCoreEvaluationsClient,
) -> float:
    """Score a single agent response for a test case.

    Returns:
        Quality score in [0.0, 1.0].
    """
    return evaluations_client.evaluate_response(
        agent_response=agent_response,
        expected_criteria=test_case.expected_criteria,
    )


def determine_pass_fail(test_case: RegressionTestCase) -> RegressionTestCase:
    """Determine pass/fail for a test case based on post-lifecycle score.

    A test case passes if and only if the post-lifecycle quality score is
    greater than or equal to the configured ``min_quality_score``.

    The quality score delta (``post_lifecycle_score - baseline_score``) is
    computed for reporting but does not directly determine pass/fail.

    Args:
        test_case: A test case with both baseline and post-lifecycle scores set.

    Returns:
        The same test case with ``passed`` populated.
    """
    if test_case.post_lifecycle_score is None:
        test_case.passed = None
        return test_case

    test_case.passed = test_case.post_lifecycle_score >= test_case.min_quality_score
    return test_case


def compute_quality_delta(test_case: RegressionTestCase) -> float | None:
    """Return the quality score delta (post - baseline), or None if unavailable."""
    if test_case.baseline_score is not None and test_case.post_lifecycle_score is not None:
        return test_case.post_lifecycle_score - test_case.baseline_score
    return None


# ---------------------------------------------------------------------------
# Regression suite runner
# ---------------------------------------------------------------------------

class RegressionSuiteRunner:
    """Orchestrates the full before/after regression test flow.

    1. Loads test case fixtures.
    2. Queries the agent with each question and records baseline quality scores.
    3. (Caller triggers the memory lifecycle run externally.)
    4. Queries the agent again and records post-lifecycle quality scores.
    5. Determines pass/fail per test case and produces a report.
    """

    def __init__(
        self,
        agent_id: str,
        fixtures: list[dict] | None = None,
        region_name: str = "us-east-1",
    ):
        self.agent_client = AgentCoreRuntimeClient(agent_id, region_name)
        self.eval_client = AgentCoreEvaluationsClient(region_name)
        self.test_cases = load_test_cases(fixtures)

    # -- Phase 1: baseline ------------------------------------------------

    def record_baseline_scores(self) -> None:
        """Query the agent before the lifecycle run and record baseline scores."""
        for tc in self.test_cases:
            try:
                response = self.agent_client.query(tc.question)
                tc.baseline_score = evaluate_test_case(tc, response, self.eval_client)
                logger.info(
                    "Baseline recorded – question=%r score=%.4f",
                    tc.question,
                    tc.baseline_score,
                )
            except Exception:
                logger.exception("Failed to record baseline for question=%r", tc.question)
                tc.baseline_score = None

    # -- Phase 2: post-lifecycle ------------------------------------------

    def record_post_lifecycle_scores(self) -> None:
        """Query the agent after the lifecycle run and record post-lifecycle scores."""
        for tc in self.test_cases:
            try:
                response = self.agent_client.query(tc.question)
                tc.post_lifecycle_score = evaluate_test_case(tc, response, self.eval_client)
                logger.info(
                    "Post-lifecycle recorded – question=%r score=%.4f",
                    tc.question,
                    tc.post_lifecycle_score,
                )
            except Exception:
                logger.exception(
                    "Failed to record post-lifecycle for question=%r", tc.question
                )
                tc.post_lifecycle_score = None

    # -- Phase 3: evaluate & report ---------------------------------------

    def evaluate_results(self) -> list[RegressionTestCase]:
        """Determine pass/fail for every test case and return them."""
        for tc in self.test_cases:
            determine_pass_fail(tc)
        return self.test_cases

    def generate_report(self) -> list[dict]:
        """Produce a JSON-serialisable report of all test case results.

        Each entry contains the question, scores, delta, and pass/fail status.
        """
        report: list[dict] = []
        for tc in self.test_cases:
            delta = compute_quality_delta(tc)
            entry = {
                "question": tc.question,
                "expected_criteria": tc.expected_criteria,
                "min_quality_score": tc.min_quality_score,
                "baseline_score": tc.baseline_score,
                "post_lifecycle_score": tc.post_lifecycle_score,
                "quality_delta": delta,
                "passed": tc.passed,
            }
            report.append(entry)
            status = "PASS" if tc.passed else "FAIL"
            delta_str = f"{delta:+.4f}" if delta is not None else "N/A"
            logger.info(
                "[%s] question=%r  min=%.2f  baseline=%s  post=%s  delta=%s",
                status,
                tc.question,
                tc.min_quality_score,
                f"{tc.baseline_score:.4f}" if tc.baseline_score is not None else "N/A",
                f"{tc.post_lifecycle_score:.4f}" if tc.post_lifecycle_score is not None else "N/A",
                delta_str,
            )
        return report

    def run_full_suite(self) -> list[dict]:
        """Execute the complete regression flow (baseline → post-lifecycle → report).

        Note: The caller is responsible for triggering the actual memory lifecycle
        run between ``record_baseline_scores`` and ``record_post_lifecycle_scores``.
        This convenience method runs both phases back-to-back, which is useful for
        integration testing where the lifecycle has already been executed.
        """
        self.record_baseline_scores()
        self.record_post_lifecycle_scores()
        self.evaluate_results()
        return self.generate_report()


# ---------------------------------------------------------------------------
# Unit tests (pytest)
# ---------------------------------------------------------------------------

import pytest


class TestLoadTestCases:
    """Tests for loading JSON fixture data into RegressionTestCase objects."""

    def test_loads_default_fixtures(self):
        cases = load_test_cases()
        assert len(cases) == len(DEFAULT_TEST_FIXTURES)
        for case, fixture in zip(cases, DEFAULT_TEST_FIXTURES):
            assert case.question == fixture["question"]
            assert case.expected_criteria == fixture["expected_criteria"]
            assert case.min_quality_score == fixture["min_quality_score"]
            assert case.baseline_score is None
            assert case.post_lifecycle_score is None
            assert case.passed is None

    def test_loads_custom_fixtures(self):
        custom = [
            {"question": "Q1", "expected_criteria": "C1", "min_quality_score": 0.8}
        ]
        cases = load_test_cases(custom)
        assert len(cases) == 1
        assert cases[0].question == "Q1"
        assert cases[0].min_quality_score == 0.8

    def test_empty_fixtures(self):
        cases = load_test_cases([])
        assert cases == []


class TestDeterminePassFail:
    """Tests for the pass/fail determination logic."""

    def test_passes_when_score_meets_threshold(self):
        tc = RegressionTestCase(
            question="Q", expected_criteria="C", min_quality_score=0.7,
            baseline_score=0.8, post_lifecycle_score=0.75,
        )
        result = determine_pass_fail(tc)
        assert result.passed is True

    def test_passes_when_score_equals_threshold(self):
        tc = RegressionTestCase(
            question="Q", expected_criteria="C", min_quality_score=0.7,
            baseline_score=0.8, post_lifecycle_score=0.7,
        )
        result = determine_pass_fail(tc)
        assert result.passed is True

    def test_fails_when_score_below_threshold(self):
        tc = RegressionTestCase(
            question="Q", expected_criteria="C", min_quality_score=0.7,
            baseline_score=0.8, post_lifecycle_score=0.5,
        )
        result = determine_pass_fail(tc)
        assert result.passed is False

    def test_none_when_post_score_missing(self):
        tc = RegressionTestCase(
            question="Q", expected_criteria="C", min_quality_score=0.7,
            baseline_score=0.8, post_lifecycle_score=None,
        )
        result = determine_pass_fail(tc)
        assert result.passed is None


class TestComputeQualityDelta:
    """Tests for quality score delta computation."""

    def test_positive_delta(self):
        tc = RegressionTestCase(
            question="Q", expected_criteria="C", min_quality_score=0.5,
            baseline_score=0.6, post_lifecycle_score=0.8,
        )
        delta = compute_quality_delta(tc)
        assert delta is not None
        assert abs(delta - 0.2) < 1e-9

    def test_negative_delta(self):
        tc = RegressionTestCase(
            question="Q", expected_criteria="C", min_quality_score=0.5,
            baseline_score=0.8, post_lifecycle_score=0.5,
        )
        delta = compute_quality_delta(tc)
        assert delta is not None
        assert abs(delta - (-0.3)) < 1e-9

    def test_zero_delta(self):
        tc = RegressionTestCase(
            question="Q", expected_criteria="C", min_quality_score=0.5,
            baseline_score=0.7, post_lifecycle_score=0.7,
        )
        delta = compute_quality_delta(tc)
        assert delta is not None
        assert abs(delta) < 1e-9

    def test_none_when_baseline_missing(self):
        tc = RegressionTestCase(
            question="Q", expected_criteria="C", min_quality_score=0.5,
            baseline_score=None, post_lifecycle_score=0.7,
        )
        assert compute_quality_delta(tc) is None

    def test_none_when_post_score_missing(self):
        tc = RegressionTestCase(
            question="Q", expected_criteria="C", min_quality_score=0.5,
            baseline_score=0.7, post_lifecycle_score=None,
        )
        assert compute_quality_delta(tc) is None


class TestGenerateReport:
    """Tests for the report generation via RegressionSuiteRunner (mocked clients)."""

    def _make_runner_with_scored_cases(self, cases: list[RegressionTestCase]):
        """Create a runner and inject pre-scored test cases."""
        runner = object.__new__(RegressionSuiteRunner)
        runner.test_cases = cases
        return runner

    def test_report_structure(self):
        cases = [
            RegressionTestCase(
                question="Q1", expected_criteria="C1", min_quality_score=0.6,
                baseline_score=0.8, post_lifecycle_score=0.75, passed=True,
            ),
            RegressionTestCase(
                question="Q2", expected_criteria="C2", min_quality_score=0.7,
                baseline_score=0.9, post_lifecycle_score=0.5, passed=False,
            ),
        ]
        runner = self._make_runner_with_scored_cases(cases)
        report = runner.generate_report()

        assert len(report) == 2
        assert report[0]["question"] == "Q1"
        assert report[0]["passed"] is True
        assert report[0]["quality_delta"] is not None
        assert abs(report[0]["quality_delta"] - (-0.05)) < 1e-9

        assert report[1]["question"] == "Q2"
        assert report[1]["passed"] is False
        assert abs(report[1]["quality_delta"] - (-0.4)) < 1e-9

    def test_report_with_missing_scores(self):
        cases = [
            RegressionTestCase(
                question="Q1", expected_criteria="C1", min_quality_score=0.6,
                baseline_score=None, post_lifecycle_score=None, passed=None,
            ),
        ]
        runner = self._make_runner_with_scored_cases(cases)
        report = runner.generate_report()

        assert len(report) == 1
        assert report[0]["quality_delta"] is None
        assert report[0]["passed"] is None
