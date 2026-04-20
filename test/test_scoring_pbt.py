"""
Property-Based Tests for Scoring Function

Feature: cloudtrail-access-scoring
Property 4: Score range invariant

**Validates: Requirements 3.5, 9.2, 9.5**

Generates random valid weight triples summing to 1.0, non-negative time deltas,
non-negative access counts, positive decay rates, and positive max_access_baseline;
verifies the score is always in [0.0, 1.0].
"""

import os
import sys
from datetime import datetime, timezone, timedelta

import pytest
from hypothesis import given, settings, assume, HealthCheck
import hypothesis.strategies as st

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LAMBDAS_ROOT = os.path.join(REPO_ROOT, "lambdas")
sys.path.insert(0, LAMBDAS_ROOT)

from memory_scorer.handler import compute_relevance_score


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

@st.composite
def weight_triples(draw):
    """Generate three non-negative weights that sum to 1.0.

    Uses a Dirichlet-like strategy: draw 3 values in (0, 1], normalize.
    """
    a = draw(st.floats(min_value=1e-9, max_value=1.0, allow_nan=False, allow_infinity=False))
    b = draw(st.floats(min_value=1e-9, max_value=1.0, allow_nan=False, allow_infinity=False))
    c = draw(st.floats(min_value=1e-9, max_value=1.0, allow_nan=False, allow_infinity=False))
    total = a + b + c
    assume(total > 0)
    return (a / total, b / total, c / total)


# Non-negative time delta in days (0 to ~10 years)
days_delta_st = st.floats(min_value=0.0, max_value=3650.0, allow_nan=False, allow_infinity=False)

# Non-negative access count
access_count_st = st.integers(min_value=0, max_value=10000)

# Positive decay rate
decay_rate_st = st.floats(min_value=0.001, max_value=1.0, allow_nan=False, allow_infinity=False)

# Positive max_access_baseline
max_access_baseline_st = st.integers(min_value=1, max_value=1000)


# ---------------------------------------------------------------------------
# Property Test
# ---------------------------------------------------------------------------

class TestScoreRangeInvariant:
    """
    Feature: cloudtrail-access-scoring
    Property 4: Score range invariant

    **Validates: Requirements 3.5, 9.2, 9.5**
    """

    @given(
        weights=weight_triples(),
        days_since_creation=days_delta_st,
        days_since_last_access=days_delta_st,
        access_count=access_count_st,
        decay_rate=decay_rate_st,
        max_access_baseline=max_access_baseline_st,
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_score_is_in_zero_one_range(
        self,
        weights,
        days_since_creation,
        days_since_last_access,
        access_count,
        decay_rate,
        max_access_baseline,
    ):
        """
        **Validates: Requirements 3.5, 9.2, 9.5**

        For any valid weight triple (w_recency, w_access, w_frequency) where
        each weight is in [0.0, 1.0] and the weights sum to 1.0, and for any
        non-negative days_since_creation, non-negative days_since_last_access,
        non-negative access_count, positive decay_rate, and positive
        max_access_baseline, the scoring function shall produce a score in
        [0.0, 1.0].
        """
        w_recency, w_access, w_frequency = weights

        # Build datetime inputs from day deltas
        now = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        created_at = now - timedelta(days=days_since_creation)
        last_accessed_at = now - timedelta(days=days_since_last_access)

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

        assert 0.0 <= score <= 1.0, (
            f"Score {score} is out of [0.0, 1.0] range. "
            f"weights=({w_recency}, {w_access}, {w_frequency}), "
            f"days_since_creation={days_since_creation}, "
            f"days_since_last_access={days_since_last_access}, "
            f"access_count={access_count}, "
            f"decay_rate={decay_rate}, "
            f"max_access_baseline={max_access_baseline}"
        )


# ---------------------------------------------------------------------------
# Property 5: Maximum score at boundary
# ---------------------------------------------------------------------------

class TestMaxScoreAtBoundary:
    """
    Feature: cloudtrail-access-scoring
    Property 5: Maximum score at boundary

    **Validates: Requirements 3.6, 9.1**
    """

    @given(
        weights=weight_triples(),
        max_access_baseline=max_access_baseline_st,
        extra_access=st.integers(min_value=0, max_value=10000),
        decay_rate=decay_rate_st,
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_max_score_at_boundary(
        self,
        weights,
        max_access_baseline,
        extra_access,
        decay_rate,
    ):
        """
        **Validates: Requirements 3.6, 9.1**

        For any valid weight triple (w_recency, w_access, w_frequency) that
        sums to 1.0, when days_since_creation is zero, days_since_last_access
        is zero, and access_count >= max_access_baseline, the scoring function
        shall produce a score equal to W_RECENCY + W_ACCESS + W_FREQUENCY
        (i.e., 1.0).
        """
        w_recency, w_access, w_frequency = weights

        # access_count is at or above the baseline so frequency term saturates
        access_count = max_access_baseline + extra_access

        # Both time deltas are zero (created_at == last_accessed_at == now)
        now = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        created_at = now
        last_accessed_at = now

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

        expected = w_recency + w_access + w_frequency  # should be 1.0

        assert score == pytest.approx(expected, abs=1e-9), (
            f"Score {score} != expected {expected}. "
            f"weights=({w_recency}, {w_access}, {w_frequency}), "
            f"access_count={access_count}, "
            f"max_access_baseline={max_access_baseline}, "
            f"decay_rate={decay_rate}"
        )


# ---------------------------------------------------------------------------
# Property 6: Monotonicity in access count
# ---------------------------------------------------------------------------

class TestMonotonicityInAccessCount:
    """
    Feature: cloudtrail-access-scoring
    Property 6: Monotonicity in access count

    **Validates: Requirements 3.4, 9.3**
    """

    @given(
        weights=weight_triples(),
        days_since_creation=days_delta_st,
        days_since_last_access=days_delta_st,
        access_count_b=access_count_st,
        extra=st.integers(min_value=1, max_value=10000),
        decay_rate=decay_rate_st,
        max_access_baseline=max_access_baseline_st,
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_higher_access_count_gives_higher_or_equal_score(
        self,
        weights,
        days_since_creation,
        days_since_last_access,
        access_count_b,
        extra,
        decay_rate,
        max_access_baseline,
    ):
        """
        **Validates: Requirements 3.4, 9.3**

        For any two inputs that are identical except for access_count, where
        access_count_A > access_count_B, the scoring function shall produce
        score_A >= score_B (monotonicity in access count).
        """
        w_recency, w_access, w_frequency = weights
        access_count_a = access_count_b + extra  # guarantees A > B

        now = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        created_at = now - timedelta(days=days_since_creation)
        last_accessed_at = now - timedelta(days=days_since_last_access)

        score_a = compute_relevance_score(
            created_at=created_at,
            last_accessed_at=last_accessed_at,
            access_count=access_count_a,
            decay_rate=decay_rate,
            now=now,
            w_recency=w_recency,
            w_access=w_access,
            w_frequency=w_frequency,
            max_access_baseline=max_access_baseline,
        )

        score_b = compute_relevance_score(
            created_at=created_at,
            last_accessed_at=last_accessed_at,
            access_count=access_count_b,
            decay_rate=decay_rate,
            now=now,
            w_recency=w_recency,
            w_access=w_access,
            w_frequency=w_frequency,
            max_access_baseline=max_access_baseline,
        )

        assert score_a >= score_b, (
            f"Monotonicity violated: score_A={score_a} < score_B={score_b}. "
            f"access_count_A={access_count_a}, access_count_B={access_count_b}, "
            f"weights=({w_recency}, {w_access}, {w_frequency}), "
            f"days_since_creation={days_since_creation}, "
            f"days_since_last_access={days_since_last_access}, "
            f"decay_rate={decay_rate}, "
            f"max_access_baseline={max_access_baseline}"
        )


# ---------------------------------------------------------------------------
# Property 7: Monotonic decay in creation age
# ---------------------------------------------------------------------------

class TestMonotonicDecayInCreationAge:
    """
    Feature: cloudtrail-access-scoring
    Property 7: Monotonic decay in creation age

    **Validates: Requirements 9.4**
    """

    @given(
        weights=weight_triples(),
        days_since_creation_b=days_delta_st,
        extra_days=st.floats(min_value=0.001, max_value=3650.0, allow_nan=False, allow_infinity=False),
        days_since_last_access=days_delta_st,
        access_count=access_count_st,
        decay_rate=decay_rate_st,
        max_access_baseline=max_access_baseline_st,
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_older_creation_gives_lower_or_equal_score(
        self,
        weights,
        days_since_creation_b,
        extra_days,
        days_since_last_access,
        access_count,
        decay_rate,
        max_access_baseline,
    ):
        """
        **Validates: Requirements 9.4**

        For any two inputs that are identical except for days_since_creation,
        where days_since_creation_A > days_since_creation_B, the scoring
        function shall produce score_A <= score_B (monotonic decay in
        creation age — older memories score lower or equal).
        """
        w_recency, w_access, w_frequency = weights

        days_since_creation_a = days_since_creation_b + extra_days  # A > B

        now = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        last_accessed_at = now - timedelta(days=days_since_last_access)

        created_at_a = now - timedelta(days=days_since_creation_a)
        created_at_b = now - timedelta(days=days_since_creation_b)

        score_a = compute_relevance_score(
            created_at=created_at_a,
            last_accessed_at=last_accessed_at,
            access_count=access_count,
            decay_rate=decay_rate,
            now=now,
            w_recency=w_recency,
            w_access=w_access,
            w_frequency=w_frequency,
            max_access_baseline=max_access_baseline,
        )

        score_b = compute_relevance_score(
            created_at=created_at_b,
            last_accessed_at=last_accessed_at,
            access_count=access_count,
            decay_rate=decay_rate,
            now=now,
            w_recency=w_recency,
            w_access=w_access,
            w_frequency=w_frequency,
            max_access_baseline=max_access_baseline,
        )

        assert score_a <= score_b, (
            f"Monotonic decay violated: score_A={score_a} > score_B={score_b}. "
            f"days_since_creation_A={days_since_creation_a}, "
            f"days_since_creation_B={days_since_creation_b}, "
            f"weights=({w_recency}, {w_access}, {w_frequency}), "
            f"days_since_last_access={days_since_last_access}, "
            f"access_count={access_count}, "
            f"decay_rate={decay_rate}, "
            f"max_access_baseline={max_access_baseline}"
        )
