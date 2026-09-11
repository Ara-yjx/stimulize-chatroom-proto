#!/usr/bin/env node
import "source-map-support/register";
import {
  App,
  Environment,
  RemovalPolicy,
  aws_dynamodb as dynamodb,
} from "aws-cdk-lib";
import { AiConversationBatchStack } from "../lib/ai-conversation-batch-stack";
import { ConversationEventStack } from "../lib/conversation-event-stack";
import { ConversationTableStack } from "../lib/conversation-table-stack";

const app = new App();
const prefix = String(app.node.tryGetContext("devPrefix") || "");
const useProdRds = app.node.tryGetContext("useProdRds") === "true";
if (!/^stimulize-chatroom-ai-batch-dev-[a-z0-9-]+$/.test(prefix)) {
  throw new Error(
    "Pass -c devPrefix=stimulize-chatroom-ai-batch-dev-<unique-name>; " +
    "this entry point refuses production-style names.",
  );
}
if (prefix.length > 42) {
  throw new Error("devPrefix must be 42 characters or fewer");
}
if (useProdRds) {
  const required = ["rdsHost", "rdsDatabase", "rdsSecretArn"];
  const missing = required.filter((key) => !app.node.tryGetContext(key));
  if (missing.length) {
    throw new Error(`useProdRds=true requires CDK context: ${missing.join(", ")}`);
  }
}

const env: Environment = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: process.env.CDK_DEFAULT_REGION,
};
const stackPrefix = prefix
  .split("-")
  .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
  .join("");

const conversation = new ConversationTableStack(app, `${stackPrefix}Metadata`, {
  env,
  tableName: `${prefix}-conversations`,
  removalPolicy: RemovalPolicy.DESTROY,
  pointInTimeRecovery: true,
  stream: dynamodb.StreamViewType.KEYS_ONLY,
});
const events = new ConversationEventStack(app, `${stackPrefix}Events`, {
  env,
  metadataTable: conversation.table,
  eventTableName: `${prefix}-events`,
  cleanupFunctionName: `${prefix}-cleanup`,
  removalPolicy: RemovalPolicy.DESTROY,
});
new AiConversationBatchStack(app, `${stackPrefix}Batch`, {
  env,
  conversationTable: conversation.table,
  eventTable: events.eventTable,
  resourcePrefix: prefix,
  useMockRds: !useProdRds,
  removalPolicy: RemovalPolicy.DESTROY,
});

app.synth();
