# AWS Setup Guide

This document describes the intended AWS deployment flow for the project. The repository currently ships as a scaffold, so these steps prepare the environment and infrastructure boundaries even though core application logic is still pending.

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

- Keep administrative control in the owning AWS account
- Allow cryptographic operations only for the intended principal
- Bind usage to enclave attestation measurements

If you plan to require additional PCR constraints such as `PCR1`, `PCR2`, or `PCR8`, extend the policy before using the key in production.

## 6. Create the DynamoDB table

```bash
./scripts/setup_dynamodb.sh
```

The initial scaffold uses a simple composite primary key layout suitable for tenant- and entity-scoped feature records.

## 7. Launch the enclave

After the EIF is built and Nitro Enclaves resources are preallocated on the parent instance, run the enclave:

```bash
./scripts/run_enclave.sh
```

Useful verification commands:

```bash
nitro-cli describe-enclaves
```

If debug mode is enabled, you can inspect the enclave console with Nitro CLI. Use debug mode only for development, because attestation measurements differ from production expectations.

## 8. Start the host API

```bash
source .venv/bin/activate
uvicorn feature_store.main:app --host 0.0.0.0 --port 8000
```

At this stage:

- `/healthz` should respond successfully
- Feature and inference routes are scaffolded
- Service implementations for DynamoDB, KMS, and vsock RPC are still intentionally incomplete

## 9. Validate the environment

Deployment validation checklist:

1. The parent instance has Nitro Enclaves enabled.
2. The enclave is visible in `nitro-cli describe-enclaves`.
3. The recorded PCR values match the ones configured for KMS access.
4. The DynamoDB table exists in the target region.
5. The FastAPI service can start with the configured environment.
6. The health route responds from the host API.

## Troubleshooting Notes

### EIF build issues

- Confirm Docker is running.
- Confirm Nitro CLI is installed.
- Confirm the build uses a Linux environment.

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
