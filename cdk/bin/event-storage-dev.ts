#!/usr/bin/env node
import "source-map-support/register";
import {
  App,
  Environment,
  RemovalPolicy,
  aws_dynamodb as dynamodb,
} from "aws-cdk-lib";
import { ChatroomApiStack } from "../lib/chatroom-api-stack";
import { ConversationEventStack } from "../lib/conversation-event-stack";
import { ConversationTableStack } from "../lib/conversation-table-stack";
import { LobbyTableStack } from "../lib/lobby-table-stack";
import { SecretsStack } from "../lib/secrets-stack";
import { TickHandlerStack } from "../lib/tick-handler-stack";
import { TickHeartbeatStack } from "../lib/tick-heartbeat-stack";

const app = new App();
const prefix = String(app.node.tryGetContext("devPrefix") || "");
const useProdRds = app.node.tryGetContext("useProdRds") === "true";
if (!/^stimulize-chatroom-event-dev-[a-z0-9-]+$/.test(prefix)) {
  throw new Error(
    "Pass -c devPrefix=stimulize-chatroom-event-dev-<unique-name>; " +
    "this entry point refuses production-style names.",
  );
}
if (prefix.length > 45) {
  throw new Error("devPrefix must be 45 characters or fewer");
}
if (useProdRds) {
  const requiredContexts = ["rdsHost", "rdsDatabase", "rdsSecretArn"];
  const missing = requiredContexts.filter((key) => !app.node.tryGetContext(key));
  if (missing.length) {
    throw new Error(
      `useProdRds=true requires CDK context: ${missing.join(", ")}`,
    );
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
const lobby = new LobbyTableStack(app, `${stackPrefix}Lobby`, {
  env,
  tableName: `${prefix}-lobbies`,
  removalPolicy: RemovalPolicy.DESTROY,
});
const secrets = new SecretsStack(app, `${stackPrefix}Secrets`, {
  env,
  secretPrefix: prefix,
  exportPrefix: null,
});
const events = new ConversationEventStack(app, `${stackPrefix}Events`, {
  env,
  metadataTable: conversation.table,
  eventTableName: `${prefix}-events`,
  cleanupFunctionName: `${prefix}-cleanup`,
  removalPolicy: RemovalPolicy.DESTROY,
});
const tick = new TickHandlerStack(app, `${stackPrefix}Tick`, {
  env,
  conversationTable: conversation.table,
  eventTable: events.eventTable,
  lobbyTable: lobby.table,
  jwtSecret: secrets.jwtSecret,
  adminToken: secrets.adminToken,
  functionName: `${prefix}-tick`,
  useMockRds: !useProdRds,
});
new ChatroomApiStack(app, `${stackPrefix}Api`, {
  env,
  table: conversation.table,
  eventTable: events.eventTable,
  lobbyTable: lobby.table,
  jwtSecret: secrets.jwtSecret,
  adminToken: secrets.adminToken,
  tickHandler: tick.lambdaFunction,
  useMockRds: !useProdRds,
  apiName: `${prefix}-api`,
  functionName: `${prefix}-api`,
});

if (app.node.tryGetContext("enableDevHeartbeat") === "true") {
  new TickHeartbeatStack(app, `${stackPrefix}Heartbeat`, {
    env,
    tickHandler: tick.lambdaFunction,
    conversationTable: conversation.table,
    functionName: `${prefix}-heartbeat`,
    intervalSeconds: 8,
  });
}

app.synth();
