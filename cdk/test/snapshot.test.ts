/**
 * CDK template snapshot tests for the BACKEND stacks (task 7.8).
 *
 * Asserts the structural shape we rely on at runtime:
 *   - DynamoDB GSIs (`status-index` on the conversation table; both GSIs on the
 *     lobby table).
 *   - Secrets Manager secret names.
 *   - Tick handler Lambda env vars and IAM (Bedrock invoke).
 *   - Tick heartbeat scheduler Lambda sizing/schedule and IAM
 *     (DynamoDB Query on the GSI + Lambda invoke).
 *
 * Out of scope for this file:
 *   - mock-management stack and its ALB listener — owned by another teammate
 *     (see tasks.md task 7.7, marked `[~]`).
 *   - chatroom-api stack — requires several context values (RDS proxy, ACM,
 *     custom domain) plus a real VPC lookup; not part of the acceptance
 *     criteria for 7.8 ("conversation-table, lobby-table, secrets, and (if
 *     possible) tick-handler + tick-heartbeat").
 *
 * VPC lookup note: older backend stacks called
 * `ec2.Vpc.fromLookup`, which without a pre-resolved cache hits AWS via the
 * CDK context provider and breaks offline tests. We pre-seed the
 * `vpc-provider` cache key with a synthetic VPC so synth never reaches AWS.
 * Approach (a) from the task description.
 */

import * as cdk from "aws-cdk-lib";
import { Match, Template } from "aws-cdk-lib/assertions";
import { ConversationTableStack } from "../lib/conversation-table-stack";
import { LobbyTableStack } from "../lib/lobby-table-stack";
import { SecretsStack } from "../lib/secrets-stack";
import { ChatroomApiStack } from "../lib/chatroom-api-stack";
import { TickHandlerStack } from "../lib/tick-handler-stack";
import { TickHeartbeatStack } from "../lib/tick-heartbeat-stack";

const TEST_ACCOUNT = "123456789012";
const TEST_REGION = "us-east-1";
const TEST_ENV: cdk.Environment = { account: TEST_ACCOUNT, region: TEST_REGION };

/**
 * Minimal app with the context every backend stack reads: vpcId, subnetIds,
 * and a pre-resolved VPC-provider cache entry so `ec2.Vpc.fromLookup` doesn't
 * call AWS during synth.
 */
function makeApp(): cdk.App {
  return new cdk.App({
    context: {
      vpcId: "vpc-test",
      subnetIds: "subnet-1,subnet-2",
      // Pre-resolved VPC lookup cache. Key format:
      //   vpc-provider:account=<acct>:filter.vpc-id=<vpcId>:region=<region>:returnAsymmetricSubnets=true
      // CDK reads this synchronously instead of calling EC2.
      [`vpc-provider:account=${TEST_ACCOUNT}:filter.vpc-id=vpc-test:region=${TEST_REGION}:returnAsymmetricSubnets=true`]: {
        vpcId: "vpc-test",
        vpcCidrBlock: "10.0.0.0/16",
        availabilityZones: [],
        subnetGroups: [
          {
            name: "Public",
            type: "Public",
            subnets: [
              {
                subnetId: "subnet-1",
                cidr: "10.0.1.0/24",
                availabilityZone: `${TEST_REGION}a`,
                routeTableId: "rtb-1",
              },
              {
                subnetId: "subnet-2",
                cidr: "10.0.2.0/24",
                availabilityZone: `${TEST_REGION}b`,
                routeTableId: "rtb-2",
              },
            ],
          },
        ],
      },
      rdsSecurityGroupId: "sg-test",
      rdsHost: "stimulusdb-instance-1.example.rds.amazonaws.com",
      rdsPort: "5432",
      rdsDatabase: "stimulize",
      rdsSecretArn: "arn:aws:secretsmanager:us-east-1:123456789012:secret:rds-secret",
      domainName: "",
      enableCustomDomain: "false",
      useVpcEndpoints: "false",
    },
  });
}

// -----------------------------------------------------------------------------
// ConversationTableStack
// -----------------------------------------------------------------------------

describe("ConversationTableStack", () => {
  it("creates conversation table with status-index GSI", () => {
    const app = makeApp();
    const stack = new ConversationTableStack(app, "ConvTable", { env: TEST_ENV });
    const t = Template.fromStack(stack);

    t.resourceCountIs("AWS::DynamoDB::Table", 1);
    t.hasResourceProperties("AWS::DynamoDB::Table", {
      TableName: "chatroom-conversations",
      KeySchema: [{ AttributeName: "conversation_id", KeyType: "HASH" }],
      BillingMode: "PAY_PER_REQUEST",
      TimeToLiveSpecification: { AttributeName: "ttl", Enabled: true },
      GlobalSecondaryIndexes: Match.arrayWith([
        Match.objectLike({
          IndexName: "status-index",
          KeySchema: Match.arrayWith([
            { AttributeName: "status", KeyType: "HASH" },
          ]),
          Projection: { ProjectionType: "KEYS_ONLY" },
        }),
      ]),
    });
  });
});

// -----------------------------------------------------------------------------
// LobbyTableStack
// -----------------------------------------------------------------------------

describe("LobbyTableStack", () => {
  it("creates lobby table with both GSIs and TTL", () => {
    const app = makeApp();
    const stack = new LobbyTableStack(app, "LobbyTable", { env: TEST_ENV });
    const t = Template.fromStack(stack);

    t.resourceCountIs("AWS::DynamoDB::Table", 1);
    t.hasResourceProperties("AWS::DynamoDB::Table", {
      TableName: "chatroom-lobbies",
      KeySchema: [{ AttributeName: "lobby_id", KeyType: "HASH" }],
      BillingMode: "PAY_PER_REQUEST",
      TimeToLiveSpecification: { AttributeName: "ttl", Enabled: true },
      GlobalSecondaryIndexes: Match.arrayWith([
        Match.objectLike({
          IndexName: "chatroom_id-status-index",
          KeySchema: Match.arrayWith([
            { AttributeName: "chatroom_id", KeyType: "HASH" },
            { AttributeName: "status", KeyType: "RANGE" },
          ]),
        }),
        Match.objectLike({
          IndexName: "conversation_id-index",
          KeySchema: Match.arrayWith([
            { AttributeName: "conversation_id", KeyType: "HASH" },
          ]),
        }),
      ]),
    });
  });
});

// -----------------------------------------------------------------------------
// SecretsStack
// -----------------------------------------------------------------------------

describe("SecretsStack", () => {
  it("creates jwt and admin token secrets", () => {
    const app = makeApp();
    const stack = new SecretsStack(app, "Secrets", { env: TEST_ENV });
    const t = Template.fromStack(stack);

    t.resourceCountIs("AWS::SecretsManager::Secret", 2);
    t.hasResourceProperties("AWS::SecretsManager::Secret", {
      Name: "stimulize-chatroom/jwt-secret",
    });
    t.hasResourceProperties("AWS::SecretsManager::Secret", {
      Name: "stimulize-chatroom/admin-token",
    });
  });
});

// -----------------------------------------------------------------------------
// TickHandlerStack — depends on conversation + lobby tables and secrets.
// We construct lightweight upstream stacks in the same app to wire props.
// -----------------------------------------------------------------------------

describe("TickHandlerStack", () => {
  it("creates tick handler Lambda with required env vars and Bedrock IAM", () => {
    const app = makeApp();
    const conv = new ConversationTableStack(app, "ConvTable", { env: TEST_ENV });
    const lobby = new LobbyTableStack(app, "LobbyTable", { env: TEST_ENV });
    const secrets = new SecretsStack(app, "Secrets", { env: TEST_ENV });
    const stack = new TickHandlerStack(app, "TickHandler", {
      env: TEST_ENV,
      conversationTable: conv.table,
      lobbyTable: lobby.table,
      jwtSecret: secrets.jwtSecret,
      adminToken: secrets.adminToken,
    });
    const t = Template.fromStack(stack);

    t.resourceCountIs("AWS::Lambda::Function", 1);
    t.hasResourceProperties("AWS::Lambda::Function", {
      FunctionName: "chatroom-tick-handler",
      Runtime: "python3.12",
      Handler: "chatroom_api.tick_handler.handle_tick",
      Environment: {
        Variables: Match.objectLike({
          // Tables — values are CFN Refs that resolve cross-stack, just check keys.
          DYNAMODB_TABLE: Match.anyValue(),
          LOBBY_TABLE: Match.anyValue(),
          JWT_SECRET_ARN: Match.anyValue(),
          ADMIN_TOKEN_SECRET_ARN: Match.anyValue(),
          BEDROCK_REGION: "us-east-2",
        }),
      },
    });

    // IAM role policy must include bedrock:InvokeModel.
    t.hasResourceProperties("AWS::IAM::Policy", {
      PolicyDocument: {
        Statement: Match.arrayWith([
          Match.objectLike({
            Action: "bedrock:InvokeModel",
            Effect: "Allow",
          }),
        ]),
      },
    });
  });
});

// -----------------------------------------------------------------------------
// TickHeartbeatStack
// -----------------------------------------------------------------------------

describe("TickHeartbeatStack", () => {
  it("creates a scheduled heartbeat lambda with reserved concurrency 1", () => {
    const app = makeApp();
    const conv = new ConversationTableStack(app, "ConvTable", { env: TEST_ENV });
    const lobby = new LobbyTableStack(app, "LobbyTable", { env: TEST_ENV });
    const secrets = new SecretsStack(app, "Secrets", { env: TEST_ENV });
    const tickHandler = new TickHandlerStack(app, "TickHandler", {
      env: TEST_ENV,
      conversationTable: conv.table,
      lobbyTable: lobby.table,
      jwtSecret: secrets.jwtSecret,
      adminToken: secrets.adminToken,
    });
    const stack = new TickHeartbeatStack(app, "TickHeartbeat", {
      env: TEST_ENV,
      tickHandler: tickHandler.lambdaFunction,
      conversationTable: conv.table,
    });
    const t = Template.fromStack(stack);

    t.hasResourceProperties("AWS::Lambda::Function", {
      FunctionName: "chatroom-tick-heartbeat",
      Runtime: "python3.12",
      Handler: "tick_loop.heartbeat_lambda.handler",
      Timeout: 900,
      ReservedConcurrentExecutions: 1,
      Environment: {
        Variables: Match.objectLike({
          HEARTBEAT_INTERVAL_SEC: "5",
          HEARTBEAT_WINDOW_SEC: "840",
          TICK_HANDLER_LAMBDA: Match.anyValue(),
          CONVERSATION_TABLE: Match.anyValue(),
          CONVERSATION_STATUS_INDEX: "status-index",
        }),
      },
    });

    t.hasResourceProperties("AWS::Events::Rule", {
      ScheduleExpression: "rate(15 minutes)",
    });

    // IAM: dynamodb:Query on status-index AND lambda:InvokeFunction on tick handler.
    t.hasResourceProperties("AWS::IAM::Policy", {
      PolicyDocument: {
        Statement: Match.arrayWith([
          Match.objectLike({
            Action: "dynamodb:Query",
            Effect: "Allow",
            Resource: Match.anyValue(),
          }),
          Match.objectLike({
            Action: "lambda:InvokeFunction",
            Effect: "Allow",
          }),
        ]),
      },
    });
  });
});

// -----------------------------------------------------------------------------
// ChatroomApiStack
// -----------------------------------------------------------------------------

describe("ChatroomApiStack", () => {
  it("creates API lambda with direct-RDS env vars and no custom domain by default", () => {
    const app = makeApp();
    const conv = new ConversationTableStack(app, "ConvTable", { env: TEST_ENV });
    const lobby = new LobbyTableStack(app, "LobbyTable", { env: TEST_ENV });
    const secrets = new SecretsStack(app, "Secrets", { env: TEST_ENV });
    const stack = new ChatroomApiStack(app, "ChatroomApi", {
      env: TEST_ENV,
      table: conv.table,
      lobbyTable: lobby.table,
      jwtSecret: secrets.jwtSecret,
      adminToken: secrets.adminToken,
    });
    const t = Template.fromStack(stack);

    t.hasResourceProperties("AWS::Lambda::Function", {
      Runtime: "python3.12",
      Handler: "chatroom_api.handler.lambda_handler",
      Environment: {
        Variables: Match.objectLike({
          DYNAMODB_TABLE: Match.anyValue(),
          LOBBY_TABLE: Match.anyValue(),
          JWT_SECRET_ARN: Match.anyValue(),
          ADMIN_TOKEN_SECRET_ARN: Match.anyValue(),
          RDS_HOST: "stimulusdb-instance-1.example.rds.amazonaws.com",
          RDS_PORT: "5432",
          RDS_DATABASE: "stimulize",
          RDS_SECRET_ARN: "arn:aws:secretsmanager:us-east-1:123456789012:secret:rds-secret",
          USE_MOCK_DYNAMO: "false",
          USE_MOCK_RDS: "false",
          USE_MOCK_LOBBY: "false",
        }),
      },
    });

    t.resourceCountIs("AWS::ApiGateway::DomainName", 0);
    t.resourceCountIs("AWS::RDS::DBProxy", 0);
  });
});
