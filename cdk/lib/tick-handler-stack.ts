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

export interface TickHandlerStackProps extends StackProps {
  conversationTable: dynamodb.ITable;
  lobbyTable: dynamodb.ITable;
  jwtSecret: secretsmanager.ISecret;
  adminToken: secretsmanager.ISecret;
}

/**
 * `chatroom-tick-handler` Lambda — async-invoke target for the heartbeat
 * container. Runs the 5-step tick procedure (idempotency guard,
 * max-duration check, gate, Bedrock with the `speak` tool, append events)
 * for one conversation.
 *
 * Deployed outside the VPC for beta so it can reach Bedrock, DynamoDB, and
 * Secrets Manager without NAT/VPC-endpoint plumbing. RDS access is not
 * required at runtime — the chatroom_setting needed for ticking is
 * snapshotted onto the conversation row at lobby-close time.
 *
 * See `docs/low-level-design.md` → "Async AI Conversation Flow".
 */
export class TickHandlerStack extends Stack {
  public readonly lambdaFunction: lambda.Function;

  constructor(scope: Construct, id: string, props: TickHandlerStackProps) {
    super(scope, id, props);

    const { conversationTable, lobbyTable, jwtSecret, adminToken } = props;

    this.lambdaFunction = new lambda.Function(this, "TickHandlerFunction", {
      functionName: "chatroom-tick-handler",
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: "chatroom_api.tick_handler.handle_tick",
      code: backendPythonCode(),
      memorySize: 512,
      // 60s gives Bedrock retries (3 attempts, exponential backoff) plus the
      // DDB writes plenty of headroom. Heartbeat fires every 5s; the dedupe
      // guard prevents pile-up if a single tick runs long.
      timeout: Duration.seconds(60),
      environment: {
        DYNAMODB_TABLE: conversationTable.tableName,
        LOBBY_TABLE: lobbyTable.tableName,
        JWT_SECRET_ARN: jwtSecret.secretArn,
        ADMIN_TOKEN_SECRET_ARN: adminToken.secretArn,
        BEDROCK_REGION: "us-east-2",
        USE_MOCK_DYNAMO: "false",
        USE_MOCK_RDS: "false",
        USE_MOCK_LOBBY: "false",
        TICK_HANDLER_LOCAL: "false",
      },
    });

    // --------------- IAM ---------------

    // DynamoDB R/W on the conversation table (idempotency guard, status
    // flip, append events, last_speak_at_by_session updates).
    conversationTable.grantReadWriteData(this.lambdaFunction);

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
  }
}
