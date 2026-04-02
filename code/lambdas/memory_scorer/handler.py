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

# Add shared module to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from shared.constants import DECAY_RATE, MAX_ACCESS_BASELINE, W_ACCESS, W_FREQUENCY, W_RECENCY

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def compute_relevance_score(
    created_at: datetime,
    last_accessed_at: datetime,
    access_count: int,
    now: datetime,
) -> float:
    """Compute relevance score using the weighted decay formula.

    score = W_RECENCY * exp(-DECAY_RATE * days_since_creation)
          + W_ACCESS  * exp(-DECAY_RATE * days_since_last_access)
          + W_FREQUENCY * min(access_count / MAX_ACCESS_BASELINE, 1.0)

    Returns a float in [0.0, 1.0].
    """
    days_since_creation = max((now - created_at).total_seconds() / 86400, 0.0)
    days_since_last_access = max((now - last_accessed_at).total_seconds() / 86400, 0.0)

    recency_factor = math.exp(-DECAY_RATE * days_since_creation)
    access_factor = math.exp(-DECAY_RATE * days_since_last_access)
    frequency_factor = min(access_count / MAX_ACCESS_BASELINE, 1.0)

    score = W_RECENCY * recency_factor + W_ACCESS * access_factor + W_FREQUENCY * frequency_factor
    return score


def handler(event: dict, context) -> dict:
    """Score all memories for an agent and return those below the relevance threshold.

    Input event:
        {
            "agent_id": str,
            "relevance_threshold": float
        }

    Returns:
        {
            "status": "success" | "failure",
            "agent_id": str,
            "total_memories": int,
            "scored_memories": int,
            "below_threshold": [{"memory_id": str, "score": float}],
            "error": str | None
        }
    """
    agent_id = event["agent_id"]
    relevance_threshold = event["relevance_threshold"]
    now = datetime.now(timezone.utc)

    logger.info(json.dumps({
        "action": "score_start",
        "agent_id": agent_id,
        "relevance_threshold": relevance_threshold,
        "timestamp": now.isoformat(),
    }))

    try:
        client = boto3.client("agentcore-memory")
        response = client.list_memories(agentId=agent_id)
        memories = response.get("memories", [])
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
    below_threshold = []
    scored_count = 0

    for memory in memories:
        memory_id = memory["memoryId"]
        created_at = datetime.fromisoformat(memory["createdAt"])
        last_accessed_at = datetime.fromisoformat(memory["lastAccessedAt"])
        access_count = memory.get("accessCount", 0)

        score = compute_relevance_score(created_at, last_accessed_at, access_count, now)
        scored_at = now.isoformat()

        # Tag the memory with its relevance score and scoring timestamp
        try:
            client.tag_memory(
                memoryId=memory_id,
                tags={
                    "relevance_score": str(score),
                    "scored_at": scored_at,
                },
            )
        except (ClientError, Exception) as exc:
            logger.warning(json.dumps({
                "action": "tag_error",
                "memory_id": memory_id,
                "agent_id": agent_id,
                "error": str(exc),
                "timestamp": now.isoformat(),
            }))

        scored_count += 1

        logger.info(json.dumps({
            "action": "score_memory",
            "memory_id": memory_id,
            "agent_id": agent_id,
            "score": score,
            "timestamp": scored_at,
        }))

        if score < relevance_threshold:
            below_threshold.append({"memory_id": memory_id, "score": score})

    logger.info(json.dumps({
        "action": "score_complete",
        "agent_id": agent_id,
        "total_memories": total_memories,
        "scored_memories": scored_count,
        "below_threshold_count": len(below_threshold),
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
