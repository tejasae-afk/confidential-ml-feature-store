# Security Model

This document describes the Phase 2 security model for the Confidential ML Feature Store. At this stage, the feature store already enforces tenant isolation in FastAPI and DynamoDB, and the cryptographic design now adds KMS integration with PCR attestation-based key release for protected model material.

The intended audience is engineers and security reviewers who need to understand what assets are protected, what assumptions the design makes, and where the residual risks remain.

## Security Objectives

The system is designed to protect:

- machine learning model weights and related serialized artifacts
- envelope-encryption data keys used to unwrap those artifacts
- tenant-scoped feature data and per-tenant cryptographic contexts
- inference request flows that should remain confidential even if the parent instance is compromised

## What We Are Protecting

### 1. ML model weights

Model weights are trade secrets. If an attacker can decrypt and copy them from the parent instance, the confidentiality and commercial value of the model may be lost.

### 2. Envelope-encryption keys

The data keys that protect model artifacts are high-value secrets. If those keys are exposed, artifact-level encryption becomes ineffective.

### 3. Tenant-bound feature material

Even before enclave-backed inference, feature data is tenant-scoped and should not be readable across tenant boundaries.

### 4. Inference execution state

The long-term goal is that sensitive inference inputs, decrypted model material, and derived runtime secrets exist only inside enclave memory.

## Threat Model

### Adversaries in scope

#### Compromised host operating system

Assume the parent EC2 instance can be compromised by malware, local privilege escalation, or operator error. In that case, the attacker may gain root on the host, inspect processes, read local disks, and manipulate host-side software.

#### Malicious or over-privileged cloud administrator

Assume an operator with broad infrastructure access may be able to inspect logs, metadata, or deployment configuration, and may try to replay old material or alter runtime configuration.

#### Other tenants

Assume another authenticated tenant may try to read, list, overwrite, or delete feature sets that do not belong to them, or may attempt to reuse ciphertext or API inputs across tenants.

### Threats addressed by this phase

- decrypting protected model keys from the parent instance without a valid enclave attestation
- using the correct IAM role from the wrong enclave image
- reusing ciphertext under the wrong tenant encryption context
- reducing KMS call volume through a bounded cache while preserving tenant isolation in the cache key

### Threats only partially addressed in this phase

- replay resistance for attestation user data and nonces beyond basic protocol hooks
- full certificate-chain and COSE signature verification of attestation documents in application code
- hardening of the host-side vsock proxy and request signing between host and enclave
- memory scraping inside the enclave after a key has already been released to trusted code

## How Attestation Works

Nitro Enclaves can request an attestation document from the Nitro Secure Module (NSM). That document includes enclave measurements such as PCR values, optional user data, and an optional public key. AWS KMS can evaluate policy conditions against the attestation document when the request includes the `Recipient` field.

In practical terms, the flow is:

1. The enclave requests an attestation document from NSM.
2. The document includes PCR values that identify the enclave image and related runtime measurements.
3. The enclave sends a KMS decrypt request through the host-side KMS proxy because the enclave itself has no direct network access.
4. The request includes the attestation document in the `Recipient` field.
5. KMS compares the attested PCR values with the key policy conditions.
6. If the PCRs match, KMS releases the decrypt response for that attested enclave request.
7. If the PCRs do not match, KMS denies the operation.

### Diagram description

Think of the system as four trust zones:

- **Client / Tenant** submits feature and inference requests to the API.
- **Host API on the parent instance** authenticates tenants, reads feature data, and relays enclave traffic over vsock.
- **Nitro Enclave** generates an attestation document and requests protected key material.
- **AWS KMS** checks both IAM permissions and attestation policy conditions before releasing key material.

The important point is that the parent instance is a transport and orchestration layer, not the ultimate trust anchor for decryption.

## PCR-Based Key Release

The KMS key policy is configured with a PCR condition such as `kms:RecipientAttestation:PCR0`. PCR0 identifies the enclave image measurement. If the attestation document contains a different PCR0 value, KMS denies the decrypt request.

This gives the design an image-bound property:

- the expected enclave image is allowed to decrypt
- a modified or substituted enclave image is denied
- a host process with the same IAM role but without valid enclave attestation is denied

In other words, IAM alone is not sufficient for protected key release.

## Tenant Isolation Through Encryption Context

Attestation alone answers the question, "Is this the expected enclave image?" It does not answer, "Is this ciphertext being used for the right tenant?"

To address that, the KMS integration uses an encryption context of the form:

```text
{"tenant_id": "<tenant-id>"}
```

This means:

- ciphertext generated for `tenant-a` must be decrypted with the exact same `tenant_id` context
- trying to decrypt `tenant-a` ciphertext with `tenant-b` context is rejected
- envelope-encrypted artifacts can share the same KMS key while still remaining tenant-bound at decrypt time

This complements the existing FastAPI and DynamoDB tenant-isolation controls by making the cryptographic layer tenant-aware as well.

## Why TTL Caching Is Acceptable

Inside the enclave, decrypted key material is cached for a bounded time-to-live (TTL). This is a deliberate performance trade-off.

### Benefit

Repeated KMS round-trips add latency and operational cost. A short-lived cache avoids re-requesting the same key for every inference call.

### Why the exposure window is bounded

- the cache is in enclave memory, not parent-instance memory
- entries expire automatically after the configured TTL
- cache keys include the tenant identifier and ciphertext hash, which prevents cross-tenant cache reuse
- the cache has a maximum size and evicts old entries

### Security interpretation

TTL caching does not eliminate exposure to code already running inside the trusted enclave. Instead, it narrows the release frequency and keeps the decrypted material within the same trusted boundary for a controlled period.

## Trust Boundaries

| Boundary | Trust Level | Why it matters |
| --- | --- | --- |
| External client to FastAPI host | Low | Public-facing request boundary; must authenticate and authorize every call |
| FastAPI host to DynamoDB | Medium | Protected by IAM and application scoping, but still outside the enclave trust boundary |
| FastAPI host to enclave over vsock | Medium | Required transport path; host can relay or drop traffic but should not gain plaintext keys |
| Enclave to AWS KMS via host-side proxy | High-value boundary | KMS policy evaluation plus attestation determine whether keys are released |
| Enclave memory | Highest trust zone in this design | Intended location for decrypted model keys and inference-time secrets |

## Security Properties Achieved in Phase 2

This phase provides the following concrete properties:

- **tenant-bound cryptography** through KMS encryption context
- **attestation-shaped decrypt requests** through the KMS `Recipient` field
- **policy-enforced PCR matching** for enclave key release
- **network-isolated enclave KMS path** using a host-side proxy abstraction instead of direct enclave networking
- **bounded in-enclave caching** for decrypted keys with TTL and size limits

## Limitations and Assumptions

### 1. Attestation parsing is structural, not full root-of-trust validation

The current helper code parses CBOR/COSE attestation structure for testing and local development, but it does not fully validate the AWS attestation certificate chain or COSE signature in application code.

### 2. The host still controls orchestration

A compromised parent instance can still decide which requests are sent to the enclave, when the enclave is invoked, and whether responses are dropped, delayed, or replayed at the transport layer.

### 3. Key release still depends on correct KMS policy design

If the key policy is too broad, attestation controls can be undermined. PCR-bound policy conditions must be maintained carefully during enclave image updates.

### 4. Debug-mode enclaves are not trustworthy for production attestation

Debug-mode enclaves produce zero-valued PCR attestations and must not be used for production key release policies.

### 5. Cached secrets remain accessible to trusted enclave code during the TTL window

TTL caching reduces KMS traffic but does not make decrypted keys inaccessible to code already executing inside the enclave.

### 6. The local-development path uses mock attestation

`MockNSM` exists for offline development and tests. It is useful for protocol testing, but it is not a substitute for real NSM-generated attestation.

## Operational Assumptions

The design assumes all of the following are true:

- the correct PCR measurements are recorded from the intended enclave image
- the KMS key policy is updated when enclave measurements legitimately change
- the enclave code is the component that handles decrypted model keys
- logs do not contain plaintext key material or decrypted model artifacts
- the host-side proxy forwards requests correctly and does not attempt to persist plaintext responses

## Summary

The central design principle is simple:

- tenant identity is enforced at the API, data, and encryption-context layers
- enclave identity is enforced at the KMS policy layer through PCR-based attestation
- the parent instance can coordinate requests, but it should not be able to decrypt protected model weights on its own

That is the security foundation for a confidential feature-store and inference platform built on Nitro Enclaves.
