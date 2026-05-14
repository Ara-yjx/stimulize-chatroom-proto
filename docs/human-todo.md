# Human To-Do: AWS & Infrastructure Setup

## 🔴 Before First Deploy

- [ ] **Set up AWS CLI programmatic access** (needed for CDK deploy):
  - IAM Console → your user → Security credentials → Create access key
  - Run `aws configure`, paste access key ID + secret
  - (Or use `aws sso login` if using SSO)
- [ ] **Run `cdk bootstrap`**: `cdk bootstrap aws://ACCOUNT_ID/REGION` (one-time)
- [ ] **Provide existing Stimulize VPC info** (for CDK config):
  - VPC ID
  - Private subnet IDs (at least 2)
  - RDS security group ID
  - RDS endpoint, port, database name
  - RDS credentials secret ARN

## 🟡 Domain: chatroom.stimulize.org

- [ ] **Add 2 CNAME records on domain.com** (values from CDK output):
  1. ACM validation CNAME
  2. `chatroom` → API Gateway custom domain name

## 🟡 RDS Schema (run once)

```sql
CREATE TABLE chatroom (
  id         VARCHAR(64) PRIMARY KEY,
  owner_id   VARCHAR(64) NOT NULL,
  name       VARCHAR(255) NOT NULL,
  status     VARCHAR(16) NOT NULL DEFAULT 'active',
  setting    JSON NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE chatroom_usage (
  id              SERIAL PRIMARY KEY,
  chatroom_id     VARCHAR(64) NOT NULL REFERENCES chatroom(id),
  conversation_id VARCHAR(64) NOT NULL,
  session_id      VARCHAR(64) NOT NULL,
  input_tokens    INT NOT NULL,
  output_tokens   INT NOT NULL,
  total_tokens    INT NOT NULL,
  created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_usage_chatroom ON chatroom_usage(chatroom_id);
```

## 🟢 After Deploy

- [ ] `curl https://chatroom.stimulize.org/auth/token` returns a response
- [ ] Create test chatroom → generate script → test in Qualtrics

## What Kiro Handles

- JWT secret in Secrets Manager (via CDK)
- DynamoDB table (via CDK)
- Lambda + API Gateway + IAM (via CDK)
- ACM certificate request (via CDK)
- RDS Proxy (via CDK)
- VPC endpoints / NAT Gateway (via CDK)
