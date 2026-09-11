import {
  App,
  RemovalPolicy,
  Stack,
  aws_dynamodb as dynamodb,
} from "aws-cdk-lib";
import { Match, Template } from "aws-cdk-lib/assertions";
import { AiConversationBatchStack } from "../lib/ai-conversation-batch-stack";


function makeStack() {
  const app = new App();
  const upstream = new Stack(app, "Upstream");
  const conversations = new dynamodb.Table(upstream, "Conversations", {
    partitionKey: { name: "conversation_id", type: dynamodb.AttributeType.STRING },
  });
  const events = new dynamodb.Table(upstream, "Events", {
    partitionKey: { name: "conversation_id", type: dynamodb.AttributeType.STRING },
    sortKey: { name: "event_key", type: dynamodb.AttributeType.STRING },
  });
  return new AiConversationBatchStack(app, "AiBatch", {
    conversationTable: conversations,
    eventTable: events,
    resourcePrefix: "stimulize-chatroom-ai-batch-dev-test",
    useMockRds: true,
    removalPolicy: RemovalPolicy.DESTROY,
  });
}


describe("AiConversationBatchStack", () => {
  it("creates isolated queues, handlers, exports, and an owner GSI", () => {
    const template = Template.fromStack(makeStack());

    template.resourceCountIs("AWS::Lambda::Function", 3);
    template.resourceCountIs("AWS::Lambda::EventSourceMapping", 3);
    template.resourceCountIs("AWS::SQS::Queue", 6);
    template.resourceCountIs("AWS::S3::Bucket", 1);
    template.resourceCountIs("AWS::CloudWatch::Alarm", 3);
    template.hasResourceProperties("AWS::DynamoDB::Table", {
      TableName: "stimulize-chatroom-ai-batch-dev-test-batches",
      BillingMode: "PAY_PER_REQUEST",
      PointInTimeRecoverySpecification: { PointInTimeRecoveryEnabled: true },
      GlobalSecondaryIndexes: Match.arrayWith([
        Match.objectLike({
          IndexName: "owner-created-index",
          Projection: { ProjectionType: "ALL" },
        }),
      ]),
    });
    template.hasResourceProperties("AWS::Lambda::Function", {
      Handler: "chatroom_api.ai_batch.worker.lambda_handler",
      Timeout: 600,
      ReservedConcurrentExecutions: 10,
      Environment: {
        Variables: Match.objectLike({
          AI_BATCH_ENABLED: "true",
          AI_BATCH_WORK_QUEUE_URL: Match.anyValue(),
          DYNAMODB_TABLE: Match.anyValue(),
          DYNAMODB_EVENT_TABLE: Match.anyValue(),
        }),
      },
    });
    template.hasResourceProperties("AWS::S3::Bucket", {
      BucketEncryption: Match.anyValue(),
      PublicAccessBlockConfiguration: {
        BlockPublicAcls: true,
        BlockPublicPolicy: true,
        IgnorePublicAcls: true,
        RestrictPublicBuckets: true,
      },
      LifecycleConfiguration: Match.objectLike({
        Rules: Match.arrayWith([Match.objectLike({ Status: "Enabled" })]),
      }),
    });
  });
});
