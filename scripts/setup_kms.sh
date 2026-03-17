#!/usr/bin/env bash
set -euo pipefail

AWS_REGION="${AWS_REGION:-us-east-1}"
KMS_KEY_ALIAS="${KMS_KEY_ALIAS:-alias/confidential-ml-feature-store}"
KMS_ALLOWED_ROLE_ARN="${KMS_ALLOWED_ROLE_ARN:-}"
ENCLAVE_PCR0="${ENCLAVE_PCR0:-}"
KEY_DESCRIPTION="${KEY_DESCRIPTION:-Confidential ML Feature Store attested enclave key}"

if [[ -z "$ENCLAVE_PCR0" ]]; then
  echo "ENCLAVE_PCR0 must be set before creating an attestation-bound KMS policy." >&2
  exit 1
fi

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"

if [[ -z "$KMS_ALLOWED_ROLE_ARN" ]]; then
  KMS_ALLOWED_ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:root"
fi

POLICY_FILE="$(mktemp)"

cat > "$POLICY_FILE" <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "EnableAccountAdministration",
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::${ACCOUNT_ID}:root"
      },
      "Action": "kms:*",
      "Resource": "*"
    },
    {
      "Sid": "AllowAttestedEnclaveCryptography",
      "Effect": "Allow",
      "Principal": {
        "AWS": "${KMS_ALLOWED_ROLE_ARN}"
      },
      "Action": [
        "kms:Decrypt",
        "kms:GenerateDataKey",
        "kms:GenerateRandom"
      ],
      "Resource": "*",
      "Condition": {
        "StringEqualsIgnoreCase": {
          "kms:RecipientAttestation:ImageSha384": "${ENCLAVE_PCR0}"
        }
      }
    }
  ]
}
EOF

KEY_ID="$(
  aws kms create-key \
    --description "$KEY_DESCRIPTION" \
    --policy "file://$POLICY_FILE" \
    --region "$AWS_REGION" \
    --query 'KeyMetadata.KeyId' \
    --output text
)"

if aws kms describe-key --key-id "$KMS_KEY_ALIAS" --region "$AWS_REGION" >/dev/null 2>&1; then
  echo "KMS alias already exists: $KMS_KEY_ALIAS"
else
  aws kms create-alias \
    --alias-name "$KMS_KEY_ALIAS" \
    --target-key-id "$KEY_ID" \
    --region "$AWS_REGION"
fi

rm -f "$POLICY_FILE"

echo "Created KMS key: $KEY_ID"
echo "Alias: $KMS_KEY_ALIAS"
echo "Add PCR1, PCR2, and PCR8 conditions later if you want tighter attestation controls."
