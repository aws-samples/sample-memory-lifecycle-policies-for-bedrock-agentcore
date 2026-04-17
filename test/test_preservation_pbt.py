"""
Preservation Property-Based Tests — Extended Coverage

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10**

These property-based tests capture the baseline behavior of non-buggy code paths
BEFORE the fix is applied. They MUST PASS on unfixed code (confirming the baseline)
and MUST STILL PASS after the fix (confirming no regressions).

This file extends coverage beyond code/test/test_preservation.py with additional
PBT tests for scoring formula, decay rate, determine_pass_fail, compute_quality_delta,
and static file assertions for IAM permissions, CDK infrastructure, package.json,
blog narrative, and handler behavior patterns.
"""

import json
import math
import os
import re
import sys
from datetime import datetime, timezone, timedelta

import pytest
from hypothesis import given, settings, assume, HealthCheck
import hypothesis.strategies as st

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CODE_ROOT = os.path.join(REPO_ROOT, "code")
LAMBDAS_ROOT = os.path.join(CODE_ROOT, "lambdas")

sys.path.insert(0, LAMBDAS_ROOT)

from memory_scorer.handler import compute_relevance_score
from shared.constants import decay_rate_from_prune_days

# Import regression suite helpers
sys.path.insert(0, os.path.join(CODE_ROOT, "test"))
from test_regression_suite import (
    RegressionTestCase,
    determine_pass_fail,
    compute_quality_delta,
)

# ---------------------------------------------------------------------------
# File paths
# ---------------------------------------------------------------------------
CDK_STACK_PATH = os.path.join(CODE_ROOT, "lib", "memory-lifecycle-stack.ts")
PACKAGE_JSON_PATH = os.path.join(CODE_ROOT, "package.json")
BLOG_PATH = os.path.join(REPO_ROOT, "blog.md")
MEMORY_SCORER_PATH = os.path.join(LAMBDAS_ROOT, "memory_scorer", "handler.py")
MEMORY_CONSOLIDATOR_PATH = os.path.join(LAMBDAS_ROOT, "memory_consolidator", "handler.py")
MEMORY_PRUNER_PATH = os.path.join(LAMBDAS_ROOT, "memory_pruner", "handler.py")
GDPR_DELETION_PATH = os.path.join(LAMBDAS_ROOT, "gdpr_deletion", "handler.py")


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ===================================================================
# Static File Assertions — CDK Stack IAM Permissions
# ===================================================================

class TestCDKIAMPreservation:
    """
    **Validates: Requirements 3.1, 3.2**

    Verify that IAM permissions for Memory Scorer, Memory Consolidator,
    Memory Pruner, and GDPR handler's DeleteMemoryRecord are present
    in the CDK stack.
    """

    def test_memory_scorer_iam_permissions(self):
        """
        **Validates: Requirements 3.2**

        Memory Scorer must have ListMemoryRecords and BatchUpdateMemoryRecords.
        """
        content = _read(CDK_STACK_PATH)
        scorer_match = re.search(
            r"// Memory Scorer.*?addToRolePolicy.*?actions:\s*\[(.*?)\]",
            content,
            re.DOTALL,
        )
        assert scorer_match is not None, "Memory Scorer IAM policy section not found"
        actions = scorer_match.group(1)
        assert "ListMemoryRecords" in actions, (
            "Memory Scorer IAM missing ListMemoryRecords"
        )
        assert "BatchUpdateMemoryRecords" in actions, (
            "Memory Scorer IAM missing BatchUpdateMemoryRecords"
        )

    def test_memory_consolidator_iam_permissions(self):
        """
        **Validates: Requirements 3.2**

        Memory Consolidator must have GetMemoryRecord, BatchCreateMemoryRecords,
        DeleteMemoryRecord, and bedrock:InvokeModel.
        """
        content = _read(CDK_STACK_PATH)

        consolidator_match = re.search(
            r"// Memory Consolidator.*?addToRolePolicy.*?actions:\s*\[(.*?)\]",
            content,
            re.DOTALL,
        )
        assert consolidator_match is not None, (
            "Memory Consolidator IAM policy section not found"
        )
        actions = consolidator_match.group(1)
        assert "GetMemoryRecord" in actions, (
            "Memory Consolidator IAM missing GetMemoryRecord"
        )
        assert "BatchCreateMemoryRecords" in actions, (
            "Memory Consolidator IAM missing BatchCreateMemoryRecords"
        )
        assert "DeleteMemoryRecord" in actions, (
            "Memory Consolidator IAM missing DeleteMemoryRecord"
        )

        # Also check bedrock:InvokeModel in a separate policy statement
        assert "bedrock:InvokeModel" in content, (
            "Memory Consolidator IAM missing bedrock:InvokeModel"
        )

    def test_memory_pruner_iam_permission(self):
        """
        **Validates: Requirements 3.2**

        Memory Pruner must have DeleteMemoryRecord.
        """
        content = _read(CDK_STACK_PATH)
        pruner_match = re.search(
            r"// Memory Pruner.*?addToRolePolicy.*?actions:\s*\[(.*?)\]",
            content,
            re.DOTALL,
        )
        assert pruner_match is not None, "Memory Pruner IAM policy section not found"
        actions = pruner_match.group(1)
        assert "DeleteMemoryRecord" in actions, (
            "Memory Pruner IAM missing DeleteMemoryRecord"
        )

    def test_gdpr_handler_delete_permission(self):
        """
        **Validates: Requirements 3.1**

        GDPR handler must have DeleteMemoryRecord permission.
        """
        content = _read(CDK_STACK_PATH)
        gdpr_match = re.search(
            r"// GDPR.*?addToRolePolicy.*?actions:\s*\[(.*?)\]",
            content,
            re.DOTALL,
        )
        assert gdpr_match is not None, "GDPR handler IAM policy section not found"
        actions = gdpr_match.group(1)
        assert "DeleteMemoryRecord" in actions, (
            "GDPR handler IAM missing DeleteMemoryRecord"
        )


# ===================================================================
# Static File Assertions — CDK Infrastructure
# ===================================================================

class TestCDKInfrastructurePreservation:
    """
    **Validates: Requirements 3.7, 3.8**

    Verify EventBridge cron schedule and Step Functions retry config
    are present in the CDK stack.
    """

    def test_eventbridge_cron_schedule(self):
        """
        **Validates: Requirements 3.8**

        EventBridge cron must be cron(0 2 * * ? *).
        """
        content = _read(CDK_STACK_PATH)
        assert "cron(0 2 * * ? *)" in content, (
            "EventBridge cron schedule 'cron(0 2 * * ? *)' not found in CDK stack"
        )

    def test_step_functions_retry_config(self):
        """
        **Validates: Requirements 3.7**

        Step Functions retry config must have maxAttempts: 2, interval: 5,
        and backoffRate: 2.0.
        """
        content = _read(CDK_STACK_PATH)
        assert "maxAttempts: 2" in content, (
            "Step Functions retry config missing maxAttempts: 2"
        )
        assert re.search(r"interval:\s*cdk\.Duration\.seconds\(5\)", content), (
            "Step Functions retry config missing interval of 5 seconds"
        )
        assert "backoffRate: 2.0" in content, (
            "Step Functions retry config missing backoffRate: 2.0"
        )


# ===================================================================
# Static File Assertions — package.json
# ===================================================================

class TestPackageJsonPreservation:
    """
    **Validates: Requirements 3.10**

    Verify aws-cdk-lib version ^2.249.0 is unchanged in package.json.
    """

    def test_aws_cdk_lib_version_unchanged(self):
        """
        **Validates: Requirements 3.10**

        aws-cdk-lib version must be ^2.249.0.
        """
        content = _read(PACKAGE_JSON_PATH)
        pkg = json.loads(content)
        deps = pkg.get("dependencies", {})
        cdk_lib_version = deps.get("aws-cdk-lib", "")
        assert cdk_lib_version == "^2.249.0", (
            f"aws-cdk-lib version is '{cdk_lib_version}', expected '^2.249.0'"
        )


# ===================================================================
# Static File Assertions — Blog Narrative
# ===================================================================

class TestBlogNarrativePreservation:
    """
    **Validates: Requirements 3.9**

    Verify blog.md contains key phrases about memory lifecycle,
    relevance decay, and GDPR.
    """

    def test_blog_key_phrases_preserved(self):
        """
        **Validates: Requirements 3.9**

        Blog must contain 'memory lifecycle', 'relevance decay', and 'GDPR'.
        """
        content = _read(BLOG_PATH)
        assert "memory lifecycle" in content.lower(), (
            "Blog missing key phrase: 'memory lifecycle'"
        )
        assert "relevance" in content.lower() and "decay" in content.lower(), (
            "Blog missing key phrases about 'relevance decay'"
        )
        assert "GDPR" in content, (
            "Blog missing key phrase: 'GDPR'"
        )


# ===================================================================
# PBT — compute_relevance_score
# ===================================================================

class TestComputeRelevanceScorePBT:
    """
    **Validates: Requirements 3.4**

    Property-based test: for all valid inputs, compute_relevance_score
    returns a value in [0.0, 1.0] that matches the expected formula
    0.5 * exp(-decay_rate * d1) + 0.5 * exp(-decay_rate * d2).
    """

    @given(
        decay_rate=st.floats(min_value=0.001, max_value=2.0),
        days_created=st.integers(min_value=0, max_value=1000),
        days_accessed=st.integers(min_value=0, max_value=1000),
    )
    @settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
    def test_score_in_range_and_matches_formula(
        self, decay_rate, days_created, days_accessed
    ):
        """
        **Validates: Requirements 3.4**

        Property: For all valid (decay_rate, days_created, days_accessed),
        score is in [0.0, 1.0] and equals
        0.5 * exp(-decay_rate * d1) + 0.5 * exp(-decay_rate * d2).
        """
        now = datetime(2025, 7, 1, 0, 0, 0, tzinfo=timezone.utc)
        created_at = now - timedelta(days=days_created)
        last_accessed_at = now - timedelta(days=days_accessed)

        score = compute_relevance_score(created_at, last_accessed_at, decay_rate, now)

        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0, f"Score {score} out of [0.0, 1.0]"

        expected = (
            0.5 * math.exp(-decay_rate * days_created)
            + 0.5 * math.exp(-decay_rate * days_accessed)
        )
        assert abs(score - expected) < 1e-9, (
            f"Score {score} != expected {expected} for "
            f"decay_rate={decay_rate}, d1={days_created}, d2={days_accessed}"
        )


# ===================================================================
# PBT — decay_rate_from_prune_days
# ===================================================================

class TestDecayRateFromPruneDaysPBT:
    """
    **Validates: Requirements 3.4**

    Property-based test: for all valid (prune_days, threshold),
    decay_rate_from_prune_days returns -ln(threshold) / prune_days.
    """

    @given(
        prune_days=st.integers(min_value=1, max_value=365),
        threshold=st.floats(min_value=0.01, max_value=0.99),
    )
    @settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
    def test_decay_rate_matches_formula(self, prune_days, threshold):
        """
        **Validates: Requirements 3.4**

        Property: For all valid (prune_days, threshold),
        result equals -ln(threshold) / prune_days.
        """
        result = decay_rate_from_prune_days(prune_days, threshold)
        expected = -math.log(threshold) / prune_days
        assert abs(result - expected) < 1e-9, (
            f"decay_rate {result} != expected {expected} for "
            f"prune_days={prune_days}, threshold={threshold}"
        )


# ===================================================================
# PBT — determine_pass_fail
# ===================================================================

class TestDeterminePassFailPBT:
    """
    **Validates: Requirements 3.3**

    Property-based test: for all RegressionTestCase instances,
    determine_pass_fail logic is correct.
    """

    @given(
        min_quality_score=st.floats(min_value=0.0, max_value=1.0),
        post_lifecycle_score=st.one_of(
            st.none(),
            st.floats(min_value=0.0, max_value=1.0),
        ),
        baseline_score=st.one_of(
            st.none(),
            st.floats(min_value=0.0, max_value=1.0),
        ),
    )
    @settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
    def test_pass_fail_logic(
        self, min_quality_score, post_lifecycle_score, baseline_score
    ):
        """
        **Validates: Requirements 3.3**

        Property: For all RegressionTestCase instances:
        - If post_lifecycle_score is None, passed is None
        - If post_lifecycle_score >= min_quality_score, passed is True
        - If post_lifecycle_score < min_quality_score, passed is False
        """
        # Filter out NaN values which break comparison logic
        assume(not math.isnan(min_quality_score))
        if post_lifecycle_score is not None:
            assume(not math.isnan(post_lifecycle_score))
        if baseline_score is not None:
            assume(not math.isnan(baseline_score))

        tc = RegressionTestCase(
            question="Test question",
            expected_criteria="Test criteria",
            min_quality_score=min_quality_score,
            baseline_score=baseline_score,
            post_lifecycle_score=post_lifecycle_score,
        )

        result = determine_pass_fail(tc)

        if post_lifecycle_score is None:
            assert result.passed is None
        elif post_lifecycle_score >= min_quality_score:
            assert result.passed is True
        else:
            assert result.passed is False


# ===================================================================
# PBT — compute_quality_delta
# ===================================================================

class TestComputeQualityDeltaPBT:
    """
    **Validates: Requirements 3.3**

    Property-based test: for all score pairs,
    compute_quality_delta equals post - baseline.
    """

    @given(
        baseline_score=st.one_of(
            st.none(),
            st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
        ),
        post_lifecycle_score=st.one_of(
            st.none(),
            st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
        ),
    )
    @settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
    def test_quality_delta_computation(
        self, baseline_score, post_lifecycle_score
    ):
        """
        **Validates: Requirements 3.3**

        Property: For all score pairs:
        - If both scores present, delta = post - baseline
        - If either score is None, delta is None
        """
        tc = RegressionTestCase(
            question="Test question",
            expected_criteria="Test criteria",
            min_quality_score=0.5,
            baseline_score=baseline_score,
            post_lifecycle_score=post_lifecycle_score,
        )

        delta = compute_quality_delta(tc)

        if baseline_score is not None and post_lifecycle_score is not None:
            assert delta is not None
            expected_delta = post_lifecycle_score - baseline_score
            assert abs(delta - expected_delta) < 1e-9, (
                f"Delta {delta} != expected {expected_delta}"
            )
        else:
            assert delta is None


# ===================================================================
# Handler Behavior Assertions — Structured Logging
# ===================================================================

class TestHandlerStructuredLogging:
    """
    **Validates: Requirements 3.5, 3.6**

    All handlers must have structured logging with json.dumps
    containing "action" and "error" keys.
    """

    @pytest.mark.parametrize("handler_path", [
        MEMORY_SCORER_PATH,
        MEMORY_CONSOLIDATOR_PATH,
        MEMORY_PRUNER_PATH,
        GDPR_DELETION_PATH,
    ])
    def test_handler_has_structured_logging(self, handler_path):
        """
        **Validates: Requirements 3.5, 3.6**

        Each handler must use json.dumps with "action" and "error" keys.
        """
        content = _read(handler_path)
        rel = os.path.relpath(handler_path, REPO_ROOT)

        assert "json.dumps(" in content, (
            f"{rel}: Missing json.dumps( for structured logging"
        )
        assert '"action"' in content, (
            f"{rel}: Missing '\"action\"' key in structured logs"
        )
        assert '"error"' in content, (
            f"{rel}: Missing '\"error\"' key in structured logs"
        )


# ===================================================================
# Handler Behavior — Consolidator 4-Step Flow Markers
# ===================================================================

class TestConsolidator4StepFlow:
    """
    **Validates: Requirements 3.5**

    Memory Consolidator must have 4-step flow markers:
    retrieve, invoke Bedrock, store consolidated, delete originals.
    """

    def test_consolidator_has_4_step_markers(self):
        """
        **Validates: Requirements 3.5**

        The consolidator must contain step markers for its 4-step flow.
        """
        content = _read(MEMORY_CONSOLIDATOR_PATH)

        assert "Step 1" in content, "Consolidator missing Step 1 marker"
        assert "Step 2" in content, "Consolidator missing Step 2 marker"
        assert "Step 3" in content, "Consolidator missing Step 3 marker"
        assert "Step 4" in content, "Consolidator missing Step 4 marker"


# ===================================================================
# Handler Behavior — No-Short-Circuit Pattern
# ===================================================================

class TestNoShortCircuitPattern:
    """
    **Validates: Requirements 3.6**

    Memory Pruner and GDPR handler must have no-short-circuit pattern:
    try/except inside a for loop (continues on individual failures).
    """

    def test_pruner_no_short_circuit(self):
        """
        **Validates: Requirements 3.6**

        Pruner must have try/except inside a for loop.
        """
        content = _read(MEMORY_PRUNER_PATH)

        # Check for the pattern: for ... : ... try: ... except
        assert re.search(r"for\s+\w+\s+in\s+.*?:\s*\n.*?try:", content, re.DOTALL), (
            "Pruner missing no-short-circuit pattern (try inside for loop)"
        )
        assert "failed_memory_ids" in content, (
            "Pruner missing failed_memory_ids tracking"
        )

    def test_gdpr_handler_no_short_circuit(self):
        """
        **Validates: Requirements 3.6**

        GDPR handler must have try/except inside a for loop.
        """
        content = _read(GDPR_DELETION_PATH)

        assert re.search(r"for\s+\w+\s+in\s+.*?:\s*\n.*?try:", content, re.DOTALL), (
            "GDPR handler missing no-short-circuit pattern (try inside for loop)"
        )
        assert "failed_memory_ids" in content, (
            "GDPR handler missing failed_memory_ids tracking"
        )
