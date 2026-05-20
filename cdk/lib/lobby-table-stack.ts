import { RemovalPolicy, Stack, StackProps, aws_dynamodb as dynamodb } from "aws-cdk-lib";
import { Construct } from "constructs";

/**
 * `chatroom-lobbies` DynamoDB table — group-mode pairing state.
 *
 * Schema and access patterns are documented in
 * `docs/low-level-design.md` ("DynamoDB: chatroom-lobbies"). Summary:
 *
 * - PK: `lobby_id` (UUID). One row per cohort; multiple historical lobbies per
 *   chatroom over its lifetime, plus at most one open lobby at a time.
 * - TTL on `ttl` (epoch seconds, ~180 days post-close) for audit retention.
 *
 * GSIs:
 * - `chatroom_id-status-index` — `/auth/token` queries this to find the
 *   currently-open lobby for a chatroom_id. Effectively sparse on
 *   `status="open"`: closed/aborted rows still appear in the GSI (audit
 *   trail), and the application uses
 *   `KeyConditionExpression chatroom_id=:c AND status=:open` to filter.
 * - `conversation_id-index` — `/chat/messages` looks up the lobby by
 *   pre-allocated `conversation_id` while the conversation row doesn't
 *   yet exist (lobby phase polling).
 */
export class LobbyTableStack extends Stack {
  public readonly table: dynamodb.Table;

  constructor(scope: Construct, id: string, props?: StackProps) {
    super(scope, id, props);

    this.table = new dynamodb.Table(this, "LobbyTable", {
      tableName: "chatroom-lobbies",
      partitionKey: {
        name: "lobby_id",
        type: dynamodb.AttributeType.STRING,
      },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      timeToLiveAttribute: "ttl",
      removalPolicy: RemovalPolicy.DESTROY,
    });

    // PK=chatroom_id, SK=status. ALL projection because /auth/token reads the
    // full lobby row from this query result (participants, deadline_at,
    // counters) without a follow-up GetItem.
    this.table.addGlobalSecondaryIndex({
      indexName: "chatroom_id-status-index",
      partitionKey: { name: "chatroom_id", type: dynamodb.AttributeType.STRING },
      sortKey: { name: "status", type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    // PK=conversation_id. ALL projection so /chat/messages can return lobby
    // state (status, actual_human_count, target_human_count, deadline_at) in
    // a single Query while the conversation row hasn't been written yet.
    this.table.addGlobalSecondaryIndex({
      indexName: "conversation_id-index",
      partitionKey: { name: "conversation_id", type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });
  }
}
