import {
  Duration,
  Stack,
  StackProps,
  aws_dynamodb as dynamodb,
  aws_events as events,
  aws_events_targets as targets,
  aws_iam as iam,
  aws_lambda as lambda,
} from "aws-cdk-lib";
import { Construct } from "constructs";
import { backendPythonCode } from "./backend-code";

export interface TickHeartbeatStackProps extends StackProps {
  tickHandler: lambda.IFunction;
  conversationTable: dynamodb.ITable;
}

/**
 * Scheduled heartbeat Lambda for beta.
 *
 * EventBridge starts one invocation every 15 minutes. Each invocation loops
 * every 5 seconds for up to 14 minutes, querying the active-conversation GSI
 * and async-invoking the tick handler. Reserved concurrency is 1 so overlapping
 * schedules cannot create duplicate heartbeat loops.
 */
export class TickHeartbeatStack extends Stack {
  public readonly lambdaFunction: lambda.Function;
  public readonly rule: events.Rule;

  constructor(scope: Construct, id: string, props: TickHeartbeatStackProps) {
    super(scope, id, props);

    const { tickHandler, conversationTable } = props;

    this.lambdaFunction = new lambda.Function(this, "HeartbeatFunction", {
      functionName: "chatroom-tick-heartbeat",
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: "tick_loop.heartbeat_lambda.handler",
      code: backendPythonCode(),
      memorySize: 256,
      timeout: Duration.minutes(15),
      reservedConcurrentExecutions: 1,
      environment: {
        HEARTBEAT_INTERVAL_SEC: "5",
        HEARTBEAT_WINDOW_SEC: "840",
        TICK_HANDLER_LAMBDA: tickHandler.functionName,
        CONVERSATION_TABLE: conversationTable.tableName,
        CONVERSATION_STATUS_INDEX: "status-index",
        HEARTBEAT_MAX_FAILURES: "3",
      },
    });

    this.lambdaFunction.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["dynamodb:Query"],
        resources: [
          conversationTable.tableArn,
          `${conversationTable.tableArn}/index/status-index`,
        ],
      }),
    );

    tickHandler.grantInvoke(this.lambdaFunction);

    this.rule = new events.Rule(this, "HeartbeatSchedule", {
      schedule: events.Schedule.rate(Duration.minutes(15)),
    });

    this.rule.addTarget(new targets.LambdaFunction(this.lambdaFunction, {
      retryAttempts: 0,
    }));
  }
}
