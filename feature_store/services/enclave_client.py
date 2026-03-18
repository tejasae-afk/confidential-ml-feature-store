"""Host-side Nitro Enclave vsock client and local development mock.

Protocol overview
=================
The host and enclave exchange length-prefixed UTF-8 JSON frames over AF_VSOCK.
Each frame is:

1. 4-byte unsigned big-endian frame length
2. JSON payload

The request payload always includes an ``action`` field and action-specific data.
The client keeps connections open after each request so they can be reused by the
connection pool for future sequential RPC calls.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import struct
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from enclave.inference_engine import (
    FeatureDimensionMismatchError,
    InferenceEngine,
    ModelLoadError,
    ModelNotLoadedError,
)
from fastapi import Request

from feature_store.config import get_settings
from feature_store.utils.exceptions import AppException, EnclaveCommunicationError

SocketFactory = Callable[[int, int, float], socket.socket]
SleepFn = Callable[[float], None]


class EnclaveTimeoutError(EnclaveCommunicationError):
    """Raised when a vsock request exceeds its deadline."""

    error_code = "enclave_timeout"


class MalformedEnclaveResponseError(EnclaveCommunicationError):
    """Raised when the enclave returns a malformed response."""

    error_code = "malformed_enclave_response"


class RemoteModelNotFoundError(AppException):
    """Raised when the enclave reports that a model is unavailable."""

    status_code = 404
    error_code = "remote_model_not_found"


class RemoteFeatureDimensionMismatchError(AppException):
    """Raised when the enclave rejects a feature vector shape."""

    status_code = 400
    error_code = "feature_dimension_mismatch"


class RemoteInvalidRequestError(AppException):
    """Raised when the enclave rejects the request payload."""

    status_code = 400
    error_code = "invalid_enclave_request"


class KeyResolver(Protocol):
    """Protocol for local key resolvers used by ``MockEnclaveClient``."""

    def __call__(self, encrypted_key: bytes, tenant_id: str) -> bytes:
        """Resolve a plaintext data key.

        Args:
            encrypted_key: Wrapped data key bytes.
            tenant_id: Tenant identifier.

        Returns:
            Plaintext data key bytes.
        """


class _StaticKeyResolverClient:
    """Adapter that makes a local resolver look like ``EnclaveKMSClient``."""

    def __init__(self, resolver: KeyResolver) -> None:
        self._resolver = resolver

    def decrypt_data_key(self, ciphertext_blob: bytes, tenant_id: str, **_: Any) -> bytes:
        """Resolve the plaintext key via the injected callback.

        Args:
            ciphertext_blob: Wrapped data key bytes.
            tenant_id: Tenant identifier.
            **_: Ignored compatibility kwargs.

        Returns:
            Plaintext data key bytes.
        """
        return self._resolver(ciphertext_blob, tenant_id)


class EnclaveClient:
    """Host-side client for the enclave inference service."""

    def __init__(
        self,
        cid: int,
        port: int,
        timeout_seconds: float = 30.0,
        *,
        max_retries: int = 3,
        max_pool_size: int = 4,
        socket_factory: SocketFactory | None = None,
        sleep_fn: SleepFn = time.sleep,
    ) -> None:
        """Initialize the client.

        Args:
            cid: Enclave CID.
            port: Enclave vsock port.
            timeout_seconds: Per-request timeout.
            max_retries: Maximum request attempts.
            max_pool_size: Maximum number of pooled connections.
            socket_factory: Optional socket factory for tests.
            sleep_fn: Sleep function used for retry backoff.
        """
        self._cid = cid
        self._port = port
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._max_pool_size = max_pool_size
        self._socket_factory = socket_factory or self._default_socket_factory
        self._sleep_fn = sleep_fn
        self._pool: list[socket.socket] = []
        self._pool_lock = threading.Lock()

    def send_request(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Send a single request to the enclave RPC service.

        Args:
            action: RPC action name.
            payload: Action-specific payload.

        Returns:
            Decoded response mapping.

        Raises:
            EnclaveCommunicationError: If the request cannot be completed.
            EnclaveTimeoutError: If the enclave does not respond in time.
            MalformedEnclaveResponseError: If the response is malformed.
        """
        request_payload = dict(payload)
        request_payload["action"] = action
        encoded_request = _encode_frame(request_payload)

        last_error: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            connection = None
            try:
                connection = self._acquire_connection()
                connection.sendall(encoded_request)
                response = _decode_frame(_recv_exact(connection, 4), connection)
                self._release_connection(connection)
                return response
            except TimeoutError as exc:
                last_error = exc
                if connection is not None:
                    self._discard_connection(connection)
                if attempt >= self._max_retries:
                    raise EnclaveTimeoutError(
                        "Timed out waiting for the enclave response.",
                    ) from exc
            except (ConnectionError, OSError) as exc:
                last_error = exc
                if connection is not None:
                    self._discard_connection(connection)
                if attempt >= self._max_retries:
                    raise EnclaveCommunicationError(
                        "Failed to communicate with the enclave over vsock.",
                    ) from exc
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
                if connection is not None:
                    self._discard_connection(connection)
                raise MalformedEnclaveResponseError(
                    "The enclave returned a malformed response.",
                ) from exc

            backoff_seconds = min(0.1 * (2 ** (attempt - 1)), 1.0)
            self._sleep_fn(backoff_seconds)

        raise EnclaveCommunicationError("The enclave request failed.") from last_error

    def predict(
        self,
        model_name: str,
        features: list[float],
        encrypted_model_key: bytes,
        *,
        encrypted_model_blob: bytes | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        """Run an inference request against the enclave.

        Args:
            model_name: Model identifier.
            features: Ordered feature vector.
            encrypted_model_key: Wrapped model data key.
            encrypted_model_blob: Optional encrypted model bundle for first load.
            tenant_id: Optional tenant identifier used for decrypt context.

        Returns:
            The enclave prediction payload.

        Raises:
            AppException: On remote model/shape errors.
            EnclaveCommunicationError: On transport or generic enclave failures.
        """
        payload: dict[str, Any] = {
            "model_name": model_name,
            "features": [float(value) for value in features],
            "encrypted_model_key": base64.b64encode(encrypted_model_key).decode("ascii"),
        }
        if encrypted_model_blob is not None:
            payload["encrypted_model_blob"] = base64.b64encode(
                encrypted_model_blob,
            ).decode("ascii")
        if tenant_id is not None:
            payload["tenant_id"] = tenant_id

        response = self.send_request("predict", payload)
        return self._normalize_predict_response(response)

    def health_check(self) -> bool:
        """Check whether the enclave is responsive.

        Returns:
            ``True`` when the enclave replies successfully.
        """
        try:
            response = self.send_request("health", {"echo": "ping"})
        except EnclaveCommunicationError:
            return False
        return response.get("status") == "ok" and response.get("echo") == "ping"

    def list_models(self) -> list[str]:
        """List models currently loaded inside the enclave.

        Returns:
            Loaded model names.
        """
        response = self.send_request("list_models", {})
        if response.get("status") != "ok":
            raise EnclaveCommunicationError("The enclave did not return a valid model list.")
        models = response.get("models", [])
        if not isinstance(models, list) or not all(isinstance(item, str) for item in models):
            raise MalformedEnclaveResponseError("The enclave returned an invalid model list.")
        return list(models)

    def _acquire_connection(self) -> socket.socket:
        """Acquire a pooled vsock connection.

        Returns:
            A connected socket.
        """
        with self._pool_lock:
            while self._pool:
                return self._pool.pop()
        return self._socket_factory(self._cid, self._port, self._timeout_seconds)

    def _release_connection(self, connection: socket.socket) -> None:
        """Return a connection to the pool.

        Args:
            connection: Connected socket.
        """
        with self._pool_lock:
            if len(self._pool) >= self._max_pool_size:
                self._safe_close(connection)
                return
            self._pool.append(connection)

    def _discard_connection(self, connection: socket.socket) -> None:
        """Close a broken connection.

        Args:
            connection: Socket to close.
        """
        self._safe_close(connection)

    def _normalize_predict_response(self, response: dict[str, Any]) -> dict[str, Any]:
        """Validate and normalize a prediction response.

        Args:
            response: Raw response mapping.

        Returns:
            Normalized prediction payload.

        Raises:
            AppException: If the enclave reported a structured error.
            MalformedEnclaveResponseError: If the response is invalid.
        """
        status = response.get("status")
        if status == "error":
            code = response.get("code")
            message = str(response.get("message", "Enclave inference failed."))
            if code == "MODEL_NOT_FOUND":
                raise RemoteModelNotFoundError(message)
            if code == "FEATURE_DIMENSION_MISMATCH":
                raise RemoteFeatureDimensionMismatchError(message)
            if code == "INVALID_REQUEST":
                raise RemoteInvalidRequestError(message)
            raise EnclaveCommunicationError(message)

        if status != "ok":
            raise MalformedEnclaveResponseError(
                "The enclave response is missing a valid status field.",
            )

        for field in ("prediction", "confidence", "latency_ms"):
            if field not in response:
                raise MalformedEnclaveResponseError(
                    f"The enclave response is missing '{field}'.",
                )

        return {
            "status": "ok",
            "prediction": float(response["prediction"]),
            "confidence": float(response["confidence"]),
            "latency_ms": float(response["latency_ms"]),
            "served_from_cache": bool(response.get("served_from_cache", False)),
        }

    @staticmethod
    def _default_socket_factory(cid: int, port: int, timeout_seconds: float) -> socket.socket:
        """Create a connected AF_VSOCK socket.

        Args:
            cid: Enclave CID.
            port: Enclave port.
            timeout_seconds: Socket timeout.

        Returns:
            A connected socket.
        """
        if not hasattr(socket, "AF_VSOCK"):
            raise EnclaveCommunicationError(
                "AF_VSOCK is not available on this host. "
                "Use MockEnclaveClient for local development.",
            )
        connection = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
        connection.settimeout(timeout_seconds)
        connection.connect((cid, port))
        return connection

    @staticmethod
    def _safe_close(connection: socket.socket) -> None:
        """Close a socket without surfacing close-time errors.

        Args:
            connection: Socket to close.
        """
        try:
            connection.close()
        except OSError:
            pass


class MockEnclaveClient:
    """Local development client that mirrors the real enclave client interface."""

    def __init__(
        self,
        *,
        key_resolver: KeyResolver | None = None,
        max_models_in_memory: int | None = None,
    ) -> None:
        """Initialize the mock client.

        Args:
            key_resolver: Callback that resolves wrapped data keys.
            max_models_in_memory: Optional model cache size.
        """
        resolver = key_resolver or (lambda encrypted_key, _tenant_id: encrypted_key)
        self._engine = InferenceEngine(
            _StaticKeyResolverClient(resolver),
            max_models_in_memory=max_models_in_memory,
        )

    def send_request(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Process a request in-process using the same protocol semantics.

        Args:
            action: RPC action name.
            payload: Action-specific payload.

        Returns:
            Response mapping.
        """
        if action == "health":
            return {
                "status": "ok",
                "echo": payload.get("echo", "ping"),
                "models_loaded": len(self._engine.list_loaded_models()),
            }

        if action == "list_models":
            return {
                "status": "ok",
                "models": self._engine.list_loaded_models(),
            }

        if action != "predict":
            return {
                "status": "error",
                "code": "INVALID_ACTION",
                "message": f"Unsupported action: {action!r}",
            }

        model_name = payload.get("model_name")
        features = payload.get("features")
        tenant_id = payload.get("tenant_id")
        encrypted_model_key = payload.get("encrypted_model_key")
        encrypted_model_blob = payload.get("encrypted_model_blob")

        if not isinstance(model_name, str) or not isinstance(features, list):
            return {
                "status": "error",
                "code": "INVALID_REQUEST",
                "message": "model_name and features are required.",
            }
        if not isinstance(tenant_id, str) or not tenant_id:
            return {
                "status": "error",
                "code": "INVALID_REQUEST",
                "message": "tenant_id is required.",
            }

        try:
            served_from_cache = self._engine.is_model_loaded(model_name)
            if encrypted_model_blob is not None:
                if not isinstance(encrypted_model_key, str) or not encrypted_model_key:
                    return {
                        "status": "error",
                        "code": "INVALID_REQUEST",
                        "message": (
                            "encrypted_model_key is required when encrypted_model_blob is provided."
                        ),
                    }
                load_bundle = {
                    "tenant_id": tenant_id,
                    "model_name": model_name,
                    "encrypted_model_key": encrypted_model_key,
                    "encrypted_model_blob": encrypted_model_blob,
                }
                self._engine.load_model(
                    model_name,
                    json.dumps(load_bundle, separators=(",", ":")).encode("utf-8"),
                )
                served_from_cache = False
            elif not served_from_cache:
                return {
                    "status": "error",
                    "code": "MODEL_NOT_FOUND",
                    "message": "Model is not loaded and no encrypted model blob was provided.",
                }

            response = self._engine.predict(model_name, [float(value) for value in features])
            response["status"] = "ok"
            response["served_from_cache"] = served_from_cache
            return response
        except FeatureDimensionMismatchError as exc:
            return {
                "status": "error",
                "code": "FEATURE_DIMENSION_MISMATCH",
                "message": str(exc),
            }
        except ModelNotLoadedError as exc:
            return {
                "status": "error",
                "code": "MODEL_NOT_FOUND",
                "message": str(exc),
            }
        except ModelLoadError as exc:
            return {
                "status": "error",
                "code": "DECRYPTION_FAILED",
                "message": str(exc),
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "status": "error",
                "code": "INVALID_REQUEST",
                "message": str(exc),
            }

    def predict(
        self,
        model_name: str,
        features: list[float],
        encrypted_model_key: bytes,
        *,
        encrypted_model_blob: bytes | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        """Mirror ``EnclaveClient.predict`` for local development.

        Args:
            model_name: Model identifier.
            features: Ordered feature vector.
            encrypted_model_key: Wrapped data key bytes.
            encrypted_model_blob: Optional encrypted model bundle bytes.
            tenant_id: Optional tenant identifier.

        Returns:
            Normalized prediction response.
        """
        response = self.send_request(
            "predict",
            {
                "model_name": model_name,
                "features": [float(value) for value in features],
                "tenant_id": tenant_id,
                "encrypted_model_key": base64.b64encode(encrypted_model_key).decode("ascii"),
                "encrypted_model_blob": (
                    base64.b64encode(encrypted_model_blob).decode("ascii")
                    if encrypted_model_blob is not None
                    else None
                ),
            },
        )
        return EnclaveClient._normalize_predict_response(self, response)

    def health_check(self) -> bool:
        """Return whether the mock enclave is healthy.

        Returns:
            ``True`` when the mock responds to the health action.
        """
        response = self.send_request("health", {"echo": "ping"})
        return response.get("status") == "ok"

    def list_models(self) -> list[str]:
        """Return the models currently loaded by the mock client.

        Returns:
            Loaded model names.
        """
        response = self.send_request("list_models", {})
        models = response.get("models", [])
        if not isinstance(models, list):
            raise MalformedEnclaveResponseError(
                "The mock enclave returned an invalid model list.",
            )
        return [str(item) for item in models]


def get_enclave_client(request: Request) -> EnclaveClient | MockEnclaveClient:
    """Resolve the configured enclave client.

    Args:
        request: Incoming FastAPI request.

    Returns:
        The configured enclave client instance.
    """
    existing_client = getattr(request.app.state, "enclave_client", None)
    if existing_client is not None:
        return existing_client

    settings = get_settings()
    if settings.use_mock_enclave:
        client: EnclaveClient | MockEnclaveClient = MockEnclaveClient()
    else:
        timeout_seconds = float(os.getenv("ENCLAVE_RPC_TIMEOUT_SECONDS", "30"))
        max_retries = int(os.getenv("ENCLAVE_RPC_MAX_RETRIES", "3"))
        max_pool_size = int(os.getenv("ENCLAVE_RPC_POOL_SIZE", "4"))
        client = EnclaveClient(
            cid=settings.enclave_cid,
            port=settings.enclave_port,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            max_pool_size=max_pool_size,
        )
    request.app.state.enclave_client = client
    return client


def _encode_frame(payload: Mapping[str, Any]) -> bytes:
    """Encode a request or response frame.

    Args:
        payload: JSON-serializable mapping.

    Returns:
        Length-prefixed frame bytes.
    """
    encoded_payload = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return struct.pack("!I", len(encoded_payload)) + encoded_payload


def _recv_exact(connection: socket.socket, length: int) -> bytes:
    """Receive exactly ``length`` bytes.

    Args:
        connection: Connected socket.
        length: Number of bytes to receive.

    Returns:
        The received bytes.

    Raises:
        ConnectionError: If the socket closes early.
    """
    buffer = bytearray()
    while len(buffer) < length:
        chunk = connection.recv(length - len(buffer))
        if not chunk:
            raise ConnectionError("Connection closed before the full frame was received.")
        buffer.extend(chunk)
    return bytes(buffer)


def _decode_frame(header: bytes, connection: socket.socket) -> dict[str, Any]:
    """Decode a length-prefixed JSON response frame.

    Args:
        header: 4-byte frame header.
        connection: Connected socket.

    Returns:
        Decoded response mapping.
    """
    message_length = struct.unpack("!I", header)[0]
    if message_length <= 0:
        raise ValueError("The enclave returned an invalid frame length.")
    payload = _recv_exact(connection, message_length)
    decoded_payload = json.loads(payload.decode("utf-8"))
    if not isinstance(decoded_payload, dict):
        raise ValueError("The enclave response payload must be a JSON object.")
    return decoded_payload
