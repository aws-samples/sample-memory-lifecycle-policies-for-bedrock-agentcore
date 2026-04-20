"""Run Output Writer Lambda handler.

Persists the full workflow run output to S3 for historical reference and
auditing.  The stored JSON includes TTL pruning results, scoring results,
consolidation results, metrics emission results, and a UTC timestamp.

The S3 key follows the pattern ``runs/{YYYY}/{MM}/{DD}/{execution-id}.json``
where date components are zero-padded and derived from the current UTC time.
"""

import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def build_s3_key(now_utc: datetime, execution_id: str) -> str:
    """Build the date-partitioned S3 key for a run output file.

    Args:
        now_utc: Current UTC datetime used for the date partition.
        execution_id: Unique identifier for this workflow execution.

    Returns:
        S3 key in the format ``runs/{YYYY}/{MM}/{DD}/{execution_id}.json``.
    """
    return (
        f"runs/{now_utc.year:04d}/{now_utc.month:02d}/{now_utc.day:02d}"
        f"/{execution_id}.json"
    )


def build_output(
    ttl_result: dict,
    scoring_result: dict,
    consolidation_results: list | None,
    metrics_result: dict,
    timestamp: str,
) -> dict:
    """Assemble the full run output JSON object.

    Args:
        ttl_result: TTL pruning result dict.
        scoring_result: Scoring result dict.
        consolidation_results: List of consolidation result dicts, or ``None``.
        metrics_result: Metrics emission result dict.
        timestamp: ISO 8601 UTC timestamp string for the write operation.

    Returns:
        Dict containing all input fields plus the ``timestamp``.
    """
    return {
        "timestamp": timestamp,
        "ttlResult": ttl_result,
        "scoringResult": scoring_result,
        "consolidationResults": consolidation_results,
        "metricsResult": metrics_result,
    }


def handler(event: dict, context) -> dict:
    """Write the full workflow run output to S3.

    Input event:
        {
            "ttlResult": {...},
            "scoringResult": {...},
            "consolidationResults": [...] | null,
            "metricsResult": {...}
        }

    Returns:
        {
            "status": "success" | "write_failure",
            "s3_key": str | null,
            "error": str | null
        }
    """
    now = datetime.now(timezone.utc)

    # Derive execution ID from Lambda request ID or fall back to UUID
    execution_id = getattr(context, "aws_request_id", None) or str(uuid.uuid4())

    ttl_result = event.get("ttlResult", {})
    scoring_result = event.get("scoringResult", {})
    consolidation_results = event.get("consolidationResults")
    metrics_result = event.get("metricsResult", {})

    timestamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    output = build_output(
        ttl_result,
        scoring_result,
        consolidation_results,
        metrics_result,
        timestamp,
    )

    s3_key = build_s3_key(now, execution_id)
    bucket_name = os.environ.get("RUN_OUTPUT_BUCKET_NAME", "")

    logger.info(json.dumps({
        "action": "run_output_write_start",
        "s3_key": s3_key,
        "bucket": bucket_name,
        "timestamp": now.isoformat(),
    }))

    s3 = boto3.client("s3")

    try:
        s3.put_object(
            Bucket=bucket_name,
            Key=s3_key,
            Body=json.dumps(output, default=str),
            ContentType="application/json",
        )
    except Exception as exc:
        error_msg = str(exc)
        logger.error(json.dumps({
            "action": "run_output_write_error",
            "error": error_msg,
            "s3_key": s3_key,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }))
        return {
            "status": "write_failure",
            "s3_key": None,
            "error": error_msg,
        }

    logger.info(json.dumps({
        "action": "run_output_write_complete",
        "s3_key": s3_key,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }))

    return {
        "status": "success",
        "s3_key": s3_key,
        "error": None,
    }
