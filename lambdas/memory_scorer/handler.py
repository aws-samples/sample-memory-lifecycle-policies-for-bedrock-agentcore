"""Memory Scorer Lambda handler.

Retrieves all memories for a given agent from AgentCore Memory, computes
relevance scores using a weighted decay formula, tags each memory with its
score and timestamp, and returns memory IDs that fall below the relevance
threshold.
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
from shared.constants import (
    PRUNE_DAYS_DEFAULT,
    RELEVANCE_THRESHOLD_DEFAULT,
    decay_rate_from_prune_days,
)

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def compute_relevance_score(
    created_at: datetime,
    last_accessed_at: datetime,
    decay_rate: float,
    now: datetime,
) -> float:
    """Compute relevance score using the 2-term decay formula.

    score = 0.5 * exp(-decay_rate * days_since_creation)
          + 0.5 * exp(-decay_rate * days_since_last_access)

    Returns a float in [0.0, 1.0].
    """
    days_since_creation = max((now - created_at).total_seconds() / 86400, 0.0)
    days_since_last_access = max((now - last_accessed_at).total_seconds() / 86400, 0.0)

    recency_factor = math.exp(-decay_rate * days_since_creation)
    access_factor = math.exp(-decay_rate * days_since_last_access)

    score = 0.5 * recency_factor + 0.5 * access_factor
    return score


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
    now = datetime.now(timezone.utc)

    logger.info(json.dumps({
        "action": "score_start",
        "agent_id": agent_id,
        "relevance_threshold": relevance_threshold,
        "timestamp": now.isoformat(),
    }))

    try:
        client = boto3.client("bedrock-agentcore")
        response = client.list_memory_records(
            memoryId=memory_id,
            namespace=agent_id,
        )
        memories = response.get("memoryRecordSummaries", [])
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
        created_at = datetime.fromtimestamp(memory["createdAt"])
        # MemoryRecordSummary does not include lastAccessedAt;
        # fall back to createdAt when the field is absent.
        last_accessed_at = datetime.fromtimestamp(
            memory.get("lastAccessedAt", memory["createdAt"])
        )

        score = compute_relevance_score(created_at, last_accessed_at, decay_rate, now)
        scored_at = now.isoformat()

        # Update the memory record with its relevance score and scoring timestamp
        try:
            client.batch_update_memory_records(
                memoryId=memory_id,
                records=[{
                    "memoryRecordId": record_id,
                    "content": {"text": json.dumps({
                        "relevance_score": str(score),
                        "scored_at": scored_at,
                    })},
                    "timestamp": now,
                }],
            )
        except (ClientError, Exception) as exc:
            logger.warning(json.dumps({
                "action": "tag_error",
                "memory_id": record_id,
                "agent_id": agent_id,
                "error": str(exc),
                "timestamp": now.isoformat(),
            }))

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

    return {
        "status": "success",
        "agent_id": agent_id,
        "total_memories": total_memories,
        "scored_memories": scored_count,
        "below_threshold": below_threshold,
        "error": None,
    }
