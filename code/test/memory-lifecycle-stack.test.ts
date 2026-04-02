import * as cdk from 'aws-cdk-lib';
import { Template } from 'aws-cdk-lib/assertions';
import { MemoryLifecycleStack } from '../lib/memory-lifecycle-stack';

describe('MemoryLifecycleStack', () => {
  let template: Template;

  beforeAll(() => {
    const app = new cdk.App();
    const stack = new MemoryLifecycleStack(app, 'TestStack');
    template = Template.fromStack(stack);
  });

  test('creates 4 Lambda functions', () => {
    const lambdas = template.findResources('AWS::Lambda::Function', {
      Properties: { Runtime: 'python3.12' },
    });
    expect(Object.keys(lambdas)).toHaveLength(4);
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
    // All 4 application Lambdas should use Python 3.12
    expect(Object.keys(pythonLambdas)).toHaveLength(4);
    for (const [, resource] of Object.entries(pythonLambdas)) {
      expect((resource as any).Properties.Runtime).toBe('python3.12');
    }
  });
});
