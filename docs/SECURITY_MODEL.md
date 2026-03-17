# Security Model

This document captures the target security model for the project scaffold. It distinguishes between what is already represented structurally in the repository and what will be implemented in later phases.

## Security Goals

The system is intended to protect:

- Proprietary model weights
- Decryption material used to unlock protected model artifacts
- Tenant-scoped feature data
- Inference execution paths that should remain isolated from a compromised host environment

## Core Assumptions

- The host API process is not fully trusted for long-lived access to protected model material.
- AWS Nitro Enclaves provide the primary isolation boundary for sensitive inference execution.
- AWS KMS policies can be used to tie cryptographic permissions to enclave attestation measurements.
- IAM identity alone is not sufficient for the highest-value decryption paths.
- Tenant identity must be preserved through request handling and service orchestration.

## Threat Model

### In scope

- Host operating system compromise exposing plaintext model artifacts
- Accidental loading of sensitive model material into the parent instance process
- Cross-tenant access caused by weak request scoping
- Misuse of KMS keys from non-attested environments
- Over-privileged host-side IAM roles

### Partially in scope for later phases

- Replay resistance in the vsock RPC protocol
- Request signing between host and enclave
- Response integrity guarantees for enclave outputs
- Detection of configuration drift in PCR-bound policies
- Audit correlation across API, KMS, and enclave lifecycle events

### Out of scope for the scaffold phase

- Full production identity federation
- Runtime anti-abuse controls such as quotas and rate limiting
- Formal verification of enclave RPC protocol semantics
- Supply-chain attestation for all dependencies

## Planned Security Controls

### 1. Hardware-isolated inference

Sensitive inference should execute inside a Nitro Enclave rather than in the host API process. The target outcome is that plaintext model material exists only inside enclave memory during execution.

### 2. Attestation-bound KMS access

KMS access is intended to require attestation-linked conditions. In practice, that means KMS permissions should depend on enclave measurements such as PCR values or image digest values, not just IAM principal identity.

### 3. Host and enclave separation

The host process should orchestrate requests and pass only the required payload over vsock. The host should not perform direct inference with protected assets.

### 4. Tenant boundary enforcement

Tenant context must be explicit in request handling. The scaffold includes middleware and route-level hooks for tenant ID propagation and denial of obvious cross-tenant access attempts.

### 5. Least-privilege IAM

The parent instance role should receive only the permissions required for:

- DynamoDB access
- KMS operations needed for the deployment path
- Observability and operational diagnostics that are explicitly approved

### 6. Auditability

The target design should emit logs and audit signals that allow operators to answer:

- Which tenant initiated a request
- Which enclave measurement was used
- Which KMS key was involved
- Which host API path initiated the enclave request
- Whether a request was allowed or denied

## Trust Boundaries

| Boundary | Trust Level | Notes |
| --- | --- | --- |
| External client to host API | Low | Public or semi-public request surface |
| Host API to DynamoDB | Medium | Controlled by IAM, network policy, and application validation |
| Host API to enclave via vsock | Medium | Controlled inter-process boundary; protocol hardening still pending |
| Enclave to KMS | High-value boundary | Intended to be protected by attestation-aware policy evaluation |
| Enclave runtime memory | Highest trust zone in this design | Intended location for protected model material |

## Current Scaffold Coverage

Present in the scaffold:

- Dedicated enclave-side module boundaries
- Tenant middleware hooks
- KMS service abstraction
- Enclave client abstraction
- Security documentation and setup scripts
- Placeholder tests for tenant isolation and attestation integration points

Not yet implemented:

- Attestation document verification
- KMS recipient construction
- Signed vsock message protocol
- Secure model wrapping and unwrap flow
- Production-grade secret rotation
- Fine-grained authorization policy beyond tenant ID matching

## Residual Risks

Even with enclaves, some risks remain and must be handled explicitly:

- The host can still influence which requests are sent to the enclave.
- Poor IAM policy design can undermine attestation controls.
- Debug-mode enclaves behave differently from production measurements.
- Logging and tracing can leak sensitive metadata if not carefully designed.
- Tenant isolation at the API layer is only as strong as the request identity and authorization model behind it.

## Security Posture of This Repository Phase

This repository phase should be treated as:

- A structural foundation
- A security-oriented design artifact
- A portfolio-ready scaffold for later implementation

It should not yet be treated as production-ready confidential computing software.
