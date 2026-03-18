"""NSM attestation scaffold."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(slots=True)
class AttestationBundle:
    """Container for enclave attestation output."""

    document: bytes | None = None
    pcrs: Mapping[str, str] | None = None


class AttestationProvider:
    """Generate attestation material from the Nitro Secure Module."""

    def create_document(
        self,
        public_key: bytes | None = None,
        user_data: bytes | None = None,
        nonce: bytes | None = None,
    ) -> AttestationBundle:
        """Create an attestation document for enclave identity verification."""
        raise NotImplementedError(
            "NSM attestation generation is not implemented in the scaffold phase.",
        )
