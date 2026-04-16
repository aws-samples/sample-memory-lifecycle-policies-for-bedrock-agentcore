"""
Preservation Property Tests — Property 2

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10**

These tests capture the baseline behavior of non-AgentCore-Memory code BEFORE
the fix is applied. They MUST PASS on unfixed code (confirming the baseline)
and MUST STILL PASS after the fix (confirming no regressions).
"""

import math
import os
import sys
from datetime import datetime, timezone, timedelta

from hypothesis import given, settings, HealthCheck
import hypothesis.strategies as st

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CODE_ROOT = os.path.join(REPO_ROOT, "code")
LAMBDAS_ROOT = os.path.join(CODE_ROOT, "lambdas")

# Add lambdas directory to sys.path so we can import handler modules
sys.path.insert(0, LAMBDAS_ROOT)

from memory_scorer.handler import compute_relevance_score
from shared.constants import decay_rate_from_prune_days

# ---------------------------------------------------------------------------
# File paths used across multiple tests
# ---------------------------------------------------------------------------
MEMORY_SCORER_PATH = os.path.join(LAMBDAS_ROOT, "memory_scorer", "handler.py")
MEMORY_CONSOLIDATOR_PATH = os.path.join(LAMBDAS_ROOT, "memory_consolidator", "handler.py")
MEMORY_PRUNER_PATH = os.path.join(LAMBDAS_ROOT, "memory_pruner", "handler.py")
GDPR_DELETION_PATH = os.path.join(LAMBDAS_ROOT, "gdpr_deletion", "handler.py")
CDK_STACK_PATH = os.path.join(CODE_ROOT, "lib", "memory-lifecycle-stack.ts")
BLOG_PATH = os.path.join(REPO_ROOT, "blog.md")

HANDLER_FILES = [
    MEMORY_SCORER_PATH,
    MEMORY_CONSOLIDATOR_PATH,
    MEMORY_PRUNER_PATH,
    GDPR_DELETION_PATH,
]


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ===================================================================
# 2a. Scoring formula preservation
# ===================================================================

class TestScoringFormulaPreservation:
    """
    **Validates: Requirements 3.1**

    Verify compute_relevance_score returns a float in [0.0, 1.0] and matches
    the expected 2-term exponential decay formula. Also verify
    decay_rate_from_prune_days(45, 0.3) ≈ 0.02676.
    """

    @given(
        decay_rate=st.floats(min_value=0.001, max_value=1.0),
        days_created=st.integers(min_value=0, max_value=365),
        days_accessed=st.integers(min_value=0, max_value=365),
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_score_range_and_formula(self, decay_rate, days_created, days_accessed):
        """
        **Validates: Requirements 3.1**

        Property: For any valid decay_rate and day offsets, compute_relevance_score
        returns a float in [0.0, 1.0] that equals
        0.5 * exp(-decay_rate * d1) + 0.5 * exp(-decay_rate * d2).
        """
        now = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        created_at = now - timedelta(days=days_created)
        last_accessed_at = now - timedelta(days=days_accessed)

        score = compute_relevance_score(created_at, last_accessed_at, decay_rate, now)

        # Must be a float
        assert isinstance(score, float)
        # Must be in [0.0, 1.0]
        assert 0.0 <= score <= 1.0

        # Must match the expected formula
        expected = (
            0.5 * math.exp(-decay_rate * days_created)
            + 0.5 * math.exp(-decay_rate * days_accessed)
        )
        assert abs(score - expected) < 1e-9, (
            f"Score {score} != expected {expected} for "
            f"decay_rate={decay_rate}, d1={days_created}, d2={days_accessed}"
        )

    def test_decay_rate_from_prune_days_default(self):
        """
        **Validates: Requirements 3.1**

        Assert decay_rate_from_prune_days(45, 0.3) ≈ 0.02676.
        """
        rate = decay_rate_from_prune_days(45, 0.3)
        expected = -math.log(0.3) / 45
        assert abs(rate - expected) < 1e-5
        assert abs(rate - 0.02676) < 1e-4


# ===================================================================
# 2b. Bedrock invocation preservation
# ===================================================================

class TestBedrockInvocationPreservation:
    """
    **Validates: Requirements 3.2**

    Assert memory_consolidator/handler.py contains the Bedrock runtime
    client instantiation and invocation patterns.
    """

    def test_bedrock_runtime_patterns(self):
        """
        **Validates: Requirements 3.2**

        The consolidator must use boto3.client("bedrock-runtime"), invoke_model,
        anthropic_version, bedrock-2023-05-31, and max_tokens.
        """
        content = _read(MEMORY_CONSOLIDATOR_PATH)

        assert 'boto3.client("bedrock-runtime")' in content, (
            "Missing bedrock-runtime client instantiation"
        )
        assert "invoke_model(" in content, "Missing invoke_model call"
        assert "anthropic_version" in content, "Missing anthropic_version"
        assert "bedrock-2023-05-31" in content, "Missing bedrock-2023-05-31"
        assert "max_tokens" in content, "Missing max_tokens"


# ===================================================================
# 2c. Consolidation prompt preservation
# ===================================================================

class TestConsolidationPromptPreservation:
    """
    **Validates: Requirements 3.3**

    Assert CONSOLIDATION_PROMPT_TEMPLATE contains the expected key phrases.
    """

    def test_prompt_template_contents(self):
        """
        **Validates: Requirements 3.3**

        The consolidation prompt must contain "memory consolidation assistant",
        "summary", "confidence", and "key_facts".
        """
        content = _read(MEMORY_CONSOLIDATOR_PATH)

        assert "memory consolidation assistant" in content, (
            "Missing 'memory consolidation assistant' in prompt"
        )
        assert '"summary"' in content, "Missing 'summary' in prompt"
        assert '"confidence"' in content, "Missing 'confidence' in prompt"
        assert '"key_facts"' in content, "Missing 'key_facts' in prompt"


# ===================================================================
# 2d. Error handling preservation
# ===================================================================

class TestErrorHandlingPreservation:
    """
    **Validates: Requirements 3.4**

    For each of the 4 handler files, assert they contain the expected
    error handling imports and patterns.
    """

    def test_error_handling_in_all_handlers(self):
        """
        **Validates: Requirements 3.4**

        Each handler must import ClientError and EndpointConnectionError
        from botocore.exceptions and use except (ClientError patterns.
        """
        for handler_path in HANDLER_FILES:
            content = _read(handler_path)
            rel = os.path.relpath(handler_path, REPO_ROOT)

            assert "from botocore.exceptions import ClientError, EndpointConnectionError" in content, (
                f"{rel}: Missing botocore error imports"
            )
            assert "except (ClientError" in content, (
                f"{rel}: Missing except (ClientError pattern"
            )


# ===================================================================
# 2e. Structured logging preservation
# ===================================================================

class TestStructuredLoggingPreservation:
    """
    **Validates: Requirements 3.9**

    For each handler, assert presence of structured JSON logging patterns.
    """

    def test_structured_logging_in_all_handlers(self):
        """
        **Validates: Requirements 3.9**

        Each handler must contain logger.info(json.dumps({, "action":,
        and "timestamp": patterns.
        """
        for handler_path in HANDLER_FILES:
            content = _read(handler_path)
            rel = os.path.relpath(handler_path, REPO_ROOT)

            assert "logger.info(json.dumps({" in content, (
                f"{rel}: Missing logger.info(json.dumps({{ pattern"
            )
            assert '"action":' in content or '"action"' in content, (
                f"{rel}: Missing '\"action\":' in structured logs"
            )
            assert '"timestamp":' in content or '"timestamp"' in content, (
                f"{rel}: Missing '\"timestamp\":' in structured logs"
            )


# ===================================================================
# 2f. CDK non-IAM preservation
# ===================================================================

class TestCDKNonIAMPreservation:
    """
    **Validates: Requirements 3.5, 3.6, 3.7, 3.8**

    Assert memory-lifecycle-stack.ts contains key CDK constructs that
    must remain unchanged by the fix.
    """

    def test_cdk_stack_non_iam_patterns(self):
        """
        **Validates: Requirements 3.5, 3.6, 3.7, 3.8**

        The CDK stack must contain Lambda runtime, timeouts, Code.fromAsset,
        Bedrock IAM, cron schedule, Step Functions constructs, CloudTrail,
        and CloudWatch dashboard patterns.
        """
        content = _read(CDK_STACK_PATH)

        patterns = [
            ("lambda.Runtime.PYTHON_3_12", "Lambda Python 3.12 runtime"),
            ("cdk.Duration.minutes(5)", "5-minute timeout"),
            ("cdk.Duration.minutes(10)", "10-minute timeout"),
            ("Code.fromAsset(", "Code.fromAsset packaging"),
            ("bedrock:InvokeModel", "Bedrock InvokeModel IAM action"),
            ("arn:aws:bedrock:", "Bedrock ARN pattern"),
            ("cron(0 2 * * ? *)", "Nightly cron schedule"),
            ("DefinitionBody.fromChainable", "Step Functions definition"),
            ("sfn.Condition.isPresent", "Step Functions choice condition"),
            ("MemoryLifecycleAuditTrail", "CloudTrail trail name"),
            ("MemoryLifecycleDashboard", "CloudWatch dashboard name"),
        ]

        for pattern, description in patterns:
            assert pattern in content, (
                f"CDK stack missing '{pattern}' ({description})"
            )


# ===================================================================
# 2g. Blog narrative preservation
# ===================================================================

class TestBlogNarrativePreservation:
    """
    **Validates: Requirements 3.10**

    Assert blog.md contains key prose phrases that describe the architecture,
    lifecycle policies, and approach. These must remain unchanged.
    """

    def test_blog_key_phrases(self):
        """
        **Validates: Requirements 3.10**

        The blog must contain key narrative phrases about the architecture,
        memory types, lifecycle policies, and compliance.
        """
        content = _read(BLOG_PATH)

        phrases = [
            "forgetting problem",
            "Episodic Memory",
            "Semantic Memory",
            "Procedural Memory",
            "TTL-Based Expiration",
            "Relevance Decay Scoring",
            "LLM-Based Consolidation",
            "0.5 * exp(-decay_rate",
            "GDPR Right-to-Be-Forgotten",
        ]

        for phrase in phrases:
            assert phrase in content, (
                f"Blog missing key phrase: '{phrase}'"
            )
