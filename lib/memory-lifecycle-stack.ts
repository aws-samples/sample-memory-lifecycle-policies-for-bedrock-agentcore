import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as sfn from 'aws-cdk-lib/aws-stepfunctions';
import * as tasks from 'aws-cdk-lib/aws-stepfunctions-tasks';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as cloudtrail from 'aws-cdk-lib/aws-cloudtrail';
import * as s3 from 'aws-cdk-lib/aws-s3';
import { Construct } from 'constructs';
import * as path from 'path';
import { Aspects } from 'aws-cdk-lib';
import { AwsSolutionsChecks, NagSuppressions } from 'cdk-nag';

export class MemoryLifecycleStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // ---------------------
    // CDK Context Parameters
    // ---------------------
    const memoryTtlDays = this.node.tryGetContext('memoryTtlDays') ?? 90;
    const relevanceThreshold = this.node.tryGetContext('relevanceThreshold') ?? 0.3;
    const consolidationBatchSize = this.node.tryGetContext('consolidationBatchSize') ?? 10;
    const bedrockModelId =
      this.node.tryGetContext('bedrockModelId') ??
      'anthropic.claude-sonnet-4-5-20250929-v1:0';
    const pruneDays = this.node.tryGetContext('pruneDays') ?? 45;
    const wRecency = this.node.tryGetContext('wRecency') ?? 0.4;
    const wAccess = this.node.tryGetContext('wAccess') ?? 0.35;
    const wFrequency = this.node.tryGetContext('wFrequency') ?? 0.25;
    const maxAccessBaseline = this.node.tryGetContext('maxAccessBaseline') ?? 50;

    // ---------------------
    // CloudTrail Trail Bucket (created early so Lambda env vars can reference it)
    // ---------------------
    const trailBucket = new s3.Bucket(this, 'MemoryLifecycleTrailBucket', {
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      enforceSSL: true,
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
    });

    // ---------------------
    // Run Output Bucket — stores workflow run output and access ledger
    // ---------------------
    const runOutputBucket = new s3.Bucket(this, 'RunOutputBucket', {
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      enforceSSL: true,
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
    });

    // ---------------------
    // Lambda Layer — Shared Python module
    // ---------------------
    // The shared/ package (constants, models) is deployed as a Lambda Layer
    // so that all handlers can `from shared.constants import ...` at runtime.
    const sharedLayer = new lambda.LayerVersion(this, 'SharedLayer', {
      code: lambda.Code.fromAsset(path.join(__dirname, '..', 'lambdas', 'shared'), {
        bundling: {
          image: lambda.Runtime.PYTHON_3_12.bundlingImage,
          command: [
            'bash', '-c',
            'mkdir -p /asset-output/python/shared && cp -r . /asset-output/python/shared/',
          ],
          local: {
            tryBundle(outputDir: string) {
              const fs = require('fs');
              const targetDir = path.join(outputDir, 'python', 'shared');
              fs.mkdirSync(targetDir, { recursive: true });
              const sourceDir = path.join(__dirname, '..', 'lambdas', 'shared');
              for (const file of fs.readdirSync(sourceDir)) {
                const srcPath = path.join(sourceDir, file);
                if (fs.statSync(srcPath).isFile()) {
                  fs.copyFileSync(srcPath, path.join(targetDir, file));
                }
              }
              return true;
            },
          },
        },
      }),
      compatibleRuntimes: [lambda.Runtime.PYTHON_3_12],
      description: 'Shared constants and models for memory lifecycle Lambdas',
    });

    // ---------------------
    // Lambda Functions
    // ---------------------
    const memoryScorerFn = new lambda.Function(this, 'MemoryScorerFunction', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'handler.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '..', 'lambdas', 'memory_scorer')),
      layers: [sharedLayer],
      timeout: cdk.Duration.minutes(5),
      environment: {
        MEMORY_TTL_DAYS: String(memoryTtlDays),
        RELEVANCE_THRESHOLD: String(relevanceThreshold),
        CONSOLIDATION_BATCH_SIZE: String(consolidationBatchSize),
        BEDROCK_MODEL_ID: bedrockModelId,
        PRUNE_DAYS: String(pruneDays),
        TRAIL_BUCKET_NAME: trailBucket.bucketName,
        TRAIL_LOOKBACK_HOURS: '25',
        W_RECENCY: String(wRecency),
        W_ACCESS: String(wAccess),
        W_FREQUENCY: String(wFrequency),
        MAX_ACCESS_BASELINE: String(maxAccessBaseline),
        AWS_ACCOUNT_ID: this.account,
        RUN_OUTPUT_BUCKET_NAME: runOutputBucket.bucketName,
      },
    });

    const memoryConsolidatorFn = new lambda.Function(this, 'MemoryConsolidatorFunction', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'handler.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '..', 'lambdas', 'memory_consolidator')),
      layers: [sharedLayer],
      timeout: cdk.Duration.minutes(10),
      environment: {
        MEMORY_TTL_DAYS: String(memoryTtlDays),
        RELEVANCE_THRESHOLD: String(relevanceThreshold),
        CONSOLIDATION_BATCH_SIZE: String(consolidationBatchSize),
        BEDROCK_MODEL_ID: bedrockModelId,
        PRUNE_DAYS: String(pruneDays),
      },
    });

    const memoryPrunerFn = new lambda.Function(this, 'MemoryPrunerFunction', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'handler.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '..', 'lambdas', 'memory_pruner')),
      layers: [sharedLayer],
      timeout: cdk.Duration.minutes(5),
      environment: {
        MEMORY_TTL_DAYS: String(memoryTtlDays),
        RELEVANCE_THRESHOLD: String(relevanceThreshold),
        CONSOLIDATION_BATCH_SIZE: String(consolidationBatchSize),
        BEDROCK_MODEL_ID: bedrockModelId,
        PRUNE_DAYS: String(pruneDays),
      },
    });

    const gdprDeletionFn = new lambda.Function(this, 'GDPRDeletionFunction', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'handler.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '..', 'lambdas', 'gdpr_deletion')),
      layers: [sharedLayer],
      timeout: cdk.Duration.minutes(10),
      environment: {
        MEMORY_TTL_DAYS: String(memoryTtlDays),
        RELEVANCE_THRESHOLD: String(relevanceThreshold),
        CONSOLIDATION_BATCH_SIZE: String(consolidationBatchSize),
        BEDROCK_MODEL_ID: bedrockModelId,
        PRUNE_DAYS: String(pruneDays),
      },
    });

    const metricsEmitterFn = new lambda.Function(this, 'MetricsEmitterFunction', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'handler.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '..', 'lambdas', 'metrics_emitter')),
      layers: [sharedLayer],
      timeout: cdk.Duration.seconds(30),
    });

    const runOutputWriterFn = new lambda.Function(this, 'RunOutputWriterFunction', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'handler.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '..', 'lambdas', 'run_output_writer')),
      layers: [sharedLayer],
      timeout: cdk.Duration.seconds(30),
      environment: {
        RUN_OUTPUT_BUCKET_NAME: runOutputBucket.bucketName,
      },
    });

    // ---------------------
    // IAM Policies — Least Privilege
    // ---------------------

    // Memory Scorer: ListMemoryRecords, BatchUpdateMemoryRecords on AgentCore Memory
    // Note: PutLogEvents is already granted by the CDK-managed AWSLambdaBasicExecutionRole
    memoryScorerFn.addToRolePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['bedrock-agentcore:ListMemoryRecords'],
      resources: [
        `arn:aws:bedrock-agentcore:${this.region}:${this.account}:memory/*`,
      ],
    }));

    // Memory Scorer: S3 read access to CloudTrail trail bucket
    memoryScorerFn.addToRolePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['s3:GetObject', 's3:ListBucket'],
      resources: [
        trailBucket.bucketArn,
        `${trailBucket.bucketArn}/*`,
      ],
    }));

    // Memory Consolidator: GetMemoryRecord, BatchCreateMemoryRecords, DeleteMemoryRecord on AgentCore Memory
    //                      + InvokeModel on Bedrock
    memoryConsolidatorFn.addToRolePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: [
        'bedrock-agentcore:GetMemoryRecord',
        'bedrock-agentcore:BatchCreateMemoryRecords',
        'bedrock-agentcore:DeleteMemoryRecord',
      ],
      resources: [
        `arn:aws:bedrock-agentcore:${this.region}:${this.account}:memory/*`,
      ],
    }));
    memoryConsolidatorFn.addToRolePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['bedrock:InvokeModel'],
      resources: [
        `arn:aws:bedrock:${this.region}::foundation-model/${bedrockModelId}`,
      ],
    }));

    // Memory Pruner: ListMemoryRecords (TTL mode), DeleteMemoryRecord on AgentCore Memory
    memoryPrunerFn.addToRolePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['bedrock-agentcore:ListMemoryRecords', 'bedrock-agentcore:DeleteMemoryRecord'],
      resources: [
        `arn:aws:bedrock-agentcore:${this.region}:${this.account}:memory/*`,
      ],
    }));

    // Memory Scorer: S3 read/write access to access ledger in run output bucket
    memoryScorerFn.addToRolePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['s3:GetObject', 's3:PutObject'],
      resources: [
        `${runOutputBucket.bucketArn}/ledger/*`,
      ],
    }));

    // Metrics Emitter: PutMetricData on CloudWatch (does not support resource-level restrictions)
    metricsEmitterFn.addToRolePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['cloudwatch:PutMetricData'],
      resources: ['*'],
    }));

    // Run Output Writer: S3 PutObject on run output bucket
    runOutputWriterFn.addToRolePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['s3:PutObject'],
      resources: [
        `${runOutputBucket.bucketArn}/*`,
      ],
    }));

    // GDPR Deletion Handler: ListMemoryRecords, DeleteMemoryRecord on AgentCore Memory
    gdprDeletionFn.addToRolePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['bedrock-agentcore:ListMemoryRecords', 'bedrock-agentcore:DeleteMemoryRecord'],
      resources: [
        `arn:aws:bedrock-agentcore:${this.region}:${this.account}:memory/*`,
      ],
    }));

    // ---------------------
    // SNS Topic for Failure Notifications
    // ---------------------
    const failureTopic = new sns.Topic(this, 'MemoryLifecycleFailureTopic', {
      displayName: 'Memory Lifecycle Workflow Failure Notifications',
    });

    // ---------------------
    // Step Functions Workflow
    // ---------------------

    // Retry configuration for transient errors
    const retryConfig: sfn.RetryProps = {
      maxAttempts: 2,
      interval: cdk.Duration.seconds(5),
      backoffRate: 2.0,
      errors: ['States.TaskFailed', 'States.Timeout'],
    };

    // HandleFailure state — publishes to SNS with step name and error details
    const handleFailure = new tasks.SnsPublish(this, 'HandleFailure', {
      topic: failureTopic,
      message: sfn.TaskInput.fromObject({
        step: sfn.JsonPath.stringAt('$.stepName'),
        error: sfn.JsonPath.stringAt('$.error'),
        cause: sfn.JsonPath.stringAt('$.cause'),
      }),
      resultPath: sfn.JsonPath.DISCARD,
    });

    // Helper to build a catch config that injects the step name
    const buildCatch = (stepName: string): sfn.CatchProps => ({
      resultPath: '$.errorInfo',
    });

    // Pass state to inject step name before SNS publish
    const formatFailure = (stepName: string) => {
      return new sfn.Pass(this, `Format${stepName}Failure`, {
        parameters: {
          stepName: stepName,
          'error.$': '$.errorInfo.Error',
          'cause.$': '$.errorInfo.Cause',
        },
      }).next(handleFailure);
    };

    // --- Task States ---

    // 1. TTLExpiration — invokes Memory Pruner to delete TTL-expired memories
    const ttlExpiration = new tasks.LambdaInvoke(this, 'TTLExpiration', {
      lambdaFunction: memoryPrunerFn,
      payloadResponseOnly: true,
      resultPath: '$.ttlResult',
    });
    ttlExpiration.addRetry(retryConfig);
    ttlExpiration.addCatch(formatFailure('TTLExpiration'), buildCatch('TTLExpiration'));

    // 2. ScoreMemories — invokes Memory Scorer
    const scoreMemories = new tasks.LambdaInvoke(this, 'ScoreMemories', {
      lambdaFunction: memoryScorerFn,
      payloadResponseOnly: true,
      resultPath: '$.scoringResult',
    });
    scoreMemories.addRetry(retryConfig);
    scoreMemories.addCatch(formatFailure('ScoreMemories'), buildCatch('ScoreMemories'));

    // 3. CheckLowScoreMemories — Choice state
    const checkLowScoreMemories = new sfn.Choice(this, 'CheckLowScoreMemories');

    // 4. BatchConsolidate — Map state invoking Memory Consolidator per batch
    const batchConsolidate = new sfn.Map(this, 'BatchConsolidate', {
      itemsPath: '$.scoringResult.below_threshold',
      maxConcurrency: 1,
      resultPath: '$.consolidationResults',
    });
    const consolidateTask = new tasks.LambdaInvoke(this, 'ConsolidateMemoryBatch', {
      lambdaFunction: memoryConsolidatorFn,
      payloadResponseOnly: true,
    });
    consolidateTask.addRetry(retryConfig);
    batchConsolidate.itemProcessor(consolidateTask);
    batchConsolidate.addCatch(formatFailure('BatchConsolidate'), buildCatch('BatchConsolidate'));

    // 5. EmitMetrics — invokes Metrics Emitter to publish CloudWatch metrics
    const emitMetrics = new tasks.LambdaInvoke(this, 'EmitMetrics', {
      lambdaFunction: metricsEmitterFn,
      payloadResponseOnly: true,
      resultPath: '$.metricsResult',
    });
    emitMetrics.addRetry(retryConfig);
    emitMetrics.addCatch(formatFailure('EmitMetrics'), buildCatch('EmitMetrics'));

    // 6. WriteRunOutput — invokes Run Output Writer to persist run results to S3
    const writeRunOutput = new tasks.LambdaInvoke(this, 'WriteRunOutput', {
      lambdaFunction: runOutputWriterFn,
      payloadResponseOnly: true,
      resultPath: '$.runOutputResult',
    });
    writeRunOutput.addRetry(retryConfig);
    writeRunOutput.addCatch(formatFailure('WriteRunOutput'), buildCatch('WriteRunOutput'));

    // --- Wire the workflow ---
    // Chain EmitMetrics → WriteRunOutput once (both branches converge here)
    const emitAndWrite = emitMetrics.next(writeRunOutput);

    const definition = ttlExpiration
      .next(scoreMemories)
      .next(
        checkLowScoreMemories
          .when(
            sfn.Condition.isPresent('$.scoringResult.below_threshold[0]'),
            batchConsolidate.next(emitAndWrite),
          )
          .otherwise(emitAndWrite),
      );

    const stateMachine = new sfn.StateMachine(this, 'MemoryLifecycleStateMachine', {
      definitionBody: sfn.DefinitionBody.fromChainable(definition),
      timeout: cdk.Duration.hours(1),
      tracingEnabled: true,
    });

    // ---------------------
    // EventBridge Rule — Nightly Trigger
    // ---------------------
    new events.Rule(this, 'NightlyMemoryLifecycleRule', {
      schedule: events.Schedule.expression('cron(0 2 * * ? *)'),
      targets: [new targets.SfnStateMachine(stateMachine)],
      description: 'Triggers the Memory Lifecycle workflow nightly at 2 AM UTC',
    });

    // ---------------------
    // CloudWatch Dashboard — Observability
    // ---------------------
    const dashboard = new cloudwatch.Dashboard(this, 'MemoryLifecycleDashboard', {
      dashboardName: 'MemoryLifecycleDashboard',
    });

    // Step Functions execution metrics
    const sfnMetrics = [
      stateMachine.metricStarted({ period: cdk.Duration.hours(1) }),
      stateMachine.metricSucceeded({ period: cdk.Duration.hours(1) }),
      stateMachine.metricFailed({ period: cdk.Duration.hours(1) }),
    ];

    dashboard.addWidgets(
      new cloudwatch.GraphWidget({
        title: 'Step Functions Workflow Executions',
        left: sfnMetrics,
        width: 12,
      }),
    );

    // Lambda invocation metrics for each function
    const lambdaFunctions = [
      { fn: memoryScorerFn, name: 'MemoryScorer' },
      { fn: memoryConsolidatorFn, name: 'MemoryConsolidator' },
      { fn: memoryPrunerFn, name: 'MemoryPruner' },
      { fn: gdprDeletionFn, name: 'GDPRDeletion' },
      { fn: metricsEmitterFn, name: 'MetricsEmitter' },
      { fn: runOutputWriterFn, name: 'RunOutputWriter' },
    ];

    dashboard.addWidgets(
      new cloudwatch.GraphWidget({
        title: 'Lambda Invocations',
        left: lambdaFunctions.map(({ fn }) =>
          fn.metricInvocations({ period: cdk.Duration.hours(1) }),
        ),
        width: 12,
      }),
      new cloudwatch.GraphWidget({
        title: 'Lambda Errors',
        left: lambdaFunctions.map(({ fn }) =>
          fn.metricErrors({ period: cdk.Duration.hours(1) }),
        ),
        width: 12,
      }),
    );

    // Custom metrics namespace for memory lifecycle
    const customMetricsNamespace = 'MemoryLifecycle';

    const memoriesProcessedMetric = new cloudwatch.Metric({
      namespace: customMetricsNamespace,
      metricName: 'MemoriesProcessed',
      statistic: 'Sum',
      period: cdk.Duration.hours(1),
    });

    const memoriesConsolidatedMetric = new cloudwatch.Metric({
      namespace: customMetricsNamespace,
      metricName: 'MemoriesConsolidated',
      statistic: 'Sum',
      period: cdk.Duration.hours(1),
    });

    const memoriesPrunedMetric = new cloudwatch.Metric({
      namespace: customMetricsNamespace,
      metricName: 'MemoriesPruned',
      statistic: 'Sum',
      period: cdk.Duration.hours(1),
    });

    const workflowExecutionStatusMetric = new cloudwatch.Metric({
      namespace: customMetricsNamespace,
      metricName: 'WorkflowExecutionStatus',
      statistic: 'Sum',
      period: cdk.Duration.hours(1),
    });

    dashboard.addWidgets(
      new cloudwatch.GraphWidget({
        title: 'Memory Lifecycle Custom Metrics',
        left: [
          memoriesProcessedMetric,
          memoriesConsolidatedMetric,
          memoriesPrunedMetric,
        ],
        right: [workflowExecutionStatusMetric],
        width: 24,
      }),
    );

    // ---------------------
    // CloudTrail — Audit Logging for AgentCore Memory API calls
    // ---------------------
    const trail = new cloudtrail.Trail(this, 'MemoryLifecycleTrail', {
      bucket: trailBucket,
      trailName: 'MemoryLifecycleAuditTrail',
      isMultiRegionTrail: false,
      includeGlobalServiceEvents: false,
      enableFileValidation: true,
    });

    // Add advanced event selectors for BedrockAgentCore Memory data events
    const cfnTrail = trail.node.defaultChild as cloudtrail.CfnTrail;
    cfnTrail.addPropertyOverride('AdvancedEventSelectors', [
      {
        Name: 'MemoryDataEvents',
        FieldSelectors: [
          {
            Field: 'eventCategory',
            EqualTo: ['Data'],
          },
          {
            Field: 'resources.type',
            EqualTo: ['AWS::BedrockAgentCore::Memory'],
          },
        ],
      },
    ]);

    // ---------------------
    // Structured JSON Logging — CloudWatch Logs
    // ---------------------
    // Ensure all Lambda functions use structured JSON log format via
    // application log level and system log level configuration.
    // CDK automatically creates log groups for Lambda functions.
    // We set explicit log groups with retention for each function.
    const lambdaLogConfigs = [
      { fn: memoryScorerFn, id: 'MemoryScorerLogGroup' },
      { fn: memoryConsolidatorFn, id: 'MemoryConsolidatorLogGroup' },
      { fn: memoryPrunerFn, id: 'MemoryPrunerLogGroup' },
      { fn: gdprDeletionFn, id: 'GDPRDeletionLogGroup' },
      { fn: metricsEmitterFn, id: 'MetricsEmitterLogGroup' },
      { fn: runOutputWriterFn, id: 'RunOutputWriterLogGroup' },
    ];

    for (const { fn, id } of lambdaLogConfigs) {
      new logs.LogGroup(this, id, {
        logGroupName: `/aws/lambda/${fn.functionName}`,
        retention: logs.RetentionDays.ONE_MONTH,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      });
    }

    // ---------------------
    // cdk-nag — AWS Solutions Checks
    // ---------------------
    Aspects.of(this).add(new AwsSolutionsChecks({ verbose: true }));

    // Suppress acceptable findings for this sample
    NagSuppressions.addStackSuppressions(this, [
      {
        id: 'AwsSolutions-IAM4',
        reason: 'AWSLambdaBasicExecutionRole is required for CloudWatch Logs access and is the minimal managed policy for Lambda.',
      },
      {
        id: 'AwsSolutions-IAM5',
        reason: 'Wildcard permissions are scoped to specific resource ARN patterns (e.g., memory/*, ledger/*) and are required for AgentCore Memory operations across dynamic resource IDs.',
      },
      {
        id: 'AwsSolutions-L1',
        reason: 'Python 3.12 is the latest supported Lambda runtime for this project.',
      },
      {
        id: 'AwsSolutions-SF1',
        reason: 'Step Functions logging is not enabled to reduce cost for this sample. Enable for production use.',
      },
      {
        id: 'AwsSolutions-SF2',
        reason: 'X-Ray tracing is enabled via tracingEnabled property on the state machine.',
      },
      {
        id: 'AwsSolutions-SNS2',
        reason: 'SNS topic encryption is not enabled to simplify this sample. Enable KMS encryption for production use.',
      },
      {
        id: 'AwsSolutions-SNS3',
        reason: 'SNS topic does not require SSL enforcement for this sample notification use case.',
      },
      {
        id: 'AwsSolutions-S1',
        reason: 'S3 server access logging is not enabled to reduce cost for this sample. Enable for production use.',
      },
      {
        id: 'AwsSolutions-CB4',
        reason: 'CloudWatch Metrics wildcard resource is required as PutMetricData does not support resource-level permissions.',
      },
    ]);
  }
}
