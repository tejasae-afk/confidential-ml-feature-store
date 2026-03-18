"""Attestation helpers for Nitro Enclaves.

This module separates production NSM integration from local-development and test
helpers. Production code uses the Nitro Secure Module (NSM) through the
``aws_nsm_interface`` package when available. Local tests can use ``MockNSM`` to
produce fake CBOR/COSE attestation documents with deterministic PCR values.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


class AttestationError(RuntimeError):
    """Raised when attestation document generation or parsing fails."""


@dataclass(frozen=True, slots=True)
class ParsedAttestationDocument:
    """Normalized representation of an attestation document.

    Attributes:
        module_id: Issuing NSM or mock module identifier.
        timestamp_ms: UTC creation time in milliseconds since the Unix epoch.
        digest: Digest algorithm used for the PCR values.
        pcrs: PCR map with integer indexes stored as lowercase hexadecimal strings.
        public_key: Optional DER-encoded public key embedded in the document.
        user_data: Optional protocol-specific signed user data.
        nonce: Optional cryptographic nonce embedded in the document.
        is_cose_sign1: Whether the input looked like a COSE_Sign1 wrapper.
    """

    module_id: str
    timestamp_ms: int
    digest: str
    pcrs: dict[str, str]
    public_key: bytes | None = None
    user_data: bytes | None = None
    nonce: bytes | None = None
    is_cose_sign1: bool = False

    @property
    def timestamp(self) -> datetime:
        """Return the attestation creation time as a timezone-aware datetime.

        Returns:
            The timestamp converted to a ``datetime`` in UTC.
        """
        return datetime.fromtimestamp(self.timestamp_ms / 1000, tz=UTC)

    def as_dict(self) -> dict[str, Any]:
        """Return a dictionary representation for tests and diagnostics.

        Returns:
            A JSON-friendly mapping.
        """
        return {
            "module_id": self.module_id,
            "timestamp_ms": self.timestamp_ms,
            "timestamp": self.timestamp.isoformat(),
            "digest": self.digest,
            "pcrs": self.pcrs,
            "public_key": base64.b64encode(self.public_key).decode("ascii")
            if self.public_key is not None
            else None,
            "user_data": base64.b64encode(self.user_data).decode("ascii")
            if self.user_data is not None
            else None,
            "nonce": base64.b64encode(self.nonce).decode("ascii")
            if self.nonce is not None
            else None,
            "is_cose_sign1": self.is_cose_sign1,
        }


def get_attestation_document(
    public_key: bytes | None = None,
    user_data: bytes | None = None,
    nonce: bytes | None = None,
) -> bytes:
    """Request an attestation document from the Nitro Secure Module.

    Args:
        public_key: Optional DER-encoded public key embedded in the document.
        user_data: Optional signed protocol-specific user data.
        nonce: Optional cryptographic nonce.

    Returns:
        A CBOR/COSE attestation document produced by the NSM.

    Raises:
        AttestationError: If the NSM library is unavailable or the call fails.
    """
    try:
        import aws_nsm_interface
    except ImportError as exc:  # pragma: no cover - depends on enclave runtime.
        raise AttestationError(
            "aws_nsm_interface is not available. Use MockNSM for local tests or "
            "install the NSM bindings inside a Nitro Enclave runtime.",
        ) from exc

    file_handle: Any | None = None
    try:
        file_handle = aws_nsm_interface.open_nsm_device()
        response = aws_nsm_interface.get_attestation_doc(
            file_handle,
            user_data=user_data,
            nonce=nonce,
            public_key=public_key,
        )
    except Exception as exc:  # pragma: no cover - depends on enclave runtime.
        raise AttestationError("Failed to retrieve attestation document from NSM.") from exc
    finally:
        if file_handle is not None:
            try:
                aws_nsm_interface.close_nsm_device(file_handle)
            except Exception:
                pass

    document = response.get("document")
    if not isinstance(document, (bytes, bytearray)):
        raise AttestationError("NSM did not return a valid attestation document.")

    return bytes(document)


def verify_attestation_document(document: bytes) -> dict[str, Any]:
    """Parse a CBOR-encoded Nitro Enclave attestation document.

    This helper focuses on structural validation and field extraction for local
    tests. It does not attempt to validate the AWS certificate chain or COSE
    signature.

    Args:
        document: Raw attestation document bytes.

    Returns:
        A mapping with normalized attestation fields.

    Raises:
        AttestationError: If the document is malformed or unsupported.
    """
    parsed = _parse_attestation_document(document)
    return parsed.as_dict()


class MockNSM:
    """Local-development NSM replacement.

    The mock produces fake CBOR/COSE documents that follow the documented
    attestation document structure closely enough for unit tests.
    """

    def __init__(
        self,
        *,
        pcrs: dict[int | str, str | bytes] | None = None,
        module_id: str = "mock-nsm",
        timestamp_ms: int | None = None,
        digest: str = "SHA384",
        certificate: bytes | None = None,
        cabundle: list[bytes] | None = None,
    ) -> None:
        self._pcrs = self._normalize_pcrs(pcrs or {0: _default_pcr0()})
        self._module_id = module_id
        self._timestamp_ms = timestamp_ms
        self._digest = digest
        self._certificate = certificate or b"mock-certificate"
        self._cabundle = cabundle or [b"mock-ca"]

    def get_attestation_document(
        self,
        public_key: bytes | None = None,
        user_data: bytes | None = None,
        nonce: bytes | None = None,
    ) -> bytes:
        """Return a fake CBOR/COSE attestation document.

        Args:
            public_key: Optional DER-encoded public key.
            user_data: Optional signed user data.
            nonce: Optional cryptographic nonce.

        Returns:
            A CBOR/COSE_Sign1-like byte string.
        """
        payload: dict[str, Any] = {
            "module_id": self._module_id,
            "timestamp": self._timestamp_ms or int(datetime.now(UTC).timestamp() * 1000),
            "digest": self._digest,
            "pcrs": {index: value for index, value in self._pcrs.items()},
            "certificate": self._certificate,
            "cabundle": self._cabundle,
        }
        if public_key is not None:
            payload["public_key"] = public_key
        if user_data is not None:
            payload["user_data"] = user_data
        if nonce is not None:
            payload["nonce"] = nonce

        payload_bytes = _cbor_encode(payload)
        cose_sign1 = [b"", {}, payload_bytes, b"mock-signature"]
        return _cbor_encode(cose_sign1)

    @staticmethod
    def _normalize_pcrs(pcrs: dict[int | str, str | bytes]) -> dict[int, bytes]:
        """Normalize PCR keys and values.

        Args:
            pcrs: Raw PCR mapping.

        Returns:
            A mapping with integer PCR indexes and byte values.

        Raises:
            AttestationError: If the mapping is invalid.
        """
        normalized: dict[int, bytes] = {}
        for index, value in pcrs.items():
            try:
                normalized_index = int(index)
            except (TypeError, ValueError) as exc:
                raise AttestationError("PCR indexes must be integers.") from exc

            if isinstance(value, bytes):
                normalized_value = value
            elif isinstance(value, str):
                try:
                    normalized_value = bytes.fromhex(value)
                except ValueError as exc:
                    raise AttestationError("PCR hex values must be valid hexadecimal.") from exc
            else:
                raise AttestationError("PCR values must be bytes or hexadecimal strings.")

            if len(normalized_value) not in {32, 48, 64}:
                raise AttestationError("PCR values must be 32, 48, or 64 bytes long.")

            normalized[normalized_index] = normalized_value
        return normalized


def _default_pcr0() -> str:
    """Return a deterministic default PCR0 value for tests.

    Returns:
        A lowercase hexadecimal SHA-384-sized value.
    """
    return os.getenv("MOCK_NSM_PCR0", "a" * 96).lower()


def _parse_attestation_document(document: bytes) -> ParsedAttestationDocument:
    """Parse an attestation document into a normalized dataclass.

    Args:
        document: Raw attestation document bytes.

    Returns:
        A normalized attestation document.

    Raises:
        AttestationError: If the document cannot be parsed.
    """
    decoded = _cbor_decode(document)
    is_cose_sign1 = False

    if isinstance(decoded, list) and len(decoded) == 4 and isinstance(decoded[2], bytes):
        decoded = _cbor_decode(decoded[2])
        is_cose_sign1 = True

    if not isinstance(decoded, dict):
        raise AttestationError("Attestation document payload is not a CBOR map.")

    module_id = decoded.get("module_id")
    timestamp_ms = decoded.get("timestamp")
    digest = decoded.get("digest")
    raw_pcrs = decoded.get("pcrs")

    if not isinstance(module_id, str):
        raise AttestationError("Attestation document is missing 'module_id'.")
    if not isinstance(timestamp_ms, int):
        raise AttestationError("Attestation document is missing 'timestamp'.")
    if not isinstance(digest, str):
        raise AttestationError("Attestation document is missing 'digest'.")
    if not isinstance(raw_pcrs, dict):
        raise AttestationError("Attestation document is missing 'pcrs'.")

    normalized_pcrs: dict[str, str] = {}
    for index, value in raw_pcrs.items():
        if not isinstance(index, int):
            raise AttestationError("PCR indexes must be integers.")
        if not isinstance(value, bytes):
            raise AttestationError("PCR values must be raw bytes.")
        normalized_pcrs[str(index)] = value.hex()

    public_key = decoded.get("public_key")
    user_data = decoded.get("user_data")
    nonce = decoded.get("nonce")

    if public_key is not None and not isinstance(public_key, bytes):
        raise AttestationError("'public_key' must be bytes when present.")
    if user_data is not None and not isinstance(user_data, bytes):
        raise AttestationError("'user_data' must be bytes when present.")
    if nonce is not None and not isinstance(nonce, bytes):
        raise AttestationError("'nonce' must be bytes when present.")

    return ParsedAttestationDocument(
        module_id=module_id,
        timestamp_ms=timestamp_ms,
        digest=digest,
        pcrs=normalized_pcrs,
        public_key=public_key,
        user_data=user_data,
        nonce=nonce,
        is_cose_sign1=is_cose_sign1,
    )


def _cbor_encode(value: Any) -> bytes:
    """Encode a limited subset of CBOR used by the attestation helpers.

    Args:
        value: Python value to encode.

    Returns:
        The CBOR-encoded bytes.

    Raises:
        AttestationError: If the value type is unsupported.
    """
    if isinstance(value, bool):
        return b"\xf5" if value else b"\xf4"
    if value is None:
        return b"\xf6"
    if isinstance(value, int):
        if value >= 0:
            return _encode_length(0, value)
        return _encode_length(1, -1 - value)
    if isinstance(value, bytes):
        return _encode_length(2, len(value)) + value
    if isinstance(value, str):
        encoded = value.encode("utf-8")
        return _encode_length(3, len(encoded)) + encoded
    if isinstance(value, list):
        return _encode_length(4, len(value)) + b"".join(_cbor_encode(item) for item in value)
    if isinstance(value, dict):
        encoded_items = []
        for key, item_value in value.items():
            encoded_items.append(_cbor_encode(key))
            encoded_items.append(_cbor_encode(item_value))
        return _encode_length(5, len(value)) + b"".join(encoded_items)
    raise AttestationError(f"Unsupported CBOR value type: {type(value)!r}")


class _CBORDecoder:
    """Minimal CBOR decoder for the attestation helper test paths."""

    def __init__(self, data: bytes) -> None:
        self._data = memoryview(data)
        self._offset = 0

    def decode(self) -> Any:
        """Decode the next CBOR value.

        Returns:
            The decoded Python value.

        Raises:
            AttestationError: If the input is malformed.
        """
        if self._offset >= len(self._data):
            raise AttestationError("Unexpected end of CBOR data.")

        initial_byte = self._read(1)[0]
        major_type = initial_byte >> 5
        additional_info = initial_byte & 0x1F

        if major_type in {0, 1}:
            value = self._read_length(additional_info)
            return value if major_type == 0 else -1 - value

        if major_type == 2:
            length = self._read_length(additional_info)
            return self._read(length)

        if major_type == 3:
            length = self._read_length(additional_info)
            return self._read(length).decode("utf-8")

        if major_type == 4:
            length = self._read_length(additional_info)
            return [self.decode() for _ in range(length)]

        if major_type == 5:
            length = self._read_length(additional_info)
            decoded_map: dict[Any, Any] = {}
            for _ in range(length):
                key = self.decode()
                decoded_map[key] = self.decode()
            return decoded_map

        if major_type == 6:
            _ = self._read_length(additional_info)
            return self.decode()

        if major_type == 7:
            if additional_info == 20:
                return False
            if additional_info == 21:
                return True
            if additional_info == 22:
                return None
            raise AttestationError("Unsupported simple CBOR value.")

        raise AttestationError("Unsupported CBOR major type.")

    def _read(self, length: int) -> bytes:
        """Read a fixed number of bytes from the buffer.

        Args:
            length: Number of bytes to read.

        Returns:
            The bytes that were read.

        Raises:
            AttestationError: If the buffer ends early.
        """
        end = self._offset + length
        if end > len(self._data):
            raise AttestationError("Unexpected end of CBOR data.")
        chunk = self._data[self._offset:end].tobytes()
        self._offset = end
        return chunk

    def _read_length(self, additional_info: int) -> int:
        """Read a CBOR length or integer value.

        Args:
            additional_info: Initial additional-information nibble.

        Returns:
            The decoded integer value.

        Raises:
            AttestationError: If the encoding is unsupported.
        """
        if additional_info < 24:
            return additional_info
        if additional_info == 24:
            return int.from_bytes(self._read(1), "big")
        if additional_info == 25:
            return int.from_bytes(self._read(2), "big")
        if additional_info == 26:
            return int.from_bytes(self._read(4), "big")
        if additional_info == 27:
            return int.from_bytes(self._read(8), "big")
        raise AttestationError("Indefinite-length CBOR items are not supported.")


def _cbor_decode(data: bytes) -> Any:
    """Decode a CBOR byte string using the local minimal decoder.

    Args:
        data: CBOR-encoded data.

    Returns:
        The decoded Python value.

    Raises:
        AttestationError: If trailing bytes remain or decoding fails.
    """
    decoder = _CBORDecoder(data)
    value = decoder.decode()
    if decoder._offset != len(data):
        raise AttestationError("Trailing bytes remain after CBOR decoding.")
    return value


def _encode_length(major_type: int, value: int) -> bytes:
    """Encode the major type and length for a CBOR item.

    Args:
        major_type: CBOR major type.
        value: Length or integer value.

    Returns:
        Encoded header bytes.
    """
    if value < 24:
        return bytes([(major_type << 5) | value])
    if value < 2**8:
        return bytes([(major_type << 5) | 24, value])
    if value < 2**16:
        return bytes([(major_type << 5) | 25]) + value.to_bytes(2, "big")
    if value < 2**32:
        return bytes([(major_type << 5) | 26]) + value.to_bytes(4, "big")
    return bytes([(major_type << 5) | 27]) + value.to_bytes(8, "big")
