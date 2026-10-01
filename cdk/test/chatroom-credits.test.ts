import { App, Stack, aws_lambda as lambda } from "aws-cdk-lib";
import { Template } from "aws-cdk-lib/assertions";
import { configureChatroomCredits } from "../lib/chatroom-credits";

function synth(context: Record<string, string> = {}) {
  const app = new App({ context });
  const stack = new Stack(app, "Credits");
  const fn = new lambda.Function(stack, "Worker", {
    runtime: lambda.Runtime.PYTHON_3_12, handler: "index.handler",
    code: lambda.Code.fromInline("def handler(event, context): return {}"),
  });
  configureChatroomCredits(stack, fn);
  return Template.fromStack(stack);
}

test("unconfigured runtime has no credit environment or secret parameter", () => {
  const template = synth().toJSON();
  expect(template.Parameters?.StimulizeApiToken).toBeUndefined();
  expect(JSON.stringify(template)).not.toContain("STIMULIZE_API_URL");
});

test.each(["false", "true"])("recording uses a private token and explicit enforcement=%s", (enforce) => {
  const template = synth({ stimulizeApiUrl: "https://management.example/live/", chatroomBalanceEnforcement: enforce });
  template.hasParameter("StimulizeApiToken", { Type: "String", NoEcho: true, MinLength: 32 });
  template.hasResourceProperties("AWS::Lambda::Function", { Environment: { Variables: {
    STIMULIZE_API_URL: "https://management.example/live",
    STIMULIZE_API_TOKEN: { Ref: "StimulizeApiToken" },
    CHATROOM_BALANCE_ENFORCEMENT_ENABLED: enforce,
  } } });
});

test("enforcement requires an endpoint", () => {
  expect(() => synth({ chatroomBalanceEnforcement: "true" })).toThrow("requires stimulizeApiUrl");
});

test.each(["http://management.example", "https://user:password@management.example", "https://management.example/?secret=value"])("rejects unsafe endpoint %s", (url) => {
  expect(() => synth({ stimulizeApiUrl: url })).toThrow("HTTPS base URL");
});

test("rejects invalid enforcement value", () => {
  expect(() => synth({ chatroomBalanceEnforcement: "yes" })).toThrow("must be true or false");
});
