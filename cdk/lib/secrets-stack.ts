import { CfnOutput, Stack, StackProps, aws_secretsmanager as secretsmanager } from "aws-cdk-lib";
import { Construct } from "constructs";

export interface SecretsStackProps extends StackProps {
  secretPrefix?: string;
  exportPrefix?: string | null;
}

/**
 * Secrets owned by the chatroom backend.
 *
 * The mock-management bearer token lives in a separate stack owned by the
 * Stimulize backend teammate (see `docs/low-level-design.md` →
 * `MockManagementStack`); the chatroom Lambda doesn't talk to the management
 * API at runtime per `docs/api-management.yml` so it doesn't need that
 * secret.
 */
export class SecretsStack extends Stack {
  public readonly jwtSecret: secretsmanager.ISecret;
  /**
   * Admin bearer token for `GET /chat/messages?include_ticks=true` debugging
   * (see `docs/low-level-design.md` → "Tick admin endpoint"). Granted only to
   * the chatroom-api and tick-handler Lambdas via Secrets Manager read.
   */
  public readonly adminToken: secretsmanager.ISecret;

  constructor(scope: Construct, id: string, props?: SecretsStackProps) {
    super(scope, id, props);

    this.jwtSecret = new secretsmanager.Secret(this, "JwtSecret", {
      secretName: `${props?.secretPrefix ?? "stimulize-chatroom"}/jwt-secret`,
      generateSecretString: {
        passwordLength: 32,
        excludePunctuation: true,
      },
    });

    new CfnOutput(this, "JwtSecretArn", {
      value: this.jwtSecret.secretArn,
      description: "ARN of the JWT signing secret",
      exportName: props?.exportPrefix === null
        ? undefined
        : `${props?.exportPrefix ?? "StimulizeChatroom"}-JwtSecretArn`,
    });

    this.adminToken = new secretsmanager.Secret(this, "AdminToken", {
      secretName: `${props?.secretPrefix ?? "stimulize-chatroom"}/admin-token`,
      generateSecretString: {
        passwordLength: 48,
        excludePunctuation: true,
      },
    });

    new CfnOutput(this, "AdminTokenArn", {
      value: this.adminToken.secretArn,
      description: "ARN of the admin bearer token (?include_ticks=true)",
      exportName: props?.exportPrefix === null
        ? undefined
        : `${props?.exportPrefix ?? "StimulizeChatroom"}-AdminTokenArn`,
    });
  }
}
