"""Shared constants for memory lifecycle Lambda functions."""

# Scoring formula weights
W_RECENCY: float = 0.4
W_ACCESS: float = 0.4
W_FREQUENCY: float = 0.2

# Decay rate for exponential decay factors
DECAY_RATE: float = 0.05

# Baseline for normalizing access count in frequency factor
MAX_ACCESS_BASELINE: int = 50
