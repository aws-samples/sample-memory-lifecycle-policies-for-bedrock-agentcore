# Designing memory lifecycle policies for Amazon Bedrock AgentCore

> **Important:** This is sample code for non-production usage. You should work with your security and legal teams to meet your organizational security, regulatory, and compliance requirements before deployment.

An automated memory lifecycle management system for AI agents built on AWS. This solution scores, consolidates, prunes, and deletes agent memories using a nightly Step Functions workflow, with GDPR right-to-be-forgotten support and full observability through CloudWatch and CloudTrail.

## Table of Contents

- [Architecture](#architecture)
- [How It Works](#how-it-works)
  - [Workflow Steps](#workflow-steps)
  - [Relevance Scoring Formula](#relevance-scoring-formula)
  - [Memory Consolidation](#memory-consolidation)
- [Lambda Functions](#lambda-functions)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Configuration](#configuration)
- [Deployment](#deployment)
- [Testing](#testing)
  - [CDK Infrastructure Tests](#cdk-infrastructure-tests)
  - [Regression Test Suite](#regression-test-suite)
- [Observability](#observability)
- [GDPR Deletion](#gdpr-deletion)
- [Cleanup](#cleanup)
- [Security](#security)
- [Contributing](#contributing)
- [License](#license)

## Architecture

The solution deploys the following AWS resources:

- **4 AWS Lambda functions** (Python 3.12) — Memory Scorer, Memory Consolidator, Memory Pruner, GDPR Deletion Handler
- **1 AWS Step Functions state machine** — orchestrates the nightly lifecycle workflow
- **1 Amazon EventBridge rule** — triggers the workflow on a cron schedule (daily at 2:00 AM UTC)
- **1 Amazon SNS topic** — delivers failure notifications when workflow steps fail
- **1 Amazon CloudWatch dashboard** — visualizes Lambda invocations, errors, Step Functions executions, and custom memory lifecycle metrics
- **1 AWS CloudTrail trail** with an S3 bucket — audit logging for AgentCore Memory API calls
- **CloudWatch Log Groups** — structured JSON logging with 1-month retention for each Lambda function
- **Amazon Bedrock** — used by the Memory Consolidator to summarize related memories via Claude Sonnet 4.5

```
                          ┌──────────────────────────┐
                          │   Amazon EventBridge      │
                          │   (cron: 0 2 * * ? *)     │
                          └────────────┬─────────────┘
                                       │
                                       ▼
                          ┌──────────────────────────┐
                          │   AWS Step Functions      │
                          │   State Machine           │
                          └────────────┬─────────────┘
                                       │
              ┌────────────────────────┼────────────────────────┐
              │                        │                        │
              ▼                        ▼                        ▼
   ┌─────────────────┐   ┌──────────────────────┐   ┌─────────────────┐
   │  TTL Expiration  │   │   Score Memories     │   │  Emit Metrics   │
   │  (Memory Pruner) │   │   (Memory Scorer)    │   │  (Pass state)   │
   └─────────────────┘   └──────────┬───────────┘   └─────────────────┘
                                    │                        ▲
                          ┌─────────▼──────────┐             │
                          │ Check Low-Score     │─── No ─────┘
                          │ Memories (Choice)   │
                          └─────────┬──────────┘
                               Yes  │
                                    ▼
                          ┌──────────────────────┐
                          │  Batch Consolidate   │
                          │  (Map → Consolidator)│
                          │  + Amazon Bedrock    │
                          └──────────┬───────────┘
                                     │
                                     ▼
                          ┌──────────────────────┐
                          │  Prune Remaining     │
                          │  (Memory Pruner)     │
                          └──────────┬───────────┘
                                     │
                                     ▼
                          ┌──────────────────────┐
                          │  Emit Metrics        │
                          └──────────────────────┘

   On failure at any step:
              │
              ▼
   ┌──────────────────────┐
   │  Amazon SNS Topic    │
   │  (Failure Alert)     │
   └──────────────────────┘
```


Additionally, the **GDPR Deletion Handler** Lambda can be invoked independently (outside the nightly workflow) to delete all memories for a specific user across all agents.

## How It Works

### Workflow Steps

The nightly Step Functions workflow executes the following steps in sequence:

1. **TTL Expiration** — Invokes the Memory Pruner to delete memories that have exceeded their time-to-live (default: 90 days).
2. **Score Memories** — Invokes the Memory Scorer to compute a relevance score for every memory belonging to an agent. Each memory is tagged with its score and scoring timestamp.
3. **Check Low-Score Memories** — A Choice state that inspects the scoring results. If any memories scored below the relevance threshold, the workflow proceeds to consolidation. Otherwise, it skips directly to metrics emission.
4. **Batch Consolidate** — A Map state that iterates over low-scoring memories and invokes the Memory Consolidator for each batch. The consolidator uses Amazon Bedrock (Claude Sonnet 4.5) to produce a single summary from multiple related memories, stores the consolidated memory with provenance tags, and deletes the originals.
5. **Prune Remaining** — Invokes the Memory Pruner to delete any remaining low-score memories that were not consolidated.
6. **Emit Metrics** — A Pass state that structures the final workflow metrics (memories processed, TTL expired, workflow status).

Each task step is configured with automatic retries (2 attempts, 5-second interval, 2x backoff) for transient errors. On failure, the step catches the error, formats it with the step name, and publishes a notification to the SNS failure topic.

### Relevance Scoring Formula

The Memory Scorer computes a relevance score for each memory using a two-term exponential decay formula:

```
score = 0.5 × exp(-decay_rate × days_since_creation)
      + 0.5 × exp(-decay_rate × days_since_last_access)
```

Where:

```
decay_rate = -ln(relevance_threshold) / prune_days
```

With the default configuration (`relevanceThreshold = 0.3`, `pruneDays = 45`), a memory that has not been accessed for 45 days will score approximately 0.3, placing it at the pruning threshold. Recently accessed memories score closer to 1.0 and are retained.

### Memory Consolidation

The Memory Consolidator uses Amazon Bedrock to merge related low-scoring memories into a single consolidated memory. The process:

1. Retrieves full content for each memory in the batch from AgentCore Memory.
2. Constructs a prompt with all memory contents and sends it to the configured Bedrock model.
3. The model returns a JSON response containing a summary, a confidence score (0.0–1.0), and a list of preserved key facts.
4. Stores the consolidated memory in AgentCore Memory with provenance tags (`consolidated: true`, `confidence_score`, `source_memories`).
5. Deletes the original source memories. Any deletion failures are tracked as orphaned memory IDs.

## Lambda Functions

| Function | Timeout | Description |
|---|---|---|
| **Memory Scorer** | 5 min | Retrieves all memories for an agent, computes relevance scores using the decay formula, tags each memory, and returns IDs below the threshold. |
| **Memory Consolidator** | 10 min | Fetches a batch of memories, invokes Bedrock for summarization, stores the consolidated memory with provenance tags, and deletes originals. |
| **Memory Pruner** | 5 min | Iterates through a list of memory IDs and deletes each from AgentCore Memory. Continues on individual failures (no short-circuit). |
| **GDPR Deletion Handler** | 10 min | Lists all memories for a user across all agents and deletes each one. Logs every deletion for CloudTrail auditing. |

All Lambda functions emit structured JSON logs to CloudWatch.

## Project Structure

```
code/
├── bin/
│   └── app.ts                          # CDK app entry point
├── lib/
│   └── memory-lifecycle-stack.ts       # CDK stack definition (all infrastructure)
├── lambdas/
│   ├── memory_scorer/
│   │   ├── handler.py                  # Relevance scoring logic
│   │   └── requirements.txt
│   ├── memory_consolidator/
│   │   ├── handler.py                  # Bedrock-powered memory consolidation
│   │   └── requirements.txt
│   ├── memory_pruner/
│   │   ├── handler.py                  # Memory deletion logic
│   │   └── requirements.txt
│   ├── gdpr_deletion/
│   │   ├── handler.py                  # GDPR right-to-be-forgotten handler
│   │   └── requirements.txt
│   └── shared/
│       ├── __init__.py
│       ├── constants.py                # Decay rate calculation, default thresholds
│       └── models.py                   # Dataclass models for all Lambda I/O
│                                       # (deployed as a Lambda Layer)
├── test/
│   ├── memory-lifecycle-stack.test.ts  # CDK infrastructure assertions
│   └── test_regression_suite.py        # Agent quality regression tests
├── cdk.json                            # CDK app config and context parameters
├── package.json
├── tsconfig.json
└── jest.config.js
```


## Prerequisites

- An [AWS account](https://aws.amazon.com/account/)
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) v2, configured with credentials (`aws configure`)
- [Node.js](https://nodejs.org/) >= 18.x
- [AWS CDK CLI](https://docs.aws.amazon.com/cdk/v2/guide/getting-started.html) v2 (`npm install -g aws-cdk`)
- [Python](https://www.python.org/downloads/) >= 3.12 (for Lambda runtime)
- [Amazon Bedrock model access](https://docs.aws.amazon.com/bedrock/latest/userguide/model-access.html) enabled for `anthropic.claude-sonnet-4-5-20250929-v1:0` (or your chosen model) in your target region
- The target AWS account must be [bootstrapped for CDK](https://docs.aws.amazon.com/cdk/v2/guide/bootstrapping.html): `npx cdk bootstrap`

## Configuration

All tunable parameters are defined as CDK context values in `cdk.json` and can be overridden at deploy time:

| Parameter | Default | Description |
|---|---|---|
| `memoryTtlDays` | `90` | Number of days after which a memory is considered expired and eligible for TTL deletion. |
| `relevanceThreshold` | `0.3` | Minimum relevance score a memory must have to be retained. Memories scoring below this are candidates for consolidation or pruning. |
| `consolidationBatchSize` | `10` | Maximum number of memories processed per consolidation batch. |
| `bedrockModelId` | `anthropic.claude-sonnet-4-5-20250929-v1:0` | The Amazon Bedrock foundation model used for memory consolidation. |
| `pruneDays` | `45` | Approximate number of days of inactivity after which a memory's score drops below the relevance threshold. Used to compute the exponential decay rate. |

To override parameters at deploy time:

```bash
npx cdk deploy -c memoryTtlDays=60 -c relevanceThreshold=0.4 -c bedrockModelId=anthropic.claude-3-haiku-20240307-v1:0
```

## Deployment

> **Important:** You are responsible for the cost of the AWS services used while running this deployment. There is no additional cost for using this sample. For full details, see the pricing pages for each AWS service used in this sample. Prices are subject to change.

1. Clone the repository and navigate to the `code/` directory:

    ```bash
    git clone <repository-url>
    cd <repository-name>/code
    ```

2. Install Node.js dependencies:

    ```bash
    npm install
    ```

3. (Optional) Install Python dependencies for local Lambda development:

    ```bash
    pip install -r lambdas/memory_scorer/requirements.txt
    pip install -r lambdas/memory_consolidator/requirements.txt
    ```

4. Synthesize the CloudFormation template to verify everything compiles:

    ```bash
    npx cdk synth
    ```

5. Deploy the stack:

    ```bash
    npx cdk deploy
    ```

    CDK will display the IAM policy changes and ask for confirmation before deploying.

## Testing

### CDK Infrastructure Tests

The project includes Jest-based CDK assertion tests that validate the synthesized CloudFormation template:

```bash
npm test
```

The tests verify:
- 4 Lambda functions are created with Python 3.12 runtime
- 1 Step Functions state machine is created
- 1 EventBridge rule with the expected cron schedule (`cron(0 2 * * ? *)`)
- 1 SNS topic for failure notifications
- 1 CloudWatch dashboard for observability

### Regression Test Suite

The file `test/test_regression_suite.py` contains a regression test framework that validates agent answer quality is maintained after memory lifecycle runs. It uses AgentCore Evaluations to compute quality scores before and after the lifecycle workflow executes.

The suite:
1. Queries the agent with predefined questions and records baseline quality scores.
2. (The memory lifecycle workflow runs.)
3. Queries the agent again and records post-lifecycle quality scores.
4. Compares scores against minimum thresholds and produces a pass/fail report.

Run the unit tests for the regression suite locally:

```bash
pip install pytest boto3
pytest test/test_regression_suite.py -v
```

## Observability

The stack deploys a CloudWatch dashboard named `MemoryLifecycleDashboard` with the following widgets:

- **Step Functions Workflow Executions** — started, succeeded, and failed execution counts (hourly)
- **Lambda Invocations** — invocation counts for all 4 Lambda functions (hourly)
- **Lambda Errors** — error counts for all 4 Lambda functions (hourly)
- **Memory Lifecycle Custom Metrics** — MemoriesProcessed, MemoriesConsolidated, MemoriesPruned, and WorkflowExecutionStatus under the `MemoryLifecycle` namespace (hourly)

All Lambda functions emit structured JSON logs to CloudWatch Logs with 1-month retention. Log entries include action type, agent/user/memory IDs, timestamps, and error details.

A CloudTrail trail (`MemoryLifecycleAuditTrail`) captures API calls to AgentCore Memory for compliance auditing. Trail logs are stored in an encrypted S3 bucket with SSL enforcement and public access blocked.

## GDPR Deletion

The GDPR Deletion Handler Lambda supports the right-to-be-forgotten by deleting all memories associated with a given user across all agents. It can be invoked independently of the nightly workflow.

Example invocation payload:

```json
{
  "user_id": "user-12345"
}
```

Example response:

```json
{
  "status": "success",
  "user_id": "user-12345",
  "deleted_count": 42,
  "failed_memory_ids": []
}
```

Every individual deletion is logged as a structured JSON event for CloudTrail auditing.

## Cleanup

To remove all deployed resources:

```bash
npx cdk destroy
```

This will delete the CloudFormation stack and all associated resources including the CloudTrail S3 bucket (configured with `RemovalPolicy.DESTROY` and `autoDeleteObjects`).

## Security

- All IAM policies follow the principle of least privilege, scoped to specific actions and account/region-bound resource ARNs.
- No AWS managed policies are used beyond `AWSLambdaBasicExecutionRole` (automatically attached by CDK for CloudWatch Logs access).
- The Bedrock IAM policy is scoped to the specific foundation model configured via `bedrockModelId`.
- The CloudTrail S3 bucket enforces SSL, uses S3-managed encryption, and blocks all public access.
- CloudTrail file validation is enabled to detect log tampering.

## Contributing

See [CONTRIBUTING](CONTRIBUTING.md) for more information.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](LICENSE) file.
