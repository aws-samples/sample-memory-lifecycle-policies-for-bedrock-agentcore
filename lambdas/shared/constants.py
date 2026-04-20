"""Shared constants for memory lifecycle Lambda functions."""

import math

# Default pruneDays value — approximate number of days after which
# an unaccessed memory's score drops below the relevance threshold.
PRUNE_DAYS_DEFAULT: int = 45

# Default relevance threshold used in the decay-rate conversion.
RELEVANCE_THRESHOLD_DEFAULT: float = 0.3


# Scoring weights for the three-term relevance formula.
W_RECENCY_DEFAULT: float = 0.4
W_ACCESS_DEFAULT: float = 0.35
W_FREQUENCY_DEFAULT: float = 0.25

# Access count at which the frequency term saturates (reaches 1.0).
MAX_ACCESS_BASELINE_DEFAULT: int = 50

# Hours to look back when scanning CloudTrail logs for access events.
TRAIL_LOOKBACK_HOURS_DEFAULT: int = 25


def decay_rate_from_prune_days(prune_days: int, threshold: float) -> float:
    """Convert pruneDays + threshold to an exponential decay rate.

    decay_rate = -ln(threshold) / prune_days

    Raises ValueError for invalid inputs.
    """
    if prune_days <= 0:
        raise ValueError(f"prune_days must be a positive integer, got: {prune_days}")
    if threshold <= 0 or threshold >= 1:
        raise ValueError(
            f"threshold must be in the open interval (0, 1), got: {threshold}"
        )
    return -math.log(threshold) / prune_days
