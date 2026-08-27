> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Bring Your Own Key (BYOK)

Enterprise Add-on. Contact [sales](mailto:sales@getzep.com) to enable BYOK for your account.

## Overview

Bring Your Own Key (BYOK) enables you to encrypt your data at rest in Zep using your own Customer Master Key (CMK) stored in your AWS KMS account. This provides full control over your encryption keys, including the ability to revoke Zep's access at any time.

BYOK is the Cloud + Your Own Keys deployment model: Zep's managed service with encryption keys you control. For a full network and compliance boundary inside your own VPC, see [Bring Your Own Cloud (BYOC)](https://www.getzep.com/enterprise).

With BYOK enabled:

* Your data is encrypted using keys derived from your CMK
* Zep never has direct access to your CMK—only cross-account usage rights
* You maintain complete control over key rotation and access revocation
* All encryption operations are logged in your AWS CloudTrail for auditability

## Prerequisites

* An AWS account with permissions to create and manage KMS keys
* Your Zep account UUID (available from your Zep dashboard)

## Setup instructions

### Step 1: Create a KMS key

Create a symmetric KMS key in your AWS account. Zep Cloud operates in `us-west-2`, so your key must be accessible from that region:

* **Single-region key**: Create directly in `us-west-2`
* **Multi-region key**: Create in any region and replicate to `us-west-2`

```bash
aws kms create-key \
  --description "Zep BYOK encryption key" \
  --key-usage ENCRYPT_DECRYPT \
  --origin AWS_KMS \
  --region us-west-2
```

Note the `KeyId` or `Arn` from the response—you'll need this for the next steps.

Optionally, create an alias for easier reference:

```bash
aws kms create-alias \
  --alias-name alias/zep-byok \
  --target-key-id <your-key-id> \
  --region us-west-2
```

#### Using Terraform

```hcl
resource "aws_kms_key" "zep_byok" {
  description             = "Zep BYOK encryption key"
  key_usage               = "ENCRYPT_DECRYPT"
  deletion_window_in_days = 30
  enable_key_rotation     = true

  # Deploy this resource in us-west-2
  provider = aws.us-west-2
}

resource "aws_kms_alias" "zep_byok" {
  name          = "alias/zep-byok"
  target_key_id = aws_kms_key.zep_byok.key_id

  provider = aws.us-west-2
}
```

### Step 2: Grant Zep cross-account access

Configure your KMS key policy to allow Zep's services to use your key for BYOK operations.

First, retrieve your AWS account ID:

```bash
aws sts get-caller-identity --query Account --output text
```

Then create and apply the key policy. Replace `<your-key-id>` with your KMS key ID from Step 1, and `<your-aws-account-id>` with the 12-digit account ID from above:

```bash
aws kms put-key-policy \
  --key-id <your-key-id> \
  --policy-name default \
  --region us-west-2 \
  --policy '{
    "Version": "2012-10-17",
    "Id": "zep-byok-key-policy",
    "Statement": [
      {
        "Sid": "EnableRootAccountPermissions",
        "Effect": "Allow",
        "Principal": {
          "AWS": "arn:aws:iam::<your-aws-account-id>:root"
        },
        "Action": "kms:*",
        "Resource": "*"
      },
      {
        "Sid": "AllowZepBYOKDescribeKey",
        "Effect": "Allow",
        "Principal": {
          "AWS": "arn:aws:iam::467218391112:role/zep-byok"
        },
        "Action": "kms:DescribeKey",
        "Resource": "*"
      },
      {
        "Sid": "AllowZepBYOKCryptoOpsScoped",
        "Effect": "Allow",
        "Principal": {
          "AWS": "arn:aws:iam::467218391112:role/zep-byok"
        },
        "Action": [
          "kms:GenerateDataKeyWithoutPlaintext",
          "kms:Decrypt",
          "kms:ReEncrypt*"
        ],
        "Resource": "*",
        "Condition": {
          "StringEquals": {
            "kms:EncryptionContext:aws-crypto-ec:service": "zep",
            "kms:EncryptionContext:aws-crypto-ec:account_uuid": "<your-zep-account-uuid>"
          }
        }
      }
    ]
  }'
```

#### Using Terraform

```hcl
locals {
  zep_byok_role_arn = "arn:aws:iam::467218391112:role/zep-byok"
}

data "aws_caller_identity" "current" {}

data "aws_iam_policy_document" "zep_byok" {
  statement {
    sid    = "EnableRootAccountPermissions"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }
    actions   = ["kms:*"]
    resources = ["*"]
  }

  statement {
    sid    = "AllowZepBYOKDescribeKey"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = [local.zep_byok_role_arn]
    }
    actions   = ["kms:DescribeKey"]
    resources = ["*"]
  }

  statement {
    sid    = "AllowZepBYOKCryptoOpsScoped"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = [local.zep_byok_role_arn]
    }
    actions = [
      "kms:GenerateDataKeyWithoutPlaintext",
      "kms:Decrypt",
      "kms:ReEncrypt*"
    ]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:EncryptionContext:aws-crypto-ec:service"
      values   = ["zep"]
    }
    condition {
      test     = "StringEquals"
      variable = "kms:EncryptionContext:aws-crypto-ec:account_uuid"
      values   = ["<your-zep-account-uuid>"]
    }
  }
}

resource "aws_kms_key_policy" "zep_byok" {
  key_id = aws_kms_key.zep_byok.id
  policy = data.aws_iam_policy_document.zep_byok.json
}

output "kms_key_arn" {
  description = "KMS Key ARN to provide to Zep"
  value       = aws_kms_key.zep_byok.arn
}

output "aws_account_id" {
  description = "AWS Account ID to provide to Zep"
  value       = data.aws_caller_identity.current.account_id
}
```

### Step 3: Configure BYOK in Zep

Navigate to **Account > Encryption** in your Zep dashboard and enter your KMS Key ARN. The region and AWS account ID will be extracted automatically from the ARN.

<img src="https://fdr-prod-docs-files-public.s3.us-east-1.amazonaws.com/zep.docs.buildwithfern.com/057379d03b415802bc1089ea6d72a179b6bf5058c45a0df8e0ab78b6027c9dcd/images/byok-config.png?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=AKIA6KXJSKKNFOCF7G4B%2F20260825%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Date=20260825T142015Z&X-Amz-Expires=604800&X-Amz-Signature=4db6d8b21a8f781860861212d12ed3a7fd9c2b8ac0cd432694bdd21bdcac0c7b&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject" alt="BYOK Configuration" />

Click **Save Configuration** to enable BYOK encryption. Zep will validate connectivity to your KMS key.

If validation fails, review your KMS key policy to ensure the `AllowZepBYOKDescribeKey` and `AllowZepBYOKCryptoOpsScoped` statements are correctly configured and that your key ARN is accurate.

New data written after activation will be encrypted with your key.

## Key rotation

AWS KMS supports automatic key rotation for customer-managed keys. When enabled, AWS automatically creates new key material annually while retaining old material for decryption.

To enable automatic rotation:

```bash
aws kms enable-key-rotation --key-id <your-key-id>
```

If using Terraform, key rotation is already enabled via `enable_key_rotation = true` in the example above.

No action is required from Zep when you rotate keys—AWS KMS handles this transparently.

When AWS KMS rotates your key, it creates a new key version but retains all previous versions. Zep does **not** re-encrypt existing data with new key versions—data remains encrypted with the key version that was active at the time of encryption.

* **Do not delete old key versions**—this will result in permanent data loss for any data encrypted with those versions
* If you need to delete old key versions for compliance reasons, contact Zep first to coordinate data re-encryption

## Revoking access

To revoke Zep's access to your data:

1. Remove the `AllowZepBYOKDescribeKey` and `AllowZepBYOKCryptoOpsScoped` statements from your KMS key policy
2. Contact Zep to disable BYOK for your account

After revocation:

* New data writes will fail immediately
* Existing encrypted data will become inaccessible once cached encryption keys expire
* Your account may be suspended until access is restored or BYOK is disabled

## Audit trail

All KMS operations performed by Zep are logged in your AWS CloudTrail.

You can use CloudTrail to monitor and alert on key usage patterns.

## FAQ

#### Can Zep access my data in plaintext?

Routine operations do not require manual access to plaintext data. Automated services decrypt data within isolated, audited environments. In exceptional cases—such as a customer-approved incident investigation—access is governed by strict separation of duties, multi-party approvals, and comprehensive logging. You retain the ability to disable your CMK, which immediately blocks further decryption.

#### What happens if I disable or delete my CMK?

All encrypted data becomes unreadable. This is by design: the key is the final arbiter of access. Ensure you have internal procedures for emergency restores before disabling or deleting a key.

#### Does BYOK introduce latency?

No. Zep caches derived data encryption keys securely in memory, so encryption and decryption happen without additional round trips to AWS KMS during live traffic.

#### Can I rotate keys without downtime?

Yes. You can enable automatic rotation in AWS KMS. Key versions created through rotation are honored automatically, and data encryption keys are re-wrapped in the background. Disabling the key immediately revokes access.

#### Is BYOK applied to every data store?

Yes. All persistent storage and backups for your tenant use envelope encryption derived from your CMK. Stateless services process data in memory and never persist plaintext content.

#### Where is my data stored?

Customer data remains within `us-west-2`, Zep Cloud's region. Data in motion is encrypted with TLS 1.3, and at rest it is encrypted using keys derived from your CMK.

#### How do I audit KMS activity?

Review the AWS CloudTrail logs generated in your account. Every encrypt, decrypt, and key management action involving your CMK is recorded. Zep maintains corresponding provider-side logs that can be shared under NDA for compliance reviews.

#### Who is responsible for key lifecycle management?

You own the CMK, including rotation, revocation, and IAM policy management. Zep monitors for key state changes and will notify your administrators if a key action affects service availability.

## Support

For assistance with BYOK setup, contact your Zep account team or email [support@getzep.com](mailto:support@getzep.com).