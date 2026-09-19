#!/usr/bin/env node
import "source-map-support/register";
import * as cdk from "aws-cdk-lib";
import { ConversationTableStack } from "../lib/conversation-table-stack";
import { LobbyTableStack } from "../lib/lobby-table-stack";
import { SecretsStack } from "../lib/secrets-stack";
import { ChatroomApiStack } from "../lib/chatroom-api-stack";
import { TickHandlerStack } from "../lib/tick-handler-stack";
import { TickHeartbeatStack } from "../lib/tick-heartbeat-stack";
import { ConversationEventStack } from "../lib/conversation-event-stack";
import { parseChatroomServiceMode } from "../lib/chatroom-service-mode";
import { AiConversationBatchStack } from "../lib/ai-conversation-batch-stack";

const app = new cdk.App();

const env: cdk.Environment = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: process.env.CDK_DEFAULT_REGION,
};
const chatroomServiceMode = parseChatroomServiceMode(
  app.node.tryGetContext("chatroomServiceMode"),
);
const eventTableName = "chatroom-conversation-events";

const conversationStack = new ConversationTableStack(app, "ConversationTableStack", {
  env,
  removalPolicy: cdk.RemovalPolicy.RETAIN,
  pointInTimeRecovery: true,
  stream: cdk.aws_dynamodb.StreamViewType.KEYS_ONLY,
  deletionProtection: true,
});
const lobbyStack = new LobbyTableStack(app, "LobbyTableStack", { env });
const secretsStack = new SecretsStack(app, "SecretsStack", { env });
new ConversationEventStack(app, "ConversationEventStack", {
  env,
  metadataTable: conversationStack.table,
  eventTableName,
  cleanupFunctionName: "chatroom-event-cleanup",
  removalPolicy: cdk.RemovalPolicy.RETAIN,
  deletionProtection: true,
});

const tickHandlerStack = new TickHandlerStack(app, "TickHandlerStack", {
  env,
  conversationTable: conversationStack.table,
  lobbyTable: lobbyStack.table,
  jwtSecret: secretsStack.jwtSecret,
  adminToken: secretsStack.adminToken,
  eventTableName,
  serviceMode: chatroomServiceMode,
});

const apiStack = new ChatroomApiStack(app, "ChatroomApiStack", {
  env,
  table: conversationStack.table,
  lobbyTable: lobbyStack.table,
  jwtSecret: secretsStack.jwtSecret,
  adminToken: secretsStack.adminToken,
  tickHandler: tickHandlerStack.lambdaFunction,
  eventTableName,
  serviceMode: chatroomServiceMode,
});

// Opt-in release wiring. Dev entry points remain independent and disposable.
const attachmentEnabled = app.node.tryGetContext('enablePromptAttachments') === 'true';
const batchEnabled = app.node.tryGetContext('enableAiBatch') === 'true';
const attachmentFunctions = [apiStack.lambdaFunction, tickHandlerStack.lambdaFunction];
if (batchEnabled) {
  const metadata = new ConversationTableStack(app, 'BatchConversationTableStack', {
    env, tableName: 'chatroom-batch-conversations', removalPolicy: cdk.RemovalPolicy.RETAIN,
    pointInTimeRecovery: true, deletionProtection: true,
    stream: cdk.aws_dynamodb.StreamViewType.KEYS_ONLY,
  });
  const events = new ConversationEventStack(app, 'BatchConversationEventStack', {
    env, metadataTable: metadata.table, eventTableName: 'chatroom-batch-events',
    cleanupFunctionName: 'chatroom-batch-event-cleanup', removalPolicy: cdk.RemovalPolicy.RETAIN,
    deletionProtection: true,
  });
  const batch = new AiConversationBatchStack(app, 'AiConversationBatchStack', {
    env, conversationTable: metadata.table, eventTable: events.eventTable,
    resourcePrefix: 'stimulize-chatroom-batch', removalPolicy: cdk.RemovalPolicy.RETAIN,
    deletionProtection: true,
  });
  attachmentFunctions.push(batch.provisioner, batch.worker, batch.exporter);
}
if (attachmentEnabled) {
  const assets = new cdk.Stack(app, 'ChatroomAssetsStack', { env });
  const bucket = new cdk.aws_s3.Bucket(assets, 'Assets', {
    blockPublicAccess: cdk.aws_s3.BlockPublicAccess.BLOCK_ALL,
    encryption: cdk.aws_s3.BucketEncryption.S3_MANAGED, enforceSSL: true,
    removalPolicy: cdk.RemovalPolicy.RETAIN,
  });
  new cdk.CfnOutput(assets, 'AttachmentBucket', { value: bucket.bucketName });
  for (const fn of attachmentFunctions) {
    fn.addEnvironment('PROMPT_ATTACHMENTS_ENABLED', 'true');
    fn.addEnvironment('PROMPT_ATTACHMENT_BUCKET', bucket.bucketName);
    fn.addEnvironment('PROMPT_ATTACHMENT_MODELS', [
      'global.anthropic.claude-sonnet-4-6', 'global.anthropic.claude-sonnet-4-5-20250929-v1:0',
      'global.anthropic.claude-haiku-4-5-20251001-v1:0', 'global.anthropic.claude-opus-4-7',
      'global.anthropic.claude-opus-4-6-v1',
    ].join(','));
    bucket.grantRead(fn, 'assets/*');
  }
}

new TickHeartbeatStack(app, "TickHeartbeatStack", {
  env,
  tickHandler: tickHandlerStack.lambdaFunction,
  conversationTable: conversationStack.table,
});

// MockManagementStack is owned by the Stimulize-backend teammate (task 7.7);
// it's wired in their CDK app.

app.synth();
