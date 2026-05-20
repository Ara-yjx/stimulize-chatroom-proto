#!/usr/bin/env node
import "source-map-support/register";
import * as cdk from "aws-cdk-lib";
import { ConversationTableStack } from "../lib/conversation-table-stack";
import { LobbyTableStack } from "../lib/lobby-table-stack";
import { SecretsStack } from "../lib/secrets-stack";
import { ChatroomApiStack } from "../lib/chatroom-api-stack";
import { TickHandlerStack } from "../lib/tick-handler-stack";
import { TickHeartbeatStack } from "../lib/tick-heartbeat-stack";

const app = new cdk.App();

const env: cdk.Environment = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: process.env.CDK_DEFAULT_REGION,
};

const conversationStack = new ConversationTableStack(app, "ConversationTableStack", { env });
const lobbyStack = new LobbyTableStack(app, "LobbyTableStack", { env });
const secretsStack = new SecretsStack(app, "SecretsStack", { env });

new ChatroomApiStack(app, "ChatroomApiStack", {
  env,
  table: conversationStack.table,
  lobbyTable: lobbyStack.table,
  jwtSecret: secretsStack.jwtSecret,
  adminToken: secretsStack.adminToken,
});

const tickHandlerStack = new TickHandlerStack(app, "TickHandlerStack", {
  env,
  conversationTable: conversationStack.table,
  lobbyTable: lobbyStack.table,
  jwtSecret: secretsStack.jwtSecret,
  adminToken: secretsStack.adminToken,
});

new TickHeartbeatStack(app, "TickHeartbeatStack", {
  env,
  tickHandler: tickHandlerStack.lambdaFunction,
  conversationTable: conversationStack.table,
});

// MockManagementStack is owned by the Stimulize-backend teammate (task 7.7);
// it's wired in their CDK app.

app.synth();
