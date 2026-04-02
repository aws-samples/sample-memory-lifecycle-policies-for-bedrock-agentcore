# Relevant Suggestions from Round 2

After reviewing the second round of feedback against the blog and code, here are the items worth acting on. Most of this feedback doubles down on points already addressed or demands scope far beyond a blog post.

## Actionable Improvements

### 1. Clarify that per-type TTL is a recommendation, not a delivered feature
The blog says "we recommend differentiating TTL by memory type" and then says "the implementation in this post uses a single configurable TTL as a baseline." This is honest, but the phrasing could be tighter. The sentence "Procedural memories may never expire via TTL at all" reads like a feature claim rather than a design recommendation. A small wording tweak to make it unambiguous that the code delivers a single TTL and per-type differentiation is left as an exercise would close this gap cleanly.

### 2. Add a concrete cost example to the Cost Considerations section
The current cost section says "consolidation costs can grow meaningfully" but doesn't give a concrete number. Adding one worked example (e.g., "For 1,000 memories with 200 below threshold at batch size 10, that's 20 Bedrock invocations — roughly $X/month at current Claude 3 Sonnet pricing") would make the section more useful. The reviewer's ask for a full pricing table is overkill for a blog, but one illustrative number grounds the discussion.

### 3. Briefly explain how AgentCore Evaluations scoring works
The Testing section uses AgentCore Evaluations as a black box. Adding 1–2 sentences explaining that the Evaluations API scores agent responses against human-defined criteria (similar to LLM-as-judge patterns) would help readers understand what the quality scores represent. No need for full evaluation templates — just enough context so readers aren't left wondering what "quality score 0.7" means.

## Items Reviewed and Excluded

- **"Implementation-policy mismatch on per-type TTL"** — The blog already explicitly states "the implementation in this post uses a single configurable TTL as a baseline; extending it to per-type policies is a natural next step." This is transparent, not a mismatch. Implementing per-type TTL in the code would add significant complexity for a blog post that's already code-heavy. Suggestion #1 above handles the minor wording issue.

- **"Arbitrary relevance scoring without validation"** — Already addressed in round 1. The weights are explained with intuition, all parameters are configurable, and demanding A/B test results in a blog post is unreasonable. The blog is presenting a starting framework, not a peer-reviewed paper.

- **"Consolidation implementation gaps (confidence threshold, S3 archiving)"** — The blog explicitly frames these as recommendations for "high-stakes domains," not as delivered features. The blog says "consider setting a confidence threshold" and "you could also archive original memories to cold storage." These are clearly positioned as extensions. Implementing them in the code would be a separate blog post.

- **"Scalability & Lambda concurrency limits"** — Lambda concurrency is a general AWS operational concern, not specific to this architecture. The Step Functions Map state already handles batching. Readers deploying at 10k+ memory scale are expected to understand Lambda concurrency — this is not a blog-level concern.

- **"Ethical & bias gaps"** — Same as round 1. Out of scope for a technical implementation blog. Bias in LLM summarization is a research topic, not something you solve with a code snippet.

- **"Overwhelming technical depth / split into sections"** — The blog follows standard AWS blog structure with clear section headings. Readers can skip to the sections they care about. Splitting into separate posts would fragment the narrative.

- **"Missing error-handling examples"** — The error handling is already shown in the Lambda code (try/except with structured logging, continue-on-failure). The Step Functions Catch blocks and SNS failure handler are described in the workflow section. Adding more error-handling code snippets would bloat the post.

- **"Inconsistent level of detail (GDPR vs real-time scoring)"** — GDPR is a delivered feature with code. Real-time scoring is a "Next Steps" idea. Different levels of detail are appropriate.
