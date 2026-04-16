"""Memory Consolidator Lambda handler.

Retrieves full content for a batch of memory record IDs from AgentCore Memory,
invokes Bedrock to produce a consolidated summary, stores the consolidated
memory record with provenance metadata, and deletes the originals.
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

CONSOLIDATION_PROMPT_TEMPLATE = """You are a memory consolidation assistant. Given the following agent memories,
create a single concise summary that preserves all essential facts, user preferences,
and actionable knowledge. Remove redundancy and outdated information.

Memories:
{memory_contents}

Output a JSON object with:
- "summary": the consolidated memory text
- "confidence": a float 0.0-1.0 indicating consolidation quality
- "key_facts": list of preserved key facts"""


def _build_prompt(memories: list[dict]) -> str:
    """Format memory contents into the consolidation prompt."""
    memory_texts = []
    for i, mem in enumerate(memories, 1):
        # content is a tagged union: {"text": "..."} in the official API
        content = mem.get("content", {})
        text = content.get("text", "") if isinstance(content, dict) else str(content)
        memory_texts.append(f"[Memory {i} (ID: {mem['memoryRecordId']})]:\n{text}")
    memory_contents = "\n\n".join(memory_texts)
    return CONSOLIDATION_PROMPT_TEMPLATE.format(memory_contents=memory_contents)


def _invoke_bedrock(bedrock_client, model_id: str, prompt: str) -> dict:
    """Invoke Bedrock with the consolidation prompt and parse the JSON response."""
    response = bedrock_client.invoke_model(
        modelId=model_id,
        contentType="application/json",
        accept="application/json",
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 2048,
            "messages": [{"role": "user", "content": prompt}],
        }),
    )
    response_body = json.loads(response["body"].read())
    # Extract text content from the Bedrock response
    result_text = response_body["content"][0]["text"]
    return json.loads(result_text)


def handler(event: dict, context) -> dict:
    """Consolidate a batch of memories using Bedrock summarization.

    Input event:
        {
            "memory_ids": [str],
            "memory_id": str,
            "agent_id": str,
            "bedrock_model_id": str
        }

    Returns:
        {
            "status": "success" | "partial_failure" | "failure",
            "consolidated_memory_id": str | None,
            "original_memory_ids": [str],
            "deleted_count": int,
            "orphaned_memory_ids": [str],
            "error": str | None
        }
    """
    memory_ids = event["memory_ids"]
    memory_id = event["memory_id"]
    agent_id = event["agent_id"]
    bedrock_model_id = event["bedrock_model_id"]
    now = datetime.now(timezone.utc)

    logger.info(json.dumps({
        "action": "consolidation_start",
        "agent_id": agent_id,
        "memory_count": len(memory_ids),
        "memory_ids": memory_ids,
        "timestamp": now.isoformat(),
    }))

    result = {
        "status": "failure",
        "consolidated_memory_id": None,
        "original_memory_ids": memory_ids,
        "deleted_count": 0,
        "orphaned_memory_ids": [],
        "error": None,
    }

    # --- Step 1: Retrieve full content for each memory ID ---
    memory_client = boto3.client("bedrock-agentcore")
    memories = []
    for mid in memory_ids:
        try:
            response = memory_client.get_memory_record(memoryId=memory_id, memoryRecordId=mid)
            mem = response["memoryRecord"]
            memories.append(mem)
        except (ClientError, EndpointConnectionError, Exception) as exc:
            error_msg = f"Failed to retrieve memory {mid}: {exc}"
            logger.error(json.dumps({
                "action": "consolidation_retrieve_error",
                "memory_id": mid,
                "agent_id": agent_id,
                "error": error_msg,
                "timestamp": now.isoformat(),
            }))
            result["error"] = error_msg
            return result

    # --- Step 2: Invoke Bedrock for consolidation ---
    prompt = _build_prompt(memories)
    try:
        bedrock_client = boto3.client("bedrock-runtime")
        bedrock_result = _invoke_bedrock(bedrock_client, bedrock_model_id, prompt)
    except (ClientError, EndpointConnectionError, json.JSONDecodeError, KeyError, Exception) as exc:
        error_msg = f"Bedrock invocation failed: {exc}"
        logger.error(json.dumps({
            "action": "consolidation_bedrock_error",
            "agent_id": agent_id,
            "memory_ids": memory_ids,
            "error": error_msg,
            "timestamp": now.isoformat(),
        }))
        result["error"] = error_msg
        return result

    summary = bedrock_result.get("summary", "")
    confidence = bedrock_result.get("confidence", 0.0)
    key_facts = bedrock_result.get("key_facts", [])

    logger.info(json.dumps({
        "action": "consolidation_bedrock_success",
        "agent_id": agent_id,
        "confidence": confidence,
        "key_facts_count": len(key_facts),
        "timestamp": now.isoformat(),
    }))

    # --- Step 3: Store consolidated memory with provenance tags ---
    try:
        create_response = memory_client.batch_create_memory_records(
            memoryId=memory_id,
            records=[{
                "content": {"text": summary},
                "timestamp": now,
                "namespaces": [agent_id],
            }],
        )
        consolidated_memory_id = create_response["successfulRecords"][0]["memoryRecordId"]
    except (ClientError, EndpointConnectionError, Exception) as exc:
        error_msg = f"Failed to store consolidated memory: {exc}"
        logger.error(json.dumps({
            "action": "consolidation_store_error",
            "agent_id": agent_id,
            "error": error_msg,
            "timestamp": now.isoformat(),
        }))
        result["error"] = error_msg
        return result

    result["consolidated_memory_id"] = consolidated_memory_id

    logger.info(json.dumps({
        "action": "consolidation_stored",
        "agent_id": agent_id,
        "consolidated_memory_id": consolidated_memory_id,
        "source_count": len(memory_ids),
        "timestamp": now.isoformat(),
    }))

    # --- Step 4: Delete original memories ---
    deleted_count = 0
    orphaned_ids = []
    for mid in memory_ids:
        try:
            memory_client.delete_memory_record(memoryId=memory_id, memoryRecordId=mid)
            deleted_count += 1
            logger.info(json.dumps({
                "action": "consolidation_delete",
                "memory_id": mid,
                "agent_id": agent_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }))
        except (ClientError, EndpointConnectionError, Exception) as exc:
            orphaned_ids.append(mid)
            logger.error(json.dumps({
                "action": "consolidation_delete_error",
                "memory_id": mid,
                "agent_id": agent_id,
                "error": str(exc),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }))

    result["deleted_count"] = deleted_count
    result["orphaned_memory_ids"] = orphaned_ids

    if orphaned_ids:
        result["status"] = "partial_failure"
        logger.warning(json.dumps({
            "action": "consolidation_partial_failure",
            "agent_id": agent_id,
            "consolidated_memory_id": consolidated_memory_id,
            "orphaned_memory_ids": orphaned_ids,
            "deleted_count": deleted_count,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }))
    else:
        result["status"] = "success"
        logger.info(json.dumps({
            "action": "consolidation_complete",
            "agent_id": agent_id,
            "consolidated_memory_id": consolidated_memory_id,
            "deleted_count": deleted_count,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }))

    return result
