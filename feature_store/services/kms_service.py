"""AWS KMS service abstraction for attestation-aware cryptographic operations."""

from __future__ import annotations


class KMSService:
    """Host-side wrapper for KMS interactions and attestation policy hooks."""

    def __init__(self, key_id: str) -> None:
        self.key_id = key_id

    def validate_attestation_document(self, attestation_document: bytes) -> None:
        """Validate an enclave attestation document before privileged operations."""
        raise NotImplementedError(
            "Attestation validation is not implemented in the scaffold phase.",
        )

    def decrypt_ciphertext(
        self,
        ciphertext_blob: bytes,
        attestation_document: bytes | None = None,
    ) -> bytes:
        """Decrypt ciphertext using KMS with optional enclave attestation context."""
        raise NotImplementedError(
            "KMS decrypt integration is not implemented in the scaffold phase.",
        )

    def generate_data_key(
        self,
        attestation_document: bytes | None = None,
    ) -> tuple[bytes, bytes]:
        """Generate a data key for protected material workflows."""
        raise NotImplementedError(
            "KMS data key generation is not implemented in the scaffold phase.",
        )
