# Confidential ML Feature Store with Hardware-Isolated Inference

A multi-tenant feature store scaffold built with FastAPI and AWS Nitro Enclaves to protect model-serving secrets and keep inference workloads isolated from the host operating system.

![Architecture diagram placeholder](architecture.png)

## Overview

This repository contains the project skeleton for a confidential machine learning serving platform. The application surface, enclave boundary, documentation, scripts, and configuration templates are in place, while business logic and enclave RPC implementation are intentionally deferred to later phases.

## Why This Project

Machine learning model weights are valuable intellectual property. In many traditional serving architectures, those weights are loaded into processes that run directly on the host operating system, which means a host compromise can expose model artifacts, decrypted keys, or inference internals.

This project explores a stronger trust boundary:

- The host API receives tenant requests and orchestrates feature access.
- Sensitive inference runs inside an AWS Nitro Enclave.
- AWS KMS access is intended to be gated by enclave attestation measurements.
- The host communicates with the enclave over vsock RPC rather than loading protected model material directly.

The goal is to demonstrate how hardware-isolated inference can reduce exposure for model weights, encryption keys, and tenant-sensitive inference paths.

## Feature List

- **FastAPI service scaffold**  
  Structured API layout for feature ingestion, retrieval, inference triggers, and health checks.

- **Nitro Enclave integration boundary**  
  Dedicated enclave-side module layout for attestation, model loading, KMS interaction, and vsock RPC handling.

- **Attestation-aware KMS design**  
  Config and script scaffolding for binding cryptographic operations to enclave PCR measurements.

- **Tenant isolation hooks**  
  Middleware and request models organized around per-tenant access boundaries.

- **DynamoDB-backed feature store layout**  
  Service and provisioning script placeholders for tenant-scoped feature persistence.

- **Security-focused project documentation**  
  Architecture, AWS setup, and security model documents included from day one.

- **Test scaffold**  
  Pytest structure covering API wiring, tenant-boundary enforcement, enclave client contracts, and attestation integration points.

## Tech Stack

| Tool | Purpose |
| --- | --- |
| Python 3.11+ | Application and enclave-side implementation language |
| FastAPI | Host-side API framework |
| AWS EC2 with Nitro Enclaves | Hardware-isolated execution environment for sensitive inference |
| AWS KMS | Attestation-aware decryption and key policy enforcement |
| DynamoDB | Tenant-scoped online feature storage |
| vsock RPC | Host-to-enclave communication channel |
| scikit-learn | Model loading and inference runtime inside the enclave |
| Docker | Enclave image build source and local development container runtime |
| pytest | Test runner for API, service, and isolation checks |
| moto | AWS mocking strategy for later unit and integration phases |

## Quick Start

Local development focuses on the API scaffold and DynamoDB Local. It does not emulate Nitro Enclaves or attested KMS flows.

### 1. Copy the environment template

```bash
cp .env.example .env
```

### 2. Start local services

```bash
docker compose up --build
```

### 3. Verify the API is up

```bash
curl http://localhost:8000/healthz
```

Expected response:

```json
{
  "status": "ok",
  "service": "feature-store-api",
  "environment": "local"
}
```

### 4. Open API docs

```text
http://localhost:8000/docs
```

### Notes

- `GET /healthz` is implemented as a basic scaffold health endpoint.
- Feature and inference endpoints are wired but intentionally return `501 Not Implemented` until application logic is added.
- DynamoDB Local runs in Docker for local-only persistence testing.

## AWS Deployment

Deployment and environment preparation steps are documented in [docs/SETUP_AWS.md](docs/SETUP_AWS.md).

That guide covers:

- Parent instance preparation for Nitro Enclaves
- Enclave image builds and PCR capture
- KMS key policy setup for attested decrypt flows
- DynamoDB table creation
- Host and enclave process startup sequence

## Security Model

A summarized security model is available in [docs/SECURITY_MODEL.md](docs/SECURITY_MODEL.md).

At a high level, this design aims to:

- Keep protected model material out of the host API process
- Restrict KMS access to attested enclave workloads
- Preserve tenant boundaries at the API layer
- Minimize trust in the parent instance during inference execution

## Testing

Run the test suite with:

```bash
make test
```

Or directly:

```bash
pytest
```

The current scaffold tests cover:

- API health route availability
- Placeholder feature CRUD endpoint wiring
- Placeholder inference endpoint wiring
- Tenant isolation middleware behavior
- Enclave client and KMS service skeleton contracts

As implementation is added, this suite can expand into:

- moto-backed DynamoDB tests
- attestation policy validation tests
- end-to-end host-to-enclave RPC tests
- inference correctness and regression tests

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
