import { RemovalPolicy, Stack, StackProps, aws_dynamodb as dynamodb } from "aws-cdk-lib";
import { Construct } from "constructs";

export class ConversationTableStack extends Stack {
  public readonly table: dynamodb.Table;

  constructor(scope: Construct, id: string, props?: StackProps) {
    super(scope, id, props);

    this.table = new dynamodb.Table(this, "ConversationTable", {
      tableName: "chatroom-conversations",
      partitionKey: {
        name: "conversation_id",
        type: dynamodb.AttributeType.STRING,
      },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      timeToLiveAttribute: "ttl",
      removalPolicy: RemovalPolicy.DESTROY,
    });

    // GSI used by the heartbeat container to enumerate tickable conversations.
    // Sparse on `status="active"` in practice — backend writes `status="active"`
    // on close_lobby and flips to `"ended"` when max_duration elapses or the
    // conversation otherwise terminates. KEYS_ONLY projection keeps the index
    // hot regardless of historical conversation volume; the heartbeat only
    // needs `conversation_id` to async-invoke the tick handler.
    this.table.addGlobalSecondaryIndex({
      indexName: "status-index",
      partitionKey: { name: "status", type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.KEYS_ONLY,
    });
  }
}
