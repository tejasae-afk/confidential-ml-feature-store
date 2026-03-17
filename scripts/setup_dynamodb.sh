#!/usr/bin/env bash
set -euo pipefail

AWS_REGION="${AWS_REGION:-us-east-1}"
DYNAMODB_TABLE="${DYNAMODB_TABLE:-confidential-ml-features}"
DYNAMODB_BILLING_MODE="${DYNAMODB_BILLING_MODE:-PAY_PER_REQUEST}"
DYNAMODB_ENDPOINT_URL="${DYNAMODB_ENDPOINT_URL:-}"

describe_cmd=(
  aws dynamodb describe-table
  --table-name "$DYNAMODB_TABLE"
  --region "$AWS_REGION"
)

create_cmd=(
  aws dynamodb create-table
  --table-name "$DYNAMODB_TABLE"
  --attribute-definitions
    AttributeName=pk,AttributeType=S
    AttributeName=sk,AttributeType=S
  --key-schema
    AttributeName=pk,KeyType=HASH
    AttributeName=sk,KeyType=RANGE
  --billing-mode "$DYNAMODB_BILLING_MODE"
  --region "$AWS_REGION"
)

if [[ -n "$DYNAMODB_ENDPOINT_URL" ]]; then
  describe_cmd+=(--endpoint-url "$DYNAMODB_ENDPOINT_URL")
  create_cmd+=(--endpoint-url "$DYNAMODB_ENDPOINT_URL")
fi

if "${describe_cmd[@]}" >/dev/null 2>&1; then
  echo "DynamoDB table already exists: $DYNAMODB_TABLE"
  exit 0
fi

"${create_cmd[@]}"

echo "Created DynamoDB table: $DYNAMODB_TABLE"
