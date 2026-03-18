#!/usr/bin/env bash
set -euo pipefail

AWS_REGION="${AWS_REGION:-us-east-1}"
DYNAMODB_TABLE_NAME="${DYNAMODB_TABLE_NAME:-confidential-ml-feature-store}"
DYNAMODB_BILLING_MODE="${DYNAMODB_BILLING_MODE:-PAY_PER_REQUEST}"
DYNAMODB_ENDPOINT="${DYNAMODB_ENDPOINT:-}"

describe_cmd=(
  aws dynamodb describe-table
  --table-name "$DYNAMODB_TABLE_NAME"
  --region "$AWS_REGION"
)

create_cmd=(
  aws dynamodb create-table
  --table-name "$DYNAMODB_TABLE_NAME"
  --attribute-definitions
    AttributeName=tenant_id,AttributeType=S
    AttributeName=resource_id,AttributeType=S
  --key-schema
    AttributeName=tenant_id,KeyType=HASH
    AttributeName=resource_id,KeyType=RANGE
  --billing-mode "$DYNAMODB_BILLING_MODE"
  --region "$AWS_REGION"
)

if [[ -n "$DYNAMODB_ENDPOINT" ]]; then
  describe_cmd+=(--endpoint-url "$DYNAMODB_ENDPOINT")
  create_cmd+=(--endpoint-url "$DYNAMODB_ENDPOINT")
fi

if "${describe_cmd[@]}" >/dev/null 2>&1; then
  echo "DynamoDB table already exists: $DYNAMODB_TABLE_NAME"
  exit 0
fi

"${create_cmd[@]}"

echo "Created DynamoDB table: $DYNAMODB_TABLE_NAME"
echo "Key schema: tenant_id (HASH), resource_id (RANGE)"
