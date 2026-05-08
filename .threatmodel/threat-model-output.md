# Comprehensive Threat Model Report

**Generated**: 2026-05-08 14:24:27
**Current Phase**: 1 - Business Context Analysis
**Overall Completion**: 80.0%

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Business Context](#business-context)
3. [System Architecture](#system-architecture)
4. [Threat Actors](#threat-actors)
5. [Trust Boundaries](#trust-boundaries)
6. [Assets and Flows](#assets-and-flows)
7. [Threats](#threats)
8. [Mitigations](#mitigations)
9. [Assumptions](#assumptions)
10. [Phase Progress](#phase-progress)

## Executive Summary

An automated memory lifecycle management system for Amazon Bedrock AgentCore AI agents. The system scores, consolidates, prunes, and deletes agent memories using a nightly Step Functions workflow. It handles sensitive AI agent memory data including user interactions, preferences, and behavioral patterns. The system includes GDPR right-to-be-forgotten support for user data deletion and full observability through CloudWatch and CloudTrail. It processes AI agent memories that may contain PII, user preferences, and conversation history.

### Key Statistics

- **Total Threats**: 11
- **Total Mitigations**: 11
- **Total Assumptions**: 4
- **System Components**: 14
- **Assets**: 12
- **Threat Actors**: 15

## Business Context

**Description**: An automated memory lifecycle management system for Amazon Bedrock AgentCore AI agents. The system scores, consolidates, prunes, and deletes agent memories using a nightly Step Functions workflow. It handles sensitive AI agent memory data including user interactions, preferences, and behavioral patterns. The system includes GDPR right-to-be-forgotten support for user data deletion and full observability through CloudWatch and CloudTrail. It processes AI agent memories that may contain PII, user preferences, and conversation history.

### Business Features

- **Industry Sector**: Technology
- **Data Sensitivity**: Confidential
- **User Base Size**: Medium
- **Geographic Scope**: Multinational
- **Regulatory Requirements**: GDPR
- **System Criticality**: High
- **Financial Impact**: Medium
- **Authentication Requirement**: MFA
- **Deployment Environment**: Cloud-Public
- **Integration Complexity**: Complex

## System Architecture

### Components

| ID | Name | Type | Service Provider | Description |
|---|---|---|---|---|
| C001 | Memory Scorer Lambda | Compute | AWS | Retrieves all memories for an agent, computes relevance scores using weighted exponential decay formula, and returns memory IDs below the threshold. Reads CloudTrail logs from S3 for access frequency data. |
| C002 | Memory Consolidator Lambda | Compute | AWS | Fetches batch of low-scoring memories, invokes Bedrock for AI summarization, stores consolidated memory with provenance tags, and deletes originals. |
| C003 | Memory Pruner Lambda | Compute | AWS | Deletes memories by explicit ID list or by TTL expiration (memories older than 90 days). Continues on individual failures. |
| C004 | GDPR Deletion Lambda | Compute | AWS | Handles GDPR right-to-be-forgotten requests by deleting all memories for a user across all agents. Logs each deletion for audit compliance. |
| C005 | Metrics Emitter Lambda | Compute | AWS | Publishes workflow metrics (memories processed, pruned, consolidated) to CloudWatch custom namespace. |
| C006 | Step Functions State Machine | Compute | AWS | Orchestrates the nightly memory lifecycle workflow: TTL expiration → scoring → consolidation → pruning → metrics emission. 1-hour timeout with retry and error handling. |
| C007 | EventBridge Cron Rule | Network | AWS | Triggers the memory lifecycle workflow nightly at 2 AM UTC via cron schedule. |
| C008 | SNS Failure Topic | Network | AWS | Delivers failure notifications when workflow steps fail. Subscriptions not configured in this sample. |
| C009 | AgentCore Memory Service | Storage | AWS | Amazon Bedrock AgentCore Memory service storing AI agent memories. Contains user interaction data, preferences, and conversation history. |
| C010 | Bedrock Foundation Model | Compute | AWS | Amazon Bedrock foundation model (Claude Sonnet 4.5) used for AI-powered memory consolidation/summarization. |
| C011 | CloudTrail S3 Bucket | Storage | AWS | S3 bucket storing CloudTrail audit logs for AgentCore Memory API calls. Encrypted with S3-managed keys, SSL enforced, public access blocked. |
| C012 | CloudTrail Trail | Network | AWS | CloudTrail trail capturing data events for BedrockAgentCore Memory resources. File validation enabled. |
| C013 | CloudWatch Observability | Network | AWS | CloudWatch dashboard and log groups for observability. Structured JSON logs with 1-month retention. |
| C014 | Run Output S3 Bucket | Storage | AWS | S3 bucket storing workflow run output and access ledger data. Encrypted, SSL enforced, public access blocked. |

### Connections

| ID | Source | Destination | Protocol | Port | Encrypted | Description |
|---|---|---|---|---|---|---|
| CN001 | C001 | C009 | HTTPS | 443 | Yes | Memory Scorer reads memories from AgentCore Memory |
| CN002 | C002 | C009 | HTTPS | 443 | Yes | Memory Consolidator reads/writes/deletes memories from AgentCore Memory |
| CN003 | C002 | C010 | HTTPS | 443 | Yes | Memory Consolidator invokes Bedrock foundation model for summarization |
| CN004 | C003 | C009 | HTTPS | 443 | Yes | Memory Pruner deletes memories from AgentCore Memory |
| CN005 | C004 | C009 | HTTPS | 443 | Yes | GDPR Deletion Lambda lists and deletes user memories from AgentCore Memory |
| CN006 | C001 | C011 | HTTPS | 443 | Yes | Memory Scorer reads CloudTrail logs from S3 for access frequency data |
| CN007 | C001 | C014 | HTTPS | 443 | Yes | Memory Scorer reads/writes access ledger to Run Output S3 Bucket |
| CN008 | C006 | C003 | HTTPS | 443 | Yes | Step Functions invokes Memory Pruner Lambda |
| CN009 | C006 | C008 | HTTPS | 443 | Yes | Step Functions publishes failure notifications to SNS topic |
| CN010 | C006 | C001 | HTTPS | 443 | Yes | Step Functions invokes Memory Scorer Lambda |
| CN011 | C006 | C002 | HTTPS | 443 | Yes | Step Functions invokes Memory Consolidator Lambda |
| CN012 | C007 | C006 | HTTPS | 443 | Yes | EventBridge triggers Step Functions state machine on cron schedule |

### Data Stores

| ID | Name | Type | Classification | Encrypted at Rest | Description |
|---|---|---|---|---|---|
| D001 | CloudTrail Audit Logs | Object Storage | Internal | Yes | CloudTrail audit logs for BedrockAgentCore Memory API calls. Stored in S3 with SSL enforcement, S3-managed encryption, and public access blocked. |
| D002 | Access Ledger | Object Storage | Internal | Yes | JSON access ledger tracking memory access frequency and last access timestamps. Used for scoring calculations. |
| D003 | CloudWatch Logs | Object Storage | Internal | Yes | Structured JSON logs from all Lambda functions with 1-month retention. Contains action types, agent/user/memory IDs, timestamps, and error details. |
| D004 | AgentCore Memory Records | NoSQL | Confidential | Yes | AI agent memories containing user interactions, preferences, conversation history, and behavioral patterns. Managed by Amazon Bedrock AgentCore. |

## Threat Actors

### Insider

- **Type**: ThreatActorType.INSIDER
- **Capability Level**: CapabilityLevel.MEDIUM
- **Motivations**: Financial, Revenge
- **Resources**: ResourceLevel.LIMITED
- **Relevant**: Yes
- **Priority**: 5/10
- **Description**: An employee or contractor with legitimate access to the system

### External Attacker

- **Type**: ThreatActorType.EXTERNAL
- **Capability Level**: CapabilityLevel.MEDIUM
- **Motivations**: Financial
- **Resources**: ResourceLevel.MODERATE
- **Relevant**: Yes
- **Priority**: 3/10
- **Description**: An external individual or group attempting to gain unauthorized access

### Nation-state Actor

- **Type**: ThreatActorType.NATION_STATE
- **Capability Level**: CapabilityLevel.HIGH
- **Motivations**: Espionage, Political
- **Resources**: ResourceLevel.EXTENSIVE
- **Relevant**: Yes
- **Priority**: 1/10
- **Description**: A government-sponsored group with advanced capabilities

### Hacktivist

- **Type**: ThreatActorType.HACKTIVIST
- **Capability Level**: CapabilityLevel.MEDIUM
- **Motivations**: Ideology, Political
- **Resources**: ResourceLevel.MODERATE
- **Relevant**: Yes
- **Priority**: 6/10
- **Description**: An individual or group motivated by ideological or political beliefs

### Organized Crime

- **Type**: ThreatActorType.ORGANIZED_CRIME
- **Capability Level**: CapabilityLevel.HIGH
- **Motivations**: Financial
- **Resources**: ResourceLevel.EXTENSIVE
- **Relevant**: Yes
- **Priority**: 2/10
- **Description**: A criminal organization with significant resources

### Competitor

- **Type**: ThreatActorType.COMPETITOR
- **Capability Level**: CapabilityLevel.MEDIUM
- **Motivations**: Financial, Espionage
- **Resources**: ResourceLevel.MODERATE
- **Relevant**: Yes
- **Priority**: 7/10
- **Description**: A business competitor seeking competitive advantage

### Script Kiddie

- **Type**: ThreatActorType.SCRIPT_KIDDIE
- **Capability Level**: CapabilityLevel.LOW
- **Motivations**: Curiosity, Reputation
- **Resources**: ResourceLevel.LIMITED
- **Relevant**: Yes
- **Priority**: 9/10
- **Description**: An inexperienced attacker using pre-made tools

### Disgruntled Employee

- **Type**: ThreatActorType.DISGRUNTLED_EMPLOYEE
- **Capability Level**: CapabilityLevel.MEDIUM
- **Motivations**: Revenge
- **Resources**: ResourceLevel.LIMITED
- **Relevant**: Yes
- **Priority**: 4/10
- **Description**: A current or former employee with a grievance

### Privileged User

- **Type**: ThreatActorType.PRIVILEGED_USER
- **Capability Level**: CapabilityLevel.HIGH
- **Motivations**: Financial, Accidental
- **Resources**: ResourceLevel.MODERATE
- **Relevant**: Yes
- **Priority**: 8/10
- **Description**: A user with elevated privileges who may abuse them or make mistakes

### Third Party

- **Type**: ThreatActorType.THIRD_PARTY
- **Capability Level**: CapabilityLevel.MEDIUM
- **Motivations**: Financial, Accidental
- **Resources**: ResourceLevel.MODERATE
- **Relevant**: Yes
- **Priority**: 10/10
- **Description**: A vendor, partner, or service provider with access to the system

### Malicious Insider (Cloud Admin)

- **Type**: ThreatActorType.INSIDER
- **Capability Level**: CapabilityLevel.HIGH
- **Motivations**: Financial, Espionage
- **Resources**: ResourceLevel.EXTENSIVE
- **Relevant**: Yes
- **Priority**: 8/10
- **Description**: AWS account administrator or developer with IAM access who could misconfigure policies, access data directly, or abuse elevated privileges.

### External Attacker (Credential Theft)

- **Type**: ThreatActorType.EXTERNAL
- **Capability Level**: CapabilityLevel.MEDIUM
- **Motivations**: Financial, Espionage
- **Resources**: ResourceLevel.MODERATE
- **Relevant**: Yes
- **Priority**: 7/10
- **Description**: External attacker targeting AWS credentials, exploiting misconfigurations, or attempting to access memory data for competitive intelligence or extortion.

### Automated Abuse (GDPR Endpoint)

- **Type**: ThreatActorType.EXTERNAL
- **Capability Level**: CapabilityLevel.LOW
- **Motivations**: Disruption, Revenge
- **Resources**: ResourceLevel.LIMITED
- **Relevant**: Yes
- **Priority**: 6/10
- **Description**: Automated bots or scripts attempting to invoke the GDPR deletion Lambda with forged user IDs to cause data loss or denial of service.

### Supply Chain Attacker

- **Type**: ThreatActorType.EXTERNAL
- **Capability Level**: CapabilityLevel.MEDIUM
- **Motivations**: Financial, Espionage
- **Resources**: ResourceLevel.MODERATE
- **Relevant**: Yes
- **Priority**: 5/10
- **Description**: Attacker who compromises the supply chain (Python dependencies, CDK packages) to inject malicious code into Lambda functions.

### Malicious End User

- **Type**: ThreatActorType.EXTERNAL
- **Capability Level**: CapabilityLevel.LOW
- **Motivations**: Revenge, Disruption
- **Resources**: ResourceLevel.LIMITED
- **Relevant**: Yes
- **Priority**: 4/10
- **Description**: Legitimate user who attempts to abuse the GDPR deletion endpoint to delete other users' data or manipulate the scoring system.

## Trust Boundaries

### Trust Zones

#### Internet

- **Trust Level**: TrustLevel.UNTRUSTED
- **Description**: The public internet, considered untrusted

#### DMZ

- **Trust Level**: TrustLevel.LOW
- **Description**: Demilitarized zone for public-facing services

#### Application

- **Trust Level**: TrustLevel.MEDIUM
- **Description**: Zone containing application servers and services

#### Data

- **Trust Level**: TrustLevel.HIGH
- **Description**: Zone containing databases and data storage

#### Admin

- **Trust Level**: TrustLevel.FULL
- **Description**: Administrative zone with highest privileges

#### External Callers

- **Trust Level**: TrustLevel.UNTRUSTED
- **Description**: External callers invoking the GDPR deletion Lambda or triggering the workflow externally

#### Application Tier (Lambda/Step Functions)

- **Trust Level**: TrustLevel.MEDIUM
- **Description**: Step Functions, EventBridge, and Lambda functions operating within the AWS account with IAM-controlled access

#### Data Tier (AWS Managed Services)

- **Trust Level**: TrustLevel.HIGH
- **Description**: AgentCore Memory service, S3 buckets, and CloudWatch Logs storing sensitive data

#### AI/ML Tier (Bedrock)

- **Trust Level**: TrustLevel.MEDIUM
- **Description**: Amazon Bedrock foundation model service used for AI summarization

### Trust Boundaries

#### Internet Boundary

- **Type**: BoundaryType.NETWORK
- **Controls**: Web Application Firewall, DDoS Protection, TLS Encryption
- **Description**: Boundary between the internet and internal systems

#### DMZ Boundary

- **Type**: BoundaryType.NETWORK
- **Controls**: Network Firewall, Intrusion Detection System, API Gateway
- **Description**: Boundary between public-facing services and internal applications

#### Data Boundary

- **Type**: BoundaryType.NETWORK
- **Controls**: Database Firewall, Encryption, Access Control Lists
- **Description**: Boundary protecting data storage systems

#### Admin Boundary

- **Type**: BoundaryType.NETWORK
- **Controls**: Privileged Access Management, Multi-Factor Authentication, Audit Logging
- **Description**: Boundary for administrative access

#### External-to-Application Boundary

- **Type**: BoundaryType.NETWORK
- **Controls**: No authentication defined in sample
- **Description**: Boundary between external callers and the GDPR deletion Lambda function

#### Application-to-AI Boundary

- **Type**: BoundaryType.PROCESS
- **Controls**: IAM Model-Scoped Policy, TLS Encryption
- **Description**: Boundary between application tier and Bedrock AI model service

#### Application-to-Data Boundary

- **Type**: BoundaryType.PROCESS
- **Controls**: IAM Least Privilege Policies, Resource-based ARN Scoping, CloudTrail Audit Logging
- **Description**: Boundary between Lambda compute and managed data services (AgentCore Memory, S3)

## Assets and Flows

### Assets

| ID | Name | Type | Classification | Sensitivity | Criticality | Owner |
|---|---|---|---|---|---|---|
| A001 | User Credentials | AssetType.CREDENTIAL | AssetClassification.CONFIDENTIAL | 5 | 5 | N/A |
| A002 | Personal Identifiable Information | AssetType.DATA | AssetClassification.CONFIDENTIAL | 4 | 4 | N/A |
| A003 | Session Token | AssetType.TOKEN | AssetClassification.CONFIDENTIAL | 5 | 5 | N/A |
| A004 | Configuration Data | AssetType.CONFIG | AssetClassification.INTERNAL | 3 | 4 | N/A |
| A005 | Encryption Keys | AssetType.KEY | AssetClassification.RESTRICTED | 5 | 5 | N/A |
| A006 | Public Content | AssetType.DATA | AssetClassification.PUBLIC | 1 | 2 | N/A |
| A007 | Audit Logs | AssetType.DATA | AssetClassification.INTERNAL | 3 | 4 | N/A |
| A008 | Agent Memory Records | AssetType.DATA | AssetClassification.CONFIDENTIAL | 5 | 5 | Platform Team |
| A009 | User Identity Data | AssetType.DATA | AssetClassification.CONFIDENTIAL | 4 | 4 | Platform Team |
| A010 | Audit Trail Logs | AssetType.DATA | AssetClassification.INTERNAL | 3 | 3 | Security Team |
| A011 | Lambda IAM Credentials | AssetType.CREDENTIAL | AssetClassification.RESTRICTED | 5 | 5 | DevOps Team |
| A012 | Consolidated Memory Summaries | AssetType.DATA | AssetClassification.CONFIDENTIAL | 4 | 4 | Platform Team |

### Asset Flows

| ID | Asset | Source | Destination | Protocol | Encrypted | Risk Level |
|---|---|---|---|---|---|---|
| F001 | User Credentials | C001 | C002 | HTTPS | Yes | 4 |
| F002 | Session Token | C002 | C001 | HTTPS | Yes | 3 |
| F003 | Personal Identifiable Information | C003 | C004 | TLS | Yes | 3 |
| F004 | Audit Logs | C003 | C005 | TLS | Yes | 2 |
| F005 | Agent Memory Records | C009 | C001 | HTTPS | Yes | 2 |
| F006 | User Identity Data | C004 | C004 | HTTPS | Yes | 5 |

## Threats

### Identified Threats

#### T1: External attacker or malicious user

**Statement**: A External attacker or malicious user with ability to invoke the GDPR Deletion Lambda can invoke GDPR deletion with a forged user_id to delete another user's memories, which leads to unauthorized deletion of all memories for a victim user, causing data loss and service degradation

- **Prerequisites**: with ability to invoke the GDPR Deletion Lambda
- **Action**: invoke GDPR deletion with a forged user_id to delete another user's memories
- **Impact**: unauthorized deletion of all memories for a victim user, causing data loss and service degradation
- **Impacted Assets**: A008
- **Tags**: STRIDE-S, GDPR, Authentication

#### T2: Insider with AWS account access

**Statement**: A Insider with AWS account access with access to Bedrock model invocation logs or model provider systems can extract sensitive memory content from Bedrock model invocation data or prompt logs, which leads to exposure of confidential user memory data including PII and conversation history

- **Prerequisites**: with access to Bedrock model invocation logs or model provider systems
- **Action**: extract sensitive memory content from Bedrock model invocation data or prompt logs
- **Impact**: exposure of confidential user memory data including PII and conversation history
- **Impacted Assets**: A008, A012
- **Tags**: STRIDE-I, AI, Data-Leakage

#### T3: Malicious insider or compromised CI/CD pipeline

**Statement**: A Malicious insider or compromised CI/CD pipeline with ability to modify scoring parameters or Lambda environment variables can manipulate relevance threshold or decay rate to cause premature deletion of valuable memories, which leads to mass deletion of important agent memories, degrading AI agent quality and user experience

- **Prerequisites**: with ability to modify scoring parameters or Lambda environment variables
- **Action**: manipulate relevance threshold or decay rate to cause premature deletion of valuable memories
- **Impact**: mass deletion of important agent memories, degrading AI agent quality and user experience
- **Impacted Assets**: A008
- **Tags**: STRIDE-T, Scoring, Configuration

#### T4: Insider with elevated AWS permissions

**Statement**: A Insider with elevated AWS permissions with S3 write access to the CloudTrail bucket can modify or delete CloudTrail audit logs to hide unauthorized memory access or deletion, which leads to loss of audit trail for GDPR compliance, inability to detect unauthorized data access

- **Prerequisites**: with S3 write access to the CloudTrail bucket
- **Action**: modify or delete CloudTrail audit logs to hide unauthorized memory access or deletion
- **Impact**: loss of audit trail for GDPR compliance, inability to detect unauthorized data access
- **Impacted Assets**: A010
- **Tags**: STRIDE-T, Audit, Compliance

#### T5: Automated bot or malicious user

**Statement**: A Automated bot or malicious user with ability to invoke the GDPR Deletion Lambda repeatedly can flood GDPR deletion endpoint with requests to exhaust Lambda concurrency or API rate limits, which leads to denial of service for legitimate GDPR requests and potential impact on nightly workflow

- **Prerequisites**: with ability to invoke the GDPR Deletion Lambda repeatedly
- **Action**: flood GDPR deletion endpoint with requests to exhaust Lambda concurrency or API rate limits
- **Impact**: denial of service for legitimate GDPR requests and potential impact on nightly workflow
- **Impacted Assets**: A008
- **Tags**: STRIDE-D, GDPR, Abuse

#### T6: Attacker who can write to agent memories

**Statement**: A Attacker who can write to agent memories with ability to manipulate Bedrock model responses can inject malicious content into memory text that manipulates the consolidation prompt to produce harmful summaries, which leads to corrupted consolidated memories containing injected content, misinformation, or data exfiltration payloads

- **Prerequisites**: with ability to manipulate Bedrock model responses
- **Action**: inject malicious content into memory text that manipulates the consolidation prompt to produce harmful summaries
- **Impact**: corrupted consolidated memories containing injected content, misinformation, or data exfiltration payloads
- **Impacted Assets**: A008
- **Tags**: STRIDE-T, AI, Prompt-Injection

#### T7: External attacker exploiting vulnerable dependency

**Statement**: A External attacker exploiting vulnerable dependency with access to Lambda execution environment or compromised dependency can exploit Lambda IAM role to access resources beyond intended scope or escalate to other AWS services, which leads to unauthorized access to all AgentCore memories, S3 data, or lateral movement to other AWS services

- **Prerequisites**: with access to Lambda execution environment or compromised dependency
- **Action**: exploit Lambda IAM role to access resources beyond intended scope or escalate to other AWS services
- **Impact**: unauthorized access to all AgentCore memories, S3 data, or lateral movement to other AWS services
- **Impacted Assets**: A011
- **Tags**: STRIDE-E, IAM, Privilege-Escalation

#### T8: Insider or attacker with Step Functions execution permissions

**Statement**: A Insider or attacker with Step Functions execution permissions with ability to trigger the Step Functions workflow or modify its input can trigger unscheduled workflow execution or manipulate scoring results to cause mass memory deletion, which leads to irreversible loss of agent memories at scale, degrading AI agent capabilities

- **Prerequisites**: with ability to trigger the Step Functions workflow or modify its input
- **Action**: trigger unscheduled workflow execution or manipulate scoring results to cause mass memory deletion
- **Impact**: irreversible loss of agent memories at scale, degrading AI agent capabilities
- **Impacted Assets**: A008
- **Tags**: STRIDE-D, Workflow, Data-Loss

#### T9: Any caller with Lambda invoke access

**Statement**: A Any caller with Lambda invoke access with ability to invoke GDPR deletion Lambda can invoke GDPR deletion without proper identity verification, making it impossible to verify legitimacy, which leads to inability to prove deletion was requested by the actual data subject, GDPR compliance risk

- **Prerequisites**: with ability to invoke GDPR deletion Lambda
- **Action**: invoke GDPR deletion without proper identity verification, making it impossible to verify legitimacy
- **Impact**: inability to prove deletion was requested by the actual data subject, GDPR compliance risk
- **Impacted Assets**: A008, A009
- **Tags**: STRIDE-R, GDPR, Audit

#### T10: Insider with CloudWatch Logs read access

**Statement**: A Insider with CloudWatch Logs read access with access to CloudWatch Logs can access structured JSON logs containing memory IDs, user IDs, and agent IDs to map user activity, which leads to exposure of user-to-memory mappings enabling targeted attacks or privacy violations

- **Prerequisites**: with access to CloudWatch Logs
- **Action**: access structured JSON logs containing memory IDs, user IDs, and agent IDs to map user activity
- **Impact**: exposure of user-to-memory mappings enabling targeted attacks or privacy violations
- **Impacted Assets**: A008
- **Tags**: STRIDE-I, Logging, PII

#### T11: Supply chain attacker targeting Python package ecosystem

**Statement**: A Supply chain attacker targeting Python package ecosystem with ability to publish malicious Python packages can compromise a Python dependency (boto3, botocore) to inject code that exfiltrates credentials or data, which leads to full compromise of Lambda execution environment, credential theft, data exfiltration

- **Prerequisites**: with ability to publish malicious Python packages
- **Action**: compromise a Python dependency (boto3, botocore) to inject code that exfiltrates credentials or data
- **Impact**: full compromise of Lambda execution environment, credential theft, data exfiltration
- **Impacted Assets**: A011
- **Tags**: STRIDE-E, Supply-Chain, Dependencies

## Mitigations

### Identified Mitigations

#### M1: Implement authentication and authorization for GDPR Deletion Lambda invocations. Require verified identity (e.g., API Gateway with Cognito authorizer or IAM auth) before processing deletion requests.

**Addresses Threats**: T1, T5, T9

#### M2: Implement rate limiting and throttling on the GDPR Deletion Lambda to prevent abuse and denial of service attacks.

**Addresses Threats**: T5

#### M3: Enable CloudTrail log file integrity validation and configure S3 Object Lock to prevent audit log tampering or deletion.

**Addresses Threats**: T4

#### M4: Implement input validation on the GDPR Deletion Lambda to verify user_id format and prevent injection attacks. Add request logging with caller identity.

**Addresses Threats**: T1, T9

#### M5: Implement soft-delete with retention period before permanent memory deletion to allow recovery from accidental or malicious mass deletions.

**Addresses Threats**: T8, T1

#### M6: Pin Python dependency versions and use hash verification in requirements.txt. Implement dependency scanning in CI/CD pipeline.

**Addresses Threats**: T11

#### M7: Implement prompt injection defenses in the Memory Consolidator by sanitizing memory content before sending to Bedrock and validating model output format.

**Addresses Threats**: T6

#### M8: Restrict Step Functions execution permissions to only the EventBridge rule. Add CloudWatch alarms for unscheduled executions.

**Addresses Threats**: T8

#### M9: Enable SNS topic encryption with KMS and enforce SSL for SNS subscriptions to protect failure notification content.

#### M10: Implement least-privilege IAM policies with condition keys to restrict Lambda access to specific memory namespaces and prevent cross-tenant access.

**Addresses Threats**: T7, T3

#### M11: Redact or mask sensitive data (user IDs, memory content) in CloudWatch Logs. Implement log access controls with IAM policies scoped to specific log groups.

**Addresses Threats**: T2, T10

## Assumptions

### A001: AWS Services

**Description**: The system operates within a single AWS region and does not replicate data across regions

- **Impact**: Limits blast radius of regional outages but concentrates data in one location
- **Rationale**: CDK stack deploys to a single region; CloudTrail is configured as single-region trail

### A002: Data Classification

**Description**: Agent memories may contain PII, user preferences, and conversation history that falls under GDPR regulation

- **Impact**: Requires strict data protection controls, right-to-erasure support, and audit logging
- **Rationale**: The system processes AI agent memories from user interactions which inherently contain personal data

### A003: Operations

**Description**: The nightly workflow runs unattended at 2 AM UTC without human oversight

- **Impact**: Automated deletion of memories without real-time human approval increases risk of unintended data loss
- **Rationale**: EventBridge cron triggers Step Functions automatically; SNS alerts are reactive not preventive

### A004: Data Processing

**Description**: Amazon Bedrock model invocations for memory consolidation may expose memory content to the model provider

- **Impact**: Sensitive memory content is sent to Bedrock for summarization, creating a data processing dependency
- **Rationale**: The consolidator sends full memory text to Claude for summarization via the Bedrock InvokeModel API

## Phase Progress

| Phase | Name | Completion |
|---|---|---|
| 1 | Business Context Analysis | 100% ✅ |
| 2 | Architecture Analysis | 100% ✅ |
| 3 | Threat Actor Analysis | 100% ✅ |
| 4 | Trust Boundary Analysis | 100% ✅ |
| 5 | Asset Flow Analysis | 100% ✅ |
| 6 | Threat Identification | 100% ✅ |
| 7 | Mitigation Planning | 100% ✅ |
| 7.5 | Code Validation Analysis | 0% ⏳ |
| 8 | Residual Risk Analysis | 0% ⏳ |
| 9 | Output Generation and Documentation | 100% ✅ |

---