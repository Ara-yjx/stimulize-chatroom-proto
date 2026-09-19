import {
  CfnOutput,
  Duration,
  RemovalPolicy,
  Size,
  Stack,
  StackProps,
  aws_cloudwatch as cloudwatch,
  aws_dynamodb as dynamodb,
  aws_iam as iam,
  aws_lambda as lambda,
  aws_lambda_event_sources as eventSources,
  aws_logs as logs,
  aws_s3 as s3,
  aws_secretsmanager as secretsmanager,
  aws_sqs as sqs,
  aws_stepfunctions as sfn,
  aws_events as events,
  aws_events_targets as targets,
} from "aws-cdk-lib";
import { Construct } from "constructs";
import { backendPythonCode } from "./backend-code";
import { conversationWorkflow, conversationRecovery } from "./ai-conversation-workflow";

export interface AiConversationBatchStackProps extends StackProps {
  conversationTable: dynamodb.ITable;
  eventTable: dynamodb.ITable;
  resourcePrefix: string;
  useMockRds?: boolean;
  removalPolicy?: RemovalPolicy;
  deletionProtection?: boolean;
}

export class AiConversationBatchStack extends Stack {
  public readonly batchTable: dynamodb.Table;
  public readonly provisionQueue: sqs.Queue;
  public readonly conversationWorkflow: sfn.StateMachine;
  public readonly conversationRecovery: sfn.StateMachine;
  public readonly exportQueue: sqs.Queue;
  public readonly exportBucket: s3.Bucket;
  public readonly provisioner: lambda.Function;
  public readonly worker: lambda.Function;
  public readonly exporter: lambda.Function;

  constructor(scope: Construct, id: string, props: AiConversationBatchStackProps) {
    super(scope, id, props);

    const prefix = props.resourcePrefix;
    const useMockRds = props.useMockRds ?? false;
    const removalPolicy = props.removalPolicy ?? RemovalPolicy.RETAIN;
    const rdsHost = this.node.tryGetContext("rdsHost") as string;
    const rdsPort = (this.node.tryGetContext("rdsPort") as string) || "5432";
    const rdsDatabase = (this.node.tryGetContext("rdsDatabase") as string) || "stimulize";
    const rdsSecretArn = this.node.tryGetContext("rdsSecretArn") as string;
    const rdsSecret = useMockRds
      ? undefined
      : secretsmanager.Secret.fromSecretCompleteArn(
          this,
          "AiBatchRdsSecret",
          rdsSecretArn,
        );

    this.batchTable = new dynamodb.Table(this, "BatchTable", {
      tableName: `${prefix}-batches`,
      partitionKey: {
        name: "batch_job_id",
        type: dynamodb.AttributeType.STRING,
      },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
      removalPolicy,
      deletionProtection: props.deletionProtection ?? false,
    });
    this.batchTable.addGlobalSecondaryIndex({
      indexName: "owner-created-index",
      partitionKey: { name: "owner_id", type: dynamodb.AttributeType.STRING },
      sortKey: { name: "created_at", type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    const provisionDlq = new sqs.Queue(this, "ProvisionDlq", {
      queueName: `${prefix}-provision-dlq`,
      retentionPeriod: Duration.days(14),
      encryption: sqs.QueueEncryption.SQS_MANAGED,
    });
    const exportDlq = new sqs.Queue(this, "ExportDlq", {
      queueName: `${prefix}-export-dlq`,
      retentionPeriod: Duration.days(14),
      encryption: sqs.QueueEncryption.SQS_MANAGED,
    });
    this.provisionQueue = new sqs.Queue(this, "ProvisionQueue", {
      queueName: `${prefix}-provision`,
      visibilityTimeout: Duration.minutes(12),
      retentionPeriod: Duration.days(14),
      encryption: sqs.QueueEncryption.SQS_MANAGED,
      deadLetterQueue: { queue: provisionDlq, maxReceiveCount: 5 },
    });
    this.exportQueue = new sqs.Queue(this, "ExportQueue", {
      queueName: `${prefix}-export`,
      visibilityTimeout: Duration.minutes(16),
      retentionPeriod: Duration.days(14),
      encryption: sqs.QueueEncryption.SQS_MANAGED,
      deadLetterQueue: { queue: exportDlq, maxReceiveCount: 5 },
    });

    this.exportBucket = new s3.Bucket(this, "ExportBucket", {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      lifecycleRules: [{ prefix: "owners/", expiration: Duration.days(7) }],
      removalPolicy,
      autoDeleteObjects: false,
    });

    const code = backendPythonCode();
    const logGroup = (name: string) => new logs.LogGroup(this, `${name}Logs`, {
      logGroupName: `/aws/lambda/${prefix}-${name.toLowerCase()}`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy,
    });
    const commonEnvironment = {
      AI_BATCH_ENABLED: "true",
      AI_BATCH_TABLE: this.batchTable.tableName,
      DYNAMODB_TABLE: props.conversationTable.tableName,
      DYNAMODB_EVENT_TABLE: props.eventTable.tableName,
      EVENT_STORAGE_ENABLED: "true",
      USE_MOCK_DYNAMO: "false",
      USE_MOCK_RDS: String(useMockRds),
      USE_MOCK_LOBBY: "false",
    };
    this.provisioner = new lambda.Function(this, "Provisioner", {
      functionName: `${prefix}-provisioner`,
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: "chatroom_api.ai_batch.provisioner.lambda_handler",
      code,
      memorySize: 512,
      timeout: Duration.minutes(10),
      reservedConcurrentExecutions: 2,
      logGroup: logGroup("Provisioner"),
      environment: {
        ...commonEnvironment,
        AI_BATCH_EXPORT_BUCKET: this.exportBucket.bucketName,
      },
    });
    this.worker = new lambda.Function(this, "Worker", {
      functionName: `${prefix}-worker`,
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: "chatroom_api.ai_batch.worker.lambda_handler",
      code,
      memorySize: 512,
      timeout: Duration.minutes(10),
      reservedConcurrentExecutions: 10,
      logGroup: logGroup("Worker"),
      environment: {
        ...commonEnvironment,
        BEDROCK_REGION: "us-east-2",
        ...(useMockRds ? {} : {
          RDS_HOST: rdsHost,
          RDS_PORT: rdsPort,
          RDS_DATABASE: rdsDatabase,
          RDS_SECRET_ARN: rdsSecret!.secretArn,
        }),
      },
    });
    this.exporter = new lambda.Function(this, "Exporter", {
      functionName: `${prefix}-exporter`,
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: "chatroom_api.ai_batch.exporter.lambda_handler",
      code,
      memorySize: 1024,
      timeout: Duration.minutes(15),
      ephemeralStorageSize: Size.mebibytes(1024),
      reservedConcurrentExecutions: 2,
      logGroup: logGroup("Exporter"),
      environment: {
        ...commonEnvironment,
        AI_BATCH_EXPORT_BUCKET: this.exportBucket.bucketName,
      },
    });

    this.provisioner.addEventSource(new eventSources.SqsEventSource(
      this.provisionQueue,
      { batchSize: 1, reportBatchItemFailures: true },
    ));
    this.exporter.addEventSource(new eventSources.SqsEventSource(
      this.exportQueue,
      { batchSize: 1, reportBatchItemFailures: true },
    ));

    this.batchTable.grantReadWriteData(this.provisioner);
    props.conversationTable.grantReadWriteData(this.provisioner);
    props.eventTable.grantReadWriteData(this.provisioner);
    this.provisionQueue.grantConsumeMessages(this.provisioner);
    this.exportBucket.grantReadWrite(this.provisioner, "prompt-references/*");

    this.batchTable.grantReadWriteData(this.worker);
    props.conversationTable.grantReadWriteData(this.worker);
    props.eventTable.grantReadWriteData(this.worker);
    this.worker.addToRolePolicy(new iam.PolicyStatement({
      actions: ["bedrock:InvokeModel"],
      resources: ["*"],
    }));
    rdsSecret?.grantRead(this.worker);

    const workflowRole = new iam.Role(this, "ConversationWorkflowRole", {
      assumedBy: new iam.ServicePrincipal("states.amazonaws.com"),
    });
    this.worker.grantInvoke(workflowRole);
    props.conversationTable.grantReadWriteData(workflowRole);
    this.batchTable.grantReadWriteData(workflowRole);
    this.conversationWorkflow = new sfn.StateMachine(this, "ConversationWorkflow", {
      stateMachineName: `${prefix}-conversation`,
      stateMachineType: sfn.StateMachineType.STANDARD,
      role: workflowRole,
      definitionBody: sfn.DefinitionBody.fromString(this.toJsonString(conversationWorkflow(
        this.worker.functionArn, props.conversationTable.tableName, this.batchTable.tableName,
      ))),
      logs: {
        destination: new logs.LogGroup(this, "ConversationWorkflowLogs", {
          retention: logs.RetentionDays.ONE_MONTH, removalPolicy,
        }),
        level: sfn.LogLevel.ERROR, includeExecutionData: false,
      },
    });
    this.conversationWorkflow.grantStartExecution(this.provisioner);
    this.provisioner.addEnvironment("AI_BATCH_STATE_MACHINE_ARN", this.conversationWorkflow.stateMachineArn);
    const recoveryRole = new iam.Role(this, "ConversationRecoveryRole", {
      assumedBy: new iam.ServicePrincipal("states.amazonaws.com"),
    });
    props.conversationTable.grantReadWriteData(recoveryRole);
    this.batchTable.grantReadWriteData(recoveryRole);
    this.conversationRecovery = new sfn.StateMachine(this, "ConversationRecovery", {
      stateMachineName: `${prefix}-conversation-recovery`,
      stateMachineType: sfn.StateMachineType.STANDARD, role: recoveryRole,
      definitionBody: sfn.DefinitionBody.fromString(this.toJsonString(conversationRecovery(
        props.conversationTable.tableName, this.batchTable.tableName,
      ))),
    });
    const recoveryDlq = new sqs.Queue(this, "RecoveryDlq", {
      queueName: `${prefix}-recovery-dlq`, retentionPeriod: Duration.days(14),
      encryption: sqs.QueueEncryption.SQS_MANAGED,
    });
    new events.Rule(this, "ConversationExecutionFailed", {
      eventPattern: {
        source: ["aws.states"], detailType: ["Step Functions Execution Status Change"],
        detail: { stateMachineArn: [this.conversationWorkflow.stateMachineArn], status: ["FAILED", "TIMED_OUT", "ABORTED"] },
      },
      targets: [new targets.SfnStateMachine(this.conversationRecovery, {
        deadLetterQueue: recoveryDlq, retryAttempts: 5,
      })],
    });
    new cloudwatch.Alarm(this, "ConversationRecoveryFailed", {
      alarmName: `${prefix}-recovery-failed`, metric: this.conversationRecovery.metricFailed(),
      threshold: 1, evaluationPeriods: 1, treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    for (const [name, metric] of [
      ["failed", this.conversationWorkflow.metricFailed()],
      ["timed-out", this.conversationWorkflow.metricTimedOut()],
      ["aborted", this.conversationWorkflow.metricAborted()],
    ] as const) {
      new cloudwatch.Alarm(this, `ConversationWorkflow-${name}`, {
        alarmName: `${prefix}-workflow-${name}`, metric,
        threshold: 1, evaluationPeriods: 1,
        treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
      });
    }

    this.batchTable.grantReadWriteData(this.exporter);
    props.conversationTable.grantReadData(this.exporter);
    props.eventTable.grantReadData(this.exporter);
    this.exportQueue.grantConsumeMessages(this.exporter);
    this.exportBucket.grantReadWrite(this.exporter);

    for (const [name, queue] of [
      ["provision", provisionDlq],
      ["export", exportDlq],
      ["recovery", recoveryDlq],
    ] as const) {
      new cloudwatch.Alarm(this, `${name}DlqAlarm`, {
        alarmName: `${prefix}-${name}-dlq-visible`,
        metric: queue.metricApproximateNumberOfMessagesVisible(),
        threshold: 1,
        evaluationPeriods: 1,
        treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
      });
    }

    new CfnOutput(this, "BatchTableName", { value: this.batchTable.tableName });
    new CfnOutput(this, "ProvisionQueueUrl", { value: this.provisionQueue.queueUrl });
    new CfnOutput(this, "ExportQueueUrl", { value: this.exportQueue.queueUrl });
    new CfnOutput(this, "ExportBucketName", { value: this.exportBucket.bucketName });
    new CfnOutput(this, "ConversationWorkflowArn", { value: this.conversationWorkflow.stateMachineArn });
    new CfnOutput(this, "ConversationRecoveryArn", { value: this.conversationRecovery.stateMachineArn });
  }
}
