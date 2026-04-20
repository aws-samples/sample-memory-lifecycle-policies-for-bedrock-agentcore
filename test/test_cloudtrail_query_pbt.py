"""
Property-Based Tests for CloudTrail Query Module — Event Aggregation Correctness

Feature: cloudtrail-access-scoring
Property 1: CloudTrail event aggregation correctness

**Validates: Requirements 2.3, 7.1**

Generates random lists of CloudTrail event records with varying memoryRecordId
and eventTime values, then verifies that _aggregate_access_data produces the
correct last_accessed_at (max eventTime) and access_count (event count) per record.
"""

import os
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta

import pytest
from hypothesis import given, settings, HealthCheck
import hypothesis.strategies as st

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LAMBDAS_ROOT = os.path.join(REPO_ROOT, "lambdas")
sys.path.insert(0, LAMBDAS_ROOT)

from memory_scorer.cloudtrail_query import _aggregate_access_data, _extract_memory_events


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Generate memory record IDs as short alphanumeric strings
memory_record_id_st = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789-",
    min_size=1,
    max_size=20,
)

# Generate eventTime as ISO 8601 timestamps within a reasonable range
event_time_st = st.datetimes(
    min_value=datetime(2024, 1, 1),
    max_value=datetime(2025, 12, 31),
    timezones=st.just(timezone.utc),
).map(lambda dt: dt.strftime("%Y-%m-%dT%H:%M:%SZ"))


def cloudtrail_event_st():
    """Strategy to generate a single valid CloudTrail GetMemoryRecord event."""
    return st.fixed_dictionaries({
        "eventName": st.just("GetMemoryRecord"),
        "eventSource": st.just("bedrock-agentcore.amazonaws.com"),
        "requestParameters": st.fixed_dictionaries({
            "memoryRecordId": memory_record_id_st,
        }),
        "eventTime": event_time_st,
    })


# List of 1 to 50 CloudTrail events
cloudtrail_events_list_st = st.lists(
    cloudtrail_event_st(),
    min_size=1,
    max_size=50,
)


# ---------------------------------------------------------------------------
# Property Test
# ---------------------------------------------------------------------------

class TestAggregationCorrectness:
    """
    Feature: cloudtrail-access-scoring
    Property 1: CloudTrail event aggregation correctness

    **Validates: Requirements 2.3, 7.1**
    """

    @given(events=cloudtrail_events_list_st)
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_aggregation_produces_correct_last_accessed_at_and_access_count(
        self, events
    ):
        """
        **Validates: Requirements 2.3, 7.1**

        For any list of valid GetMemoryRecord events, _aggregate_access_data
        must produce a mapping where each memoryRecordId has:
        - last_accessed_at equal to the maximum eventTime across its events
        - access_count equal to the total count of its events
        """
        # Compute expected values from the raw events
        expected: dict[str, dict] = defaultdict(lambda: {"times": [], "count": 0})
        for event in events:
            record_id = event["requestParameters"]["memoryRecordId"]
            event_time_str = event["eventTime"]
            event_time = datetime.fromisoformat(
                event_time_str.replace("Z", "+00:00")
            )
            expected[record_id]["times"].append(event_time)
            expected[record_id]["count"] += 1

        # Run the function under test
        result = _aggregate_access_data(events)

        # Verify all expected record IDs are present
        assert set(result.keys()) == set(expected.keys()), (
            f"Record ID mismatch: result has {set(result.keys())}, "
            f"expected {set(expected.keys())}"
        )

        # Verify each record's aggregated values
        for record_id, exp in expected.items():
            access_data = result[record_id]
            expected_max_time = max(exp["times"])
            expected_count = exp["count"]

            assert access_data.last_accessed_at == expected_max_time, (
                f"Record {record_id}: last_accessed_at={access_data.last_accessed_at}, "
                f"expected={expected_max_time}"
            )
            assert access_data.access_count == expected_count, (
                f"Record {record_id}: access_count={access_data.access_count}, "
                f"expected={expected_count}"
            )


# ---------------------------------------------------------------------------
# Property 2: Absent records excluded from access data lookup
# ---------------------------------------------------------------------------


class TestAbsentRecordsExcluded:
    """
    Feature: cloudtrail-access-scoring
    Property 2: Absent records excluded from access data lookup

    **Validates: Requirements 7.2**
    """

    @given(
        events=st.lists(cloudtrail_event_st(), min_size=0, max_size=50),
        absent_id=st.text(
            alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ_",
            min_size=1,
            max_size=20,
        ),
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_absent_record_id_not_in_access_data(self, events, absent_id):
        """
        **Validates: Requirements 7.2**

        For any list of CloudTrail event records and any memoryRecordId that
        does not appear in any event's requestParameters.memoryRecordId,
        the resulting access data lookup shall NOT contain an entry for that ID.
        """
        # Collect all record IDs present in the events
        present_ids = {
            event["requestParameters"]["memoryRecordId"]
            for event in events
            if "requestParameters" in event
            and "memoryRecordId" in event["requestParameters"]
        }

        # Use assume to ensure the absent_id is truly absent from all events
        from hypothesis import assume
        assume(absent_id not in present_ids)

        # Run the function under test
        result = _aggregate_access_data(events)

        # The absent ID must not appear in the result
        assert absent_id not in result, (
            f"memoryRecordId '{absent_id}' should not be in access data, "
            f"but was found with: {result[absent_id]}"
        )


# ---------------------------------------------------------------------------
# Property 3: Event filtering correctness
# ---------------------------------------------------------------------------

# Strategies for mixed CloudTrail events with varied eventName and eventSource

EVENT_NAMES = [
    "GetMemoryRecord",
    "ListMemoryRecords",
    "PutObject",
    "GetObject",
    "CreateMemory",
    "DeleteMemoryRecord",
    "AssumeRole",
]

EVENT_SOURCES = [
    "bedrock-agentcore.amazonaws.com",
    "s3.amazonaws.com",
    "sts.amazonaws.com",
    "lambda.amazonaws.com",
    "bedrock-agentcore.us-east-1.amazonaws.com",
    "dynamodb.amazonaws.com",
]


def mixed_cloudtrail_event_st():
    """Strategy to generate a single CloudTrail event with varied eventName and eventSource."""
    return st.fixed_dictionaries({
        "eventName": st.sampled_from(EVENT_NAMES),
        "eventSource": st.sampled_from(EVENT_SOURCES),
        "requestParameters": st.fixed_dictionaries({
            "memoryRecordId": memory_record_id_st,
        }),
        "eventTime": event_time_st,
    })


mixed_cloudtrail_events_list_st = st.lists(
    mixed_cloudtrail_event_st(),
    min_size=0,
    max_size=50,
)


class TestEventFilteringCorrectness:
    """
    Feature: cloudtrail-access-scoring
    Property 3: Event filtering correctness

    **Validates: Requirements 2.2**
    """

    @given(events=mixed_cloudtrail_events_list_st)
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_filtering_returns_exactly_matching_events(self, events):
        """
        **Validates: Requirements 2.2**

        For any mixed list of CloudTrail events with various eventName and
        eventSource values, _extract_memory_events must return exactly those
        records where eventName == "GetMemoryRecord" AND eventSource contains
        "bedrock-agentcore".
        """
        # Compute expected filtered events using the same criteria
        expected = [
            event for event in events
            if event.get("eventName") == "GetMemoryRecord"
            and "bedrock-agentcore" in event.get("eventSource", "")
        ]

        # Run the function under test
        result = _extract_memory_events(events)

        # Verify the result matches exactly
        assert len(result) == len(expected), (
            f"Expected {len(expected)} filtered events, got {len(result)}"
        )

        # Verify each returned event is one we expected (order preserved)
        for i, (res, exp) in enumerate(zip(result, expected)):
            assert res is exp, (
                f"Event at index {i} differs: result={res}, expected={exp}"
            )

        # Verify no non-matching events snuck in
        for event in result:
            assert event.get("eventName") == "GetMemoryRecord", (
                f"Filtered event has wrong eventName: {event.get('eventName')}"
            )
            assert "bedrock-agentcore" in event.get("eventSource", ""), (
                f"Filtered event has wrong eventSource: {event.get('eventSource')}"
            )
