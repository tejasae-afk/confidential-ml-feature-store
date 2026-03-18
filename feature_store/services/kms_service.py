"""AWS KMS helpers for tenant-bound and attested cryptographic operations."""

from __future__ import annotations

from typing import Any

import boto3
from botocore.client import BaseClient
from botocore.exceptions import BotoCoreError, ClientError

from feature_store.utils.logger import get_logger

logger = get_logger(__name__)

_RECIPIENT_KEY_ENCRYPTION_ALGORITHM = "RSAES_OAEP_SHA_256"


class KMSServiceError(RuntimeError):
    """Raised when a KMS operation cannot be completed safely."""


class KMSService:
    """Host-side wrapper for AWS KMS.

    The service uses an encryption context of ``{"tenant_id": <tenant>}`` for all
    tenant-scoped operations. This binds ciphertext to a specific tenant and lets
    the same KMS key protect many tenants while still requiring an exact context
    match at decrypt time.
    """

    def __init__(
        self,
        key_id: str,
        *,
        region_name: str = "us-east-1",
        endpoint_url: str | None = None,
        kms_client: BaseClient | None = None,
    ) -> None:
        """Initialize the service.

        Args:
            key_id: KMS key ID or ARN.
            region_name: AWS region for the client.
            endpoint_url: Optional custom endpoint for local testing.
            kms_client: Optional preconfigured boto3 KMS client.
        """
        self._key_id = key_id.strip()
        if not self._key_id:
            raise ValueError("key_id must not be empty.")

        self._client = kms_client or boto3.client(
            "kms",
            region_name=region_name,
            endpoint_url=endpoint_url,
        )

    @property
    def key_id(self) -> str:
        """Return the configured KMS key identifier.

        Returns:
            The configured KMS key identifier.
        """
        return self._key_id

    def encrypt_data(self, plaintext: bytes, tenant_id: str) -> bytes:
        """Encrypt tenant-scoped data with AWS KMS.

        Args:
            plaintext: Raw plaintext bytes to encrypt.
            tenant_id: Tenant identifier used in the encryption context.

        Returns:
            The KMS ciphertext blob.

        Raises:
            KMSServiceError: If the KMS encrypt operation fails.
        """
        if not plaintext:
            raise ValueError("plaintext must not be empty.")

        try:
            response = self._client.encrypt(
                KeyId=self._key_id,
                Plaintext=plaintext,
                EncryptionContext=self._build_encryption_context(tenant_id),
            )
        except (BotoCoreError, ClientError) as exc:
            logger.exception("KMS encrypt failed")
            raise self._translate_kms_error("Failed to encrypt data with KMS.", exc) from exc

        ciphertext_blob = response.get("CiphertextBlob")
        if not isinstance(ciphertext_blob, (bytes, bytearray)):
            raise KMSServiceError("KMS encrypt response did not include a ciphertext blob.")
        return bytes(ciphertext_blob)

    def decrypt_data(self, ciphertext: bytes, tenant_id: str) -> bytes:
        """Decrypt tenant-scoped data directly on the host.

        This method exists for local testing and non-enclave paths. In production,
        protected model keys should be decrypted through the attested enclave flow.

        Args:
            ciphertext: KMS ciphertext blob.
            tenant_id: Tenant identifier required in the encryption context.

        Returns:
            The plaintext bytes.

        Raises:
            KMSServiceError: If the KMS decrypt operation fails.
        """
        if not ciphertext:
            raise ValueError("ciphertext must not be empty.")

        try:
            response = self._client.decrypt(
                KeyId=self._key_id,
                CiphertextBlob=ciphertext,
                EncryptionContext=self._build_encryption_context(tenant_id),
            )
        except (BotoCoreError, ClientError) as exc:
            logger.exception("KMS decrypt failed")
            raise self._translate_kms_error("Failed to decrypt data with KMS.", exc) from exc

        plaintext = response.get("Plaintext")
        if not isinstance(plaintext, (bytes, bytearray)):
            raise KMSServiceError("KMS decrypt response did not include plaintext data.")
        return bytes(plaintext)

    def generate_data_key(self, tenant_id: str) -> tuple[bytes, bytes]:
        """Generate a tenant-bound data key for envelope encryption.

        Args:
            tenant_id: Tenant identifier used in the encryption context.

        Returns:
            A tuple of ``(plaintext_key, encrypted_key)``.

        Raises:
            KMSServiceError: If the KMS generate-data-key operation fails.
        """
        try:
            response = self._client.generate_data_key(
                KeyId=self._key_id,
                KeySpec="AES_256",
                EncryptionContext=self._build_encryption_context(tenant_id),
            )
        except (BotoCoreError, ClientError) as exc:
            logger.exception("KMS generate_data_key failed")
            raise self._translate_kms_error("Failed to generate a data key with KMS.", exc) from exc

        plaintext = response.get("Plaintext")
        ciphertext_blob = response.get("CiphertextBlob")
        if not isinstance(plaintext, (bytes, bytearray)):
            raise KMSServiceError("KMS generate_data_key response did not include plaintext.")
        if not isinstance(ciphertext_blob, (bytes, bytearray)):
            raise KMSServiceError("KMS generate_data_key response did not include ciphertext.")
        return bytes(plaintext), bytes(ciphertext_blob)

    def build_attested_recipient(self, attestation_document: bytes) -> dict[str, Any]:
        """Build the KMS Recipient field for an attested enclave request.

        Args:
            attestation_document: Raw CBOR/COSE attestation document from the enclave.

        Returns:
            A ``Recipient`` structure suitable for boto3 KMS operations.

        Raises:
            ValueError: If the attestation document is empty.
        """
        if not attestation_document:
            raise ValueError("attestation_document must not be empty.")

        return {
            "KeyEncryptionAlgorithm": _RECIPIENT_KEY_ENCRYPTION_ALGORITHM,
            "AttestationDocument": attestation_document,
        }

    def prepare_attested_decrypt_request(
        self,
        ciphertext: bytes,
        tenant_id: str,
        attestation_document: bytes,
    ) -> dict[str, Any]:
        """Prepare a KMS decrypt request for an attested enclave recipient.

        The returned payload is what an enclave-backed caller presents to AWS KMS
        so the service can evaluate the attestation document against key-policy PCR
        conditions and encrypt the plaintext response for the enclave recipient.

        Args:
            ciphertext: KMS ciphertext blob to decrypt.
            tenant_id: Tenant identifier bound into the encryption context.
            attestation_document: Raw enclave attestation document.

        Returns:
            A boto3-compatible request mapping for ``kms.decrypt``.
        """
        if not ciphertext:
            raise ValueError("ciphertext must not be empty.")

        return {
            "KeyId": self._key_id,
            "CiphertextBlob": ciphertext,
            "EncryptionContext": self._build_encryption_context(tenant_id),
            "Recipient": self.build_attested_recipient(attestation_document),
        }

    @staticmethod
    def _build_encryption_context(tenant_id: str) -> dict[str, str]:
        """Build the KMS encryption context for a tenant.

        Args:
            tenant_id: Tenant identifier.

        Returns:
            A KMS encryption context mapping.

        Raises:
            ValueError: If the tenant identifier is blank.
        """
        normalized_tenant_id = tenant_id.strip()
        if not normalized_tenant_id:
            raise ValueError("tenant_id must not be empty.")
        return {"tenant_id": normalized_tenant_id}

    @staticmethod
    def _translate_kms_error(message: str, error: Exception) -> KMSServiceError:
        """Translate boto errors into stable service errors.

        Args:
            message: Base message for the translated error.
            error: Original boto or botocore exception.

        Returns:
            A ``KMSServiceError`` with a concise, non-sensitive message.
        """
        if isinstance(error, ClientError):
            error_code = error.response.get("Error", {}).get("Code", "UnknownError")
            if error_code == "InvalidCiphertextException":
                return KMSServiceError(
                    f"{message} The ciphertext or encryption context is invalid.",
                )
            if error_code == "AccessDeniedException":
                return KMSServiceError(
                    f"{message} Access to the requested KMS operation was denied.",
                )
            if error_code == "DisabledException":
                return KMSServiceError(f"{message} The configured KMS key is disabled.")
            return KMSServiceError(f"{message} AWS KMS returned {error_code}.")

        return KMSServiceError(message)
