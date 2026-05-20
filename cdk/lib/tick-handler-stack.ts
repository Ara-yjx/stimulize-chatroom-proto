import * as path from "path";
import {
  Duration,
  Stack,
  StackProps,
  aws_dynamodb as dynamodb,
  aws_ec2 as ec2,
  aws_iam as iam,
  aws_lambda as lambda,
  aws_secretsmanager as secretsmanager,
} from "aws-cdk-lib";
import { Construct } from "constructs";

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
 * Deployed in the same VPC as the chatroom-api Lambda with an equivalent
 * IAM surface, minus API Gateway integration (the heartbeat invokes it via
 * `lambda:InvokeFunction` with `InvocationType=Event`). RDS access is not
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

    const vpcId = this.node.tryGetContext("vpcId") as string;
    const subnetIds = (this.node.tryGetContext("subnetIds") as string).split(",");

    const vpc = ec2.Vpc.fromLookup(this, "ExistingVpc", { vpcId });
    const subnets = subnetIds.map((subnetId, i) =>
      ec2.Subnet.fromSubnetId(this, `Subnet${i}`, subnetId.trim()),
    );

    // Dedicated SG. Allows outbound to AWS services (DynamoDB, Bedrock,
    // Secrets Manager) via NAT Gateway or VPC endpoints provisioned by the
    // chatroom-api stack. No inbound rules required — async-invoke only.
    const lambdaSg = new ec2.SecurityGroup(this, "TickLambdaSg", {
      vpc,
      description: "Security group for chatroom tick handler",
      allowAllOutbound: true,
    });

    this.lambdaFunction = new lambda.Function(this, "TickHandlerFunction", {
      functionName: "chatroom-tick-handler",
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: "chatroom_api.tick_handler.handle_tick",
      code: lambda.Code.fromAsset(path.join(__dirname, "..", "..", "backend")),
      memorySize: 512,
      // 60s gives Bedrock retries (3 attempts, exponential backoff) plus the
      // DDB writes plenty of headroom. Heartbeat fires every 5s; the dedupe
      // guard prevents pile-up if a single tick runs long.
      timeout: Duration.seconds(60),
      vpc,
      vpcSubnets: { subnets },
      securityGroups: [lambdaSg],
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
    // but kept symmetric with chatroom-api so shared helpers (`config.py`)
    // initialize cleanly without raising.
    jwtSecret.grantRead(this.lambdaFunction);
    adminToken.grantRead(this.lambdaFunction);
  }
}
