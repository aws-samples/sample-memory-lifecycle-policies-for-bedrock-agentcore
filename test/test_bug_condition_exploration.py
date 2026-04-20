"""
Bug Condition Exploration Tests — Property 1: Memory Lifecycle Runtime Failures

**Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 1.10, 1.11**

This test encodes the EXPECTED CORRECT behavior for all 8 bug conditions
identified in the memory lifecycle validation report. Each assertion checks
what the code SHOULD do after the fix.

- On UNFIXED code these tests MUST FAIL — failure confirms the bugs exist.
- On FIXED code these tests MUST PASS — passing confirms the bugs are resolved.

Bug conditions tested:
  Bug 1 — Datetime TypeError (Scorer): datetime.fromtimestamp(datetime(...)) raises TypeError
  Bug 2 — Datetime TypeError (Pruner): same TypeError in TTL mode
  Bug 3 — Missing requestIdentifier (Consolidator): batch_create_memory_records missing required field
  Bug 4 — Content Overwrite (Scorer): batch_update_memory_records overwrites original memory text
  Bug 5 — Missing Pagination (Scorer): only first page of list_memory_records processed
  Bug 6 — Missing Pagination (Pruner TTL): only first page processed in TTL mode
  Bug 7 — Missing Pagination (GDPR): only first page processed in GDPR deletion
  Bug 8 — STS Implicit Dependency (Scorer): sts.get_caller_identity() called instead of env var
"""

import json
import os
import sys
import uuid
import importlib
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Path setup — ensure Lambda handler modules are importable
# ---------------------------------------------------------------------------
CODE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LAMBDAS_ROOT = os.path.join(CODE_ROOT, "lambdas")

# Add paths so we can import the handlers
sys.path.insert(0, LAMBDAS_ROOT)
sys.path.insert(0, os.path.join(LAMBDAS_ROOT, "memory_scorer"))
sys.path.insert(0, os.path.join(LAMBDAS_ROOT, "memory_pruner"))
sys.path.insert(0, os.path.join(LAMBDAS_ROOT, "memory_consolidator"))
sys.path.insert(0, os.path.join(LAMBDAS_ROOT, "gdpr_deletion"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_memory_record(record_id: str, created_at: datetime) -> dict:
    """Build a mock memory record summary as returned by list_memory_records."""
    return {
        "memoryRecordId": record_id,
        "createdAt": created_at,
        "content": {"text": f"Original content for {record_id}"},
    }


SCORER_ENV_VARS = {
    "TRAIL_BUCKET_NAME": "my-trail-bucket",
    "AWS_REGION": "us-east-1",
    "CONSOLIDATION_BATCH_SIZE": "10",
    "BEDROCK_MODEL_ID": "anthropic.claude-v2",
    "RELEVANCE_THRESHOLD": "0.3",
    "PRUNE_DAYS": "45",
    "AWS_ACCOUNT_ID": "123456789012",
}


def make_scorer_mocks(memory_records, paginated=False, page1_records=None, page2_records=None):
    """Create mock boto3 clients for scorer tests."""
    mock_client = MagicMock()

    if paginated:
        call_count = {"n": 0}

        def mock_list(**kwargs):
            call_count["n"] += 1
            if "nextToken" not in kwargs:
                return {
                    "memoryRecordSummaries": page1_records,
                    "nextToken": "page2-token",
                }
            else:
                return {
                    "memoryRecordSummaries": page2_records,
                }

        mock_client.list_memory_records.side_effect = mock_list
    else:
        mock_client.list_memory_records.return_value = {
            "memoryRecordSummaries": memory_records,
        }

    mock_client.batch_update_memory_records.return_value = {}

    mock_s3 = MagicMock()
    mock_s3.get_paginator.return_value.paginate.return_value = [{"Contents": []}]

    mock_sts = MagicMock()
    mock_sts.get_caller_identity.return_value = {"Account": "123456789012"}

    clients_created = []

    def mock_boto3_client(service_name, **kwargs):
        clients_created.append(service_name)
        if service_name == "bedrock-agentcore":
            return mock_client
        elif service_name == "s3":
            return mock_s3
        elif service_name == "sts":
            return mock_sts
        return MagicMock()

    return mock_client, mock_sts, mock_boto3_client, clients_created


# ---------------------------------------------------------------------------
# Bug 1 — Datetime TypeError (Scorer)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("created_at", [
    datetime(2020, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
    datetime(2023, 6, 15, 12, 30, 0, tzinfo=timezone.utc),
    datetime(2025, 12, 31, 23, 59, 59, tzinfo=timezone.utc),
])
def test_bug1_scorer_datetime_no_typeerror(created_at):
    """
    **Validates: Requirements 1.1, 1.3**

    Mock list_memory_records to return records where createdAt is a datetime
    object (as boto3 does). Call scorer handler(). Assert it does NOT raise
    TypeError.

    On unfixed code, datetime.fromtimestamp(datetime(...)) raises
    TypeError: an integer is required (got type datetime.datetime).
    """
    record_id = "rec-" + uuid.uuid4().hex[:8]
    mock_memory_record = make_memory_record(record_id, created_at)

    mock_client, mock_sts, mock_boto3_client, _ = make_scorer_mocks([mock_memory_record])

    with patch("boto3.client", side_effect=mock_boto3_client), \
         patch.dict(os.environ, SCORER_ENV_VARS, clear=False):
        import memory_scorer.handler as scorer_mod
        importlib.reload(scorer_mod)

        event = {"agent_id": "agent-001", "memory_id": "mem-001"}

        # This should NOT raise TypeError
        result = scorer_mod.handler(event, None)
        assert result["status"] == "success", f"Scorer failed: {result.get('error')}"
        assert result["scored_memories"] == 1


# ---------------------------------------------------------------------------
# Bug 2 — Datetime TypeError (Pruner)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("created_at", [
    datetime(2020, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
    datetime(2023, 6, 15, 12, 30, 0, tzinfo=timezone.utc),
    datetime(2025, 12, 31, 23, 59, 59, tzinfo=timezone.utc),
])
def test_bug2_pruner_datetime_no_typeerror(created_at):
    """
    **Validates: Requirements 1.2, 1.4**

    Mock list_memory_records to return records where createdAt is a datetime
    object. Call pruner handler() in TTL mode. Assert it does NOT raise
    TypeError.

    On unfixed code, datetime.fromtimestamp(datetime(...)) raises TypeError.
    """
    record_id = "rec-" + uuid.uuid4().hex[:8]
    mock_memory_record = make_memory_record(record_id, created_at)

    mock_client = MagicMock()
    mock_client.list_memory_records.return_value = {
        "memoryRecordSummaries": [mock_memory_record],
    }
    mock_client.delete_memory_record.return_value = {}

    with patch("boto3.client", return_value=mock_client), \
         patch.dict(os.environ, {"MEMORY_TTL_DAYS": "90"}, clear=False):
        import memory_pruner.handler as pruner_mod
        importlib.reload(pruner_mod)

        # TTL mode: no memory_ids in event
        event = {"memory_id": "mem-001", "agent_id": "agent-001"}

        # This should NOT raise TypeError
        result = pruner_mod.handler(event, None)
        assert result["status"] in ("success", "partial_failure", "failure")


# ---------------------------------------------------------------------------
# Bug 3 — Missing requestIdentifier (Consolidator)
# ---------------------------------------------------------------------------

def test_bug3_consolidator_request_identifier_present():
    """
    **Validates: Requirements 1.5**

    Mock all boto3 calls. Call consolidator handler(). Capture the
    batch_create_memory_records call args. Assert requestIdentifier is
    present in each record.

    On unfixed code, requestIdentifier is missing from the record dict.
    """
    mock_client = MagicMock()

    mock_client.get_memory_record.return_value = {
        "memoryRecord": {
            "memoryRecordId": "rec-001",
            "content": {"text": "User prefers dark mode"},
            "createdAt": datetime(2025, 1, 15, tzinfo=timezone.utc),
        }
    }

    mock_client.batch_create_memory_records.return_value = {
        "successfulRecords": [{"memoryRecordId": "consolidated-001"}],
        "failedRecords": [],
    }
    mock_client.delete_memory_record.return_value = {}

    # Mock Bedrock
    mock_bedrock = MagicMock()
    mock_bedrock_response = {"body": MagicMock()}
    mock_bedrock_response["body"].read.return_value = json.dumps({
        "content": [{"text": json.dumps({
            "summary": "User prefers dark mode",
            "confidence": 0.95,
            "key_facts": ["dark mode preference"],
        })}]
    }).encode()
    mock_bedrock.invoke_model.return_value = mock_bedrock_response

    def mock_boto3_client(service_name, **kwargs):
        if service_name == "bedrock-agentcore":
            return mock_client
        elif service_name == "bedrock-runtime":
            return mock_bedrock
        return MagicMock()

    with patch("boto3.client", side_effect=mock_boto3_client):
        import memory_consolidator.handler as consolidator_mod
        importlib.reload(consolidator_mod)

        event = {
            "memory_ids": ["rec-001"],
            "memory_id": "mem-001",
            "agent_id": "agent-001",
            "bedrock_model_id": "anthropic.claude-v2",
        }

        result = consolidator_mod.handler(event, None)

        # Verify batch_create_memory_records was called
        assert mock_client.batch_create_memory_records.called, (
            "batch_create_memory_records was not called"
        )

        # Get the call args
        call_kwargs = mock_client.batch_create_memory_records.call_args
        records = call_kwargs.kwargs.get("records", [])

        assert len(records) > 0, "No records passed to batch_create_memory_records"

        for record in records:
            assert "requestIdentifier" in record, (
                f"requestIdentifier missing from batch_create_memory_records record. "
                f"Record keys: {list(record.keys())}"
            )


# ---------------------------------------------------------------------------
# Bug 4 — Content Overwrite (Scorer)
# ---------------------------------------------------------------------------

def test_bug4_scorer_does_not_call_batch_update():
    """
    **Validates: Requirements 1.6**

    Mock all boto3 calls. Call scorer handler(). Assert
    batch_update_memory_records is NOT called at all.

    On unfixed code, it IS called with score metadata in the content field,
    destroying original memory text.
    """
    created_at = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    mock_memory_record = make_memory_record("rec-content-001", created_at)

    mock_client, mock_sts, mock_boto3_client, _ = make_scorer_mocks([mock_memory_record])

    with patch("boto3.client", side_effect=mock_boto3_client), \
         patch.dict(os.environ, SCORER_ENV_VARS, clear=False):
        import memory_scorer.handler as scorer_mod
        importlib.reload(scorer_mod)

        event = {"agent_id": "agent-001", "memory_id": "mem-001"}
        result = scorer_mod.handler(event, None)

        # The scorer should NOT call batch_update_memory_records
        assert not mock_client.batch_update_memory_records.called, (
            "Scorer called batch_update_memory_records, which overwrites "
            "original memory content with score metadata. "
            f"Call args: {mock_client.batch_update_memory_records.call_args}"
        )


# ---------------------------------------------------------------------------
# Bug 5 — Missing Pagination (Scorer)
# ---------------------------------------------------------------------------

def test_bug5_scorer_paginates_all_records():
    """
    **Validates: Requirements 1.7**

    Mock list_memory_records to return a nextToken on the first page and
    more records on the second page. Call scorer handler(). Assert all
    records from both pages are scored.

    On unfixed code, only the first page is processed.
    """
    page1_records = [
        make_memory_record(f"rec-p1-{i}", datetime(2025, 7, 1, tzinfo=timezone.utc))
        for i in range(3)
    ]
    page2_records = [
        make_memory_record(f"rec-p2-{i}", datetime(2025, 7, 1, tzinfo=timezone.utc))
        for i in range(2)
    ]

    mock_client, mock_sts, mock_boto3_client, _ = make_scorer_mocks(
        [], paginated=True, page1_records=page1_records, page2_records=page2_records
    )

    with patch("boto3.client", side_effect=mock_boto3_client), \
         patch.dict(os.environ, SCORER_ENV_VARS, clear=False):
        import memory_scorer.handler as scorer_mod
        importlib.reload(scorer_mod)

        event = {"agent_id": "agent-001", "memory_id": "mem-001"}
        result = scorer_mod.handler(event, None)

        total_expected = len(page1_records) + len(page2_records)
        assert result["scored_memories"] == total_expected, (
            f"Scorer only scored {result['scored_memories']} records but expected "
            f"{total_expected}. Only the first page was processed (missing pagination)."
        )
        assert result["total_memories"] == total_expected


# ---------------------------------------------------------------------------
# Bug 6 — Missing Pagination (Pruner TTL)
# ---------------------------------------------------------------------------

def test_bug6_pruner_ttl_paginates_all_records():
    """
    **Validates: Requirements 1.8**

    Mock list_memory_records to return a nextToken on the first page.
    Call pruner handler() in TTL mode. Assert all records from both pages
    are checked for TTL expiration.

    On unfixed code, only the first page is processed.
    """
    old_date = datetime(2020, 1, 1, tzinfo=timezone.utc)

    page1_records = [
        make_memory_record(f"rec-p1-{i}", old_date) for i in range(3)
    ]
    page2_records = [
        make_memory_record(f"rec-p2-{i}", old_date) for i in range(2)
    ]

    def mock_list_memory_records(**kwargs):
        if "nextToken" not in kwargs:
            return {
                "memoryRecordSummaries": page1_records,
                "nextToken": "page2-token",
            }
        else:
            return {
                "memoryRecordSummaries": page2_records,
            }

    mock_client = MagicMock()
    mock_client.list_memory_records.side_effect = mock_list_memory_records
    mock_client.delete_memory_record.return_value = {}

    with patch("boto3.client", return_value=mock_client), \
         patch.dict(os.environ, {"MEMORY_TTL_DAYS": "90"}, clear=False):
        import memory_pruner.handler as pruner_mod
        importlib.reload(pruner_mod)

        event = {"memory_id": "mem-001", "agent_id": "agent-001"}
        result = pruner_mod.handler(event, None)

        total_expected = len(page1_records) + len(page2_records)
        assert result.get("expired_count", 0) == total_expected, (
            f"Pruner only found {result.get('expired_count', 0)} expired records "
            f"but expected {total_expected}. Only the first page was processed "
            f"(missing pagination)."
        )


# ---------------------------------------------------------------------------
# Bug 7 — Missing Pagination (GDPR)
# ---------------------------------------------------------------------------

def test_bug7_gdpr_paginates_all_records():
    """
    **Validates: Requirements 1.9**

    Mock list_memory_records to return a nextToken on the first page.
    Call GDPR handler(). Assert all records from both pages are deleted.

    On unfixed code, only the first page is processed.
    """
    page1_records = [{"memoryRecordId": f"rec-p1-{i}"} for i in range(3)]
    page2_records = [{"memoryRecordId": f"rec-p2-{i}"} for i in range(2)]

    def mock_list_memory_records(**kwargs):
        if "nextToken" not in kwargs:
            return {
                "memoryRecordSummaries": page1_records,
                "nextToken": "page2-token",
            }
        else:
            return {
                "memoryRecordSummaries": page2_records,
            }

    mock_client = MagicMock()
    mock_client.list_memory_records.side_effect = mock_list_memory_records
    mock_client.delete_memory_record.return_value = {}

    with patch("boto3.client", return_value=mock_client):
        import gdpr_deletion.handler as gdpr_mod
        importlib.reload(gdpr_mod)

        event = {"user_id": "user-001", "memory_id": "mem-001"}
        result = gdpr_mod.handler(event, None)

        total_expected = len(page1_records) + len(page2_records)
        assert result["deleted_count"] == total_expected, (
            f"GDPR handler only deleted {result['deleted_count']} records but "
            f"expected {total_expected}. Only the first page was processed "
            f"(missing pagination)."
        )


# ---------------------------------------------------------------------------
# Bug 8 — STS Implicit Dependency (Scorer)
# ---------------------------------------------------------------------------

def test_bug8_scorer_uses_env_var_not_sts():
    """
    **Validates: Requirements 1.10**

    Mock boto3 clients. Call scorer handler() with AWS_ACCOUNT_ID env var
    set. Assert sts.get_caller_identity() is NOT called and
    os.environ["AWS_ACCOUNT_ID"] is used instead.

    On unfixed code, STS is called regardless of the env var.
    """
    created_at = datetime(2025, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
    mock_memory_record = make_memory_record("rec-sts-001", created_at)

    mock_client, mock_sts, mock_boto3_client, clients_created = make_scorer_mocks(
        [mock_memory_record]
    )

    with patch("boto3.client", side_effect=mock_boto3_client), \
         patch.dict(os.environ, SCORER_ENV_VARS, clear=False):
        import memory_scorer.handler as scorer_mod
        importlib.reload(scorer_mod)

        event = {"agent_id": "agent-001", "memory_id": "mem-001"}
        result = scorer_mod.handler(event, None)

        # STS should NOT be called when AWS_ACCOUNT_ID env var is set
        assert not mock_sts.get_caller_identity.called, (
            "Scorer called sts.get_caller_identity() even though AWS_ACCOUNT_ID "
            "env var is set. The scorer should read the account ID from the "
            "environment variable instead of making an STS API call."
        )
        assert "sts" not in clients_created, (
            "Scorer created an STS client even though AWS_ACCOUNT_ID env var is set."
        )
