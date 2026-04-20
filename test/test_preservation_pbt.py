"""
Preservation Property-Based Tests — Extended Coverage

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8**

These property-based tests capture the baseline behavior of non-buggy code paths
BEFORE the fix is applied. They MUST PASS on unfixed code (confirming the baseline)
and MUST STILL PASS after the fix (confirming no regressions).

This file covers:
- Preservation 1: Scoring Formula (compute_relevance_score with full 3-term formula)
- Preservation 2: Batch Grouping (below-threshold ID batching)
- Preservation 3: Pruner Mode 1 (explicit ID deletion, no-short-circuit)
- Preservation 4: GDPR No-Short-Circuit Deletion
- Preservation 5: decay_rate_from_prune_days formula and error handling
Plus static file assertions for CDK, infrastructure, blog, handler patterns.
"""

import json
import math
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

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
# Hypothesis strategies
# ===================================================================

# Timezone-aware datetimes within a reasonable range
tz_aware_datetimes = st.datetimes(
    min_value=datetime(2020, 1, 1),
    max_value=datetime(2025, 12, 31),
    timezones=st.just(timezone.utc),
)


# ===================================================================
# Preservation 1 — Scoring Formula
# ===================================================================

class TestPreservation1ScoringFormula:
    """
    **Validates: Requirements 3.1**

    Property-based test: for all valid inputs, compute_relevance_score()
    returns a float >= 0.0 that matches the 3-term weighted decay formula:
    w_recency * exp(-decay_rate * days_since_creation)
    + w_access * exp(-decay_rate * days_since_last_access)
    + w_frequency * min(access_count / max_access_baseline, 1.0)

    When weights sum to 1.0, the result is in [0, 1].
    """

    @given(
        created_at=tz_aware_datetimes,
        last_accessed_at=tz_aware_datetimes,
        access_count=st.integers(min_value=0, max_value=200),
        decay_rate=st.floats(min_value=0.001, max_value=1.0),
        w_recency=st.floats(min_value=0.0, max_value=1.0),
        w_access=st.floats(min_value=0.0, max_value=1.0),
        w_frequency=st.floats(min_value=0.0, max_value=1.0),
        max_access_baseline=st.integers(min_value=1, max_value=200),
    )
    @settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
    def test_scoring_formula_matches_reference(
        self,
        created_at,
        last_accessed_at,
        access_count,
        decay_rate,
        w_recency,
        w_access,
        w_frequency,
        max_access_baseline,
    ):
        """
        **Validates: Requirements 3.1**

        Property: For all valid inputs, compute_relevance_score returns a float
        >= 0.0 that matches the reference formula exactly.
        """
        # Filter NaN values
        assume(not math.isnan(w_recency))
        assume(not math.isnan(w_access))
        assume(not math.isnan(w_frequency))
        assume(not math.isnan(decay_rate))

        # Use a fixed 'now' that is always >= both datetimes
        now = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

        score = compute_relevance_score(
            created_at=created_at,
            last_accessed_at=last_accessed_at,
            access_count=access_count,
            decay_rate=decay_rate,
            now=now,
            w_recency=w_recency,
            w_access=w_access,
            w_frequency=w_frequency,
            max_access_baseline=max_access_baseline,
        )

        # Must be a float
        assert isinstance(score, float)
        # Must be non-negative
        assert score >= 0.0, f"Score {score} is negative"

        # Compute reference value
        days_since_creation = max((now - created_at).total_seconds() / 86400, 0.0)
        days_since_last_access = max((now - last_accessed_at).total_seconds() / 86400, 0.0)

        expected = (
            w_recency * math.exp(-decay_rate * days_since_creation)
            + w_access * math.exp(-decay_rate * days_since_last_access)
            + w_frequency * min(access_count / max_access_baseline, 1.0)
        )

        assert abs(score - expected) < 1e-9, (
            f"Score {score} != expected {expected}"
        )

    @given(
        created_at=tz_aware_datetimes,
        last_accessed_at=tz_aware_datetimes,
        access_count=st.integers(min_value=0, max_value=200),
        decay_rate=st.floats(min_value=0.001, max_value=1.0),
        max_access_baseline=st.integers(min_value=1, max_value=200),
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_scoring_formula_in_unit_range_when_weights_sum_to_one(
        self,
        created_at,
        last_accessed_at,
        access_count,
        decay_rate,
        max_access_baseline,
    ):
        """
        **Validates: Requirements 3.1**

        Property: When weights sum to 1.0 (the default), score is in [0, 1].
        """
        assume(not math.isnan(decay_rate))

        now = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

        score = compute_relevance_score(
            created_at=created_at,
            last_accessed_at=last_accessed_at,
            access_count=access_count,
            decay_rate=decay_rate,
            now=now,
            w_recency=0.4,
            w_access=0.35,
            w_frequency=0.25,
            max_access_baseline=max_access_baseline,
        )

        assert 0.0 <= score <= 1.0, f"Score {score} out of [0.0, 1.0] with default weights"


# ===================================================================
# Preservation 2 — Batch Grouping
# ===================================================================

class TestPreservation2BatchGrouping:
    """
    **Validates: Requirements 3.2**

    Property-based test: the batching logic
    [ids[i:i+batch_size] for i in range(0, len(ids), batch_size)]
    preserves all IDs exactly once, each batch has at most batch_size
    elements, and the total count equals the input count.
    """

    @given(
        num_ids=st.integers(min_value=1, max_value=200),
        batch_size=st.integers(min_value=1, max_value=50),
    )
    @settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
    def test_batch_grouping_preserves_all_ids(self, num_ids, batch_size):
        """
        **Validates: Requirements 3.2**

        Property: For any list of IDs and batch size, batching produces
        groups where all IDs appear exactly once, each batch has at most
        batch_size elements, and total count equals input count.
        """
        ids = [f"mem-{i}" for i in range(num_ids)]

        # This is the exact batching logic from the scorer handler
        batches = [ids[i:i + batch_size] for i in range(0, len(ids), batch_size)]

        # All IDs appear exactly once across batches
        all_ids_in_batches = []
        for batch in batches:
            all_ids_in_batches.extend(batch)
        assert sorted(all_ids_in_batches) == sorted(ids), (
            "Not all IDs appear exactly once across batches"
        )

        # Each batch has at most batch_size elements
        for batch in batches:
            assert len(batch) <= batch_size, (
                f"Batch has {len(batch)} elements, exceeds batch_size={batch_size}"
            )

        # Total count equals input count
        total = sum(len(b) for b in batches)
        assert total == num_ids, (
            f"Total batched count {total} != input count {num_ids}"
        )


# ===================================================================
# Preservation 3 — Pruner Mode 1 (Explicit ID Deletion)
# ===================================================================

class TestPreservation3PrunerMode1:
    """
    **Validates: Requirements 3.3**

    Property-based test: Pruner Mode 1 (explicit memory_ids) uses the
    no-short-circuit pattern. deleted_count + failed_count == len(memory_ids),
    and the return structure has status, deleted_count, failed_count,
    failed_memory_ids.
    """

    @given(
        num_ids=st.integers(min_value=1, max_value=50),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_pruner_mode1_no_short_circuit(self, num_ids):
        """
        **Validates: Requirements 3.3**

        Property: For any list of memory IDs, pruner Mode 1 processes all
        IDs (no short-circuit), and deleted_count + failed_count == len(memory_ids).
        """
        memory_ids = [f"rec-{i}" for i in range(num_ids)]

        mock_client = MagicMock()
        # delete_memory_record succeeds for all
        mock_client.delete_memory_record.return_value = {}

        event = {
            "memory_id": "mem-container-1",
            "memory_ids": memory_ids,
            "agent_id": "agent-test",
        }

        with patch("boto3.client", return_value=mock_client):
            from memory_pruner.handler import handler as pruner_handler
            result = pruner_handler(event, None)

        # Return structure must have these keys
        assert "status" in result
        assert "deleted_count" in result
        assert "failed_count" in result
        assert "failed_memory_ids" in result

        # No-short-circuit: all IDs processed
        assert result["deleted_count"] + result["failed_count"] == len(memory_ids), (
            f"deleted_count({result['deleted_count']}) + failed_count({result['failed_count']}) "
            f"!= len(memory_ids)({len(memory_ids)})"
        )

    @given(
        num_ids=st.integers(min_value=2, max_value=30),
        fail_indices=st.lists(st.integers(min_value=0, max_value=29), min_size=1, max_size=10),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_pruner_mode1_partial_failure(self, num_ids, fail_indices):
        """
        **Validates: Requirements 3.3**

        Property: When some deletions fail, pruner still processes all IDs
        and correctly reports partial_failure.
        """
        memory_ids = [f"rec-{i}" for i in range(num_ids)]
        # Normalize fail_indices to be within range
        valid_fail_indices = set(i % num_ids for i in fail_indices)

        mock_client = MagicMock()
        call_count = [0]

        def side_effect(**kwargs):
            idx = call_count[0]
            call_count[0] += 1
            if idx in valid_fail_indices:
                raise Exception(f"Simulated failure for index {idx}")
            return {}

        mock_client.delete_memory_record.side_effect = side_effect

        event = {
            "memory_id": "mem-container-1",
            "memory_ids": memory_ids,
            "agent_id": "agent-test",
        }

        with patch("boto3.client", return_value=mock_client):
            from memory_pruner.handler import handler as pruner_handler
            result = pruner_handler(event, None)

        # No-short-circuit invariant
        assert result["deleted_count"] + result["failed_count"] == len(memory_ids)
        assert result["failed_count"] == len(valid_fail_indices)
        assert len(result["failed_memory_ids"]) == len(valid_fail_indices)



# ===================================================================
# Preservation 4 — GDPR No-Short-Circuit Deletion
# ===================================================================

class TestPreservation4GDPRNoShortCircuit:
    """
    **Validates: Requirements 3.5**

    Property-based test: GDPR handler uses the no-short-circuit deletion
    pattern. deleted_count + len(failed_memory_ids) == total_records,
    and the return structure has status, user_id, deleted_count,
    failed_memory_ids.
    """

    @given(
        num_records=st.integers(min_value=1, max_value=50),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_gdpr_no_short_circuit_all_succeed(self, num_records):
        """
        **Validates: Requirements 3.5**

        Property: For any set of user memory records, GDPR handler processes
        all records (no short-circuit), and deleted_count + len(failed_memory_ids)
        == total_records.
        """
        memories = [
            {"memoryRecordId": f"rec-{i}"}
            for i in range(num_records)
        ]

        mock_client = MagicMock()
        mock_client.list_memory_records.return_value = {
            "memoryRecordSummaries": memories,
        }
        mock_client.delete_memory_record.return_value = {}

        event = {
            "user_id": "user-test-123",
            "memory_id": "mem-container-1",
        }

        with patch("boto3.client", return_value=mock_client):
            from gdpr_deletion.handler import handler as gdpr_handler
            result = gdpr_handler(event, None)

        # Return structure must have these keys
        assert "status" in result
        assert "user_id" in result
        assert "deleted_count" in result
        assert "failed_memory_ids" in result

        # No-short-circuit: all records processed
        total_records = len(memories)
        assert result["deleted_count"] + len(result["failed_memory_ids"]) == total_records, (
            f"deleted_count({result['deleted_count']}) + failed({len(result['failed_memory_ids'])}) "
            f"!= total_records({total_records})"
        )

    @given(
        num_records=st.integers(min_value=2, max_value=30),
        fail_indices=st.lists(st.integers(min_value=0, max_value=29), min_size=1, max_size=10),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_gdpr_no_short_circuit_partial_failure(self, num_records, fail_indices):
        """
        **Validates: Requirements 3.5**

        Property: When some deletions fail, GDPR handler still processes all
        records and correctly reports partial_failure.
        """
        memories = [
            {"memoryRecordId": f"rec-{i}"}
            for i in range(num_records)
        ]
        valid_fail_indices = set(i % num_records for i in fail_indices)

        mock_client = MagicMock()
        mock_client.list_memory_records.return_value = {
            "memoryRecordSummaries": memories,
        }

        call_count = [0]

        def side_effect(**kwargs):
            idx = call_count[0]
            call_count[0] += 1
            if idx in valid_fail_indices:
                raise Exception(f"Simulated failure for index {idx}")
            return {}

        mock_client.delete_memory_record.side_effect = side_effect

        event = {
            "user_id": "user-test-456",
            "memory_id": "mem-container-1",
        }

        with patch("boto3.client", return_value=mock_client):
            from gdpr_deletion.handler import handler as gdpr_handler
            result = gdpr_handler(event, None)

        total_records = len(memories)
        assert result["deleted_count"] + len(result["failed_memory_ids"]) == total_records
        assert len(result["failed_memory_ids"]) == len(valid_fail_indices)
        if valid_fail_indices:
            assert result["status"] == "partial_failure"


# ===================================================================
# Preservation 5 — decay_rate_from_prune_days
# ===================================================================

class TestPreservation5DecayRateFromPruneDays:
    """
    **Validates: Requirements 3.1**

    Property-based test: for all valid (prune_days, threshold),
    decay_rate_from_prune_days returns -ln(threshold) / prune_days.
    Also verifies ValueError for invalid inputs.
    """

    @given(
        prune_days=st.integers(min_value=1, max_value=365),
        threshold=st.floats(min_value=0.01, max_value=0.99),
    )
    @settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
    def test_decay_rate_matches_formula(self, prune_days, threshold):
        """
        **Validates: Requirements 3.1**

        Property: For all valid (prune_days, threshold),
        result equals -ln(threshold) / prune_days.
        """
        assume(not math.isnan(threshold))

        result = decay_rate_from_prune_days(prune_days, threshold)
        expected = -math.log(threshold) / prune_days
        assert abs(result - expected) < 1e-9, (
            f"decay_rate {result} != expected {expected} for "
            f"prune_days={prune_days}, threshold={threshold}"
        )

    @given(
        prune_days=st.integers(min_value=-100, max_value=0),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_decay_rate_invalid_prune_days(self, prune_days):
        """
        **Validates: Requirements 3.1**

        Property: prune_days <= 0 raises ValueError.
        """
        with pytest.raises(ValueError):
            decay_rate_from_prune_days(prune_days, 0.3)

    @given(
        threshold=st.one_of(
            st.floats(max_value=0.0),
            st.floats(min_value=1.0),
        ),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_decay_rate_invalid_threshold(self, threshold):
        """
        **Validates: Requirements 3.1**

        Property: threshold <= 0 or >= 1 raises ValueError.
        """
        assume(not math.isnan(threshold))
        with pytest.raises(ValueError):
            decay_rate_from_prune_days(45, threshold)


# ===================================================================
# Static File Assertions — CDK Stack IAM Permissions
# ===================================================================

class TestCDKIAMPreservation:
    """
    **Validates: Requirements 3.6**

    Verify that IAM permissions for Memory Scorer, Memory Consolidator,
    Memory Pruner, and GDPR handler's DeleteMemoryRecord are present
    in the CDK stack.
    """

    def test_memory_scorer_iam_permissions(self):
        """
        **Validates: Requirements 3.6**

        Memory Scorer must have ListMemoryRecords only (read-only).
        BatchUpdateMemoryRecords was intentionally removed — scorer no longer writes.
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
        assert "BatchUpdateMemoryRecords" not in actions, (
            "Memory Scorer IAM should NOT have BatchUpdateMemoryRecords — scorer is read-only"
        )

    def test_memory_consolidator_iam_permissions(self):
        """
        **Validates: Requirements 3.6**

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
        **Validates: Requirements 3.6**

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
        **Validates: Requirements 3.6**

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
    **Validates: Requirements 3.6**

    Verify aws-cdk-lib version ^2.249.0 is unchanged in package.json.
    """

    def test_aws_cdk_lib_version_unchanged(self):
        """
        **Validates: Requirements 3.6**

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
    **Validates: Requirements 3.8**

    Verify blog.md contains key phrases about memory lifecycle,
    relevance decay, and GDPR.
    """

    def test_blog_key_phrases_preserved(self):
        """
        **Validates: Requirements 3.8**

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
    **Validates: Requirements 3.4**

    Memory Consolidator must have 4-step flow markers:
    retrieve, invoke Bedrock, store consolidated, delete originals.
    """

    def test_consolidator_has_4_step_markers(self):
        """
        **Validates: Requirements 3.4**

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
    **Validates: Requirements 3.3, 3.5**

    Memory Pruner and GDPR handler must have no-short-circuit pattern:
    try/except inside a for loop (continues on individual failures).
    """

    def test_pruner_no_short_circuit(self):
        """
        **Validates: Requirements 3.3**

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
        **Validates: Requirements 3.5**

        GDPR handler must have try/except inside a for loop.
        """
        content = _read(GDPR_DELETION_PATH)

        assert re.search(r"for\s+\w+\s+in\s+.*?:\s*\n.*?try:", content, re.DOTALL), (
            "GDPR handler missing no-short-circuit pattern (try inside for loop)"
        )
        assert "failed_memory_ids" in content, (
            "GDPR handler missing failed_memory_ids tracking"
        )
