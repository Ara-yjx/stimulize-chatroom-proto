import {
  Duration,
  Stack,
  StackProps,
  aws_dynamodb as dynamodb,
  aws_iam as iam,
  aws_lambda as lambda,
  aws_secretsmanager as secretsmanager,
} from "aws-cdk-lib";
import { Construct } from "constructs";
import { backendPythonCode } from "./backend-code";
import { ChatroomServiceMode } from "./chatroom-service-mode";

export interface TickHandlerStackProps extends StackProps {
  conversationTable: dynamodb.ITable;
  lobbyTable: dynamodb.ITable;
  jwtSecret: secretsmanager.ISecret;
  adminToken: secretsmanager.ISecret;
  eventTable?: dynamodb.ITable;
  eventTableName?: string;
  serviceMode?: ChatroomServiceMode;
  functionName?: string;
  useMockRds?: boolean;
}

/**
 * `chatroom-tick-handler` Lambda — async-invoke target for the heartbeat
 * container. Runs the 5-step tick procedure (idempotency guard,
 * max-duration check, gate, Bedrock with the `speak` tool, append events)
 * for one conversation.
 *
 * Deployed outside the VPC for beta so it can reach Bedrock, DynamoDB, and
 * Secrets Manager without NAT/VPC-endpoint plumbing. It also reaches the
 * shared Postgres cluster directly so each model invocation can write a usage
 * row for later aggregation in the Stimulize backend.
 *
 * See `docs/low-level-design.md` → "Async AI Conversation Flow".
 */
export class TickHandlerStack extends Stack {
  public readonly lambdaFunction: lambda.Function;

  constructor(scope: Construct, id: string, props: TickHandlerStackProps) {
    super(scope, id, props);

    const { conversationTable, lobbyTable, jwtSecret, adminToken } = props;
    const useMockRds = props.useMockRds ?? false;
    const eventTable = props.eventTable ?? (
      props.eventTableName
        ? dynamodb.Table.fromTableName(
            this,
            "RuntimeEventTable",
            props.eventTableName,
          )
        : undefined
    );
    if (!eventTable) {
      throw new Error(
        "TickHandlerStack requires eventTable or eventTableName; " +
        "the post-cutover runtime cannot use legacy event storage.",
      );
    }
    const rdsHost = this.node.tryGetContext("rdsHost") as string;
    const rdsPort = this.node.tryGetContext("rdsPort") as string || "5432";
    const rdsDatabase = this.node.tryGetContext("rdsDatabase") as string || "postgres";
    const rdsSecretArn = this.node.tryGetContext("rdsSecretArn") as string;
    const rdsSecret = useMockRds
      ? undefined
      : secretsmanager.Secret.fromSecretCompleteArn(
          this,
          "TickHandlerRdsSecret",
          rdsSecretArn,
        );

    this.lambdaFunction = new lambda.Function(this, "TickHandlerFunction", {
      functionName: props.functionName ?? "chatroom-tick-handler",
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: "chatroom_api.tick_handler.handle_tick",
      code: backendPythonCode(),
      memorySize: 512,
      // Includes Bedrock retries plus up to five sequential 2-8s typing
      // delays. The conversation-level tick lease prevents overlap.
      timeout: Duration.seconds(120),
      environment: {
        DYNAMODB_TABLE: conversationTable.tableName,
        DYNAMODB_EVENT_TABLE: eventTable.tableName,
        EVENT_STORAGE_ENABLED: "true",
        CHATROOM_SERVICE_MODE: props.serviceMode ?? "normal",
        LOBBY_TABLE: lobbyTable.tableName,
        JWT_SECRET_ARN: jwtSecret.secretArn,
        ADMIN_TOKEN_SECRET_ARN: adminToken.secretArn,
        ...(useMockRds ? {} : {
          RDS_HOST: rdsHost,
          RDS_PORT: rdsPort,
          RDS_DATABASE: rdsDatabase,
          RDS_SECRET_ARN: rdsSecret!.secretArn,
        }),
        BEDROCK_REGION: "us-east-2",
        USE_MOCK_DYNAMO: "false",
        USE_MOCK_RDS: String(useMockRds),
        USE_MOCK_LOBBY: "false",
        TICK_HANDLER_LOCAL: "false",
      },
    });

    // --------------- IAM ---------------

    // DynamoDB R/W on the conversation table (idempotency guard, status
    // flip, append events, last_speak_at_by_session updates).
    conversationTable.grantReadWriteData(this.lambdaFunction);
    eventTable.grantReadWriteData(this.lambdaFunction);

    // The handler doesn't write to lobby rows during tick processing, but
    // grant read so debugging utilities and shared helpers stay working.
    // Cheap; tightenable in prod.
    lobbyTable.grantReadData(this.lambdaFunction);

    // Bedrock InvokeModel for Converse API.
    this.lambdaFunction.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["bedrock:InvokeModel"],
        resources: ["*"],
      }),
    );

    // Secrets Manager — JWT not strictly needed by the tick handler today,
    // but kept symmetric with chatroom-api so shared helpers initialize cleanly.
    jwtSecret.grantRead(this.lambdaFunction);
    adminToken.grantRead(this.lambdaFunction);
    rdsSecret?.grantRead(this.lambdaFunction);
  }
}
