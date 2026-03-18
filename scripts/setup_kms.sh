#!/usr/bin/env bash
set -euo pipefail

AWS_REGION="${AWS_REGION:-us-east-1}"
KMS_KEY_ALIAS="${KMS_KEY_ALIAS:-alias/confidential-ml-feature-store}"
KMS_ALLOWED_ROLE_ARN="${KMS_ALLOWED_ROLE_ARN:-}"
ENCLAVE_PCR0_HASH="${ENCLAVE_PCR0_HASH:-${ENCLAVE_PCR0:-}}"
KEY_DESCRIPTION="${KEY_DESCRIPTION:-Confidential ML Feature Store attested enclave key}"

if [[ -z "$ENCLAVE_PCR0_HASH" ]]; then
  echo "ENCLAVE_PCR0_HASH (or ENCLAVE_PCR0) must be set before creating the attested KMS policy." >&2
  exit 1
fi

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text --region "$AWS_REGION")"

# Default to the conventional enclave role ARN requested by the project spec.
if [[ -z "$KMS_ALLOWED_ROLE_ARN" ]]; then
  KMS_ALLOWED_ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/enclave-role"
fi

POLICY_FILE="$(mktemp)"

# Policy statement guide:
# 1. EnableAccountAdministration
#    Grants the owning AWS account full administrative control over the key.
#    This is required so operators can manage aliases, rotation, policy updates,
#    and recovery actions.
# 2. AllowAttestedEnclaveDecrypt
#    Allows kms:Decrypt only for the designated IAM role and only when the
#    request includes a valid attestation document whose PCR0 value matches the
#    expected enclave image measurement.
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
      "Sid": "AllowAttestedEnclaveDecrypt",
      "Effect": "Allow",
      "Principal": {
        "AWS": "${KMS_ALLOWED_ROLE_ARN}"
      },
      "Action": [
        "kms:Decrypt"
      ],
      "Resource": "*",
      "Condition": {
        "StringEqualsIgnoreCase": {
          "kms:RecipientAttestation:PCR0": "${ENCLAVE_PCR0_HASH}"
        }
      }
    }
  ]
}
EOF

CREATE_RESPONSE="$(
  aws kms create-key \
    --description "$KEY_DESCRIPTION" \
    --policy "file://${POLICY_FILE}" \
    --region "$AWS_REGION"
)"

KEY_ID="$(printf '%s' "$CREATE_RESPONSE" | python3 -c 'import json,sys; print(json.load(sys.stdin)["KeyMetadata"]["KeyId"])')"
KEY_ARN="$(printf '%s' "$CREATE_RESPONSE" | python3 -c 'import json,sys; print(json.load(sys.stdin)["KeyMetadata"]["Arn"])')"

if aws kms describe-key --key-id "$KMS_KEY_ALIAS" --region "$AWS_REGION" >/dev/null 2>&1; then
  echo "KMS alias already exists: $KMS_KEY_ALIAS"
else
  aws kms create-alias \
    --alias-name "$KMS_KEY_ALIAS" \
    --target-key-id "$KEY_ID" \
    --region "$AWS_REGION" >/dev/null
fi

rm -f "$POLICY_FILE"

echo "Created KMS key ID: $KEY_ID"
echo "Created KMS key ARN: $KEY_ARN"
echo "Created/verified alias: $KMS_KEY_ALIAS"
echo "Allowed IAM role ARN: $KMS_ALLOWED_ROLE_ARN"
echo "Required PCR0 measurement: $ENCLAVE_PCR0_HASH"
