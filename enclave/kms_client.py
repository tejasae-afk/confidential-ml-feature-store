"""KMS client scaffold for enclave-side decrypt operations."""

from __future__ import annotations


class EnclaveKMSClient:
    """Perform attestation-aware KMS calls from inside the enclave."""

    def __init__(self, key_id: str, region: str) -> None:
        self.key_id = key_id
        self.region = region

    def decrypt_ciphertext(self, ciphertext_blob: bytes) -> bytes:
        """Decrypt ciphertext using enclave-attested KMS access."""
        raise NotImplementedError(
            "Enclave-side KMS decrypt logic is not implemented in the scaffold phase.",
        )

    def generate_data_key(self) -> tuple[bytes, bytes]:
        """Generate a data key from inside the enclave."""
        raise NotImplementedError(
            "Enclave-side KMS data key generation is not implemented in the scaffold phase.",
        )
