"""Memory Pruner Lambda handler.

Operates in two modes:

Mode 1 (explicit IDs): When ``memory_ids`` is provided in the event, deletes
those specific memory record IDs from AgentCore Memory.  Continues processing
on individual deletion failures (no short-circuit).

Mode 2 (TTL expiration): When ``memory_ids`` is NOT provided but ``memory_id``
is present, queries all memories via ``list_memory_records``, filters those
whose ``createdAt`` is older than ``MEMORY_TTL_DAYS`` days, and deletes the
expired records using the same no-short-circuit pattern.

Returns a summary with deleted_count, failed_count, and failed_memory_ids.
"""

import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

import boto3
from botocore.exceptions import ClientError, EndpointConnectionError

# The shared module is deployed as a Lambda Layer at runtime.
# The sys.path fallback enables local development and testing.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event: dict, context) -> dict:
    """Prune memories by deleting each record from AgentCore Memory.

    Input event (Mode 1 — explicit IDs):
        {
            "memory_id": str,          # memory resource container ID
            "memory_ids": [str],       # list of memoryRecordId values to delete
            "agent_id": str
        }

    Input event (Mode 2 — TTL expiration):
        {
            "memory_id": str,          # memory resource container ID
            "agent_id": str
            # memory_ids is absent
        }

    Returns:
        {
            "status": "success" | "partial_failure",
            "deleted_count": int,
            "failed_count": int,
            "failed_memory_ids": [str],
            "expired_count": int        # only present in TTL mode
        }
    """
    memory_id = event["memory_id"]
    memory_ids = event.get("memory_ids")
    agent_id = event["agent_id"]
    now = datetime.now(timezone.utc)

    client = boto3.client("bedrock-agentcore")

    # ------------------------------------------------------------------
    # Mode 1: Explicit memory_ids provided — delete those specific IDs
    # ------------------------------------------------------------------
    if memory_ids is not None:
        logger.info(json.dumps({
            "action": "prune_start",
            "agent_id": agent_id,
            "memory_count": len(memory_ids),
            "timestamp": now.isoformat(),
        }))

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

    # ------------------------------------------------------------------
    # Mode 2: TTL expiration — query all memories and delete expired ones
    # ------------------------------------------------------------------
    ttl_days = int(os.environ.get("MEMORY_TTL_DAYS", "90"))

    logger.info(json.dumps({
        "action": "ttl_prune_start",
        "agent_id": agent_id,
        "memory_id": memory_id,
        "ttl_days": ttl_days,
        "timestamp": now.isoformat(),
    }))

    # Retrieve all memory records
    try:
        response = client.list_memory_records(memoryId=memory_id)
        memories = response.get("memoryRecordSummaries", [])
    except (ClientError, EndpointConnectionError, Exception) as exc:
        error_msg = f"Failed to list memory records: {exc}"
        logger.error(json.dumps({
            "action": "ttl_prune_error",
            "agent_id": agent_id,
            "error": error_msg,
            "timestamp": now.isoformat(),
        }))
        return {
            "status": "failure",
            "deleted_count": 0,
            "failed_count": 0,
            "failed_memory_ids": [],
            "expired_count": 0,
        }

    # Filter memories older than MEMORY_TTL_DAYS
    cutoff = now - timedelta(days=ttl_days)
    expired_ids = []
    for memory in memories:
        created_at = datetime.fromisoformat(memory["createdAt"])
        if created_at < cutoff:
            expired_ids.append(memory["memoryRecordId"])

    expired_count = len(expired_ids)

    logger.info(json.dumps({
        "action": "ttl_filter_complete",
        "agent_id": agent_id,
        "total_memories": len(memories),
        "expired_count": expired_count,
        "ttl_days": ttl_days,
        "timestamp": now.isoformat(),
    }))

    # Delete expired memories using the same no-short-circuit pattern
    deleted_count = 0
    failed_memory_ids = []

    for record_id in expired_ids:
        try:
            client.delete_memory_record(memoryId=memory_id, memoryRecordId=record_id)
            deleted_count += 1
            logger.info(json.dumps({
                "action": "ttl_prune",
                "memory_id": record_id,
                "agent_id": agent_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }))
        except (ClientError, EndpointConnectionError, Exception) as exc:
            failed_memory_ids.append(record_id)
            logger.error(json.dumps({
                "action": "ttl_prune_error",
                "memory_id": record_id,
                "agent_id": agent_id,
                "error": str(exc),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }))

    failed_count = len(failed_memory_ids)
    status = "success" if failed_count == 0 else "partial_failure"

    logger.info(json.dumps({
        "action": "ttl_prune_complete",
        "agent_id": agent_id,
        "deleted_count": deleted_count,
        "failed_count": failed_count,
        "expired_count": expired_count,
        "failed_memory_ids": failed_memory_ids,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }))

    return {
        "status": status,
        "deleted_count": deleted_count,
        "failed_count": failed_count,
        "failed_memory_ids": failed_memory_ids,
        "expired_count": expired_count,
    }
