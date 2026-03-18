"""KMS attestation and tenant-bound encryption tests."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import boto3
import pytest
from botocore.exceptions import ClientError
from enclave.attestation import MockNSM, verify_attestation_document
from enclave.kms_client import EnclaveKMSClient

from feature_store.services.kms_service import KMSService


@dataclass
class KMSBackend:
    """Container for the moto-backed KMS test backend."""

    kms_client: Any
    key_id: str
    service: KMSService


class FakeClock:
    """Simple controllable monotonic clock for cache tests."""

    def __init__(self, start: float = 1000.0) -> None:
        self._now = start

    def now(self) -> float:
        """Return the current fake time.

        Returns:
            The current monotonic timestamp.
        """
        return self._now

    def advance(self, seconds: float) -> None:
        """Advance the fake clock.

        Args:
            seconds: Number of seconds to advance.
        """
        self._now += seconds


class FakeAttestedKMSProxy:
    """Simulates the host-side KMS proxy used by a Nitro Enclave."""

    def __init__(self, *, kms_backend: KMSBackend, required_pcr0: str) -> None:
        self._kms_backend = kms_backend
        self._required_pcr0 = required_pcr0.lower()
        self.decrypt_call_count = 0
        self.last_request: dict[str, Any] | None = None

    def decrypt(self, request: Mapping[str, Any]) -> dict[str, bytes]:
        """Validate attestation and forward the decrypt to moto-backed KMS.

        Args:
            request: KMS decrypt request payload.

        Returns:
            A response mapping with plaintext bytes.

        Raises:
            ClientError: If the attestation is missing or invalid.
        """
        self.decrypt_call_count += 1
        self.last_request = dict(request)

        recipient = request.get("Recipient")
        if not isinstance(recipient, Mapping):
            raise self._access_denied("Missing attestation recipient.")

        attestation_document = recipient.get("AttestationDocument")
        if not isinstance(attestation_document, (bytes, bytearray)) or not attestation_document:
            raise self._access_denied("Missing attestation document.")

        parsed = verify_attestation_document(bytes(attestation_document))
        if parsed["pcrs"].get("0") != self._required_pcr0:
            raise self._access_denied("PCR0 mismatch.")

        encryption_context = request.get("EncryptionContext")
        if not isinstance(encryption_context, Mapping):
            raise self._access_denied("Missing encryption context.")

        response = self._kms_backend.kms_client.decrypt(
            KeyId=self._kms_backend.key_id,
            CiphertextBlob=request["CiphertextBlob"],
            EncryptionContext=dict(encryption_context),
        )
        return {"Plaintext": bytes(response["Plaintext"])}

    @staticmethod
    def _access_denied(message: str) -> ClientError:
        """Build a synthetic AccessDeniedException.

        Args:
            message: Human-readable denial reason.

        Returns:
            A ``ClientError`` matching AWS KMS semantics.
        """
        return ClientError(
            {
                "Error": {
                    "Code": "AccessDeniedException",
                    "Message": message,
                },
            },
            "Decrypt",
        )


@pytest.fixture()
def kms_backend(aws_mock) -> KMSBackend:
    """Create a moto-backed KMS key and service wrapper.

    Returns:
        A configured ``KMSBackend`` instance.
    """
    kms_client = boto3.client("kms", region_name="us-east-1")
    key_metadata = kms_client.create_key(Description="attestation-test-key")["KeyMetadata"]
    service = KMSService(
        key_id=key_metadata["KeyId"],
        region_name="us-east-1",
        kms_client=kms_client,
    )
    return KMSBackend(
        kms_client=kms_client,
        key_id=key_metadata["KeyId"],
        service=service,
    )


@pytest.fixture()
def valid_pcr0() -> str:
    """Return a deterministic SHA-384-sized PCR0 value.

    Returns:
        A lowercase hexadecimal PCR0 value.
    """
    return "1" * 96


def test_valid_attestation_decrypts(kms_backend: KMSBackend, valid_pcr0: str) -> None:
    """A valid attestation document should allow decrypt."""
    plaintext = b"super-secret-model-key"
    ciphertext = kms_backend.service.encrypt_data(plaintext, "tenant-a")

    mock_nsm = MockNSM(pcrs={0: valid_pcr0})
    proxy = FakeAttestedKMSProxy(kms_backend=kms_backend, required_pcr0=valid_pcr0)
    client = EnclaveKMSClient(
        key_id=kms_backend.key_id,
        proxy_transport=proxy,
        attestation_provider=mock_nsm.get_attestation_document,
        cache_ttl_seconds=30.0,
        attestation_public_key=b"mock-rsa-public-key",
    )

    decrypted = client.decrypt_data_key(ciphertext, "tenant-a")

    assert decrypted == plaintext
    assert proxy.decrypt_call_count == 1
    assert proxy.last_request is not None
    parsed = verify_attestation_document(proxy.last_request["Recipient"]["AttestationDocument"])
    assert parsed["pcrs"]["0"] == valid_pcr0
    assert parsed["module_id"] == "mock-nsm"


def test_spoofed_attestation_rejected(kms_backend: KMSBackend, valid_pcr0: str) -> None:
    """A mismatched PCR value should be rejected by policy evaluation."""
    ciphertext = kms_backend.service.encrypt_data(b"model-key", "tenant-a")

    spoofed_nsm = MockNSM(pcrs={0: "2" * 96})
    proxy = FakeAttestedKMSProxy(kms_backend=kms_backend, required_pcr0=valid_pcr0)
    client = EnclaveKMSClient(
        key_id=kms_backend.key_id,
        proxy_transport=proxy,
        attestation_provider=spoofed_nsm.get_attestation_document,
        cache_ttl_seconds=30.0,
        attestation_public_key=b"mock-rsa-public-key",
    )

    with pytest.raises(ClientError) as exc_info:
        client.decrypt_data_key(ciphertext, "tenant-a")

    assert exc_info.value.response["Error"]["Code"] == "AccessDeniedException"
    assert "PCR0 mismatch" in exc_info.value.response["Error"]["Message"]
    assert proxy.decrypt_call_count == 1


def test_no_attestation_rejected(kms_backend: KMSBackend) -> None:
    """Decrypt requests without attestation should be rejected."""
    ciphertext = kms_backend.service.encrypt_data(b"model-key", "tenant-a")
    proxy = FakeAttestedKMSProxy(kms_backend=kms_backend, required_pcr0="1" * 96)

    with pytest.raises(ClientError) as exc_info:
        proxy.decrypt(
            {
                "KeyId": kms_backend.key_id,
                "CiphertextBlob": ciphertext,
                "EncryptionContext": {"tenant_id": "tenant-a"},
            },
        )

    assert exc_info.value.response["Error"]["Code"] == "AccessDeniedException"
    assert "Missing attestation recipient" in exc_info.value.response["Error"]["Message"]


def test_expired_cache_refetches_key(kms_backend: KMSBackend, valid_pcr0: str) -> None:
    """Expired cached entries should trigger a new proxy/KMS call."""
    ciphertext = kms_backend.service.encrypt_data(b"cached-model-key", "tenant-a")
    clock = FakeClock()
    mock_nsm = MockNSM(pcrs={0: valid_pcr0})
    proxy = FakeAttestedKMSProxy(kms_backend=kms_backend, required_pcr0=valid_pcr0)
    client = EnclaveKMSClient(
        key_id=kms_backend.key_id,
        proxy_transport=proxy,
        attestation_provider=mock_nsm.get_attestation_document,
        cache_ttl_seconds=5.0,
        attestation_public_key=b"mock-rsa-public-key",
        clock=clock.now,
    )

    first = client.decrypt_data_key(ciphertext, "tenant-a")
    assert first == b"cached-model-key"
    assert proxy.decrypt_call_count == 1

    clock.advance(6.0)
    second = client.decrypt_data_key(ciphertext, "tenant-a")
    assert second == b"cached-model-key"
    assert proxy.decrypt_call_count == 2


def test_cache_hit_skips_kms(kms_backend: KMSBackend, valid_pcr0: str) -> None:
    """A hot cache entry should skip the second KMS call."""
    ciphertext = kms_backend.service.encrypt_data(b"cached-model-key", "tenant-a")
    clock = FakeClock()
    mock_nsm = MockNSM(pcrs={0: valid_pcr0})
    proxy = FakeAttestedKMSProxy(kms_backend=kms_backend, required_pcr0=valid_pcr0)
    client = EnclaveKMSClient(
        key_id=kms_backend.key_id,
        proxy_transport=proxy,
        attestation_provider=mock_nsm.get_attestation_document,
        cache_ttl_seconds=60.0,
        attestation_public_key=b"mock-rsa-public-key",
        clock=clock.now,
    )

    first = client.decrypt_data_key(ciphertext, "tenant-a")
    second = client.decrypt_data_key(ciphertext, "tenant-a")

    assert first == b"cached-model-key"
    assert second == b"cached-model-key"
    assert proxy.decrypt_call_count == 1
    assert client.cache_size() == 1


def test_encryption_context_mismatch(kms_backend: KMSBackend, valid_pcr0: str) -> None:
    """Decrypt must fail when the tenant encryption context does not match."""
    ciphertext = kms_backend.service.encrypt_data(b"tenant-bound-key", "tenant-a")
    mock_nsm = MockNSM(pcrs={0: valid_pcr0})
    proxy = FakeAttestedKMSProxy(kms_backend=kms_backend, required_pcr0=valid_pcr0)
    client = EnclaveKMSClient(
        key_id=kms_backend.key_id,
        proxy_transport=proxy,
        attestation_provider=mock_nsm.get_attestation_document,
        cache_ttl_seconds=30.0,
        attestation_public_key=b"mock-rsa-public-key",
    )

    with pytest.raises(ClientError) as exc_info:
        client.decrypt_data_key(ciphertext, "tenant-b")

    assert exc_info.value.response["Error"]["Code"] == "InvalidCiphertextException"
