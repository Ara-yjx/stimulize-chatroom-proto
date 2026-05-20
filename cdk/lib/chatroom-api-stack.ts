import * as path from "path";
import {
  CfnOutput,
  Duration,
  Stack,
  StackProps,
  aws_apigateway as apigw,
  aws_certificatemanager as acm,
  aws_dynamodb as dynamodb,
  aws_ec2 as ec2,
  aws_iam as iam,
  aws_lambda as lambda,
  aws_rds as rds,
  aws_secretsmanager as secretsmanager,
} from "aws-cdk-lib";
import { Construct } from "constructs";

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
    const vpcId = this.node.tryGetContext("vpcId") as string;
    const subnetIds = (this.node.tryGetContext("subnetIds") as string).split(",");
    const rdsSecurityGroupId = this.node.tryGetContext("rdsSecurityGroupId") as string;
    const rdsHost = this.node.tryGetContext("rdsHost") as string;
    const rdsPort = this.node.tryGetContext("rdsPort") as string || "5432";
    const rdsDatabase = this.node.tryGetContext("rdsDatabase") as string || "stimulize";
    const rdsSecretArn = this.node.tryGetContext("rdsSecretArn") as string;
    const domainName = this.node.tryGetContext("domainName") as string || "chatroom.stimulize.org";
    const useVpcEndpoints = this.node.tryGetContext("useVpcEndpoints") === "true";

    // --------------- VPC lookup ---------------
    const vpc = ec2.Vpc.fromLookup(this, "ExistingVpc", { vpcId });

    const subnets = subnetIds.map((subnetId, i) =>
      ec2.Subnet.fromSubnetId(this, `Subnet${i}`, subnetId.trim())
    );

    const rdsSg = ec2.SecurityGroup.fromSecurityGroupId(
      this, "RdsSecurityGroup", rdsSecurityGroupId
    );

    // Lambda security group — allows outbound, RDS SG should allow inbound from this
    const lambdaSg = new ec2.SecurityGroup(this, "LambdaSg", {
      vpc,
      description: "Security group for chatroom Lambda",
      allowAllOutbound: true,
    });

    // --------------- VPC Endpoints (task 5.6) ---------------
    if (useVpcEndpoints) {
      // Gateway endpoint for DynamoDB (free)
      vpc.addGatewayEndpoint("DynamoDbEndpoint", {
        service: ec2.GatewayVpcEndpointAwsService.DYNAMODB,
        subnets: [{ subnets }],
      });

      // Interface endpoint for Bedrock Runtime
      vpc.addInterfaceEndpoint("BedrockRuntimeEndpoint", {
        service: ec2.InterfaceVpcEndpointAwsService.BEDROCK_RUNTIME,
        subnets: { subnets },
        securityGroups: [lambdaSg],
        privateDnsEnabled: true,
      });

      // Interface endpoint for Secrets Manager
      vpc.addInterfaceEndpoint("SecretsManagerEndpoint", {
        service: ec2.InterfaceVpcEndpointAwsService.SECRETS_MANAGER,
        subnets: { subnets },
        securityGroups: [lambdaSg],
        privateDnsEnabled: true,
      });
    }
    // If not using VPC endpoints, Lambda reaches these services via NAT Gateway.
    // NAT Gateway is typically already provisioned in the existing Stimulize VPC.

    // --------------- RDS Proxy (task 5.5) ---------------
    const rdsSecret = secretsmanager.Secret.fromSecretCompleteArn(
      this, "RdsSecret", rdsSecretArn
    );

    const rdsProxy = new rds.DatabaseProxy(this, "RdsProxy", {
      proxyTarget: rds.ProxyTarget.fromCluster(
        // Use a dummy cluster reference — the actual RDS instance is pre-existing.
        // CDK requires a target; we override the endpoint via env var.
        rds.DatabaseCluster.fromDatabaseClusterAttributes(this, "ExistingRdsCluster", {
          clusterIdentifier: "stimulize-rds-cluster",
          engine: rds.DatabaseClusterEngine.auroraPostgres({
            version: rds.AuroraPostgresEngineVersion.VER_16_1,
          }),
        })
      ),
      secrets: [rdsSecret],
      vpc,
      vpcSubnets: { subnets },
      securityGroups: [rdsSg],
      dbProxyName: "stimulize-chatroom-rds-proxy",
      requireTLS: true,
    });

    // --------------- Lambda ---------------

    this.lambdaFunction = new lambda.Function(this, "ChatroomApiFunction", {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: "chatroom_api.handler.lambda_handler",
      code: lambda.Code.fromAsset(path.join(__dirname, "..", "..", "backend")),
      memorySize: 256,
      timeout: Duration.seconds(30),
      vpc,
      vpcSubnets: { subnets },
      securityGroups: [lambdaSg],
      environment: {
        DYNAMODB_TABLE: table.tableName,
        LOBBY_TABLE: lobbyTable.tableName,
        JWT_SECRET_ARN: jwtSecret.secretArn,
        ADMIN_TOKEN_SECRET_ARN: adminToken.secretArn,
        RDS_HOST: rdsProxy.endpoint, // Lambda connects through RDS Proxy
        RDS_PORT: rdsPort,
        RDS_DATABASE: rdsDatabase,
        RDS_USERNAME: "stimulize",
        RDS_PASSWORD: "", // Retrieved from Secrets Manager at runtime
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

    // Secrets Manager read (RDS secret — for RDS Proxy auth)
    rdsSecret.grantRead(this.lambdaFunction);

    // RDS access is handled via VPC security group (Lambda SG → RDS SG), not IAM policy.

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

    const certificate = new acm.Certificate(this, "ChatroomCert", {
      domainName,
      validation: acm.CertificateValidation.fromDns(),
    });

    const customDomain = this.api.addDomainName("CustomDomain", {
      domainName,
      certificate,
      endpointType: apigw.EndpointType.EDGE,
    });

    // --------------- Outputs ---------------

    new CfnOutput(this, "ApiUrl", {
      value: this.api.url,
      description: "API Gateway URL",
    });

    new CfnOutput(this, "CustomDomainTarget", {
      value: customDomain.domainNameAliasDomainName,
      description: "CNAME target for DNS — point your domain here",
    });

    new CfnOutput(this, "RdsProxyEndpoint", {
      value: rdsProxy.endpoint,
      description: "RDS Proxy endpoint used by Lambda",
    });
  }
}
