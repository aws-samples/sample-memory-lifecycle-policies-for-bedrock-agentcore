"""
Unit tests for CloudTrail query module edge cases.

Tests S3 prefix generation, corrupted file handling, empty inputs,
missing memoryRecordId, and unparseable eventTime.

**Validates: Requirements 2.5**
"""

import gzip
import io
import json
import logging
import os
import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LAMBDAS_ROOT = os.path.join(REPO_ROOT, "lambdas")
sys.path.insert(0, LAMBDAS_ROOT)

from memory_scorer.cloudtrail_query import (
    _list_log_file_keys,
    _parse_log_file,
    _aggregate_access_data,
    query_access_data,
    AccessData,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_s3_client_with_keys(keys_by_prefix: dict[str, list[str]]) -> MagicMock:
    """Create a mock S3 client whose paginator returns keys grouped by prefix."""
    s3 = MagicMock()
    paginator = MagicMock()

    def paginate(Bucket, Prefix):
        matched = keys_by_prefix.get(Prefix, [])
        return [{"Contents": [{"Key": k} for k in matched]}] if matched else [{"Contents": []}]

    paginator.paginate = MagicMock(side_effect=paginate)
    s3.get_paginator.return_value = paginator
    return s3


def _make_gzipped_log(records: list[dict]) -> bytes:
    """Create gzipped JSON bytes mimicking a CloudTrail log file."""
    raw = json.dumps({"Records": records}).encode()
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        gz.write(raw)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Tests: _list_log_file_keys — S3 prefix generation
# ---------------------------------------------------------------------------

class TestListLogFileKeys:
    """Test S3 prefix generation for various lookback windows and dates."""

    @patch("memory_scorer.cloudtrail_query.datetime")
    def test_single_day_lookback(self, mock_dt):
        """A 0-hour lookback should generate a single prefix for today."""
        fixed_now = datetime(2025, 6, 15, 10, 0, 0, tzinfo=timezone.utc)
        mock_dt.now.return_value = fixed_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        prefix = "AWSLogs/123456789012/CloudTrail/us-east-1/2025/06/15/"
        s3 = _make_s3_client_with_keys({
            prefix: [prefix + "file1.json.gz"],
        })

        keys = _list_log_file_keys(s3, "my-bucket", "123456789012", "us-east-1", 0)
        assert keys == [prefix + "file1.json.gz"]

    @patch("memory_scorer.cloudtrail_query.datetime")
    def test_lookback_spans_two_days(self, mock_dt):
        """A 25-hour lookback at 01:00 UTC should cover today and yesterday."""
        fixed_now = datetime(2025, 6, 15, 1, 0, 0, tzinfo=timezone.utc)
        mock_dt.now.return_value = fixed_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        prefix_today = "AWSLogs/111/CloudTrail/us-west-2/2025/06/15/"
        prefix_yesterday = "AWSLogs/111/CloudTrail/us-west-2/2025/06/14/"

        s3 = _make_s3_client_with_keys({
            prefix_today: [prefix_today + "a.json.gz"],
            prefix_yesterday: [prefix_yesterday + "b.json.gz"],
        })

        keys = _list_log_file_keys(s3, "bucket", "111", "us-west-2", 25)
        assert prefix_today + "a.json.gz" in keys
        assert prefix_yesterday + "b.json.gz" in keys

    @patch("memory_scorer.cloudtrail_query.datetime")
    def test_lookback_across_month_boundary(self, mock_dt):
        """Lookback that crosses a month boundary generates correct prefixes."""
        fixed_now = datetime(2025, 7, 1, 2, 0, 0, tzinfo=timezone.utc)
        mock_dt.now.return_value = fixed_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        prefix_jul = "AWSLogs/999/CloudTrail/eu-west-1/2025/07/01/"
        prefix_jun = "AWSLogs/999/CloudTrail/eu-west-1/2025/06/30/"

        s3 = _make_s3_client_with_keys({
            prefix_jul: [prefix_jul + "x.json.gz"],
            prefix_jun: [prefix_jun + "y.json.gz"],
        })

        keys = _list_log_file_keys(s3, "bucket", "999", "eu-west-1", 25)
        assert prefix_jul + "x.json.gz" in keys
        assert prefix_jun + "y.json.gz" in keys

    @patch("memory_scorer.cloudtrail_query.datetime")
    def test_non_json_gz_files_are_excluded(self, mock_dt):
        """Only .json.gz files should be returned."""
        fixed_now = datetime(2025, 6, 15, 10, 0, 0, tzinfo=timezone.utc)
        mock_dt.now.return_value = fixed_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        prefix = "AWSLogs/123/CloudTrail/us-east-1/2025/06/15/"
        s3 = _make_s3_client_with_keys({
            prefix: [
                prefix + "log.json.gz",
                prefix + "digest.json",
                prefix + "readme.txt",
            ],
        })

        keys = _list_log_file_keys(s3, "bucket", "123", "us-east-1", 0)
        assert keys == [prefix + "log.json.gz"]


# ---------------------------------------------------------------------------
# Tests: _parse_log_file — corrupted/unreadable files
# ---------------------------------------------------------------------------

class TestParseLogFile:
    """Test handling of corrupted/unreadable log files."""

    def test_valid_gzipped_log_file(self):
        """A valid gzipped log file returns its records."""
        records = [{"eventName": "GetMemoryRecord", "eventTime": "2025-06-15T10:00:00Z"}]
        compressed = _make_gzipped_log(records)

        s3 = MagicMock()
        s3.get_object.return_value = {"Body": io.BytesIO(compressed)}

        result = _parse_log_file(s3, "bucket", "key.json.gz")
        assert result == records

    def test_corrupted_gzip_returns_empty_and_logs_warning(self, caplog):
        """A corrupted (non-gzip) file returns [] and logs a warning."""
        s3 = MagicMock()
        s3.get_object.return_value = {"Body": io.BytesIO(b"not-gzip-data")}

        with caplog.at_level(logging.WARNING):
            result = _parse_log_file(s3, "bucket", "bad.json.gz")

        assert result == []
        assert any("Could not read CloudTrail log file" in msg for msg in caplog.messages)

    def test_malformed_json_returns_empty_and_logs_warning(self, caplog):
        """A file that decompresses but contains invalid JSON returns [] and logs warning."""
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
            gz.write(b"{not valid json!!!")
        compressed = buf.getvalue()

        s3 = MagicMock()
        s3.get_object.return_value = {"Body": io.BytesIO(compressed)}

        with caplog.at_level(logging.WARNING):
            result = _parse_log_file(s3, "bucket", "malformed.json.gz")

        assert result == []
        assert any("Could not read CloudTrail log file" in msg for msg in caplog.messages)

    def test_s3_get_object_exception_returns_empty_and_logs_warning(self, caplog):
        """An S3 download failure returns [] and logs a warning."""
        s3 = MagicMock()
        s3.get_object.side_effect = Exception("Access Denied")

        with caplog.at_level(logging.WARNING):
            result = _parse_log_file(s3, "bucket", "denied.json.gz")

        assert result == []
        assert any("Could not read CloudTrail log file" in msg for msg in caplog.messages)


# ---------------------------------------------------------------------------
# Tests: _aggregate_access_data — edge cases
# ---------------------------------------------------------------------------

class TestAggregateAccessData:
    """Test aggregation edge cases: empty input, missing fields, bad timestamps."""

    def test_empty_events_returns_empty_dict(self):
        """An empty event list returns an empty dict."""
        result = _aggregate_access_data([])
        assert result == {}

    def test_event_missing_request_parameters_is_skipped(self):
        """Events without requestParameters are skipped."""
        events = [
            {
                "eventName": "GetMemoryRecord",
                "eventSource": "bedrock-agentcore.amazonaws.com",
                "eventTime": "2025-06-15T10:00:00Z",
                # no requestParameters
            }
        ]
        result = _aggregate_access_data(events)
        assert result == {}

    def test_event_missing_memory_record_id_is_skipped(self):
        """Events with requestParameters but no memoryRecordId are skipped."""
        events = [
            {
                "eventName": "GetMemoryRecord",
                "eventSource": "bedrock-agentcore.amazonaws.com",
                "eventTime": "2025-06-15T10:00:00Z",
                "requestParameters": {"memoryId": "mem-123"},
                # no memoryRecordId
            }
        ]
        result = _aggregate_access_data(events)
        assert result == {}

    def test_event_with_unparseable_event_time_is_skipped(self, caplog):
        """Events with unparseable eventTime are skipped with a warning."""
        events = [
            {
                "eventName": "GetMemoryRecord",
                "eventSource": "bedrock-agentcore.amazonaws.com",
                "eventTime": "not-a-timestamp",
                "requestParameters": {"memoryRecordId": "rec-001"},
            }
        ]
        with caplog.at_level(logging.WARNING):
            result = _aggregate_access_data(events)

        assert result == {}
        assert any("unparseable eventTime" in msg for msg in caplog.messages)

    def test_event_with_empty_event_time_is_skipped(self, caplog):
        """Events with empty eventTime string are skipped with a warning."""
        events = [
            {
                "eventName": "GetMemoryRecord",
                "eventSource": "bedrock-agentcore.amazonaws.com",
                "eventTime": "",
                "requestParameters": {"memoryRecordId": "rec-002"},
            }
        ]
        with caplog.at_level(logging.WARNING):
            result = _aggregate_access_data(events)

        assert result == {}

    def test_mixed_valid_and_invalid_events(self):
        """Valid events are aggregated while invalid ones are skipped."""
        events = [
            {
                "eventName": "GetMemoryRecord",
                "eventSource": "bedrock-agentcore.amazonaws.com",
                "eventTime": "2025-06-15T10:00:00Z",
                "requestParameters": {"memoryRecordId": "rec-good"},
            },
            {
                "eventName": "GetMemoryRecord",
                "eventSource": "bedrock-agentcore.amazonaws.com",
                "eventTime": "garbage",
                "requestParameters": {"memoryRecordId": "rec-bad-time"},
            },
            {
                "eventName": "GetMemoryRecord",
                "eventSource": "bedrock-agentcore.amazonaws.com",
                "eventTime": "2025-06-15T12:00:00Z",
                "requestParameters": {},  # missing memoryRecordId
            },
        ]
        result = _aggregate_access_data(events)
        assert len(result) == 1
        assert "rec-good" in result
        assert result["rec-good"].access_count == 1


# ---------------------------------------------------------------------------
# Tests: query_access_data — empty log file set
# ---------------------------------------------------------------------------

class TestQueryAccessData:
    """Test query_access_data returns empty dict when no log files exist."""

    @patch("memory_scorer.cloudtrail_query._list_log_file_keys")
    def test_empty_log_file_set_returns_empty_dict(self, mock_list_keys):
        """When no log files are found, query_access_data returns {}."""
        mock_list_keys.return_value = []
        s3 = MagicMock()

        result = query_access_data(s3, "bucket", "123456789012", "us-east-1", 25)

        assert result == {}
        mock_list_keys.assert_called_once_with(s3, "bucket", "123456789012", "us-east-1", 25)
