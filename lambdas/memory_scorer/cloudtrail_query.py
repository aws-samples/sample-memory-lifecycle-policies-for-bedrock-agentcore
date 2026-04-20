"""CloudTrail S3 log query module for memory access data.

Reads CloudTrail log files from S3, parses GetMemoryRecord events from
bedrock-agentcore, and aggregates per-record access data (lastAccessedAt
and accessCount). Modeled after cloudtrail-memory-logging/verify_trail_events.py
but adapted for Lambda use (no CLI, returns structured data).
"""

import gzip
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


@dataclass
class AccessData:
    """Per-memory-record access data extracted from CloudTrail logs."""

    last_accessed_at: datetime  # max eventTime across GetMemoryRecord events
    access_count: int  # count of GetMemoryRecord events


def _list_log_file_keys(
    s3_client,
    bucket: str,
    account_id: str,
    region: str,
    lookback_hours: int,
) -> list[str]:
    """List S3 keys matching the CloudTrail path pattern for the lookback window.

    CloudTrail delivers logs at:
        AWSLogs/{account}/CloudTrail/{region}/YYYY/MM/DD/*.json.gz

    Scans each day covered by the lookback window.
    """
    now = datetime.now(timezone.utc)
    keys: list[str] = []

    for hour_offset in range(lookback_hours + 1):
        dt = now - timedelta(hours=hour_offset)
        prefix = (
            f"AWSLogs/{account_id}/CloudTrail/{region}/"
            f"{dt.strftime('%Y/%m/%d')}/"
        )

        paginator = s3_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key.endswith(".json.gz"):
                    keys.append(key)

    logger.info("Found %d CloudTrail log file(s) in the last %d hour(s)", len(keys), lookback_hours)
    return keys


def _parse_log_file(s3_client, bucket: str, key: str) -> list[dict]:
    """Download, decompress, and parse a single gzipped JSON CloudTrail log file.

    Returns the list of event records from the file.
    Logs a warning and returns [] on any failure.
    """
    try:
        obj = s3_client.get_object(Bucket=bucket, Key=key)
        compressed = obj["Body"].read()
        raw = gzip.decompress(compressed)
        log_data = json.loads(raw)
        return log_data.get("Records", [])
    except Exception as exc:
        logger.warning("Could not read CloudTrail log file %s: %s", key, exc)
        return []


def _extract_memory_events(records: list[dict]) -> list[dict]:
    """Filter records for GetMemoryRecord events from bedrock-agentcore.

    Returns only records where eventName is 'GetMemoryRecord' and
    eventSource contains 'bedrock-agentcore'.
    """
    return [
        record
        for record in records
        if record.get("eventName") == "GetMemoryRecord"
        and "bedrock-agentcore" in record.get("eventSource", "")
    ]


def _aggregate_access_data(events: list[dict]) -> dict[str, AccessData]:
    """Group GetMemoryRecord events by memoryRecordId and compute access data.

    For each memoryRecordId:
    - last_accessed_at = max eventTime across events
    - access_count = number of events

    Events missing requestParameters.memoryRecordId or with unparseable
    eventTime are skipped with a warning.
    """
    aggregation: dict[str, dict] = {}

    for event in events:
        request_params = event.get("requestParameters", {})
        if not request_params:
            logger.debug("Skipping event with missing requestParameters")
            continue

        record_id = request_params.get("memoryRecordId")
        if not record_id:
            logger.debug("Skipping event with missing memoryRecordId")
            continue

        event_time_str = event.get("eventTime", "")
        if not event_time_str:
            logger.warning("Skipping event with missing eventTime for record %s", record_id)
            continue

        try:
            event_time = datetime.fromisoformat(event_time_str.replace("Z", "+00:00"))
        except (ValueError, TypeError) as exc:
            logger.warning(
                "Skipping event with unparseable eventTime '%s' for record %s: %s",
                event_time_str,
                record_id,
                exc,
            )
            continue

        if record_id not in aggregation:
            aggregation[record_id] = {
                "last_accessed_at": event_time,
                "access_count": 0,
            }

        aggregation[record_id]["access_count"] += 1
        if event_time > aggregation[record_id]["last_accessed_at"]:
            aggregation[record_id]["last_accessed_at"] = event_time

    return {
        record_id: AccessData(
            last_accessed_at=data["last_accessed_at"],
            access_count=data["access_count"],
        )
        for record_id, data in aggregation.items()
    }


def query_access_data(
    s3_client,
    bucket: str,
    account_id: str,
    region: str,
    lookback_hours: int,
) -> dict[str, AccessData]:
    """Query CloudTrail logs from S3 and return per-memoryRecordId access data.

    Orchestrates the full pipeline:
    1. List log file keys for the lookback window
    2. Parse each log file
    3. Filter for GetMemoryRecord events
    4. Aggregate access data per memoryRecordId

    Returns a dict mapping memoryRecordId -> AccessData.
    Records with no GetMemoryRecord events are absent from the dict.
    """
    keys = _list_log_file_keys(s3_client, bucket, account_id, region, lookback_hours)

    all_events: list[dict] = []
    for key in keys:
        records = _parse_log_file(s3_client, bucket, key)
        memory_events = _extract_memory_events(records)
        all_events.extend(memory_events)

    logger.info("Found %d GetMemoryRecord event(s) across %d log file(s)", len(all_events), len(keys))

    return _aggregate_access_data(all_events)
