# The Forgetting Problem: Designing Memory Lifecycle Policies for Long-Running AgentCore Agents

## Outline

1. **Why Agent Memory Is Harder Than You Think**
   - Short-term (session) vs. long-term (persistent) memory in AgentCore
   - The accumulation problem: agents that remember everything eventually drown in irrelevant context
   - Real-world failure: a support agent that references a resolved issue from 6 months ago as if it's still active

2. **A Taxonomy of Agent Memory**
   - Episodic memory: what happened in past conversations
   - Semantic memory: distilled facts and preferences about the user
   - Procedural memory: learned workflows and tool-use patterns
   - Mapping each type to AgentCore Memory capabilities

3. **Designing Memory Lifecycle Policies**
   - TTL-based expiration: auto-expiring episodic memories after N days
   - Relevance decay: scoring memories by recency and access frequency, pruning low-scoring entries
   - Consolidation: periodically using an LLM to summarize and merge related memories into compact semantic entries

4. **Implementation with AgentCore Memory + Step Functions**
   - A Step Functions workflow that runs nightly to score, consolidate, and prune agent memories
   - Using Bedrock to generate memory summaries (meta-cognition: an LLM reasoning about its own memories)
   - Tagging memories with confidence scores and source attribution

5. **Testing Memory Quality**
   - Building a memory regression test suite: does the agent still answer correctly after pruning?
   - Using AgentCore Evaluations to measure answer quality before and after memory consolidation

6. **Privacy and Compliance Considerations**
   - GDPR right-to-be-forgotten: implementing user-scoped memory deletion
   - Audit logging of memory mutations via CloudTrail

## Use Case and Relevance

AgentCore Memory is one of the most powerful features of the platform, enabling agents to maintain context across sessions and build long-term relationships with users. But every blog about agent memory focuses on how to store and retrieve memories — none address the equally critical question of when and how to forget. In production, agents that accumulate unbounded memories suffer from context pollution, where outdated or irrelevant memories degrade response quality, increase token costs, and can even cause compliance violations when stale personal data lingers. This blog introduces the concept of memory lifecycle management for AI agents, borrowing from database lifecycle patterns but adapted for the unique challenges of LLM-consumed context. It provides a deployable architecture using AgentCore Memory, Step Functions, and Bedrock itself to periodically score, consolidate, and prune memories. For AWS customers building customer-facing agents in support, sales, or advisory roles, this solves a problem that only surfaces after weeks of production use: the slow degradation of agent quality as memory bloats. It also addresses GDPR compliance by showing how to implement right-to-be-forgotten at the agent memory layer.
