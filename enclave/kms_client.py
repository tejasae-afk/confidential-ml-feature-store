"""Enclave-side KMS client with attestation-aware proxy requests and TTL caching."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from botocore.exceptions import BotoCoreError, ClientError
from enclave.attestation import get_attestation_document

_RECIPIENT_KEY_ENCRYPTION_ALGORITHM = "RSAES_OAEP_SHA_256"
_DEFAULT_CACHE_TTL_SECONDS = 60.0
_DEFAULT_CACHE_MAX_SIZE = 128

AttestationProvider = Callable[[bytes | None, bytes | None, bytes | None], bytes]


class KMSProxyError(RuntimeError):
    """Raised when the enclave cannot complete a proxied KMS operation."""


class KMSProxyTransport(Protocol):
    """Protocol implemented by the host-side vsock KMS proxy transport."""

    def decrypt(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        """Forward an attested decrypt request to the host-side KMS proxy.

        Args:
            request: KMS request payload.

        Returns:
            A response mapping from the proxy.
        """


@dataclass(slots=True)
class _CacheEntry:
    """Represents a cached decrypted key."""

    plaintext: bytes
    expires_at: float


class EnclaveKMSClient:
    """Perform attestation-aware KMS decrypts through a host-side proxy.

    The enclave itself does not use direct network access. Instead, it obtains an
    attestation document locally and sends a request over a transport abstraction
    representing the host-side vsock KMS proxy.
    """

    def __init__(
        self,
        *,
        key_id: str,
        proxy_transport: KMSProxyTransport,
        attestation_provider: AttestationProvider | None = None,
        cache_ttl_seconds: float | None = None,
        max_cache_size: int | None = None,
        attestation_public_key: bytes | None = None,
        unwrap_ciphertext_for_recipient: Callable[[Mapping[str, Any]], bytes] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialize the enclave KMS client.

        Args:
            key_id: KMS key ID or ARN.
            proxy_transport: Host-side proxy transport for KMS requests.
            attestation_provider: Callable that returns a Nitro attestation document.
            cache_ttl_seconds: Optional cache TTL override.
            max_cache_size: Maximum number of cached keys.
            attestation_public_key: Optional DER-encoded public key for KMS recipient mode.
            unwrap_ciphertext_for_recipient: Optional handler for AWS-style
                ``CiphertextForRecipient`` responses.
            clock: Monotonic clock used for cache expiry.
        """
        normalized_key_id = key_id.strip()
        if not normalized_key_id:
            raise ValueError("key_id must not be empty.")

        self._key_id = normalized_key_id
        self._proxy_transport = proxy_transport
        self._attestation_provider = attestation_provider or get_attestation_document
        self._cache_ttl_seconds = self._resolve_cache_ttl(cache_ttl_seconds)
        self._max_cache_size = self._resolve_cache_size(max_cache_size)
        self._attestation_public_key = attestation_public_key or self._load_public_key_from_env()
        self._unwrap_ciphertext_for_recipient = unwrap_ciphertext_for_recipient
        self._clock = clock

        self._cache: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._cache_lock = threading.Lock()

    @property
    def cache_ttl_seconds(self) -> float:
        """Return the configured cache TTL.

        Returns:
            Cache TTL in seconds.
        """
        return self._cache_ttl_seconds

    @property
    def max_cache_size(self) -> int:
        """Return the configured maximum cache size.

        Returns:
            Maximum number of cached entries.
        """
        return self._max_cache_size

    def decrypt_data_key(
        self,
        ciphertext_blob: bytes,
        tenant_id: str,
        *,
        user_data: bytes | None = None,
        nonce: bytes | None = None,
        force_refresh: bool = False,
    ) -> bytes:
        """Decrypt a wrapped data key through the host-side KMS proxy.

        Args:
            ciphertext_blob: KMS-encrypted data key.
            tenant_id: Tenant identifier bound into the encryption context.
            user_data: Optional signed user-data override for the attestation document.
            nonce: Optional nonce included in the attestation document.
            force_refresh: When true, bypass the local TTL cache.

        Returns:
            The decrypted plaintext data key.

        Raises:
            KMSProxyError: If the proxy call fails or returns an invalid response.
        """
        normalized_tenant_id = tenant_id.strip()
        if not normalized_tenant_id:
            raise ValueError("tenant_id must not be empty.")
        if not ciphertext_blob:
            raise ValueError("ciphertext_blob must not be empty.")

        cache_key = self._build_cache_key(ciphertext_blob, normalized_tenant_id)
        if not force_refresh:
            cached_value = self._get_cached(cache_key)
            if cached_value is not None:
                return cached_value

        attestation_document = self._attestation_provider(
            self._attestation_public_key,
            user_data or self._build_user_data(normalized_tenant_id),
            nonce,
        )
        if not attestation_document:
            raise KMSProxyError("Attestation provider returned an empty document.")

        request_payload = {
            "KeyId": self._key_id,
            "CiphertextBlob": ciphertext_blob,
            "EncryptionContext": {"tenant_id": normalized_tenant_id},
            "Recipient": {
                "KeyEncryptionAlgorithm": _RECIPIENT_KEY_ENCRYPTION_ALGORITHM,
                "AttestationDocument": attestation_document,
            },
        }

        try:
            response = self._proxy_transport.decrypt(request_payload)
        except (ClientError, BotoCoreError):
            raise
        except Exception as exc:
            raise KMSProxyError("Failed to reach the host-side KMS proxy.") from exc

        plaintext = self._extract_plaintext(response)
        self._store_cache_entry(cache_key, plaintext)
        return plaintext

    def clear_cache(self) -> None:
        """Remove all cached decrypted keys."""
        with self._cache_lock:
            self._cache.clear()

    def cache_size(self) -> int:
        """Return the number of live cache entries.

        Returns:
            The count of currently cached entries.
        """
        with self._cache_lock:
            self._purge_expired_locked(self._clock())
            return len(self._cache)

    def _build_cache_key(self, ciphertext_blob: bytes, tenant_id: str) -> str:
        """Build a stable cache key for a ciphertext/context pair.

        Args:
            ciphertext_blob: KMS-encrypted data key.
            tenant_id: Tenant identifier.

        Returns:
            A SHA-256 cache key.
        """
        digest = hashlib.sha256()
        digest.update(tenant_id.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(ciphertext_blob)
        return digest.hexdigest()

    def _get_cached(self, cache_key: str) -> bytes | None:
        """Return a cached key when it is still valid.

        Args:
            cache_key: Cache entry identifier.

        Returns:
            The cached plaintext, if present and not expired.
        """
        now = self._clock()
        with self._cache_lock:
            self._purge_expired_locked(now)
            entry = self._cache.get(cache_key)
            if entry is None:
                return None
            self._cache.move_to_end(cache_key)
            return entry.plaintext

    def _store_cache_entry(self, cache_key: str, plaintext: bytes) -> None:
        """Store a plaintext data key in the TTL cache.

        Args:
            cache_key: Cache entry identifier.
            plaintext: Plaintext data key bytes.
        """
        expires_at = self._clock() + self._cache_ttl_seconds
        with self._cache_lock:
            self._purge_expired_locked(self._clock())
            self._cache[cache_key] = _CacheEntry(
                plaintext=plaintext,
                expires_at=expires_at,
            )
            self._cache.move_to_end(cache_key)
            while len(self._cache) > self._max_cache_size:
                self._cache.popitem(last=False)

    def _purge_expired_locked(self, now: float) -> None:
        """Remove expired cache entries.

        Args:
            now: Current monotonic timestamp.
        """
        expired_keys = [key for key, entry in self._cache.items() if entry.expires_at <= now]
        for key in expired_keys:
            self._cache.pop(key, None)

    def _extract_plaintext(self, response: Mapping[str, Any]) -> bytes:
        """Extract plaintext key material from a proxy response.

        The local test proxy can return ``Plaintext`` directly. A production proxy
        calling AWS KMS may return ``CiphertextForRecipient`` instead, in which case
        an unwrap handler must be configured.

        Args:
            response: Proxy response mapping.

        Returns:
            Plaintext key bytes.

        Raises:
            KMSProxyError: If the response is malformed.
        """
        for key in ("Plaintext", "plaintext"):
            value = response.get(key)
            if isinstance(value, (bytes, bytearray)):
                return bytes(value)

        ciphertext_for_recipient = response.get("CiphertextForRecipient")
        if isinstance(ciphertext_for_recipient, (bytes, bytearray)):
            if self._unwrap_ciphertext_for_recipient is None:
                raise KMSProxyError(
                    "Proxy returned CiphertextForRecipient but no unwrap handler is configured.",
                )
            return self._unwrap_ciphertext_for_recipient(response)

        raise KMSProxyError("Proxy response did not include plaintext key material.")

    def _build_user_data(self, tenant_id: str) -> bytes:
        """Build signed user data for the attestation document.

        Args:
            tenant_id: Tenant identifier.

        Returns:
            JSON-encoded user data bytes.
        """
        payload = {
            "tenant_id": tenant_id,
            "operation": "kms.decrypt",
            "key_id": self._key_id,
        }
        return json.dumps(payload, separators=(",", ":")).encode("utf-8")

    @staticmethod
    def _resolve_cache_ttl(value: float | None) -> float:
        """Resolve the TTL cache setting.

        Args:
            value: Optional explicit TTL value.

        Returns:
            A positive TTL in seconds.
        """
        if value is not None:
            resolved = float(value)
        else:
            resolved = float(
                os.getenv(
                    "ENCLAVE_KMS_CACHE_TTL_SECONDS",
                    str(_DEFAULT_CACHE_TTL_SECONDS),
                ),
            )
        if resolved <= 0:
            raise ValueError("cache_ttl_seconds must be greater than 0.")
        return resolved

    @staticmethod
    def _resolve_cache_size(value: int | None) -> int:
        """Resolve the maximum cache size.

        Args:
            value: Optional explicit cache size.

        Returns:
            A positive integer cache size.
        """
        if value is not None:
            resolved = int(value)
        else:
            resolved = int(
                os.getenv(
                    "ENCLAVE_KMS_CACHE_MAX_SIZE",
                    str(_DEFAULT_CACHE_MAX_SIZE),
                ),
            )
        if resolved <= 0:
            raise ValueError("max_cache_size must be greater than 0.")
        return resolved

    @staticmethod
    def _load_public_key_from_env() -> bytes | None:
        """Load an optional DER-encoded public key from the environment.

        Returns:
            The decoded public key bytes when configured, otherwise ``None``.

        Raises:
            ValueError: If the environment variable is invalid base64.
        """
        encoded_key = os.getenv("ENCLAVE_ATTESTATION_PUBLIC_KEY_B64")
        if not encoded_key:
            return None
        try:
            return base64.b64decode(encoded_key, validate=True)
        except ValueError as exc:
            raise ValueError(
                "ENCLAVE_ATTESTATION_PUBLIC_KEY_B64 is not valid base64.",
            ) from exc
