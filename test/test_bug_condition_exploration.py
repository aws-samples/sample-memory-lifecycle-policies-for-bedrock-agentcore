"""
Bug Condition Exploration Test — Property 1: Blog Code Hallucinations and Misconfigurations

**Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8**

This test encodes the EXPECTED CORRECT behavior for all 8 bug conditions
identified in the validation report. Each assertion checks what the code
SHOULD look like after the fix.

- On UNFIXED code this test MUST FAIL — failure confirms the bugs exist.
- On FIXED code this test MUST PASS — passing confirms the bugs are resolved.

Bug conditions tested:
  1.1 IAM mismatch: GDPR handler should have ListMemoryRecords (not RetrieveMemoryRecords)
  1.2 Hallucinated client agentcore-evaluations → should be bedrock-agentcore
  1.3 Hallucinated client agentcore-runtime → should be bedrock-agentcore
  1.4 Hallucinated method evaluate_response() → should be evaluate()
  1.5 Hallucinated method invoke_agent() → should be invoke_agent_runtime()
  1.6 TTL data flow gap: pruner should handle TTL mode when memory_ids absent
  1.7 Batching mismatch: scorer should return batched below_threshold with memory_ids key
  1.8 Bad package version: aws-cdk should be ^2.249.0 (not ^2.1118.0)
"""

import json
import os
import re

from hypothesis import given, settings, HealthCheck
import hypothesis.strategies as st

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CODE_ROOT = os.path.join(REPO_ROOT, "code")

CDK_STACK_PATH = os.path.join(CODE_ROOT, "lib", "memory-lifecycle-stack.ts")
REGRESSION_SUITE_PATH = os.path.join(CODE_ROOT, "test", "test_regression_suite.py")
PRUNER_PATH = os.path.join(CODE_ROOT, "lambdas", "memory_pruner", "handler.py")
SCORER_PATH = os.path.join(CODE_ROOT, "lambdas", "memory_scorer", "handler.py")
PACKAGE_JSON_PATH = os.path.join(CODE_ROOT, "package.json")


def _read(path: str) -> str:
    """Read a file and return its content as a string."""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# File-based bug conditions: (file_path, expected_correct_value) pairs
# These are checked via sampled_from PBT strategy.
# ---------------------------------------------------------------------------

FILE_BASED_BUG_CONDITIONS = [
    # 1.1 IAM mismatch — GDPR handler should grant ListMemoryRecords
    (
        CDK_STACK_PATH,
        "ListMemoryRecords",
        "GDPR handler IAM policy must contain ListMemoryRecords",
    ),
    # 1.2 Hallucinated client agentcore-evaluations → bedrock-agentcore
    (
        REGRESSION_SUITE_PATH,
        'boto3.client(\n            "bedrock-agentcore"',
        "AgentCoreEvaluationsClient must use bedrock-agentcore client",
    ),
    # 1.8 Bad package version — aws-cdk should be ^2.249.0
    (
        PACKAGE_JSON_PATH,
        '"aws-cdk": "^2.249.0"',
        "aws-cdk version must be ^2.249.0",
    ),
]


# ---------------------------------------------------------------------------
# 1.1 IAM mismatch
# ---------------------------------------------------------------------------

def test_gdpr_handler_iam_has_list_memory_records():
    """
    **Validates: Requirements 1.1**

    The GDPR deletion handler's IAM policy in the CDK stack must grant
    'bedrock-agentcore:ListMemoryRecords' (not 'RetrieveMemoryRecords').

    FAILS on unfixed code because the CDK stack has RetrieveMemoryRecords.
    """
    content = _read(CDK_STACK_PATH)

    # Find the GDPR section's IAM policy
    # The GDPR handler IAM block should contain ListMemoryRecords
    gdpr_section_match = re.search(
        r"// GDPR.*?addToRolePolicy.*?actions:\s*\[(.*?)\]",
        content,
        re.DOTALL,
    )
    assert gdpr_section_match is not None, "Could not find GDPR handler IAM policy section"

    actions_block = gdpr_section_match.group(1)
    assert "ListMemoryRecords" in actions_block, (
        f"GDPR handler IAM policy does not contain 'ListMemoryRecords'. "
        f"Found actions: {actions_block.strip()}"
    )
    assert "RetrieveMemoryRecords" not in actions_block, (
        f"GDPR handler IAM policy still contains 'RetrieveMemoryRecords'. "
        f"Found actions: {actions_block.strip()}"
    )


# ---------------------------------------------------------------------------
# 1.2 Hallucinated client agentcore-evaluations
# ---------------------------------------------------------------------------

def test_evaluations_client_uses_bedrock_agentcore():
    """
    **Validates: Requirements 1.2**

    AgentCoreEvaluationsClient must use boto3.client("bedrock-agentcore"),
    not the hallucinated "agentcore-evaluations".

    FAILS on unfixed code because it uses agentcore-evaluations.
    """
    content = _read(REGRESSION_SUITE_PATH)

    # Find the AgentCoreEvaluationsClient class __init__
    eval_class_match = re.search(
        r"class AgentCoreEvaluationsClient.*?def __init__.*?boto3\.client\(\s*\"([^\"]+)\"",
        content,
        re.DOTALL,
    )
    assert eval_class_match is not None, "Could not find AgentCoreEvaluationsClient.__init__"

    client_name = eval_class_match.group(1)
    assert client_name == "bedrock-agentcore", (
        f"AgentCoreEvaluationsClient uses boto3.client(\"{client_name}\") "
        f"but should use boto3.client(\"bedrock-agentcore\")"
    )


# ---------------------------------------------------------------------------
# 1.3 Hallucinated client agentcore-runtime
# ---------------------------------------------------------------------------

def test_runtime_client_uses_bedrock_agentcore():
    """
    **Validates: Requirements 1.3**

    AgentCoreRuntimeClient must use boto3.client("bedrock-agentcore"),
    not the hallucinated "agentcore-runtime".

    FAILS on unfixed code because it uses agentcore-runtime.
    """
    content = _read(REGRESSION_SUITE_PATH)

    # Find the AgentCoreRuntimeClient class __init__
    runtime_class_match = re.search(
        r"class AgentCoreRuntimeClient.*?def __init__.*?boto3\.client\(\s*\"([^\"]+)\"",
        content,
        re.DOTALL,
    )
    assert runtime_class_match is not None, "Could not find AgentCoreRuntimeClient.__init__"

    client_name = runtime_class_match.group(1)
    assert client_name == "bedrock-agentcore", (
        f"AgentCoreRuntimeClient uses boto3.client(\"{client_name}\") "
        f"but should use boto3.client(\"bedrock-agentcore\")"
    )


# ---------------------------------------------------------------------------
# 1.4 Hallucinated method evaluate_response()
# ---------------------------------------------------------------------------

def test_evaluations_client_calls_evaluate():
    """
    **Validates: Requirements 1.4**

    The evaluations client's scoring method must call self._client.evaluate(
    not the hallucinated self._client.evaluate_response(.

    FAILS on unfixed code because it calls evaluate_response(.
    """
    content = _read(REGRESSION_SUITE_PATH)

    # Find the evaluate_response method body in AgentCoreEvaluationsClient
    eval_method_match = re.search(
        r"class AgentCoreEvaluationsClient.*?def evaluate_response\(.*?\).*?:"
        r"(.*?)(?=\nclass |\Z)",
        content,
        re.DOTALL,
    )
    assert eval_method_match is not None, (
        "Could not find evaluate_response method in AgentCoreEvaluationsClient"
    )

    method_body = eval_method_match.group(1)

    assert "self._client.evaluate(" in method_body, (
        "AgentCoreEvaluationsClient should call self._client.evaluate() "
        "but does not contain 'self._client.evaluate('"
    )
    assert "self._client.evaluate_response(" not in method_body, (
        "AgentCoreEvaluationsClient still calls self._client.evaluate_response() "
        "which is a hallucinated method"
    )


# ---------------------------------------------------------------------------
# 1.5 Hallucinated method invoke_agent()
# ---------------------------------------------------------------------------

def test_runtime_client_calls_invoke_agent_runtime():
    """
    **Validates: Requirements 1.5**

    The runtime client's query method must call self._client.invoke_agent_runtime(
    not the hallucinated self._client.invoke_agent(.

    FAILS on unfixed code because it calls invoke_agent(.
    """
    content = _read(REGRESSION_SUITE_PATH)

    # Find the query method body in AgentCoreRuntimeClient
    query_method_match = re.search(
        r"class AgentCoreRuntimeClient.*?def query\(.*?\).*?:"
        r"(.*?)(?=\nclass |\ndef |\Z)",
        content,
        re.DOTALL,
    )
    assert query_method_match is not None, (
        "Could not find query method in AgentCoreRuntimeClient"
    )

    method_body = query_method_match.group(1)

    assert "self._client.invoke_agent_runtime(" in method_body, (
        "AgentCoreRuntimeClient.query() should call self._client.invoke_agent_runtime() "
        "but does not contain 'self._client.invoke_agent_runtime('"
    )
    assert "self._client.invoke_agent(" not in method_body or \
           "self._client.invoke_agent_runtime(" in method_body, (
        "AgentCoreRuntimeClient.query() still calls self._client.invoke_agent() "
        "which is a hallucinated method"
    )


# ---------------------------------------------------------------------------
# 1.6 TTL data flow gap
# ---------------------------------------------------------------------------

def test_pruner_handles_ttl_mode():
    """
    **Validates: Requirements 1.6**

    The memory pruner must handle TTL mode when memory_ids is absent.
    It should have logic to query memories (list_memory_records) or handle
    ttl_mode when memory_ids is not provided in the event.

    FAILS on unfixed code because the pruner unconditionally requires memory_ids.
    """
    content = _read(PRUNER_PATH)

    # The pruner should have some form of TTL handling or list_memory_records call
    has_ttl_handling = (
        "list_memory_records" in content
        or "ttl_mode" in content
        or "MEMORY_TTL_DAYS" in content
        or "memory_ids" in content and "event.get(" in content
    )

    # Check that memory_ids is accessed safely (not unconditionally via event["memory_ids"])
    # The fixed code should use event.get("memory_ids") or check for its presence
    has_safe_memory_ids_access = (
        'event.get("memory_ids"' in content
        or "event.get('memory_ids'" in content
        or '"memory_ids" in event' in content
        or "'memory_ids' in event" in content
    )

    assert has_ttl_handling and has_safe_memory_ids_access, (
        "Memory pruner does not handle TTL mode when memory_ids is absent. "
        "The pruner unconditionally accesses event['memory_ids'] without "
        "fallback logic for TTL-based deletion."
    )


# ---------------------------------------------------------------------------
# 1.7 Batching mismatch
# ---------------------------------------------------------------------------

def test_scorer_returns_batched_below_threshold():
    """
    **Validates: Requirements 1.7**

    The memory scorer must return batched below_threshold payloads where each
    item contains a 'memory_ids' key (list of IDs), not individual
    {"memory_id": str, "score": float} items.

    FAILS on unfixed code because the scorer returns individual items.
    """
    content = _read(SCORER_PATH)

    # The scorer should batch below_threshold results with memory_ids key
    has_batching = (
        "memory_ids" in content
        and "CONSOLIDATION_BATCH_SIZE" in content
    )

    # Check that below_threshold items use memory_ids (plural) key
    # The fixed code should build batch objects with memory_ids lists
    has_memory_ids_key = re.search(
        r"""["']memory_ids["']\s*:""",
        content,
    )

    assert has_batching and has_memory_ids_key, (
        "Memory scorer does not return batched below_threshold payloads. "
        "The scorer returns individual {'memory_id': str, 'score': float} items "
        "but should return batched payloads with 'memory_ids' key."
    )


# ---------------------------------------------------------------------------
# 1.8 Bad package version
# ---------------------------------------------------------------------------

def test_package_json_has_correct_cdk_version():
    """
    **Validates: Requirements 1.8**

    package.json must specify aws-cdk version ^2.249.0, not ^2.1118.0.

    FAILS on unfixed code because it has ^2.1118.0.
    """
    content = _read(PACKAGE_JSON_PATH)
    pkg = json.loads(content)

    dev_deps = pkg.get("devDependencies", {})
    cdk_version = dev_deps.get("aws-cdk", "")

    assert cdk_version == "^2.249.0", (
        f"aws-cdk version is '{cdk_version}' but should be '^2.249.0'"
    )


# ---------------------------------------------------------------------------
# PBT: sampled_from over file-based checks
# ---------------------------------------------------------------------------

# Strategy pairs: (file_path, check_fn_name, description)
# Each check function verifies one expected correct value in a file.

_FILE_CHECKS = [
    ("iam_mismatch", CDK_STACK_PATH, "ListMemoryRecords in GDPR IAM policy"),
    ("eval_client", REGRESSION_SUITE_PATH, "bedrock-agentcore in AgentCoreEvaluationsClient"),
    ("runtime_client", REGRESSION_SUITE_PATH, "bedrock-agentcore in AgentCoreRuntimeClient"),
    ("eval_method", REGRESSION_SUITE_PATH, "self._client.evaluate( in evaluations client"),
    ("runtime_method", REGRESSION_SUITE_PATH, "self._client.invoke_agent_runtime( in runtime client"),
    ("ttl_mode", PRUNER_PATH, "TTL mode handling in pruner"),
    ("batching", SCORER_PATH, "batched memory_ids in scorer output"),
    ("pkg_version", PACKAGE_JSON_PATH, "aws-cdk ^2.249.0 in package.json"),
]


def _check_bug_condition(check_id: str, file_path: str) -> None:
    """Dispatch to the appropriate check for a given bug condition."""
    content = _read(file_path)

    if check_id == "iam_mismatch":
        gdpr_match = re.search(
            r"// GDPR.*?addToRolePolicy.*?actions:\s*\[(.*?)\]",
            content, re.DOTALL,
        )
        assert gdpr_match is not None, "GDPR IAM policy section not found"
        assert "ListMemoryRecords" in gdpr_match.group(1), (
            "GDPR IAM policy missing ListMemoryRecords"
        )

    elif check_id == "eval_client":
        m = re.search(
            r"class AgentCoreEvaluationsClient.*?boto3\.client\(\s*\"([^\"]+)\"",
            content, re.DOTALL,
        )
        assert m and m.group(1) == "bedrock-agentcore", (
            f"AgentCoreEvaluationsClient uses wrong client: {m.group(1) if m else 'not found'}"
        )

    elif check_id == "runtime_client":
        m = re.search(
            r"class AgentCoreRuntimeClient.*?boto3\.client\(\s*\"([^\"]+)\"",
            content, re.DOTALL,
        )
        assert m and m.group(1) == "bedrock-agentcore", (
            f"AgentCoreRuntimeClient uses wrong client: {m.group(1) if m else 'not found'}"
        )

    elif check_id == "eval_method":
        m = re.search(
            r"class AgentCoreEvaluationsClient.*?def evaluate_response\(.*?\).*?:(.*?)(?=\nclass |\Z)",
            content, re.DOTALL,
        )
        assert m is not None, "evaluate_response method not found"
        body = m.group(1)
        assert "self._client.evaluate(" in body and "self._client.evaluate_response(" not in body, (
            "Evaluations client should call self._client.evaluate(), not evaluate_response()"
        )

    elif check_id == "runtime_method":
        m = re.search(
            r"class AgentCoreRuntimeClient.*?def query\(.*?\).*?:(.*?)(?=\nclass |\ndef |\Z)",
            content, re.DOTALL,
        )
        assert m is not None, "query method not found"
        body = m.group(1)
        assert "self._client.invoke_agent_runtime(" in body, (
            "Runtime client should call self._client.invoke_agent_runtime(), not invoke_agent()"
        )

    elif check_id == "ttl_mode":
        has_safe_access = (
            'event.get("memory_ids"' in content
            or "event.get('memory_ids'" in content
            or '"memory_ids" in event' in content
            or "'memory_ids' in event" in content
        )
        has_ttl_logic = (
            "list_memory_records" in content
            or "ttl_mode" in content
            or "MEMORY_TTL_DAYS" in content
        )
        assert has_safe_access and has_ttl_logic, (
            "Pruner does not handle TTL mode when memory_ids is absent"
        )

    elif check_id == "batching":
        has_memory_ids_key = re.search(r"""["']memory_ids["']\s*:""", content)
        has_batch_size = "CONSOLIDATION_BATCH_SIZE" in content
        assert has_memory_ids_key and has_batch_size, (
            "Scorer does not return batched below_threshold with memory_ids key"
        )

    elif check_id == "pkg_version":
        pkg = json.loads(content)
        version = pkg.get("devDependencies", {}).get("aws-cdk", "")
        assert version == "^2.249.0", (
            f"aws-cdk version is '{version}', expected '^2.249.0'"
        )


@given(check=st.sampled_from(_FILE_CHECKS))
@settings(
    max_examples=len(_FILE_CHECKS),
    suppress_health_check=[HealthCheck.too_slow],
)
def test_all_bug_conditions_fixed(check):
    """
    **Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8**

    Property: For every bug condition identified in the validation report,
    the expected correct value should be present in the corresponding file.

    Uses hypothesis.strategies.sampled_from over the concrete set of
    (check_id, file_path, description) tuples.

    On UNFIXED code this test FAILS — confirming the bugs exist.
    On FIXED code this test PASSES — confirming the bugs are resolved.
    """
    check_id, file_path, description = check
    assert os.path.exists(file_path), f"File not found: {file_path}"
    _check_bug_condition(check_id, file_path)
