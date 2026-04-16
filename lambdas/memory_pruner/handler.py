"""Memory Pruner Lambda handler.

Iterates through a list of memory record IDs and deletes each from AgentCore Memory.
Continues processing on individual deletion failures (no short-circuit).
Returns a summary with deleted_count, failed_count, and failed_memory_ids.
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError, EndpointConnectionError

# The shared module is deployed as a Lambda Layer at runtime.
# The sys.path fallback enables local development and testing.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event: dict, context) -> dict:
    """Prune memories by deleting each record from AgentCore Memory.

    Input event:
        {
            "memory_id": str,          # memory resource container ID
            "memory_ids": [str],       # list of memoryRecordId values to delete
            "agent_id": str
        }

    Returns:
        {
            "status": "success" | "partial_failure",
            "deleted_count": int,
            "failed_count": int,
            "failed_memory_ids": [str]
        }
    """
    memory_id = event["memory_id"]
    memory_ids = event["memory_ids"]
    agent_id = event["agent_id"]
    now = datetime.now(timezone.utc)

    logger.info(json.dumps({
        "action": "prune_start",
        "agent_id": agent_id,
        "memory_count": len(memory_ids),
        "timestamp": now.isoformat(),
    }))

    client = boto3.client("bedrock-agentcore")

    deleted_count = 0
    failed_memory_ids = []

    for record_id in memory_ids:
        try:
            client.delete_memory_record(memoryId=memory_id, memoryRecordId=record_id)
            deleted_count += 1
            logger.info(json.dumps({
                "action": "prune",
                "memory_id": record_id,
                "agent_id": agent_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }))
        except (ClientError, EndpointConnectionError, Exception) as exc:
            failed_memory_ids.append(record_id)
            logger.error(json.dumps({
                "action": "prune_error",
                "memory_id": record_id,
                "agent_id": agent_id,
                "error": str(exc),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }))

    failed_count = len(failed_memory_ids)
    status = "success" if failed_count == 0 else "partial_failure"

    logger.info(json.dumps({
        "action": "prune_complete",
        "agent_id": agent_id,
        "deleted_count": deleted_count,
        "failed_count": failed_count,
        "failed_memory_ids": failed_memory_ids,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }))

    return {
        "status": status,
        "deleted_count": deleted_count,
        "failed_count": failed_count,
        "failed_memory_ids": failed_memory_ids,
    }
