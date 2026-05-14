#!/usr/bin/env node
import "source-map-support/register";
import * as cdk from "aws-cdk-lib";
import { ConversationTableStack } from "../lib/conversation-table-stack";
import { SecretsStack } from "../lib/secrets-stack";
import { ChatroomApiStack } from "../lib/chatroom-api-stack";

const app = new cdk.App();

const env: cdk.Environment = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: process.env.CDK_DEFAULT_REGION,
};

const tableStack = new ConversationTableStack(app, "ConversationTableStack", {
  env,
});

const secretsStack = new SecretsStack(app, "SecretsStack", { env });

new ChatroomApiStack(app, "ChatroomApiStack", {
  env,
  table: tableStack.table,
  jwtSecret: secretsStack.jwtSecret,
});

app.synth();
