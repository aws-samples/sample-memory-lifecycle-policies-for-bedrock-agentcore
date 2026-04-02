"""Shared data models for memory lifecycle Lambda functions."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class AgentMemory:
    """Represents a memory stored in AgentCore Memory."""

    memory_id: str
    agent_id: str
    user_id: Optional[str]
    content: str
    memory_type: str  # "episodic" | "semantic" | "procedural"
    created_at: datetime
    last_accessed_at: datetime
    access_count: int
    tags: dict = field(default_factory=dict)


@dataclass
class ScoringResult:
    """Result returned by the Memory Scorer Lambda."""

    agent_id: str
    total_memories: int
    scored_memories: int
    below_threshold: list  # [{"memory_id": str, "score": float}]


@dataclass
class ConsolidationResult:
    """Result returned by the Memory Consolidator Lambda."""

    consolidated_memory_id: Optional[str]
    original_memory_ids: list
    deleted_count: int
    orphaned_memory_ids: list
    status: str  # "success" | "partial_failure" | "failure"


@dataclass
class PruningResult:
    """Result returned by the Memory Pruner Lambda."""

    deleted_count: int
    failed_count: int
    failed_memory_ids: list
    status: str  # "success" | "partial_failure"


@dataclass
class GDPRDeletionResult:
    """Result returned by the GDPR Deletion Handler Lambda."""

    user_id: str
    deleted_count: int
    failed_memory_ids: list
    status: str  # "success" | "partial_failure"


@dataclass
class WorkflowMetrics:
    """Metrics emitted by the Memory Lifecycle Workflow."""

    memories_processed: int
    memories_consolidated: int
    memories_pruned: int
    ttl_expired: int
    execution_status: str  # "success" | "failure"
    execution_timestamp: str
