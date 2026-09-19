#!/usr/bin/env node
import 'source-map-support/register';
import { App, Stack, RemovalPolicy, CfnOutput, aws_s3 as s3, aws_dynamodb as dynamodb, aws_lambda as lambda } from 'aws-cdk-lib';
import { ConversationTableStack } from '../lib/conversation-table-stack';
import { ConversationEventStack } from '../lib/conversation-event-stack';
import { LobbyTableStack } from '../lib/lobby-table-stack';
import { SecretsStack } from '../lib/secrets-stack';
import { ChatroomApiStack } from '../lib/chatroom-api-stack';
import { TickHandlerStack } from '../lib/tick-handler-stack';
import { TickHeartbeatStack } from '../lib/tick-heartbeat-stack';
import { AiConversationBatchStack } from '../lib/ai-conversation-batch-stack';

const app = new App();
const prefix = String(app.node.tryGetContext('devPrefix') || '');
if (!/^stimulize-attachment-dev-[a-z0-9-]+$/.test(prefix) || prefix.length > 40) {
  throw new Error('Use a unique stimulize-attachment-dev- prefix, at most 40 characters');
}
const sharedRds = app.node.tryGetContext('confirmSharedRds') === 'stimulusdb/postgres';
if (sharedRds && (app.node.tryGetContext('rdsHost') !== 'stimulusdb.cluster-cf6cmwe2izyn.us-east-2.rds.amazonaws.com'
  || app.node.tryGetContext('rdsDatabase') !== 'postgres' || !app.node.tryGetContext('rdsSecretArn'))) {
  throw new Error('Shared RDS requires the reviewed writer, database and secret');
}
if (!sharedRds && (app.node.tryGetContext('rdsHost') || app.node.tryGetContext('useProdRds') === 'true')) {
  throw new Error('Shared RDS requires explicit confirmSharedRds=stimulusdb/postgres');
}
const env = { account: process.env.CDK_DEFAULT_ACCOUNT, region: 'us-east-2' };
const id = prefix.split('-').map(x => x[0].toUpperCase() + x.slice(1)).join('');
const storage = new Stack(app, `${id}Files`, { env });
const bucket = new s3.Bucket(storage, 'Files', {
  bucketName: `${prefix}-files`, blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
  encryption: s3.BucketEncryption.S3_MANAGED, enforceSSL: true,
  removalPolicy: RemovalPolicy.RETAIN,
});
new CfnOutput(storage, 'AttachmentBucket', { value: bucket.bucketName });
const metadata = new ConversationTableStack(app, `${id}Metadata`, {
  env, tableName: `${prefix}-conversations`, removalPolicy: RemovalPolicy.DESTROY,
  pointInTimeRecovery: true, stream: dynamodb.StreamViewType.KEYS_ONLY,
});
const events = new ConversationEventStack(app, `${id}Events`, {
  env, metadataTable: metadata.table, eventTableName: `${prefix}-events`,
  cleanupFunctionName: `${prefix}-cleanup`, removalPolicy: RemovalPolicy.DESTROY,
});
const lobby = new LobbyTableStack(app, `${id}Lobby`, { env, tableName: `${prefix}-lobbies`, removalPolicy: RemovalPolicy.DESTROY });
const secrets = new SecretsStack(app, `${id}Secrets`, { env, secretPrefix: prefix, exportPrefix: null });
const tick = new TickHandlerStack(app, `${id}Tick`, {
  env, conversationTable: metadata.table, eventTable: events.eventTable, lobbyTable: lobby.table,
  jwtSecret: secrets.jwtSecret, adminToken: secrets.adminToken, functionName: `${prefix}-tick`, useMockRds: !sharedRds,
});
const api = new ChatroomApiStack(app, `${id}Api`, {
  env, table: metadata.table, eventTable: events.eventTable, lobbyTable: lobby.table,
  jwtSecret: secrets.jwtSecret, adminToken: secrets.adminToken, tickHandler: tick.lambdaFunction,
  useMockRds: !sharedRds, apiName: `${prefix}-api`, functionName: `${prefix}-api`,
});
const batch = new AiConversationBatchStack(app, `${id}Batch`, {
  env, conversationTable: metadata.table, eventTable: events.eventTable, resourcePrefix: prefix,
  useMockRds: !sharedRds, removalPolicy: RemovalPolicy.DESTROY,
});
for (const fn of [api.lambdaFunction, tick.lambdaFunction, batch.provisioner, batch.worker, batch.exporter] as lambda.Function[]) {
  fn.addEnvironment('PROMPT_ATTACHMENTS_ENABLED', 'true');
  fn.addEnvironment('PROMPT_ATTACHMENT_BUCKET', bucket.bucketName);
  fn.addEnvironment('PROMPT_ATTACHMENT_MODELS', [
    'global.anthropic.claude-sonnet-4-6', 'global.anthropic.claude-sonnet-4-5-20250929-v1:0',
    'global.anthropic.claude-haiku-4-5-20251001-v1:0', 'global.anthropic.claude-opus-4-7',
    'global.anthropic.claude-opus-4-6-v1',
  ].join(','));
  bucket.grantRead(fn, 'assets/*');
}
if (app.node.tryGetContext('enableDevHeartbeat') === 'true') {
  new TickHeartbeatStack(app, `${id}Heartbeat`, { env, tickHandler: tick.lambdaFunction,
    conversationTable: metadata.table, functionName: `${prefix}-heartbeat`, intervalSeconds: 8 });
}
new CfnOutput(storage, 'TestPrefix', { value: prefix });
app.synth();
