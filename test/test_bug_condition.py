"""
Bug Condition Exploration Test — Property 1

Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 1.10, 1.11, 1.12, 1.13, 1.14

This test encodes the expected behavior: after the fix, NO hallucinated
AgentCore Memory patterns should be found in any affected file.

When run on UNFIXED code, this test MUST FAIL — failure confirms the bugs exist.
When run on FIXED code, this test MUST PASS — passing confirms the bugs are resolved.
"""

import os
from hypothesis import given, settings, HealthCheck
import hypothesis.strategies as st

# Resolve paths relative to the repository root (two levels up from this test file)
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CODE_ROOT = os.path.join(REPO_ROOT, "code")

# Full set of (file_path, hallucinated_pattern) pairs from the bug condition analysis.
# Each pair identifies a specific hallucinated SDK reference that should NOT exist
# in the codebase after the fix is applied.
BUG_CONDITION_PAIRS = [
    # memory_scorer/handler.py
    (os.path.join(CODE_ROOT, "lambdas", "memory_scorer", "handler.py"), 'boto3.client("agentcore-memory")'),
    (os.path.join(CODE_ROOT, "lambdas", "memory_scorer", "handler.py"), "client.tag_memory("),
    (os.path.join(CODE_ROOT, "lambdas", "memory_scorer", "handler.py"), "client.list_memories(agentId="),
    # memory_consolidator/handler.py
    (os.path.join(CODE_ROOT, "lambdas", "memory_consolidator", "handler.py"), 'boto3.client("agentcore-memory")'),
    (os.path.join(CODE_ROOT, "lambdas", "memory_consolidator", "handler.py"), "memory_client.get_memory(memoryId="),
    (os.path.join(CODE_ROOT, "lambdas", "memory_consolidator", "handler.py"), "memory_client.create_memory("),
    (os.path.join(CODE_ROOT, "lambdas", "memory_consolidator", "handler.py"), "memory_client.delete_memory(memoryId="),
    # memory_pruner/handler.py
    (os.path.join(CODE_ROOT, "lambdas", "memory_pruner", "handler.py"), 'boto3.client("agentcore-memory")'),
    (os.path.join(CODE_ROOT, "lambdas", "memory_pruner", "handler.py"), "client.delete_memory(memoryId="),
    # gdpr_deletion/handler.py
    (os.path.join(CODE_ROOT, "lambdas", "gdpr_deletion", "handler.py"), 'boto3.client("agentcore-memory")'),
    (os.path.join(CODE_ROOT, "lambdas", "gdpr_deletion", "handler.py"), "client.list_memories(userId="),
    (os.path.join(CODE_ROOT, "lambdas", "gdpr_deletion", "handler.py"), "client.delete_memory(memoryId="),
    # memory-lifecycle-stack.ts
    (os.path.join(CODE_ROOT, "lib", "memory-lifecycle-stack.ts"), "agentcore-memory:GetMemories"),
    (os.path.join(CODE_ROOT, "lib", "memory-lifecycle-stack.ts"), "agentcore-memory:TagMemory"),
    (os.path.join(CODE_ROOT, "lib", "memory-lifecycle-stack.ts"), "agentcore-memory:GetMemory"),
    (os.path.join(CODE_ROOT, "lib", "memory-lifecycle-stack.ts"), "agentcore-memory:CreateMemory"),
    (os.path.join(CODE_ROOT, "lib", "memory-lifecycle-stack.ts"), "agentcore-memory:DeleteMemory"),
    (os.path.join(CODE_ROOT, "lib", "memory-lifecycle-stack.ts"), "agentcore-memory:ListMemories"),
    (os.path.join(CODE_ROOT, "lib", "memory-lifecycle-stack.ts"), "arn:aws:agentcore-memory:"),
    # blog.md
    (os.path.join(REPO_ROOT, "blog.md"), 'boto3.client("agentcore-memory")'),
    (os.path.join(REPO_ROOT, "blog.md"), "client.list_memories(userId="),
    (os.path.join(REPO_ROOT, "blog.md"), "client.delete_memory(memoryId="),
    (os.path.join(REPO_ROOT, "blog.md"), "agentcore-memory:GetMemories"),
    (os.path.join(REPO_ROOT, "blog.md"), "agentcore-memory:TagMemory"),
]


@given(pair=st.sampled_from(BUG_CONDITION_PAIRS))
@settings(
    max_examples=len(BUG_CONDITION_PAIRS),
    suppress_health_check=[HealthCheck.too_slow],
)
def test_no_hallucinated_patterns_in_files(pair):
    """
    **Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8,
    1.9, 1.10, 1.11, 1.12, 1.13, 1.14**

    Property: For every (file_path, hallucinated_pattern) pair identified by
    the bug condition, the hallucinated pattern should NOT be found in the file.

    On UNFIXED code this test FAILS — confirming the bugs exist.
    On FIXED code this test PASSES — confirming the bugs are resolved.
    """
    file_path, pattern = pair

    assert os.path.exists(file_path), f"File not found: {file_path}"

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert pattern not in content, (
        f"Hallucinated pattern found in {os.path.relpath(file_path, REPO_ROOT)}: "
        f"'{pattern}'"
    )
