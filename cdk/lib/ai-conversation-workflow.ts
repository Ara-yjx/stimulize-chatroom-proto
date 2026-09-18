/** ASL kept separate from CDK resources so the actual definition can be exercised locally. */
export function conversationWorkflow(workerArn: string, conversationTable: string, batchTable: string) {
  const retry = [{ ErrorEquals: ["States.TaskFailed"], IntervalSeconds: 2, MaxAttempts: 3, BackoffRate: 2 }];
  const ddb = (operation: string, parameters: object, resultPath: string | null, next: string) => ({
    Type: "Task", Resource: `arn:aws:states:::aws-sdk:dynamodb:${operation}`,
    Parameters: parameters, ResultPath: resultPath, Retry: retry, Next: next,
  });
  const conversationKey = { conversation_id: { "S.$": "$.conversation_id" } };
  const batchKey = { batch_job_id: { "S.$": "$.batch_job_id" } };
  const terminalTransaction = (sourceCounter: string) => ddb("transactWriteItems", {
    TransactItems: [
      { Update: {
        TableName: conversationTable, Key: conversationKey,
        UpdateExpression: "SET #s = :status, execution_state = :terminal, outcome = :outcome, last_error = :reason, updated_at = :now",
        ConditionExpression: "#s = :old AND state_version = :version AND execution_state <> :terminal",
        ExpressionAttributeNames: { "#s": "status" },
        ExpressionAttributeValues: {
          ":status": { "S.$": "$.finish.status" }, ":terminal": { S: "terminal" },
          ":outcome": { "S.$": "$.finish.outcome" }, ":reason": { "S.$": "$.finish.reason" },
          ":now": { "S.$": "$$.State.EnteredTime" },
          ":old": { "S.$": "$.row.Item.status.S" }, ":version": { "N.$": "$.row.Item.state_version.N" },
        },
      } },
      { Update: {
        TableName: batchTable, Key: batchKey,
        UpdateExpression: `SET updated_at = :now ADD unfinished_count :minus, ${sourceCounter} :minus, #counter :one`,
        ConditionExpression: `unfinished_count > :zero AND ${sourceCounter} > :zero`,
        ExpressionAttributeNames: { "#counter.$": "$.finish.counter" },
        ExpressionAttributeValues: {
          ":now": { "S.$": "$$.State.EnteredTime" }, ":minus": { N: "-1" }, ":one": { N: "1" }, ":zero": { N: "0" },
        },
      } },
    ],
  }, null, "ReadBatch");
  const finish = (status: string, reason: string) => ({
    Type: "Pass", Result: { status, outcome: status, reason, counter: `${status}_count` },
    ResultPath: "$.finish", Next: "ReadBeforeFinish",
  });
  const states: Record<string, any> = {
    Initialize: { Type: "Pass", Parameters: {
      "conversation_id.$": "$.conversation_id", "batch_job_id.$": "$.batch_job_id",
      "deadline.$": "$.deadline", dispatches: 0, failures: 0,
    }, Next: "ReadConversation" },
    ReadConversation: ddb("getItem", {
      TableName: conversationTable, Key: conversationKey, ConsistentRead: true,
      ProjectionExpression: "execution_state, #s, state_version",
      ExpressionAttributeNames: { "#s": "status" },
    }, "$.row", "CheckTerminal"),
    CheckTerminal: { Type: "Choice", Choices: [
      { Variable: "$.row.Item", IsPresent: false, Next: "InfrastructureFailure" },
      { Variable: "$.row.Item.execution_state.S", StringEquals: "terminal", Next: "ReadBatch" },
    ], Default: "Clock" },
    Clock: { Type: "Pass", Parameters: { "now.$": "$$.State.EnteredTime" }, ResultPath: "$.clock", Next: "CheckBounds" },
    CheckBounds: { Type: "Choice", Choices: [
      { Variable: "$.deadline", TimestampLessThanEqualsPath: "$.clock.now", Next: "Timeout" },
      { Variable: "$.dispatches", NumericGreaterThanEquals: 1000, Next: "IterationLimit" },
      { Variable: "$.failures", NumericGreaterThanEquals: 3, Next: "WorkerFailure" },
    ], Default: "CountDispatch" },
    CountDispatch: { Type: "Pass", Parameters: {
      "conversation_id.$": "$.conversation_id", "batch_job_id.$": "$.batch_job_id", "deadline.$": "$.deadline",
      "dispatches.$": "States.MathAdd($.dispatches, 1)", "failures.$": "$.failures",
    }, Next: "RunWorker" },
    RunWorker: {
      Type: "Task", Resource: "arn:aws:states:::lambda:invoke", TimeoutSeconds: 660,
      Parameters: { FunctionName: workerArn, Payload: {
        "conversation_id.$": "$.conversation_id", "batch_job_id.$": "$.batch_job_id",
      } }, ResultPath: null, Next: "ResetFailures",
      Catch: [{ ErrorEquals: ["States.ALL"], ResultPath: "$.error", Next: "CountFailure" }],
    },
    ResetFailures: { Type: "Pass", Result: 0, ResultPath: "$.failures", Next: "ReadConversation" },
    CountFailure: { Type: "Pass", Parameters: {
      "conversation_id.$": "$.conversation_id", "batch_job_id.$": "$.batch_job_id", "deadline.$": "$.deadline",
      "dispatches.$": "$.dispatches", "failures.$": "States.MathAdd($.failures, 1)",
    }, Next: "RetryDelay" },
    RetryDelay: { Type: "Wait", Seconds: 2, Next: "ReadConversation" },
    Timeout: finish("timed_out", "deadline_reached"),
    IterationLimit: finish("failed", "worker_iteration_limit"),
    WorkerFailure: finish("failed", "worker_retry_exhausted; see execution history"),
    ReadBeforeFinish: ddb("getItem", {
      TableName: conversationTable, Key: conversationKey, ConsistentRead: true,
      ProjectionExpression: "execution_state, #s, state_version", ExpressionAttributeNames: { "#s": "status" },
    }, "$.row", "SelectFinish"),
    SelectFinish: { Type: "Choice", Choices: [
      { Variable: "$.row.Item.execution_state.S", StringEquals: "terminal", Next: "ReadBatch" },
      { Variable: "$.row.Item.status.S", StringEquals: "queued", Next: "FinishQueued" },
      { Variable: "$.row.Item.status.S", StringEquals: "running", Next: "FinishRunning" },
    ], Default: "InfrastructureFailure" },
    FinishQueued: terminalTransaction("queued_count"),
    FinishRunning: terminalTransaction("running_count"),
    ReadAfterFinishConflict: ddb("getItem", {
      TableName: conversationTable, Key: conversationKey, ConsistentRead: true,
      ProjectionExpression: "execution_state",
    }, "$.row", "FinishAlreadyCommitted"),
    FinishAlreadyCommitted: { Type: "Choice", Choices: [
      { Variable: "$.row.Item.execution_state.S", StringEquals: "terminal", Next: "ReadBatch" },
    ], Default: "InfrastructureFailure" },
    ReadBatch: ddb("getItem", {
      TableName: batchTable, Key: batchKey, ConsistentRead: true,
      ProjectionExpression: "unfinished_count, completed_count, failed_count, timed_out_count, #s",
      ExpressionAttributeNames: { "#s": "status" },
    }, "$.batch", "BatchDone"),
    BatchDone: { Type: "Choice", Choices: [
      { Variable: "$.batch.Item.unfinished_count.N", StringEquals: "0", Next: "BatchOutcome" },
    ], Default: "Done" },
    BatchOutcome: { Type: "Choice", Choices: [
      { And: [
        { Variable: "$.batch.Item.failed_count.N", StringEquals: "0" },
        { Variable: "$.batch.Item.timed_out_count.N", StringEquals: "0" },
      ], Next: "BatchSucceeded" },
      { Not: { Variable: "$.batch.Item.completed_count.N", StringEquals: "0" }, Next: "BatchPartial" },
      { Variable: "$.batch.Item.failed_count.N", StringEquals: "0", Next: "BatchTimedOut" },
    ], Default: "BatchFailed" },
    Done: { Type: "Succeed" },
    InfrastructureFailure: { Type: "Fail", Error: "ConversationFinalizationFailed" },
  };
  for (const name of ["FinishQueued", "FinishRunning"]) {
    states[name].Catch = [{
      ErrorEquals: ["DynamoDb.TransactionCanceledException"], ResultPath: null, Next: "ReadAfterFinishConflict",
    }];
  }
  for (const [name, status] of Object.entries({
    BatchSucceeded: "completed", BatchPartial: "partial_failure", BatchTimedOut: "timed_out", BatchFailed: "failed",
  })) {
    states[name] = ddb("updateItem", {
      TableName: batchTable, Key: batchKey,
      UpdateExpression: "SET #s = :status, updated_at = :now",
      ConditionExpression: "unfinished_count = :zero",
      ExpressionAttributeNames: { "#s": "status" },
      ExpressionAttributeValues: { ":status": { S: status }, ":zero": { N: "0" }, ":now": { "S.$": "$$.State.EnteredTime" } },
    }, null, "Done");
  }
  return { Comment: "Checkpointed AI conversation; no per-turn queue or lease", StartAt: "Initialize", TimeoutSeconds: 90000, States: states };
}

/** Exceptional execution-level cleanup, also callable by management reconciliation. */
export function conversationRecovery(conversationTable: string, batchTable: string) {
  const definition = conversationWorkflow("unused", conversationTable, batchTable);
  const states = definition.States;
  for (const name of ["CountDispatch", "RunWorker", "ResetFailures", "CountFailure", "RetryDelay", "IterationLimit", "WorkerFailure"]) {
    delete states[name];
  }
  states.Initialize = { Type: "Pass", Parameters: {
    "original.$": "States.StringToJson($.detail.input)", "reason.$": "$.detail.status",
  }, Next: "RecoveryInput" };
  states.RecoveryInput = { Type: "Pass", Parameters: {
    "conversation_id.$": "$.original.conversation_id", "batch_job_id.$": "$.original.batch_job_id",
    "deadline.$": "$.original.deadline", finish: {
      status: "failed", outcome: "failed", counter: "failed_count",
      "reason.$": "States.Format('workflow_{}', $.reason)",
    },
  }, Next: "ReadConversation" };
  states.CheckBounds = { Type: "Choice", Choices: [
    { Variable: "$.deadline", TimestampLessThanEqualsPath: "$.clock.now", Next: "Timeout" },
  ], Default: "ReadBeforeFinish" };
  return { ...definition, Comment: "Native DDB terminal reconciliation; never invokes a model or worker", TimeoutSeconds: 600 };
}
