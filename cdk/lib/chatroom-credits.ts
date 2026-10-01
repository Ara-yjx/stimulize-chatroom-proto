import { CfnParameter, Stack, aws_lambda as lambda } from "aws-cdk-lib";

/** Configure incurred-cost recording separately from balance admission checks. */
export function configureChatroomCredits(stack: Stack, fn: lambda.Function): void {
  const url = stack.node.tryGetContext("stimulizeApiUrl") || "";
  const enforce = stack.node.tryGetContext("chatroomBalanceEnforcement") ?? "false";
  if (!["true", "false"].includes(enforce)) {
    throw new Error("chatroomBalanceEnforcement must be true or false");
  }
  if (!url) {
    if (enforce === "true") throw new Error("Balance enforcement requires stimulizeApiUrl");
    return;
  }
  const parsed = new URL(url);
  if (parsed.protocol !== "https:" || parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new Error("stimulizeApiUrl must be an HTTPS base URL without credentials, query or fragment");
  }
  // No plaintext token in source, context, or synthesized templates. Supply this
  // NoEcho parameter privately at deployment; existing stacks retain its value.
  const token = new CfnParameter(stack, "StimulizeApiToken", {
    type: "String", noEcho: true, minLength: 32,
    description: "Matches management CHATROOM_SERVICE_TOKEN; never commit this value.",
  });
  fn.addEnvironment("STIMULIZE_API_URL", url.replace(/\/$/, ""));
  fn.addEnvironment("STIMULIZE_API_TOKEN", token.valueAsString);
  fn.addEnvironment("CHATROOM_BALANCE_ENFORCEMENT_ENABLED", enforce);
}
