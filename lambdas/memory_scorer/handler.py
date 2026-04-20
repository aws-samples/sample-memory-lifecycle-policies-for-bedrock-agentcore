"""Memory Scorer Lambda handler.

Retrieves all memories for a given agent from AgentCore Memory, computes
relevance scores using a weighted decay formula, and returns memory IDs
that fall below the relevance threshold.  The scorer is read-only — scores
are tracked in the return value and structured log output only.
"""

import json
import logging
import math
import os
import sys
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError, EndpointConnectionError

# The shared module is deployed as a Lambda Layer at runtime.
# The sys.path fallback enables local development and testing.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from shared.constants import (
    PRUNE_DAYS_DEFAULT,
    RELEVANCE_THRESHOLD_DEFAULT,
    W_RECENCY_DEFAULT,
    W_ACCESS_DEFAULT,
    W_FREQUENCY_DEFAULT,
    MAX_ACCESS_BASELINE_DEFAULT,
    TRAIL_LOOKBACK_HOURS_DEFAULT,
    decay_rate_from_prune_days,
)
from cloudtrail_query import AccessData, query_access_data

logger = logging.getLogger()
logger.setLevel(logging.INFO)

LEDGER_KEY = "ledger/access_ledger.json"


def compute_relevance_score(
    created_at: datetime,
    last_accessed_at: datetime,
    access_count: int,
    decay_rate: float,
    now: datetime,
    w_recency: float = 0.4,
    w_access: float = 0.35,
    w_frequency: float = 0.25,
    max_access_baseline: int = 50,
) -> float:
    """Compute relevance score using the 3-term weighted decay formula.

    score = w_recency * exp(-decay_rate * days_since_creation)
          + w_access  * exp(-decay_rate * days_since_last_access)
          + w_frequency * min(access_count / max_access_baseline, 1.0)

    Returns a float in [0.0, 1.0] when weights sum to 1.0.

    Raises ValueError if max_access_baseline is zero or negative.
    """
    if max_access_baseline <= 0:
        raise ValueError(
            f"max_access_baseline must be a positive integer, got: {max_access_baseline}"
        )

    days_since_creation = max((now - created_at).total_seconds() / 86400, 0.0)
    days_since_last_access = max((now - last_accessed_at).total_seconds() / 86400, 0.0)

    recency_term = w_recency * math.exp(-decay_rate * days_since_creation)
    access_term = w_access * math.exp(-decay_rate * days_since_last_access)
    frequency_term = w_frequency * min(access_count / max_access_baseline, 1.0)

    score = recency_term + access_term + frequency_term
    return score


def read_access_ledger(s3_client, bucket: str, key: str) -> dict[str, AccessData]:
    """Read the previous access ledger from S3.

    Returns a dict mapping memory_record_id → AccessData.
    Returns an empty dict if the object does not exist (404/NoSuchKey)
    or on any other error.
    """
    try:
        response = s3_client.get_object(Bucket=bucket, Key=key)
        body = response["Body"].read()
        data = json.loads(body)
        entries = data.get("entries", {})
        result: dict[str, AccessData] = {}
        for record_id, entry in entries.items():
            result[record_id] = AccessData(
                last_accessed_at=datetime.fromisoformat(entry["last_accessed_at"]),
                access_count=entry["access_count"],
            )
        return result
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "")
        if error_code in ("NoSuchKey", "404"):
            logger.info("No previous access ledger found at s3://%s/%s (first run).", bucket, key)
        else:
            logger.warning("Failed to read access ledger from s3://%s/%s: %s", bucket, key, exc)
        return {}
    except Exception as exc:
        logger.warning("Failed to read access ledger from s3://%s/%s: %s", bucket, key, exc)
        return {}


def merge_access_data(
    fresh: dict[str, AccessData],
    previous: dict[str, AccessData],
) -> dict[str, AccessData]:
    """Merge fresh CloudTrail data with previous ledger.

    For IDs in both: max(last_accessed_at), sum(access_count).
    For IDs in only one source: keep as-is.
    """
    merged: dict[str, AccessData] = {}
    all_ids = set(fresh.keys()) | set(previous.keys())
    for record_id in all_ids:
        if record_id in fresh and record_id in previous:
            f = fresh[record_id]
            p = previous[record_id]
            merged[record_id] = AccessData(
                last_accessed_at=max(f.last_accessed_at, p.last_accessed_at),
                access_count=f.access_count + p.access_count,
            )
        elif record_id in fresh:
            merged[record_id] = fresh[record_id]
        else:
            merged[record_id] = previous[record_id]
    return merged


def write_access_ledger(
    s3_client,
    bucket: str,
    key: str,
    ledger: dict[str, AccessData],
    active_record_ids: set[str],
) -> None:
    """Write updated ledger to S3, excluding records not in active_record_ids.

    Serializes to JSON with version, updated_at, and entries fields.
    Logs error on failure but does not raise.
    """
    now = datetime.now(timezone.utc)
    filtered_entries = {}
    for record_id, data in ledger.items():
        if record_id in active_record_ids:
            filtered_entries[record_id] = {
                "last_accessed_at": data.last_accessed_at.isoformat(),
                "access_count": data.access_count,
            }

    ledger_json = {
        "version": 1,
        "updated_at": now.isoformat(),
        "entries": filtered_entries,
    }

    try:
        s3_client.put_object(
            Bucket=bucket,
            Key=key,
            Body=json.dumps(ledger_json),
            ContentType="application/json",
        )
        logger.info("Wrote access ledger with %d entries to s3://%s/%s", len(filtered_entries), bucket, key)
    except Exception as exc:
        logger.error("Failed to write access ledger to s3://%s/%s: %s", bucket, key, exc)


def handler(event: dict, context) -> dict:
    """Score all memories for an agent and return those below the relevance threshold.

    Input event:
        {
            "agent_id": str,
            "memory_id": str,
            "relevance_threshold": float
        }

    Returns:
        {
            "status": "success" | "failure",
            "agent_id": str,
            "total_memories": int,
            "scored_memories": int,
            "below_threshold": [{"memory_ids": [str], "memory_id": str, "agent_id": str, "bedrock_model_id": str}],
            "error": str | None
        }
    """
    agent_id = event["agent_id"]
    memory_id = event["memory_id"]
    consolidation_batch_size = int(os.environ.get("CONSOLIDATION_BATCH_SIZE", "10"))
    bedrock_model_id = os.environ.get("BEDROCK_MODEL_ID", "")
    relevance_threshold = float(os.environ.get(
        "RELEVANCE_THRESHOLD", str(RELEVANCE_THRESHOLD_DEFAULT)
    ))
    prune_days = int(os.environ.get("PRUNE_DAYS", str(PRUNE_DAYS_DEFAULT)))
    decay_rate = decay_rate_from_prune_days(prune_days, relevance_threshold)
    trail_bucket_name = os.environ.get("TRAIL_BUCKET_NAME", "")
    trail_lookback_hours = int(os.environ.get("TRAIL_LOOKBACK_HOURS", str(TRAIL_LOOKBACK_HOURS_DEFAULT)))
    w_recency = float(os.environ.get("W_RECENCY", str(W_RECENCY_DEFAULT)))
    w_access = float(os.environ.get("W_ACCESS", str(W_ACCESS_DEFAULT)))
    w_frequency = float(os.environ.get("W_FREQUENCY", str(W_FREQUENCY_DEFAULT)))
    max_access_baseline = int(os.environ.get("MAX_ACCESS_BASELINE", str(MAX_ACCESS_BASELINE_DEFAULT)))
    now = datetime.now(timezone.utc)

    logger.info(json.dumps({
        "action": "score_start",
        "agent_id": agent_id,
        "relevance_threshold": relevance_threshold,
        "timestamp": now.isoformat(),
    }))

    run_output_bucket = os.environ.get("RUN_OUTPUT_BUCKET_NAME", "")

    # Build access data lookup from CloudTrail logs before scoring
    access_data = {}
    s3_client = boto3.client("s3")
    if trail_bucket_name:
        try:
            account_id = os.environ.get("AWS_ACCOUNT_ID", "")
            region = os.environ.get("AWS_REGION", "us-east-1")
            access_data = query_access_data(s3_client, trail_bucket_name, account_id, region, trail_lookback_hours)
        except Exception as exc:
            logger.error("CloudTrail query failed: %s. Proceeding with empty access data.", exc)
            access_data = {}
    else:
        logger.error("TRAIL_BUCKET_NAME not set. Skipping CloudTrail query.")

    # Read previous access ledger from S3 and merge with fresh CloudTrail data
    if run_output_bucket:
        previous_ledger = read_access_ledger(s3_client, run_output_bucket, LEDGER_KEY)
        access_data = merge_access_data(access_data, previous_ledger)

    try:
        client = boto3.client("bedrock-agentcore")
        memories = []
        next_token = None
        while True:
            kwargs = {
                "memoryId": memory_id,
                "namespace": agent_id,
            }
            if next_token is not None:
                kwargs["nextToken"] = next_token
            response = client.list_memory_records(**kwargs)
            memories.extend(response.get("memoryRecordSummaries", []))
            next_token = response.get("nextToken")
            if not next_token:
                break
    except (ClientError, EndpointConnectionError, Exception) as exc:
        error_msg = f"AgentCore Memory unreachable: {exc}"
        logger.error(json.dumps({
            "action": "score_error",
            "agent_id": agent_id,
            "error": error_msg,
            "timestamp": now.isoformat(),
        }))
        return {
            "status": "failure",
            "agent_id": agent_id,
            "total_memories": 0,
            "scored_memories": 0,
            "below_threshold": [],
            "error": error_msg,
        }

    total_memories = len(memories)
    below_threshold_ids = []
    scored_count = 0

    for memory in memories:
        record_id = memory["memoryRecordId"]
        created_at = memory["createdAt"]
        # Look up access data from CloudTrail; fall back to createdAt and 0
        # when the record has no GetMemoryRecord events.
        record_access = access_data.get(record_id)
        if record_access:
            last_accessed_at = record_access.last_accessed_at
            access_count = record_access.access_count
        else:
            last_accessed_at = memory["createdAt"]
            access_count = 0

        score = compute_relevance_score(
            created_at, last_accessed_at, access_count, decay_rate, now,
            w_recency=w_recency, w_access=w_access, w_frequency=w_frequency,
            max_access_baseline=max_access_baseline,
        )
        scored_at = now.isoformat()

        scored_count += 1

        logger.info(json.dumps({
            "action": "score_memory",
            "memory_id": record_id,
            "agent_id": agent_id,
            "score": score,
            "timestamp": scored_at,
        }))

        if score < relevance_threshold:
            below_threshold_ids.append(record_id)

    # Batch below-threshold IDs into groups of CONSOLIDATION_BATCH_SIZE
    # Each batch matches the consolidator's expected input schema
    below_threshold = [
        {
            "memory_ids": below_threshold_ids[i:i + consolidation_batch_size],
            "memory_id": memory_id,
            "agent_id": agent_id,
            "bedrock_model_id": bedrock_model_id,
        }
        for i in range(0, len(below_threshold_ids), consolidation_batch_size)
    ]

    logger.info(json.dumps({
        "action": "score_complete",
        "agent_id": agent_id,
        "total_memories": total_memories,
        "scored_memories": scored_count,
        "below_threshold_count": len(below_threshold_ids),
        "timestamp": now.isoformat(),
    }))

    # Write updated access ledger back to S3, filtered to only active record IDs
    if run_output_bucket:
        active_record_ids = {m["memoryRecordId"] for m in memories}
        write_access_ledger(s3_client, run_output_bucket, LEDGER_KEY, access_data, active_record_ids)

    return {
        "status": "success",
        "agent_id": agent_id,
        "total_memories": total_memories,
        "scored_memories": scored_count,
        "below_threshold": below_threshold,
        "error": None,
    }
