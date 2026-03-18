# Confidential ML Feature Store with Hardware-Isolated Inference

A multi-tenant online feature store built with FastAPI and DynamoDB, designed as the foundation for attested, hardware-isolated inference with AWS Nitro Enclaves.

![Architecture diagram placeholder](architecture.png)

## Overview

Phase 1 delivers a working feature-store core:

- tenant-scoped feature-set CRUD over FastAPI
- tenant authentication via `X-Tenant-ID` and `X-API-Key`
- strict tenant isolation at the API and service layers
- DynamoDB-backed persistence with partitioning by `tenant_id`
- a health endpoint with DynamoDB connectivity checks
- a test suite that runs fully offline with moto

The Nitro Enclaves and KMS integration boundaries remain in the repository and will be expanded in a later phase for confidential inference.

## Why This Project

Machine learning model weights are valuable intellectual property. In many traditional serving architectures, those weights are loaded into processes that run directly on the host operating system, which means a host compromise can expose model artifacts, decrypted keys, or inference internals.

This project moves toward a stronger trust boundary:

- the host API receives tenant requests and orchestrates feature access
- DynamoDB stores tenant-scoped online features
- sensitive inference is intended to run inside an AWS Nitro Enclave
- AWS KMS access is intended to be gated by enclave attestation measurements
- the host communicates with the enclave over vsock rather than loading protected model material directly

The result is a feature-store architecture that can support confidential model serving while already enforcing strong tenant ownership at the storage and API layers.

## Feature List

- **Tenant-scoped feature CRUD**  
  Create, read, list, and delete feature sets for the authenticated tenant.

- **Header-based tenant authentication**  
  Requests must include `X-Tenant-ID` and `X-API-Key`, which are validated against stored tenant records.

- **Tenant isolation enforcement**  
  A tenant cannot read, list, or delete another tenant's feature sets, even if they try to override tenant identifiers in requests.

- **DynamoDB-backed persistence**  
  Feature sets are stored under a composite key of `tenant_id` and `resource_id`.

- **Health and readiness checks**  
  `/health` reports service version and DynamoDB connectivity.

- **Inference preparation hooks**  
  The service layer can prepare deterministic feature vectors for the future enclave-backed inference pipeline.

- **Confidential-computing integration boundary**  
  Enclave, attestation, KMS, and vsock modules remain in place for the next implementation phase.

## Tech Stack

| Tool | Purpose |
| --- | --- |
| Python 3.11+ | Application and enclave-side implementation language |
| FastAPI | Host-side API framework |
| AWS DynamoDB | Tenant-scoped online feature storage |
| AWS EC2 with Nitro Enclaves | Hardware-isolated execution environment for sensitive inference |
| AWS KMS | Attestation-aware decryption and key policy enforcement |
| vsock RPC | Host-to-enclave communication channel |
| scikit-learn | Model loading and inference runtime inside the enclave |
| Docker | Local development and enclave image builds |
| pytest | Test runner for API and service validation |
| moto | Offline AWS mocking for test execution |

## Quick Start

Local development uses Docker Compose plus DynamoDB Local.

### 1. Copy the environment template

```bash
cp .env.example .env
```

### 2. Start local services

```bash
docker compose up --build
```

### 3. Create the local DynamoDB table

Run the setup script against the host-mapped DynamoDB Local port:

```bash
AWS_REGION=us-east-1 \
DYNAMODB_TABLE_NAME=confidential-ml-feature-store \
DYNAMODB_ENDPOINT=http://localhost:8001 \
./scripts/setup_dynamodb.sh
```

### 4. Verify service health

```bash
curl http://localhost:8000/health
```

Expected response:

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

### 5. Create a tenant-scoped feature set

The API expects a tenant record to exist in DynamoDB. In automated tests, tenants are seeded by fixtures. For local manual testing, seed a tenant record first, then call the API with the corresponding headers.

### 6. Open API docs

```text
http://localhost:8000/docs
```

## API Surface

### Working Phase 1 routes

- `GET /health` — service status and DynamoDB connectivity
- `POST /features/` — create a tenant-owned feature set
- `GET /features/` — list feature sets for the authenticated tenant
- `GET /features/{feature_set_name}` — fetch a tenant-owned feature set
- `DELETE /features/{feature_set_name}` — delete a tenant-owned feature set

### Authentication headers

All feature routes require:

- `X-Tenant-ID`
- `X-API-Key`

### Example create request

```bash
curl -X POST http://localhost:8000/features/ \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: tenant-a" \
  -H "X-API-Key: tenant-a-api-key" \
  -d '{
    "tenant_id": "tenant-a",
    "feature_set_name": "customer-profile",
    "features": {
      "age": 34.0,
      "balance": 1200.5
    }
  }'
```

## AWS Deployment

Deployment and environment preparation steps are documented in [docs/SETUP_AWS.md](docs/SETUP_AWS.md).

That guide covers:

- parent instance preparation for Nitro Enclaves
- enclave image builds and PCR capture
- KMS key policy setup for attested decrypt flows
- DynamoDB table creation using the Phase 1 schema
- host API startup and validation

## Security Model

A summarized security model is available in [docs/SECURITY_MODEL.md](docs/SECURITY_MODEL.md).

At a high level, the current implementation already enforces:

- API-key-based tenant authentication
- strict tenant ownership checks before all feature operations
- tenant partitioning in DynamoDB
- denial of cross-tenant read, list, and delete attempts

The next phase extends that foundation into enclave-backed inference and attestation-aware cryptographic access.

## Testing

Run the full test suite with:

```bash
make test
```

Run linting and type checks with:

```bash
make lint
```

The Phase 1 test suite covers:

- feature-set create, read, list, and delete behavior
- tenant isolation enforcement
- invalid API key rejection
- missing-header rejection
- cross-tenant attack attempts against read, list, and delete paths
- health endpoint behavior under a moto-backed DynamoDB table

Tests run without external AWS dependencies by using moto to mock DynamoDB.

## Project Structure

```text
confidential-ml-feature-store/
├── README.md
├── LICENSE
├── .gitignore
├── .env.example
├── architecture.png
├── docs/
│   ├── SETUP_AWS.md
│   ├── ARCHITECTURE.md
│   └── SECURITY_MODEL.md
├── feature_store/
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   ├── models/
│   ├── routers/
│   ├── services/
│   ├── middleware/
│   └── utils/
├── enclave/
│   ├── Dockerfile.enclave
│   ├── server.py
│   ├── inference_engine.py
│   ├── attestation.py
│   ├── kms_client.py
│   └── requirements.txt
├── scripts/
│   ├── build_enclave.sh
│   ├── run_enclave.sh
│   ├── setup_dynamodb.sh
│   ├── setup_kms.sh
│   └── train_model.py
├── tests/
│   ├── conftest.py
│   ├── test_feature_store.py
│   ├── test_kms_attestation.py
│   ├── test_enclave_client.py
│   ├── test_inference.py
│   └── test_tenant_isolation.py
├── docker-compose.yml
├── Makefile
├── pyproject.toml
└── requirements.txt
```

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).
