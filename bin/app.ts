#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { MemoryLifecycleStack } from '../lib/memory-lifecycle-stack';

const app = new cdk.App();

try {
  new MemoryLifecycleStack(app, 'MemoryLifecycleStack', {
    description: 'Agent Memory Lifecycle — scoring, consolidation, pruning, and GDPR deletion',
    env: {
      account: process.env.CDK_DEFAULT_ACCOUNT,
      region: process.env.CDK_DEFAULT_REGION,
    },
  });
} catch (error) {
  const message = error instanceof Error ? error.message : String(error);
  console.error(
    `Failed to instantiate MemoryLifecycleStack: ${message}\n` +
    'Ensure the following prerequisites are met:\n' +
    '  - AWS CDK CLI is installed (npm install -g aws-cdk)\n' +
    '  - AWS credentials are configured (aws configure)\n' +
    '  - The target AWS account has been bootstrapped (npx cdk bootstrap)\n' +
    '  - Required dependencies are installed (npm install)',
  );
  process.exit(1);
}
