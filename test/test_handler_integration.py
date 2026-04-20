"""
Unit tests for handler integration with CloudTrail access data.

Validates: Requirements 6.1, 6.2, 6.3

Tests:
- CloudTrail query is called before memory iteration when TRAIL_BUCKET_NAME is set
- Access data from CloudTrail is used for scoring each record
- Fallback behavior when memoryRecordId not in access data (uses createdAt and 0)
- Handler proceeds with empty access data when TRAIL_BUCKET_NAME is missing
"""

import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LAMBDAS_ROOT = os.path.join(REPO_ROOT, "lambdas")
sys.path.insert(0, LAMBDAS_ROOT)


# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------
AGENT_ID = "agent-test-001"
MEMORY_ID = "mem-test-001"
EVENT = {"agent_id": AGENT_ID, "memory_id": MEMORY_ID}

# Epoch timestamps for deterministic testing
CREATED_EPOCH_1 = datetime(2025, 6, 1, 0, 0, 0, tzinfo=timezone.utc).timestamp()
CREATED_EPOCH_2 = datetime(2025, 6, 5, 0, 0, 0, tzinfo=timezone.utc).timestamp()

BASE_ENV = {
    "PRUNE_DAYS": "45",
    "RELEVANCE_THRESHOLD": "0.3",
    "CONSOLIDATION_BATCH_SIZE": "10",
    "BEDROCK_MODEL_ID": "model-test",
    "AWS_REGION": "us-east-1",
    "W_RECENCY": "0.4",
    "W_ACCESS": "0.35",
    "W_FREQUENCY": "0.25",
    "MAX_ACCESS_BASELINE": "50",
    "TRAIL_LOOKBACK_HOURS": "25",
}


def _make_memory_record(record_id: str, created_at_epoch: float) -> dict:
    """Build a minimal MemoryRecordSummary dict."""
    return {"memoryRecordId": record_id, "createdAt": created_at_epoch}


def _build_mock_boto3(memory_records: list[dict]):
    """Return a mock boto3.client factory and the individual service mocks.

    The factory dispatches on the service name:
      - "bedrock-agentcore" → agentcore_mock
      - "s3"               → s3_mock
      - "sts"              → sts_mock
    """
    agentcore_mock = MagicMock()
    agentcore_mock.list_memory_records.return_value = {
        "memoryRecordSummaries": memory_records,
    }
    agentcore_mock.batch_update_memory_records.return_value = {}

    s3_mock = MagicMock()
    sts_mock = MagicMock()
    sts_mock.get_caller_identity.return_value = {"Account": "123456789012"}

    def client_factory(service_name, **kwargs):
        if service_name == "bedrock-agentcore":
            return agentcore_mock
        if service_name == "s3":
            return s3_mock
        if service_name == "sts":
            return sts_mock
        raise ValueError(f"Unexpected service: {service_name}")

    return client_factory, agentcore_mock, s3_mock, sts_mock


def _get_positional_arg(call_args, index):
    """Extract a positional argument from a mock call.

    The handler calls compute_relevance_score with the first 5 args
    positionally: (created_at, last_accessed_at, access_count, decay_rate, now)
    and the rest as keyword args.
    """
    return call_args[0][index]


# ===================================================================
# Test 1: CloudTrail query is called before memory iteration
# Validates: Requirement 6.1
# ===================================================================
class TestCloudTrailQueryCalledBeforeIteration:
    """Verify that query_access_data runs before list_memory_records."""

    def test_query_called_before_memory_listing(self, monkeypatch):
        """When TRAIL_BUCKET_NAME is set, query_access_data must be called
        before the handler starts iterating over memory records."""
        env = {**BASE_ENV, "TRAIL_BUCKET_NAME": "my-trail-bucket"}
        monkeypatch.setattr(os, "environ", env)

        records = [_make_memory_record("rec-1", CREATED_EPOCH_1)]
        client_factory, agentcore_mock, s3_mock, sts_mock = _build_mock_boto3(records)

        call_order: list[str] = []

        def tracking_query(*args, **kwargs):
            call_order.append("query_access_data")
            return {}

        original_list = agentcore_mock.list_memory_records

        def tracking_list(*args, **kwargs):
            call_order.append("list_memory_records")
            return original_list(*args, **kwargs)

        agentcore_mock.list_memory_records = tracking_list

        with patch("boto3.client", side_effect=client_factory):
            with patch(
                "memory_scorer.handler.query_access_data",
                side_effect=tracking_query,
            ):
                with patch(
                    "memory_scorer.handler.compute_relevance_score",
                    return_value=0.5,
                ):
                    from memory_scorer.handler import handler

                    handler(EVENT, None)

        assert "query_access_data" in call_order, "query_access_data was not called"
        assert "list_memory_records" in call_order, "list_memory_records was not called"
        assert call_order.index("query_access_data") < call_order.index(
            "list_memory_records"
        ), "query_access_data must be called before list_memory_records"


# ===================================================================
# Test 2: Access data lookup is used during scoring
# Validates: Requirement 6.2
# ===================================================================
class TestAccessDataUsedDuringScoring:
    """Verify that pre-fetched access data is looked up for each record."""

    def test_access_data_values_affect_score(self, monkeypatch):
        """When query_access_data returns data for a record, the handler
        should use that data (last_accessed_at, access_count) for scoring."""
        env = {**BASE_ENV, "TRAIL_BUCKET_NAME": "my-trail-bucket"}
        monkeypatch.setattr(os, "environ", env)

        records = [
            _make_memory_record("rec-1", CREATED_EPOCH_1),
            _make_memory_record("rec-2", CREATED_EPOCH_2),
        ]
        client_factory, agentcore_mock, _, _ = _build_mock_boto3(records)

        from cloudtrail_query import AccessData

        last_access_time = datetime(2025, 6, 14, 12, 0, 0, tzinfo=timezone.utc)
        mock_access_data = {
            "rec-1": AccessData(last_accessed_at=last_access_time, access_count=30),
            "rec-2": AccessData(last_accessed_at=last_access_time, access_count=10),
        }

        with patch("boto3.client", side_effect=client_factory):
            with patch(
                "memory_scorer.handler.query_access_data",
                return_value=mock_access_data,
            ):
                with patch(
                    "memory_scorer.handler.compute_relevance_score",
                    return_value=0.5,
                ) as mock_score:
                    from memory_scorer.handler import handler

                    handler(EVENT, None)

        # The scoring function should have been called twice (once per record)
        assert mock_score.call_count == 2

        # Verify rec-1 was scored with CloudTrail access data
        # Handler passes: (created_at, last_accessed_at, access_count, decay_rate, now, ...)
        first_access_count = _get_positional_arg(mock_score.call_args_list[0], 2)
        assert first_access_count == 30

        first_last_accessed = _get_positional_arg(mock_score.call_args_list[0], 1)
        assert first_last_accessed == last_access_time

        # Verify rec-2 was scored with CloudTrail access data
        second_access_count = _get_positional_arg(mock_score.call_args_list[1], 2)
        assert second_access_count == 10


# ===================================================================
# Test 3: Fallback when memoryRecordId not in access data
# Validates: Requirement 6.3
# ===================================================================
class TestFallbackWhenRecordNotInAccessData:
    """When a record is absent from access_data, the handler falls back
    to createdAt as last_accessed_at and 0 as access_count."""

    def test_fallback_uses_created_at_and_zero(self, monkeypatch):
        env = {**BASE_ENV, "TRAIL_BUCKET_NAME": "my-trail-bucket"}
        monkeypatch.setattr(os, "environ", env)

        records = [_make_memory_record("rec-missing", CREATED_EPOCH_1)]
        client_factory, agentcore_mock, _, _ = _build_mock_boto3(records)

        # Return access data that does NOT contain "rec-missing"
        mock_access_data = {}

        with patch("boto3.client", side_effect=client_factory):
            with patch(
                "memory_scorer.handler.query_access_data",
                return_value=mock_access_data,
            ):
                with patch(
                    "memory_scorer.handler.compute_relevance_score",
                    return_value=0.5,
                ) as mock_score:
                    from memory_scorer.handler import handler

                    handler(EVENT, None)

        assert mock_score.call_count == 1

        # Handler passes positional: (created_at, last_accessed_at, access_count, ...)
        created_at = _get_positional_arg(mock_score.call_args, 0)
        last_accessed_at = _get_positional_arg(mock_score.call_args, 1)
        access_count = _get_positional_arg(mock_score.call_args, 2)

        assert access_count == 0, "Fallback access_count should be 0"
        assert created_at == last_accessed_at, (
            "When record is absent from access_data, last_accessed_at "
            "should equal created_at"
        )

    def test_mixed_present_and_absent_records(self, monkeypatch):
        """With two records, one in access_data and one not, verify each
        gets the correct treatment."""
        env = {**BASE_ENV, "TRAIL_BUCKET_NAME": "my-trail-bucket"}
        monkeypatch.setattr(os, "environ", env)

        records = [
            _make_memory_record("rec-present", CREATED_EPOCH_1),
            _make_memory_record("rec-absent", CREATED_EPOCH_2),
        ]
        client_factory, agentcore_mock, _, _ = _build_mock_boto3(records)

        from cloudtrail_query import AccessData

        last_access_time = datetime(2025, 6, 14, 0, 0, 0, tzinfo=timezone.utc)
        mock_access_data = {
            "rec-present": AccessData(
                last_accessed_at=last_access_time, access_count=42
            ),
        }

        with patch("boto3.client", side_effect=client_factory):
            with patch(
                "memory_scorer.handler.query_access_data",
                return_value=mock_access_data,
            ):
                with patch(
                    "memory_scorer.handler.compute_relevance_score",
                    return_value=0.5,
                ) as mock_score:
                    from memory_scorer.handler import handler

                    handler(EVENT, None)

        assert mock_score.call_count == 2

        # First call: rec-present — should use CloudTrail data
        first_access_count = _get_positional_arg(mock_score.call_args_list[0], 2)
        assert first_access_count == 42

        first_last_accessed = _get_positional_arg(mock_score.call_args_list[0], 1)
        assert first_last_accessed == last_access_time

        # Second call: rec-absent — should fall back to 0
        second_access_count = _get_positional_arg(mock_score.call_args_list[1], 2)
        assert second_access_count == 0

        # last_accessed_at should equal created_at for the absent record
        second_created = _get_positional_arg(mock_score.call_args_list[1], 0)
        second_last_accessed = _get_positional_arg(mock_score.call_args_list[1], 1)
        assert second_created == second_last_accessed


# ===================================================================
# Test 4: Handler proceeds with empty access data when TRAIL_BUCKET_NAME missing
# Validates: Requirement 6.1, 6.3
# ===================================================================
class TestEmptyAccessDataWhenBucketMissing:
    """When TRAIL_BUCKET_NAME is not set, the handler skips the CloudTrail
    query and proceeds with an empty access_data dict."""

    def test_no_trail_bucket_skips_query(self, monkeypatch):
        """query_access_data should NOT be called when TRAIL_BUCKET_NAME
        is missing from the environment."""
        env = {k: v for k, v in BASE_ENV.items() if k != "TRAIL_BUCKET_NAME"}
        monkeypatch.setattr(os, "environ", env)

        records = [_make_memory_record("rec-1", CREATED_EPOCH_1)]
        client_factory, agentcore_mock, _, _ = _build_mock_boto3(records)

        with patch("boto3.client", side_effect=client_factory):
            with patch(
                "memory_scorer.handler.query_access_data",
            ) as mock_query:
                with patch(
                    "memory_scorer.handler.compute_relevance_score",
                    return_value=0.5,
                ):
                    from memory_scorer.handler import handler

                    handler(EVENT, None)

        mock_query.assert_not_called()

    def test_no_trail_bucket_uses_fallback_for_all_records(self, monkeypatch):
        """Without TRAIL_BUCKET_NAME, every record should fall back to
        createdAt / access_count=0."""
        env = {k: v for k, v in BASE_ENV.items() if k != "TRAIL_BUCKET_NAME"}
        monkeypatch.setattr(os, "environ", env)

        records = [
            _make_memory_record("rec-a", CREATED_EPOCH_1),
            _make_memory_record("rec-b", CREATED_EPOCH_2),
        ]
        client_factory, agentcore_mock, _, _ = _build_mock_boto3(records)

        with patch("boto3.client", side_effect=client_factory):
            with patch(
                "memory_scorer.handler.query_access_data",
            ) as mock_query:
                with patch(
                    "memory_scorer.handler.compute_relevance_score",
                    return_value=0.5,
                ) as mock_score:
                    from memory_scorer.handler import handler

                    handler(EVENT, None)

        mock_query.assert_not_called()
        assert mock_score.call_count == 2

        # Both records should have access_count=0
        for i in range(mock_score.call_count):
            access_count = _get_positional_arg(mock_score.call_args_list[i], 2)
            assert access_count == 0, f"Record {i} should have access_count=0"

    def test_empty_trail_bucket_skips_query(self, monkeypatch):
        """An empty string for TRAIL_BUCKET_NAME should also skip the query."""
        env = {**BASE_ENV, "TRAIL_BUCKET_NAME": ""}
        monkeypatch.setattr(os, "environ", env)

        records = [_make_memory_record("rec-1", CREATED_EPOCH_1)]
        client_factory, agentcore_mock, _, _ = _build_mock_boto3(records)

        with patch("boto3.client", side_effect=client_factory):
            with patch(
                "memory_scorer.handler.query_access_data",
            ) as mock_query:
                with patch(
                    "memory_scorer.handler.compute_relevance_score",
                    return_value=0.5,
                ):
                    from memory_scorer.handler import handler

                    handler(EVENT, None)

        mock_query.assert_not_called()
