import * as path from "path";
import {
  Stack,
  StackProps,
  aws_dynamodb as dynamodb,
  aws_ec2 as ec2,
  aws_ecs as ecs,
  aws_iam as iam,
  aws_lambda as lambda,
  aws_logs as logs,
} from "aws-cdk-lib";
import { Construct } from "constructs";

export interface TickHeartbeatStackProps extends StackProps {
  tickHandler: lambda.IFunction;
  conversationTable: dynamodb.ITable;
}

/**
 * `chatroom-tick-heartbeat` ECS Fargate service — the heartbeat container.
 *
 * Loops every `HEARTBEAT_INTERVAL_SEC` (default 5), queries
 * `chatroom-conversations` `status-index` for `status="active"` rows, and
 * async-invokes the tick handler Lambda for each. `desiredCount=1` because
 * a second concurrent loop would double-fire ticks for every active
 * conversation (the tick handler dedupes within
 * `TICK_DEDUPE_WINDOW_MS`, but the duplicate Bedrock invocations would
 * still cost money). Single-task means a ~30s gap during container
 * restart — accepted for beta per `docs/low-level-design.md`.
 *
 * See `docs/low-level-design.md` → "Heartbeat container".
 */
export class TickHeartbeatStack extends Stack {
  public readonly cluster: ecs.Cluster;
  public readonly service: ecs.FargateService;

  constructor(scope: Construct, id: string, props: TickHeartbeatStackProps) {
    super(scope, id, props);

    const { tickHandler, conversationTable } = props;

    const vpcId = this.node.tryGetContext("vpcId") as string;
    const vpc = ec2.Vpc.fromLookup(this, "ExistingVpc", { vpcId });

    this.cluster = new ecs.Cluster(this, "TickHeartbeatCluster", {
      clusterName: "stimulize-chatroom-cluster",
      vpc,
    });

    const taskDef = new ecs.FargateTaskDefinition(this, "HeartbeatTaskDef", {
      cpu: 256,
      memoryLimitMiB: 512,
    });

    // Build the container image directly from `backend/tick_loop/`. The
    // Dockerfile in that folder pins Python 3.12-slim and installs pinned
    // requirements; no separate ECR repo to manage.
    const heartbeatImage = ecs.ContainerImage.fromAsset(
      path.join(__dirname, "..", "..", "backend", "tick_loop"),
    );

    taskDef.addContainer("heartbeat", {
      image: heartbeatImage,
      logging: ecs.LogDrivers.awsLogs({
        streamPrefix: "tick-heartbeat",
        logRetention: logs.RetentionDays.ONE_MONTH,
      }),
      environment: {
        HEARTBEAT_INTERVAL_SEC: "5",
        TICK_HANDLER_LAMBDA: tickHandler.functionName,
        CONVERSATION_TABLE: conversationTable.tableName,
        CONVERSATION_STATUS_INDEX: "status-index",
        AWS_REGION: this.region,
      },
    });

    // --------------- IAM ---------------

    // dynamodb:Query on the status-index. Table-level grant is too broad,
    // and there's no helper for index-only — use an explicit policy
    // statement targeting both the table ARN (required for some
    // DescribeTable-style sanity checks at SDK init) and the GSI ARN.
    taskDef.taskRole.addToPrincipalPolicy(
      new iam.PolicyStatement({
        actions: ["dynamodb:Query"],
        resources: [
          conversationTable.tableArn,
          `${conversationTable.tableArn}/index/status-index`,
        ],
      }),
    );

    // lambda:InvokeFunction on the tick handler.
    tickHandler.grantInvoke(taskDef.taskRole);

    this.service = new ecs.FargateService(this, "HeartbeatService", {
      cluster: this.cluster,
      taskDefinition: taskDef,
      desiredCount: 1,
      assignPublicIp: false,
    });
  }
}
