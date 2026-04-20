import * as cdk from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import { MemoryLifecycleStack } from '../lib/memory-lifecycle-stack';

describe('MemoryLifecycleStack', () => {
  let template: Template;

  beforeAll(() => {
    const app = new cdk.App();
    const stack = new MemoryLifecycleStack(app, 'TestStack');
    template = Template.fromStack(stack);
  });

  test('creates 6 Lambda functions', () => {
    const lambdas = template.findResources('AWS::Lambda::Function', {
      Properties: { Runtime: 'python3.12' },
    });
    expect(Object.keys(lambdas)).toHaveLength(6);
  });

  test('creates 1 Step Functions state machine', () => {
    template.resourceCountIs('AWS::StepFunctions::StateMachine', 1);
  });

  test('creates 1 EventBridge rule', () => {
    template.resourceCountIs('AWS::Events::Rule', 1);
  });

  test('creates 1 SNS topic', () => {
    template.resourceCountIs('AWS::SNS::Topic', 1);
  });

  test('creates 1 CloudWatch dashboard', () => {
    template.resourceCountIs('AWS::CloudWatch::Dashboard', 1);
  });

  test('EventBridge rule has cron(0 2 * * ? *) schedule', () => {
    template.hasResourceProperties('AWS::Events::Rule', {
      ScheduleExpression: 'cron(0 2 * * ? *)',
    });
  });

  test('all Lambda functions use Python 3.12 runtime', () => {
    const pythonLambdas = template.findResources('AWS::Lambda::Function', {
      Properties: { Runtime: 'python3.12' },
    });
    // All 6 application Lambdas should use Python 3.12
    expect(Object.keys(pythonLambdas)).toHaveLength(6);
    for (const [, resource] of Object.entries(pythonLambdas)) {
      expect((resource as any).Properties.Runtime).toBe('python3.12');
    }
  });

  test('CloudTrail trail has advanced event selectors for Memory data events', () => {
    template.hasResourceProperties('AWS::CloudTrail::Trail', {
      AdvancedEventSelectors: [
        {
          Name: 'MemoryDataEvents',
          FieldSelectors: [
            { Field: 'eventCategory', EqualTo: ['Data'] },
            { Field: 'resources.type', EqualTo: ['AWS::BedrockAgentCore::Memory'] },
          ],
        },
      ],
    });
  });

  test('S3 read permissions for scorer Lambda include GetObject and ListBucket', () => {
    template.hasResourceProperties('AWS::IAM::Policy', {
      PolicyDocument: {
        Statement: Match.arrayWith([
          Match.objectLike({
            Action: ['s3:GetObject', 's3:ListBucket'],
            Effect: 'Allow',
          }),
        ]),
      },
    });
  });

  test('Memory Scorer Lambda has CloudTrail environment variables', () => {
    const lambdas = template.findResources('AWS::Lambda::Function');
    const scorerLambda = Object.values(lambdas).find((l: any) =>
      l.Properties?.Environment?.Variables?.TRAIL_BUCKET_NAME !== undefined
    );
    expect(scorerLambda).toBeDefined();
    const vars = (scorerLambda as any).Properties.Environment.Variables;
    expect(vars.TRAIL_LOOKBACK_HOURS).toBe('25');
    expect(vars.W_RECENCY).toBe('0.4');
    expect(vars.W_ACCESS).toBe('0.35');
    expect(vars.W_FREQUENCY).toBe('0.25');
    expect(vars.MAX_ACCESS_BASELINE).toBe('50');
  });

  test('CDK context parameters override defaults', () => {
    const app = new cdk.App({
      context: {
        wRecency: 0.5,
        wAccess: 0.3,
        wFrequency: 0.2,
        maxAccessBaseline: 100,
      },
    });
    const stack = new MemoryLifecycleStack(app, 'ContextTestStack');
    const contextTemplate = Template.fromStack(stack);
    const lambdas = contextTemplate.findResources('AWS::Lambda::Function');
    const scorerLambda = Object.values(lambdas).find((l: any) =>
      l.Properties?.Environment?.Variables?.W_RECENCY !== undefined
    );
    expect(scorerLambda).toBeDefined();
    const vars = (scorerLambda as any).Properties.Environment.Variables;
    expect(vars.W_RECENCY).toBe('0.5');
    expect(vars.W_ACCESS).toBe('0.3');
    expect(vars.W_FREQUENCY).toBe('0.2');
    expect(vars.MAX_ACCESS_BASELINE).toBe('100');
  });
});
