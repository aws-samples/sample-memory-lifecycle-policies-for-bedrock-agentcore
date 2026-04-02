"""GDPR Deletion Handler Lambda.

Lists all memories associated with a user_id across all agents in AgentCore Memory,
deletes each one, and logs each deletion for CloudTrail auditing.
Continues processing on individual deletion failures (no short-circuit).
Returns confirmation with deleted_count, user_id, and any failed_memory_ids.
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError, EndpointConnectionError

# Add shared module to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event: dict, context) -> dict:
    """Delete all memories for a user across all agents (GDPR right-to-be-forgotten).

    Input event:
        {
            "user_id": str
        }

    Returns:
        {
            "status": "success" | "partial_failure",
            "user_id": str,
            "deleted_count": int,
            "failed_memory_ids": [str]
        }
    """
    user_id = event["user_id"]
    now = datetime.now(timezone.utc)

    logger.info(json.dumps({
        "action": "gdpr_delete_start",
        "user_id": user_id,
        "timestamp": now.isoformat(),
    }))

    client = boto3.client("agentcore-memory")

    # List all memories for this user across all agents
    try:
        response = client.list_memories(userId=user_id)
        memories = response.get("memories", [])
    except (ClientError, EndpointConnectionError, Exception) as exc:
        logger.error(json.dumps({
            "action": "gdpr_delete_error",
            "user_id": user_id,
            "error": str(exc),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }))
        return {
            "status": "partial_failure",
            "user_id": user_id,
            "deleted_count": 0,
            "failed_memory_ids": [],
        }

    deleted_count = 0
    failed_memory_ids = []

    for memory in memories:
        memory_id = memory["memoryId"]
        try:
            client.delete_memory(memoryId=memory_id)
            deleted_count += 1
            logger.info(json.dumps({
                "action": "gdpr_delete",
                "user_id": user_id,
                "memory_id": memory_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }))
        except (ClientError, EndpointConnectionError, Exception) as exc:
            failed_memory_ids.append(memory_id)
            logger.error(json.dumps({
                "action": "gdpr_delete_error",
                "user_id": user_id,
                "memory_id": memory_id,
                "error": str(exc),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }))

    status = "success" if len(failed_memory_ids) == 0 else "partial_failure"

    logger.info(json.dumps({
        "action": "gdpr_delete_complete",
        "user_id": user_id,
        "deleted_count": deleted_count,
        "failed_count": len(failed_memory_ids),
        "failed_memory_ids": failed_memory_ids,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }))

    return {
        "status": status,
        "user_id": user_id,
        "deleted_count": deleted_count,
        "failed_memory_ids": failed_memory_ids,
    }
