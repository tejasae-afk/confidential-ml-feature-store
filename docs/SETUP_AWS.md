# AWS Setup Guide

This document describes the intended AWS deployment flow for the current repository state. Phase 1 includes a working, tenant-isolated feature-store core backed by DynamoDB, while enclave inference, attestation, and KMS recipient flows remain reserved for later phases.

## Prerequisites

- An AWS account with permissions for EC2, IAM, KMS, DynamoDB, and CloudWatch
- A Linux-based EC2 parent instance configured for Nitro Enclaves
- Docker installed on the parent instance
- AWS CLI installed and configured
- Nitro CLI installed on the parent instance
- Python 3.11+ available on the parent instance or a deployment host
- This repository cloned onto the machine used for deployment

## 1. Prepare the parent instance

Launch an EC2 instance that supports Nitro Enclaves and enable enclave support at launch time.

Operational guidance:

- Reserve enough memory and vCPUs for both the parent instance and the enclave.
- Use a least-privilege IAM role for the host API process.
- Keep the parent instance image minimal and patched.
- Disable access paths that are not required for operations.

## 2. Clone the repository

```bash
git clone <your-repository-url> confidential-ml-feature-store
cd confidential-ml-feature-store
cp .env.example .env
```

Update `.env` with your AWS region, table name, KMS key information, and enclave runtime settings.

The Phase 1 application reads the following core variables from `.env`:

- `AWS_REGION`
- `DYNAMODB_TABLE_NAME`
- `DYNAMODB_ENDPOINT`
- `KMS_KEY_ID`
- `ENCLAVE_CID`
- `ENCLAVE_PORT`
- `LOG_LEVEL`

## 3. Install local Python dependencies

```bash
python3.11 -m venv .venv
source .venv/bin/activate
make install
```

## 4. Build the enclave image file

Build the enclave Docker image and convert it into an EIF.

```bash
./scripts/build_enclave.sh
```

Expected outcome:

- A Docker image is built from `enclave/Dockerfile.enclave`
- An EIF is created at the configured output path
- Nitro CLI prints PCR measurements for the EIF

Record the emitted measurements:

- `PCR0`
- `PCR1`
- `PCR2`
- `PCR8` if the EIF is signed

Store those measurements in `.env`, a secure parameter store, or deployment automation inputs.

## 5. Create the KMS key policy for attested access

Export the PCR values and any deployment-specific role ARN before running the KMS setup script.

```bash
export ENCLAVE_PCR0=<captured-pcr0>
export KMS_ALLOWED_ROLE_ARN=arn:aws:iam::<account-id>:role/<host-api-role>
./scripts/setup_kms.sh
```

The generated policy is designed to:

- keep administrative control in the owning AWS account
- allow cryptographic operations only for the intended principal
- bind usage to enclave attestation measurements

If you plan to require additional PCR constraints such as `PCR1`, `PCR2`, or `PCR8`, extend the policy before using the key in production.

## 6. Create the DynamoDB table

```bash
./scripts/setup_dynamodb.sh
```

Phase 1 uses this composite primary key layout:

- partition key: `tenant_id`
- sort key: `resource_id`

Feature-set items use a `FEATURE_SET#` prefix in `resource_id`, and tenant records use a `TENANT#` prefix.

## 7. Seed tenant records

Feature routes require valid tenant credentials stored in DynamoDB. Seed at least one tenant record before calling the authenticated API.

Required tenant item shape:

```json
{
  "tenant_id": "tenant-a",
  "resource_id": "TENANT#tenant-a",
  "entity_type": "TENANT",
  "api_key": "tenant-a-api-key",
  "created_at": "2026-03-17T00:00:00+00:00",
  "is_active": true,
  "allowed_models": ["fraud-model"]
}
```

## 8. Launch the enclave

After the EIF is built and Nitro Enclaves resources are preallocated on the parent instance, run the enclave:

```bash
./scripts/run_enclave.sh
```

Useful verification commands:

```bash
nitro-cli describe-enclaves
```

If debug mode is enabled, you can inspect the enclave console with Nitro CLI. Use debug mode only for development, because attestation measurements differ from production expectations.

## 9. Start the host API

```bash
source .venv/bin/activate
uvicorn feature_store.main:app --host 0.0.0.0 --port 8000
```

At this stage:

- `/health` should respond successfully
- `/features/` and `/features/{feature_set_name}` are operational for authenticated tenants
- the service validates `X-Tenant-ID` and `X-API-Key` against stored tenant records
- the inference route remains a placeholder until the enclave-serving phase is implemented

## 10. Validate the environment

Deployment validation checklist:

1. The parent instance has Nitro Enclaves enabled.
2. The enclave is visible in `nitro-cli describe-enclaves`.
3. The recorded PCR values match the ones configured for KMS access.
4. The DynamoDB table exists in the target region with `tenant_id` and `resource_id` as keys.
5. Tenant records exist for any tenants you want to authenticate.
6. The FastAPI service can start with the configured environment.
7. The `/health` route responds from the host API.
8. Authenticated feature CRUD requests succeed for the owning tenant and fail for cross-tenant access attempts.

## Troubleshooting Notes

### DynamoDB schema issues

- Confirm the table uses `tenant_id` as the partition key.
- Confirm the table uses `resource_id` as the sort key.
- Confirm you are writing feature sets with the `FEATURE_SET#` prefix and tenant records with the `TENANT#` prefix.

### KMS permission issues

- Verify the correct IAM principal is listed in the key policy.
- Verify the PCR values were copied exactly.
- Verify the deployment is using the expected region and key alias or key ID.

### Enclave launch issues

- Confirm enough memory and vCPUs are reserved for the enclave.
- Confirm the EIF path exists.
- Confirm the parent instance was launched with enclave support enabled.

## Related Documents

- [ARCHITECTURE.md](ARCHITECTURE.md)
- [SECURITY_MODEL.md](SECURITY_MODEL.md)
