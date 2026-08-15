import {
  Duration,
  RemovalPolicy,
  Stack,
  StackProps,
  aws_cloudwatch as cloudwatch,
  aws_dynamodb as dynamodb,
  aws_lambda as lambda,
  aws_lambda_event_sources as eventSources,
  aws_logs as logs,
  aws_sqs as sqs,
} from "aws-cdk-lib";
import { Construct } from "constructs";
import { backendPythonCode } from "./backend-code";

export interface ConversationEventStackProps extends StackProps {
  metadataTable: dynamodb.ITable;
  eventTableName: string;
  cleanupFunctionName: string;
  removalPolicy?: RemovalPolicy;
  deletionProtection?: boolean;
}

export class ConversationEventStack extends Stack {
  public readonly eventTable: dynamodb.Table;
  public readonly cleanupFunction: lambda.Function;
  public readonly failureQueue: sqs.Queue;

  constructor(scope: Construct, id: string, props: ConversationEventStackProps) {
    super(scope, id, props);

    this.eventTable = new dynamodb.Table(this, "ConversationEventTable", {
      tableName: props.eventTableName,
      partitionKey: {
        name: "conversation_id",
        type: dynamodb.AttributeType.STRING,
      },
      sortKey: {
        name: "event_key",
        type: dynamodb.AttributeType.STRING,
      },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
      removalPolicy: props.removalPolicy ?? RemovalPolicy.RETAIN,
      deletionProtection: props.deletionProtection ?? false,
    });

    this.failureQueue = new sqs.Queue(this, "CleanupFailureQueue", {
      queueName: `${props.cleanupFunctionName}-dlq`,
      retentionPeriod: Duration.days(14),
      encryption: sqs.QueueEncryption.SQS_MANAGED,
    });

    this.cleanupFunction = new lambda.Function(this, "EventCleanupFunction", {
      functionName: props.cleanupFunctionName,
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: "chatroom_api.event_cleanup.handler",
      code: backendPythonCode(),
      memorySize: 256,
      timeout: Duration.minutes(5),
      logRetention: logs.RetentionDays.ONE_MONTH,
      environment: {
        DYNAMODB_EVENT_TABLE: this.eventTable.tableName,
      },
    });
    this.eventTable.grantReadWriteData(this.cleanupFunction);

    this.cleanupFunction.addEventSource(new eventSources.DynamoEventSource(
      props.metadataTable,
      {
        startingPosition: lambda.StartingPosition.TRIM_HORIZON,
        batchSize: 10,
        bisectBatchOnError: true,
        retryAttempts: 3,
        onFailure: new eventSources.SqsDlq(this.failureQueue),
      },
    ));

    new cloudwatch.Alarm(this, "CleanupFailureAlarm", {
      alarmName: `${props.cleanupFunctionName}-failures`,
      metric: this.failureQueue.metricApproximateNumberOfMessagesVisible(),
      threshold: 1,
      evaluationPeriods: 1,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
  }
}
