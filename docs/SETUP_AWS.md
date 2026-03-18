# AWS Setup Guide

This guide documents the current project exactly as it exists today:

- the FastAPI application is `feature_store.main:app`
- the tenant-scoped feature store is backed by `feature_store/services/dynamo_service.py::DynamoDBService`
- authenticated tenant resolution happens in `feature_store/middleware/tenant_auth.py::get_current_tenant`
- inference requests are handled by `feature_store/routers/inference.py::run_inference`
- the host talks to the enclave through `feature_store/services/enclave_client.py::EnclaveClient`
- the enclave listens through `enclave/server.py::VsockRPCServer`
- model loading and prediction happen in `enclave/inference_engine.py::InferenceEngine`

This document has two goals:

1. show how to deploy the project on AWS with Nitro Enclaves
2. show a local development path that mirrors the running screenshots you provided

## Prerequisites

You need the following before starting:

- an AWS account
- AWS CLI v2 configured locally
- an SSH key pair already imported into EC2
- a Nitro-enabled EC2 instance type such as `m5.xlarge` or `c5.xlarge`
- an Amazon Linux 2 AMI
- permission to create EC2 instances, IAM roles, security groups, DynamoDB tables, and KMS keys
- Docker installed on the parent instance for enclave image builds
- Python 3.11 on the development machine or parent instance

## Local Development Reference

The project can be run locally before you move to AWS. The screenshots below show the expected local flow.

### Start Docker Compose

```bash
docker compose up -d
```

![Docker Compose start output](docs/screenshots/docker-compose-start.png)

### Create the DynamoDB table and seed a tenant

The project expects the actual DynamoDB key schema used by `feature_store/services/dynamo_service.py::DynamoDBService`:

- partition key: `tenant_id`
- sort key: `resource_id`

The screenshot below shows the expected local setup shape.

![DynamoDB table setup and tenant insert](docs/screenshots/dynamodb-setup.png)

### Verify the health endpoint

```bash
curl http://localhost:8000/health
```

![Health check output](docs/screenshots/health-check.png)

### Create features and run inference locally

The screenshot below shows the working local request flow:

- `POST /features/`
- `POST /inference/`

![Feature creation and inference requests](docs/screenshots/curl-endpoints.png)

## Step 1: Launch an EC2 Parent Instance

Use a Nitro Enclaves-capable instance. The example below uses `m5.xlarge`. If you prefer `c5.xlarge`, change `INSTANCE_TYPE` before running the launch command.

### 1.1 Resolve the latest Amazon Linux 2 AMI ID

```bash
export AWS_REGION=us-east-1
export AMI_ID="$(aws ssm get-parameter \
  --name /aws/service/ami-amazon-linux-latest/amzn2-ami-kernel-5.10-hvm-x86_64-gp2 \
  --query 'Parameter.Value' \
  --output text \
  --region "$AWS_REGION")"
echo "$AMI_ID"
```

### 1.2 Create a security group limited to your IP

```bash
export VPC_ID="$(aws ec2 describe-vpcs \
  --filters Name=isDefault,Values=true \
  --query 'Vpcs[0].VpcId' \
  --output text \
  --region "$AWS_REGION")"

export MY_IP="<YOUR_PUBLIC_IP>/32"
export SG_NAME="confidential-ml-feature-store-sg"

export SG_ID="$(aws ec2 create-security-group \
  --group-name "$SG_NAME" \
  --description "Security group for Confidential ML Feature Store" \
  --vpc-id "$VPC_ID" \
  --query 'GroupId' \
  --output text \
  --region "$AWS_REGION")"

aws ec2 authorize-security-group-ingress \
  --group-id "$SG_ID" \
  --ip-permissions "[
    {
      \"IpProtocol\": \"tcp\",
      \"FromPort\": 22,
      \"ToPort\": 22,
      \"IpRanges\": [{\"CidrIp\": \"${MY_IP}\", \"Description\": \"SSH from my IP\"}]
    },
    {
      \"IpProtocol\": \"tcp\",
      \"FromPort\": 8000,
      \"ToPort\": 8000,
      \"IpRanges\": [{\"CidrIp\": \"${MY_IP}\", \"Description\": \"Feature store API from my IP\"}]
    }
  ]" \
  --region "$AWS_REGION"
```

### 1.3 Create the EC2 IAM role and instance profile

The current project needs:

- `kms:Encrypt`
- `kms:Decrypt`
- `kms:GenerateDataKey`
- `dynamodb:*`
- `logs:*`

Create the trust policy:

```bash
cat > ec2-trust-policy.json <<'JSON'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "ec2.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
JSON
```

Create the role:

```bash
export ROLE_NAME="confidential-ml-feature-store-ec2-role"

aws iam create-role \
  --role-name "$ROLE_NAME" \
  --assume-role-policy-document file://ec2-trust-policy.json
```

Attach the inline permissions policy:

```bash
cat > ec2-inline-policy.json <<'JSON'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowKMSOperations",
      "Effect": "Allow",
      "Action": [
        "kms:Encrypt",
        "kms:Decrypt",
        "kms:GenerateDataKey"
      ],
      "Resource": "*"
    },
    {
      "Sid": "AllowDynamoDBOperations",
      "Effect": "Allow",
      "Action": [
        "dynamodb:*"
      ],
      "Resource": "*"
    },
    {
      "Sid": "AllowCloudWatchLogs",
      "Effect": "Allow",
      "Action": [
        "logs:*"
      ],
      "Resource": "*"
    }
  ]
}
JSON

aws iam put-role-policy \
  --role-name "$ROLE_NAME" \
  --policy-name confidential-ml-feature-store-inline \
  --policy-document file://ec2-inline-policy.json
```

Create and attach the instance profile:

```bash
export INSTANCE_PROFILE_NAME="confidential-ml-feature-store-ec2-profile"

aws iam create-instance-profile \
  --instance-profile-name "$INSTANCE_PROFILE_NAME"

aws iam add-role-to-instance-profile \
  --instance-profile-name "$INSTANCE_PROFILE_NAME" \
  --role-name "$ROLE_NAME"

sleep 10
```

### 1.4 Launch the EC2 instance with enclaves enabled

```bash
export INSTANCE_TYPE="m5.xlarge"
export KEY_NAME="<YOUR_EXISTING_EC2_KEYPAIR_NAME>"

export INSTANCE_ID="$(aws ec2 run-instances \
  --image-id "$AMI_ID" \
  --count 1 \
  --instance-type "$INSTANCE_TYPE" \
  --key-name "$KEY_NAME" \
  --security-group-ids "$SG_ID" \
  --iam-instance-profile Name="$INSTANCE_PROFILE_NAME" \
  --enclave-options 'Enabled=true' \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=confidential-ml-feature-store}]' \
  --query 'Instances[0].InstanceId' \
  --output text \
  --region "$AWS_REGION")"

aws ec2 wait instance-running \
  --instance-ids "$INSTANCE_ID" \
  --region "$AWS_REGION"

export PUBLIC_DNS="$(aws ec2 describe-instances \
  --instance-ids "$INSTANCE_ID" \
  --query 'Reservations[0].Instances[0].PublicDnsName' \
  --output text \
  --region "$AWS_REGION")"

echo "Instance ID: $INSTANCE_ID"
echo "Public DNS:  $PUBLIC_DNS"
echo "SSH command: ssh -i <path-to-key.pem> ec2-user@${PUBLIC_DNS}"
```

## Step 2: Install the Nitro Enclaves CLI

SSH into the instance and run the following commands on Amazon Linux 2.

### 2.1 Install Docker

```bash
sudo yum update -y
sudo amazon-linux-extras install docker -y
sudo service docker start
sudo usermod -a -G docker ec2-user
```

Log out and reconnect after adding `ec2-user` to the `docker` group.

### 2.2 Install the Nitro Enclaves CLI and development tools

```bash
sudo amazon-linux-extras install aws-nitro-enclaves-cli -y
sudo yum install aws-nitro-enclaves-cli-devel -y
sudo usermod -aG ne ec2-user
sudo usermod -aG docker ec2-user
```

Log out and reconnect again so both `ne` and `docker` group membership are active.

### 2.3 Verify the install

```bash
nitro-cli --version
```

### 2.4 Configure the allocator

The current `scripts/run_enclave.sh` defaults to:

- `ENCLAVE_CPU_COUNT=2`
- `ENCLAVE_MEMORY_MIB=512`

Match the allocator to those defaults:

```bash
sudo tee /etc/nitro_enclaves/allocator.yaml >/dev/null <<'YAML'
memory_mib: 512
cpu_count: 2
YAML
```

Enable the allocator:

```bash
sudo systemctl enable --now nitro-enclaves-allocator.service
sudo systemctl enable --now docker
```

## Step 3: Set Up the DynamoDB Table

The project already includes the correct table-creation script:

- `scripts/setup_dynamodb.sh`

Copy the repository to the instance and run:

```bash
git clone <YOUR_REPOSITORY_URL> confidential-ml-feature-store
cd confidential-ml-feature-store
cp .env.example .env
```

Create the table:

```bash
./scripts/setup_dynamodb.sh
```

This script creates the actual key schema used by the running code:

- `tenant_id` as `HASH`
- `resource_id` as `RANGE`

### 3.1 Seed a tenant record

Authenticated routes require a tenant record to exist. The item shape must match `feature_store/models/tenant.py::Tenant` and `feature_store/services/dynamo_service.py::get_tenant_record`.

```bash
python3 - <<'PY'
from datetime import datetime, timezone
import boto3

table = boto3.resource("dynamodb", region_name="us-east-1").Table("confidential-ml-feature-store")
table.put_item(
    Item={
        "tenant_id": "tenant-1",
        "resource_id": "TENANT#tenant-1",
        "entity_type": "TENANT",
        "api_key": "test-key-1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "is_active": True,
        "allowed_models": ["test-model"],
    }
)
print("Seeded tenant-1")
PY
```

## Step 4: Set Up KMS

The repository already includes the KMS policy setup script:

- `scripts/setup_kms.sh`

That script expects a PCR0 value through `ENCLAVE_PCR0_HASH` (or `ENCLAVE_PCR0`) and creates a policy condition on `kms:RecipientAttestation:PCR0`.

### 4.1 Build once to capture PCR0

Run the actual build script first so it can print the measured PCR0 hash:

```bash
./scripts/build_enclave.sh
```

The script prints output similar to:

```text
EIF build complete: enclave.eif
PCR0: <lowercase-hex-measurement>
```

Export that value before creating the key:

```bash
export ENCLAVE_PCR0_HASH="<PASTE_THE_PCR0_VALUE_FROM_BUILD_OUTPUT>"
export KMS_ALLOWED_ROLE_ARN="arn:aws:iam::<ACCOUNT_ID>:role/enclave-role"
./scripts/setup_kms.sh
```

The script prints:

- KMS key ID
- KMS key ARN
- alias
- allowed IAM role ARN
- required PCR0 measurement

## Step 5: Build and Run the Enclave

### 5.1 Build the EIF

Use the repository's actual build script:

```bash
./scripts/build_enclave.sh
```

This wraps:

- `docker build -f enclave/Dockerfile.enclave -t enclave-image:latest .`
- `nitro-cli build-enclave --docker-uri enclave-image:latest --output-file enclave.eif`

### 5.2 Run the enclave

Use the repository's actual run script:

```bash
./scripts/run_enclave.sh
```

This wraps:

- `nitro-cli run-enclave --eif-path enclave.eif --cpu-count 2 --memory 512 --enclave-cid 16 --enclave-name confidential-ml-inference`
- startup of the Nitro Enclaves KMS vsock proxy service if it is installed

### 5.3 Verify the enclave is running

```bash
nitro-cli describe-enclaves
```

You should see the enclave ID, CID, CPU allocation, and memory allocation.

### 5.4 Read enclave logs

The enclave writes structured JSON logs to stdout. Read them with:

```bash
nitro-cli console --enclave-name confidential-ml-inference
```

## Step 6: Run the Feature Store

### 6.1 Install Python dependencies

Use the Makefile from this repository:

```bash
make install
```

### 6.2 Configure `.env`

Use the corrected environment file:

```bash
cp .env.example .env
```

A typical AWS deployment `.env` looks like:

```dotenv
AWS_REGION=us-east-1
DYNAMODB_TABLE_NAME=confidential-ml-feature-store
DYNAMODB_ENDPOINT=
KMS_KEY_ID=<YOUR_KMS_KEY_ID>
ENCLAVE_CID=16
ENCLAVE_PORT=5005
LOG_LEVEL=INFO
USE_MOCK_ENCLAVE=false
```

### 6.3 Start the FastAPI application

```bash
uvicorn feature_store.main:app --host 0.0.0.0 --port 8000 --reload
```

### 6.4 Test the health route

The current health endpoint is exactly:

- `GET /health`

```bash
curl http://127.0.0.1:8000/health
```

Expected response shape:

```json
{
  "status": "ok",
  "version": "0.1.0",
  "dynamodb": {
    "reachable": true,
    "table_name": "confidential-ml-feature-store"
  }
}
```

### 6.5 Create a feature set

The current feature-create endpoint is exactly:

- `POST /features/`

It accepts the `FeatureSetCreate` schema:

```json
{
  "tenant_id": "tenant-1",
  "feature_set_name": "test-features",
  "features": {
    "sepal_length": 5.1,
    "sepal_width": 3.5,
    "petal_length": 1.4,
    "petal_width": 0.2
  }
}
```

Example request:

```bash
curl -X POST http://127.0.0.1:8000/features/ \
  -H "X-Tenant-ID: tenant-1" \
  -H "X-API-Key: test-key-1" \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "tenant-1",
    "feature_set_name": "test-features",
    "features": {
      "sepal_length": 5.1,
      "sepal_width": 3.5,
      "petal_length": 1.4,
      "petal_width": 0.2
    }
  }'
```

Expected response shape:

```json
{
  "tenant_id": "tenant-1",
  "feature_set_name": "test-features",
  "features": {
    "sepal_length": 5.1,
    "sepal_width": 3.5,
    "petal_length": 1.4,
    "petal_width": 0.2
  },
  "created_at": "2026-03-18T13:03:36.783871Z",
  "updated_at": "2026-03-18T13:03:36.783871Z",
  "version": 1
}
```

### 6.6 List the feature sets for the tenant

```bash
curl http://127.0.0.1:8000/features/ \
  -H "X-Tenant-ID: tenant-1" \
  -H "X-API-Key: test-key-1"
```

### 6.7 Get a single feature set

```bash
curl http://127.0.0.1:8000/features/test-features \
  -H "X-Tenant-ID: tenant-1" \
  -H "X-API-Key: test-key-1"
```

## Step 7: Train and Deploy a Model

The repository includes the actual training script:

- `scripts/train_model.py`

It trains a `RandomForestClassifier`, serializes it with `joblib`, generates a KMS data key, AES-GCM encrypts the model bytes, and writes:

- `encrypted_model.bin`
- `encrypted_data_key.bin`

### 7.1 Train and write an encrypted model bundle

```bash
python scripts/train_model.py \
  --tenant-id tenant-1 \
  --model-name test-model \
  --output-dir artifacts \
  --kms-key-id "$KMS_KEY_ID" \
  --aws-region "$AWS_REGION"
```

The files will be written under:

```text
artifacts/tenant-1/test-model/encrypted_model.bin
artifacts/tenant-1/test-model/encrypted_data_key.bin
```

### 7.2 Call the inference endpoint

The current inference route is exactly:

- `POST /inference/`

It accepts the `InferenceRequest` schema:

```json
{
  "tenant_id": "tenant-1",
  "feature_set_name": "test-features",
  "model_name": "test-model"
}
```

Example request:

```bash
curl -X POST http://127.0.0.1:8000/inference/ \
  -H "X-Tenant-ID: tenant-1" \
  -H "X-API-Key: test-key-1" \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "tenant-1",
    "feature_set_name": "test-features",
    "model_name": "test-model"
  }'
```

Expected response shape:

```json
{
  "prediction": 2.0,
  "confidence": 0.94,
  "latency_ms": 911.79,
  "served_from_cache": false
}
```

### 7.3 List available and loaded models

The current model-list route is exactly:

- `GET /inference/models`

```bash
curl http://127.0.0.1:8000/inference/models \
  -H "X-Tenant-ID: tenant-1" \
  -H "X-API-Key: test-key-1"
```

Expected response shape:

```json
{
  "available_models": ["test-model"],
  "loaded_models": ["test-model"]
}
```

## Troubleshooting

### `401 unauthorized_access`

Cause:
- missing `X-Tenant-ID`
- missing `X-API-Key`
- tenant record missing from DynamoDB
- API key mismatch

Fix:
- verify the tenant item exists
- verify the `api_key` value matches exactly
- verify the request includes both headers

### `403 forbidden_access`

Cause:
- the request tries to access another tenant's feature set through `tenant_id` override

Fix:
- send the authenticated tenant's own `tenant_id`
- do not pass another tenant's `tenant_id` to `/features/` or `/inference/`

### `404 model_artifact_not_found`

Cause:
- `feature_store/routers/inference.py::_load_model_artifacts()` could not find:
  - `encrypted_model.bin`
  - `encrypted_data_key.bin`

Fix:
- place files under `MODEL_STORAGE_DIR/<tenant_id>/<model_name>/`
- verify the directory names match the request exactly

### `502 enclave_communication_error`

Cause:
- the app is trying to use `feature_store/services/enclave_client.py::EnclaveClient`
- the enclave is not running
- vsock is not available
- the KMS vsock proxy is not running

Fix:
- verify `nitro-cli describe-enclaves`
- verify `nitro-cli console --enclave-name confidential-ml-inference`
- verify `sudo systemctl status nitro-enclaves-vsock-proxy.service`

### Local inference returns `502` instead of a prediction

Cause:
- `USE_MOCK_ENCLAVE` is not enabled for the running FastAPI process

Fix:
- for local development, start the app with:
  ```bash
  DYNAMODB_ENDPOINT=http://localhost:8001 USE_MOCK_ENCLAVE=true uvicorn feature_store.main:app --reload
  ```

### `feature_dimension_mismatch`

Cause:
- the posted feature set does not match the model's expected number of inputs

Fix:
- verify the feature set contains the same number of ordered numeric values expected by the model
- for the sample Iris-style model, use four numeric features

### `scripts/setup_kms.sh` fails because `ENCLAVE_PCR0_HASH` is empty

Cause:
- the enclave has not been built yet, or the PCR0 value was not exported

Fix:
- run `./scripts/build_enclave.sh`
- copy the printed PCR0 hash
- export it:
  ```bash
  export ENCLAVE_PCR0_HASH="<PCR0_FROM_BUILD_OUTPUT>"
  ```
- rerun `./scripts/setup_kms.sh`
