"""Metrics Emitter Lambda handler.

Receives workflow results (TTL pruning, scoring, consolidation) and publishes
real metrics to CloudWatch under the ``MemoryLifecycle`` namespace.

Returns a summary with the published metrics or a graceful error response
if the PutMetricData call fails.
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logger = logging.getLogger()
logger.setLevel(logging.INFO)

METRICS_NAMESPACE = "MemoryLifecycle"


def compute_metrics(
    ttl_result: dict,
    scoring_result: dict,
    consolidation_results: list | None,
) -> dict:
    """Pure function that computes metrics from workflow results.

    Args:
        ttl_result: TTL pruning result with ``deleted_count``.
        scoring_result: Scoring result with ``total_memories``.
        consolidation_results: List of consolidation result dicts, each with
            ``status`` and ``deleted_count``. May be ``None`` if no
            consolidation was needed.

    Returns:
        Dict with keys ``MemoriesProcessed``, ``MemoriesPruned``,
        ``MemoriesConsolidated``, and ``WorkflowExecutionStatus``.
    """
    memories_processed = scoring_result["total_memories"]
    memories_pruned = ttl_result["deleted_count"]

    if consolidation_results:
        consolidated_total = sum(
            r["deleted_count"]
            for r in consolidation_results
            if r["status"] in ("success", "partial_failure")
        )
        failure_count = sum(
            1 for r in consolidation_results if r["status"] == "failure"
        )
        workflow_status = 0 if failure_count > 0 else 1
    else:
        consolidated_total = 0
        workflow_status = 1

    return {
        "MemoriesProcessed": memories_processed,
        "MemoriesPruned": memories_pruned,
        "MemoriesConsolidated": consolidated_total,
        "WorkflowExecutionStatus": workflow_status,
    }


def handler(event: dict, context) -> dict:
    """Publish workflow metrics to CloudWatch.

    Input event:
        {
            "ttlResult": {"deleted_count": int, ...},
            "scoringResult": {"total_memories": int, ...},
            "consolidationResults": [{"status": str, "deleted_count": int, ...}] | null
        }

    Returns:
        {
            "status": "success" | "metrics_emission_failure",
            "metrics_published": {...} | null,
            "error": str | null
        }
    """
    now = datetime.now(timezone.utc)

    ttl_result = event.get("ttlResult", {})
    scoring_result = event.get("scoringResult", {})
    consolidation_results = event.get("consolidationResults")

    logger.info(json.dumps({
        "action": "metrics_emission_start",
        "timestamp": now.isoformat(),
    }))

    metrics = compute_metrics(ttl_result, scoring_result, consolidation_results)

    cloudwatch = boto3.client("cloudwatch")

    metric_data = [
        {
            "MetricName": "MemoriesProcessed",
            "Value": metrics["MemoriesProcessed"],
            "Unit": "Count",
        },
        {
            "MetricName": "MemoriesPruned",
            "Value": metrics["MemoriesPruned"],
            "Unit": "Count",
        },
        {
            "MetricName": "MemoriesConsolidated",
            "Value": metrics["MemoriesConsolidated"],
            "Unit": "Count",
        },
        {
            "MetricName": "WorkflowExecutionStatus",
            "Value": metrics["WorkflowExecutionStatus"],
            "Unit": "None",
        },
    ]

    try:
        cloudwatch.put_metric_data(
            Namespace=METRICS_NAMESPACE,
            MetricData=metric_data,
        )
    except Exception as exc:
        error_msg = str(exc)
        logger.error(json.dumps({
            "action": "metrics_emission_error",
            "error": error_msg,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }))
        return {
            "status": "metrics_emission_failure",
            "metrics_published": None,
            "error": error_msg,
        }

    logger.info(json.dumps({
        "action": "metrics_emission_complete",
        "metrics": metrics,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }))

    return {
        "status": "success",
        "metrics_published": metrics,
        "error": None,
    }
