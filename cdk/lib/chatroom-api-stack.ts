import {
  CfnOutput,
  Duration,
  Stack,
  StackProps,
  aws_apigateway as apigw,
  aws_certificatemanager as acm,
  aws_dynamodb as dynamodb,
  aws_iam as iam,
  aws_lambda as lambda,
  aws_secretsmanager as secretsmanager,
} from "aws-cdk-lib";
import { Construct } from "constructs";
import { backendPythonCode } from "./backend-code";

export interface ChatroomApiStackProps extends StackProps {
  table: dynamodb.ITable;
  lobbyTable: dynamodb.ITable;
  jwtSecret: secretsmanager.ISecret;
  adminToken: secretsmanager.ISecret;
}

export class ChatroomApiStack extends Stack {
  public readonly lambdaFunction: lambda.Function;
  public readonly api: apigw.RestApi;

  constructor(scope: Construct, id: string, props: ChatroomApiStackProps) {
    super(scope, id, props);

    const { table, lobbyTable, jwtSecret, adminToken } = props;

    // --------------- Context params ---------------
    const rdsHost = this.node.tryGetContext("rdsHost") as string;
    const rdsPort = this.node.tryGetContext("rdsPort") as string || "5432";
    const rdsDatabase = this.node.tryGetContext("rdsDatabase") as string || "stimulize";
    const rdsSecretArn = this.node.tryGetContext("rdsSecretArn") as string;
    const domainName = this.node.tryGetContext("domainName") as string || "";
    const enableCustomDomain = this.node.tryGetContext("enableCustomDomain") === "true";

    // --------------- RDS direct connection (beta) ---------------
    const rdsSecret = secretsmanager.Secret.fromSecretCompleteArn(
      this, "RdsSecret", rdsSecretArn
    );

    // --------------- Lambda ---------------

    this.lambdaFunction = new lambda.Function(this, "ChatroomApiFunction", {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: "chatroom_api.handler.lambda_handler",
      code: backendPythonCode(),
      memorySize: 256,
      timeout: Duration.seconds(30),
      environment: {
        DYNAMODB_TABLE: table.tableName,
        LOBBY_TABLE: lobbyTable.tableName,
        JWT_SECRET_ARN: jwtSecret.secretArn,
        ADMIN_TOKEN_SECRET_ARN: adminToken.secretArn,
        RDS_HOST: rdsHost,
        RDS_PORT: rdsPort,
        RDS_DATABASE: rdsDatabase,
        RDS_SECRET_ARN: rdsSecret.secretArn,
        BEDROCK_REGION: "us-east-2",
        USE_MOCK_DYNAMO: "false",
        USE_MOCK_RDS: "false",
        USE_MOCK_LOBBY: "false",
        // MGMT_API_URL / MGMT_API_TOKEN_SECRET_ARN are intentionally NOT set:
        // per docs/api-management.yml the chatroom Lambda doesn't talk to the
        // management API at runtime (it reads chatroom settings directly from
        // RDS and writes usage to RDS). The editor talks to the management
        // API; that wiring lives in the Stimulize-backend teammate's stack.
      },
    });

    // --------------- IAM policies ---------------

    // DynamoDB read/write — conversation table
    table.grantReadWriteData(this.lambdaFunction);

    // DynamoDB read/write — lobby table (open lobby query, atomic join,
    // close_lobby, last_seen_at heartbeat updates).
    lobbyTable.grantReadWriteData(this.lambdaFunction);

    // Bedrock InvokeModel
    this.lambdaFunction.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["bedrock:InvokeModel"],
        resources: ["*"],
      })
    );

    // Secrets Manager read (JWT secret)
    jwtSecret.grantRead(this.lambdaFunction);

    // Secrets Manager read (admin bearer token — for /chat/messages?include_ticks=true)
    adminToken.grantRead(this.lambdaFunction);

    // Secrets Manager read (RDS credentials)
    rdsSecret.grantRead(this.lambdaFunction);

    // --------------- API Gateway ---------------

    this.api = new apigw.RestApi(this, "ChatroomApi", {
      restApiName: "Chatroom API",
      defaultCorsPreflightOptions: {
        allowOrigins: apigw.Cors.ALL_ORIGINS,
        allowMethods: apigw.Cors.ALL_METHODS,
        allowHeaders: ["Content-Type", "Authorization"],
      },
    });

    const integration = new apigw.LambdaIntegration(this.lambdaFunction);

    // POST /auth/token
    const authResource = this.api.root.addResource("auth");
    authResource.addResource("token").addMethod("POST", integration);

    // POST /chat/send
    const chatResource = this.api.root.addResource("chat");
    chatResource.addResource("send").addMethod("POST", integration);

    // GET /chat/messages
    chatResource.addResource("messages").addMethod("GET", integration);

    // --------------- Custom domain ---------------

    if (enableCustomDomain && domainName) {
      const certificate = new acm.Certificate(this, "ChatroomCert", {
        domainName,
        validation: acm.CertificateValidation.fromDns(),
      });

      const customDomain = this.api.addDomainName("CustomDomain", {
        domainName,
        certificate,
        endpointType: apigw.EndpointType.EDGE,
      });

      new CfnOutput(this, "CustomDomainTarget", {
        value: customDomain.domainNameAliasDomainName,
        description: "CNAME target for DNS — point your domain here",
      });
    }

    // --------------- Outputs ---------------

    new CfnOutput(this, "ApiUrl", {
      value: this.api.url,
      description: "API Gateway URL",
    });

    new CfnOutput(this, "RdsHost", {
      value: rdsHost,
      description: "Direct RDS hostname configured for Lambda",
    });
  }
}
