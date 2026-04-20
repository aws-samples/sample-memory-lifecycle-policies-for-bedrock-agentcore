"""
Unit tests for scoring function edge cases.

Validates: Requirements 3.2, 3.3, 3.4

Tests:
- Zero access count produces zero frequency term
- access_count >= MAX_ACCESS_BASELINE caps frequency term at w_frequency
- ValueError raised when max_access_baseline <= 0
- Function accepts all specified parameters per the new signature
"""

import math
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LAMBDAS_ROOT = os.path.join(REPO_ROOT, "lambdas")
sys.path.insert(0, LAMBDAS_ROOT)

from memory_scorer.handler import compute_relevance_score

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------
NOW = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
DECAY_RATE = 0.05


class TestZeroAccessCountFrequencyTerm:
    """Validates: Requirement 3.3

    WHEN access_count is zero, the frequency term SHALL be zero, so the score
    depends only on the recency and access decay terms.
    """

    def test_zero_access_count_produces_zero_frequency_term(self):
        created_at = NOW - timedelta(days=10)
        last_accessed_at = NOW - timedelta(days=5)

        score = compute_relevance_score(
            created_at=created_at,
            last_accessed_at=last_accessed_at,
            access_count=0,
            decay_rate=DECAY_RATE,
            now=NOW,
            w_recency=0.4,
            w_access=0.35,
            w_frequency=0.25,
            max_access_baseline=50,
        )

        # Manually compute expected score with zero frequency term
        days_since_creation = 10.0
        days_since_last_access = 5.0
        expected_recency = 0.4 * math.exp(-DECAY_RATE * days_since_creation)
        expected_access = 0.35 * math.exp(-DECAY_RATE * days_since_last_access)
        expected_frequency = 0.0  # access_count=0 → frequency term is 0
        expected_score = expected_recency + expected_access + expected_frequency

        assert score == pytest.approx(expected_score, abs=1e-9)
        # Confirm frequency term contributed nothing
        assert score == pytest.approx(expected_recency + expected_access, abs=1e-9)

    def test_zero_access_count_with_zero_time_deltas(self):
        """When both time deltas are zero and access_count is zero,
        score = w_recency + w_access (frequency term is 0)."""
        score = compute_relevance_score(
            created_at=NOW,
            last_accessed_at=NOW,
            access_count=0,
            decay_rate=DECAY_RATE,
            now=NOW,
            w_recency=0.4,
            w_access=0.35,
            w_frequency=0.25,
            max_access_baseline=50,
        )

        # exp(0) = 1.0 for both decay terms, frequency = 0
        expected = 0.4 + 0.35
        assert score == pytest.approx(expected, abs=1e-9)


class TestFrequencyTermCapping:
    """Validates: Requirement 3.4

    WHEN access_count >= MAX_ACCESS_BASELINE, the frequency term SHALL be
    capped at w_frequency (the min(..., 1.0) saturates).
    """

    def test_access_count_equals_baseline_caps_frequency(self):
        score = compute_relevance_score(
            created_at=NOW,
            last_accessed_at=NOW,
            access_count=50,
            decay_rate=DECAY_RATE,
            now=NOW,
            w_recency=0.4,
            w_access=0.35,
            w_frequency=0.25,
            max_access_baseline=50,
        )

        # All terms at maximum: exp(0)=1 for decay terms, 50/50=1.0 for frequency
        expected = 0.4 + 0.35 + 0.25
        assert score == pytest.approx(expected, abs=1e-9)

    def test_access_count_exceeds_baseline_caps_frequency(self):
        score = compute_relevance_score(
            created_at=NOW,
            last_accessed_at=NOW,
            access_count=200,
            decay_rate=DECAY_RATE,
            now=NOW,
            w_recency=0.4,
            w_access=0.35,
            w_frequency=0.25,
            max_access_baseline=50,
        )

        # Frequency term: min(200/50, 1.0) = 1.0 → capped at w_frequency
        expected = 0.4 + 0.35 + 0.25
        assert score == pytest.approx(expected, abs=1e-9)

    def test_capped_frequency_with_nonzero_time_deltas(self):
        """Even with time decay, the frequency term should still cap at w_frequency."""
        created_at = NOW - timedelta(days=30)
        last_accessed_at = NOW - timedelta(days=10)

        score = compute_relevance_score(
            created_at=created_at,
            last_accessed_at=last_accessed_at,
            access_count=100,
            decay_rate=DECAY_RATE,
            now=NOW,
            w_recency=0.4,
            w_access=0.35,
            w_frequency=0.25,
            max_access_baseline=50,
        )

        expected_recency = 0.4 * math.exp(-DECAY_RATE * 30.0)
        expected_access = 0.35 * math.exp(-DECAY_RATE * 10.0)
        expected_frequency = 0.25 * 1.0  # capped
        expected = expected_recency + expected_access + expected_frequency

        assert score == pytest.approx(expected, abs=1e-9)


class TestValueErrorOnInvalidBaseline:
    """Validates: Requirement 3.2 (error handling)

    WHEN max_access_baseline is zero or negative, the function SHALL raise
    ValueError.
    """

    def test_zero_baseline_raises_value_error(self):
        with pytest.raises(ValueError, match="max_access_baseline must be a positive integer"):
            compute_relevance_score(
                created_at=NOW,
                last_accessed_at=NOW,
                access_count=10,
                decay_rate=DECAY_RATE,
                now=NOW,
                max_access_baseline=0,
            )

    def test_negative_baseline_raises_value_error(self):
        with pytest.raises(ValueError, match="max_access_baseline must be a positive integer"):
            compute_relevance_score(
                created_at=NOW,
                last_accessed_at=NOW,
                access_count=10,
                decay_rate=DECAY_RATE,
                now=NOW,
                max_access_baseline=-5,
            )

    def test_negative_one_baseline_raises_value_error(self):
        with pytest.raises(ValueError, match="max_access_baseline must be a positive integer"):
            compute_relevance_score(
                created_at=NOW,
                last_accessed_at=NOW,
                access_count=1,
                decay_rate=DECAY_RATE,
                now=NOW,
                max_access_baseline=-1,
            )


class TestFunctionAcceptsAllParameters:
    """Validates: Requirement 3.2

    The function SHALL accept all specified parameters per the new signature:
    created_at, last_accessed_at, access_count, decay_rate, now,
    w_recency, w_access, w_frequency, max_access_baseline.
    """

    def test_all_parameters_explicit(self):
        """Call with every parameter explicitly to confirm the signature."""
        score = compute_relevance_score(
            created_at=NOW - timedelta(days=7),
            last_accessed_at=NOW - timedelta(days=2),
            access_count=25,
            decay_rate=0.03,
            now=NOW,
            w_recency=0.5,
            w_access=0.3,
            w_frequency=0.2,
            max_access_baseline=100,
        )

        # Verify the result is a float and reasonable
        assert isinstance(score, float)
        assert score >= 0.0

    def test_default_weights_used_when_omitted(self):
        """Call with only required params; defaults should apply."""
        score = compute_relevance_score(
            created_at=NOW - timedelta(days=5),
            last_accessed_at=NOW - timedelta(days=1),
            access_count=10,
            decay_rate=DECAY_RATE,
            now=NOW,
        )

        # Manually compute with defaults: w_recency=0.4, w_access=0.35,
        # w_frequency=0.25, max_access_baseline=50
        expected_recency = 0.4 * math.exp(-DECAY_RATE * 5.0)
        expected_access = 0.35 * math.exp(-DECAY_RATE * 1.0)
        expected_frequency = 0.25 * min(10 / 50, 1.0)
        expected = expected_recency + expected_access + expected_frequency

        assert score == pytest.approx(expected, abs=1e-9)
