import { CfnOutput, Stack, StackProps, aws_secretsmanager as secretsmanager } from "aws-cdk-lib";
import { Construct } from "constructs";

export class SecretsStack extends Stack {
  public readonly jwtSecret: secretsmanager.ISecret;

  constructor(scope: Construct, id: string, props?: StackProps) {
    super(scope, id, props);

    this.jwtSecret = new secretsmanager.Secret(this, "JwtSecret", {
      secretName: "stimulize-chatroom/jwt-secret",
      generateSecretString: {
        passwordLength: 32,
        excludePunctuation: true,
      },
    });

    new CfnOutput(this, "JwtSecretArn", {
      value: this.jwtSecret.secretArn,
      description: "ARN of the JWT signing secret",
      exportName: "StimulizeChatroom-JwtSecretArn",
    });
  }
}
