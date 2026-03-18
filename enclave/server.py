"""Nitro Enclave vsock RPC server.

Protocol overview
=================
This server listens on an AF_VSOCK socket inside the enclave and exchanges
length-prefixed UTF-8 JSON frames with the parent instance.

Frame format
------------
1. 4-byte unsigned big-endian message length
2. JSON payload of that exact length

Supported requests
------------------
- {"action": "health", "echo": "ping"}
- {
    "action": "predict",
    "tenant_id": "tenant-a",
    "model_name": "fraud-model",
    "features": [0.1, 0.2, 0.3, 0.4],
    "encrypted_model_key": "<base64...>",
    "encrypted_model_blob": "<base64...>"  # optional when already cached
  }
- {"action": "list_models"}

Responses
---------
- {"status": "ok", "prediction": 1.0, "confidence": 0.95, "latency_ms": 12.3}
- {"status": "ok", "models": ["fraud-model"]}
- {"status": "error", "message": "...", "code": "DECRYPTION_FAILED"}
"""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import struct
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from enclave.inference_engine import (
    FeatureDimensionMismatchError,
    InferenceEngine,
    InferenceEngineError,
    ModelLoadError,
    ModelNotLoadedError,
)
from enclave.kms_client import EnclaveKMSClient

VMADDR_CID_ANY = getattr(socket, "VMADDR_CID_ANY", 0xFFFFFFFF)


class JsonFormatter(logging.Formatter):
    """Serialize log records to JSON for Nitro console capture."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "thread": record.threadName,
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging() -> logging.Logger:
    """Configure structured stdout logging for the enclave process.

    Returns:
        The configured module logger.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
    return logging.getLogger(__name__)


logger = configure_logging()


class VsockRPCServer:
    """Threaded vsock RPC server for enclave inference."""

    def __init__(
        self,
        *,
        bind_cid: int,
        port: int,
        engine: InferenceEngine,
        max_workers: int | None = None,
    ) -> None:
        """Initialize the server.

        Args:
            bind_cid: Vsock CID to bind.
            port: Vsock port to listen on.
            engine: Inference engine instance.
            max_workers: Thread-pool size for concurrent clients.
        """
        self._bind_cid = bind_cid
        self._port = port
        self._engine = engine
        self._max_workers = max_workers or int(os.getenv("ENCLAVE_SERVER_MAX_WORKERS", "8"))
        self._stop_event = threading.Event()
        self._server_socket: socket.socket | None = None
        self._executor = ThreadPoolExecutor(
            max_workers=self._max_workers,
            thread_name_prefix="vsock",
        )

    def serve_forever(self) -> None:
        """Run the server until shutdown is requested."""
        if not hasattr(socket, "AF_VSOCK"):
            raise RuntimeError("AF_VSOCK is not available in this Python runtime.")

        server_socket = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind((self._bind_cid, self._port))
        server_socket.listen(int(os.getenv("ENCLAVE_SERVER_BACKLOG", "128")))
        self._server_socket = server_socket

        logger.info(
            "Enclave vsock server listening on cid=%s port=%s",
            self._bind_cid,
            self._port,
        )

        try:
            while not self._stop_event.is_set():
                try:
                    connection, client_address = server_socket.accept()
                except OSError:
                    if self._stop_event.is_set():
                        break
                    raise
                self._executor.submit(self._handle_connection, connection, client_address)
        finally:
            self.stop()

    def stop(self) -> None:
        """Request a graceful shutdown."""
        if self._stop_event.is_set():
            return
        self._stop_event.set()
        if self._server_socket is not None:
            try:
                self._server_socket.close()
            except OSError:
                pass
            self._server_socket = None
        self._executor.shutdown(wait=True, cancel_futures=False)
        logger.info("Enclave vsock server stopped")

    def _handle_connection(
        self,
        connection: socket.socket,
        client_address: tuple[int, int],
    ) -> None:
        """Serve requests on a single vsock connection.

        The handler keeps the socket open so the parent can reuse the same
        connection for multiple sequential requests.

        Args:
            connection: Accepted socket connection.
            client_address: Client cid/port tuple.
        """
        connection.settimeout(float(os.getenv("ENCLAVE_SOCKET_TIMEOUT_SECONDS", "30")))
        logger.info("Accepted vsock connection from %s", client_address)
        try:
            while not self._stop_event.is_set():
                request = self._read_message(connection)
                if request is None:
                    break
                response = self._dispatch_request(request)
                self._write_message(connection, response)
        except Exception:  # noqa: BLE001
            logger.exception("Unhandled connection error")
        finally:
            try:
                connection.close()
            except OSError:
                pass
            logger.info("Closed vsock connection from %s", client_address)

    def _dispatch_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """Dispatch an RPC request.

        Args:
            request: Parsed request mapping.

        Returns:
            A response mapping.
        """
        action = request.get("action")
        if action == "health":
            return {
                "status": "ok",
                "echo": request.get("echo", "ping"),
                "models_loaded": len(self._engine.list_loaded_models()),
            }
        if action == "list_models":
            return {
                "status": "ok",
                "models": self._engine.list_loaded_models(),
            }
        if action == "predict":
            return self._handle_predict(request)
        return self._error_response(
            code="INVALID_ACTION",
            message=f"Unsupported action: {action!r}",
        )

    def _handle_predict(self, request: dict[str, Any]) -> dict[str, Any]:
        """Handle a prediction request.

        Args:
            request: Request payload.

        Returns:
            A response mapping.
        """
        model_name = request.get("model_name")
        features = request.get("features")
        tenant_id = request.get("tenant_id")
        encrypted_model_key = request.get("encrypted_model_key")
        encrypted_model_blob = request.get("encrypted_model_blob")

        if not isinstance(model_name, str) or not model_name.strip():
            return self._error_response("INVALID_REQUEST", "model_name is required.")
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            return self._error_response("INVALID_REQUEST", "tenant_id is required.")
        if not isinstance(features, list):
            return self._error_response("INVALID_REQUEST", "features must be a list of floats.")

        try:
            served_from_cache = self._engine.is_model_loaded(model_name)
            if encrypted_model_blob is not None:
                if not isinstance(encrypted_model_key, str) or not encrypted_model_key:
                    return self._error_response(
                        "INVALID_REQUEST",
                        "encrypted_model_key is required when encrypted_model_blob is provided.",
                    )
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
                return self._error_response(
                    "MODEL_NOT_FOUND",
                    "Model is not loaded and no encrypted model blob was provided.",
                )

            prediction = self._engine.predict(model_name, [float(value) for value in features])
            prediction["status"] = "ok"
            prediction["served_from_cache"] = served_from_cache
            return prediction
        except (ValueError, TypeError):
            return self._error_response(
                "INVALID_REQUEST",
                "features must contain only numeric values.",
            )
        except ModelLoadError as exc:
            return self._error_response("DECRYPTION_FAILED", str(exc))
        except ModelNotLoadedError as exc:
            return self._error_response("MODEL_NOT_FOUND", str(exc))
        except FeatureDimensionMismatchError as exc:
            return self._error_response("FEATURE_DIMENSION_MISMATCH", str(exc))
        except InferenceEngineError as exc:
            logger.exception("Inference engine error")
            return self._error_response("INFERENCE_FAILED", str(exc))
        except Exception:  # noqa: BLE001
            logger.exception("Unexpected prediction failure")
            return self._error_response(
                "INTERNAL_ERROR",
                "Unexpected enclave inference failure.",
            )

    @staticmethod
    def _read_message(connection: socket.socket) -> dict[str, Any] | None:
        """Read a single length-prefixed JSON frame.

        Args:
            connection: Connected socket.

        Returns:
            The decoded request mapping, or ``None`` on EOF.
        """
        header = _recv_exact(connection, 4)
        if header is None:
            return None
        message_length = struct.unpack("!I", header)[0]
        if message_length == 0:
            raise ValueError("Received an empty frame.")
        payload_bytes = _recv_exact(connection, message_length)
        if payload_bytes is None:
            raise ValueError("Connection closed mid-frame.")
        payload = json.loads(payload_bytes.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Frame payload must be a JSON object.")
        return payload

    @staticmethod
    def _write_message(connection: socket.socket, response: dict[str, Any]) -> None:
        """Write a single length-prefixed JSON response.

        Args:
            connection: Connected socket.
            response: Response mapping.
        """
        payload = json.dumps(response, separators=(",", ":")).encode("utf-8")
        frame = struct.pack("!I", len(payload)) + payload
        connection.sendall(frame)

    @staticmethod
    def _error_response(code: str, message: str) -> dict[str, str]:
        """Build a protocol error response.

        Args:
            code: Stable error code.
            message: Human-readable error message.

        Returns:
            Error response mapping.
        """
        return {
            "status": "error",
            "code": code,
            "message": message,
        }


def _recv_exact(connection: socket.socket, length: int) -> bytes | None:
    """Receive exactly ``length`` bytes from a socket.

    Args:
        connection: Connected socket.
        length: Number of bytes to read.

    Returns:
        The received bytes, or ``None`` on EOF before reading any bytes.
    """
    buffer = bytearray()
    while len(buffer) < length:
        chunk = connection.recv(length - len(buffer))
        if not chunk:
            return None if not buffer else bytes(buffer)
        buffer.extend(chunk)
    return bytes(buffer)


def _create_inference_engine() -> InferenceEngine:
    """Create the enclave inference engine.

    Returns:
        A configured ``InferenceEngine`` instance.
    """
    key_id = os.getenv("KMS_KEY_ID", "local-placeholder-kms-key")
    region = os.getenv("AWS_REGION", "us-east-1")
    kms_proxy_port = int(os.getenv("KMS_PROXY_PORT", "8000"))
    kms_proxy_cid = int(os.getenv("KMS_PROXY_CID", "3"))
    try:
        kms_client = EnclaveKMSClient(
            key_id=key_id,
            region=region,
            proxy_cid=kms_proxy_cid,
            proxy_port=kms_proxy_port,
        )
    except TypeError:
        # Compatibility path for older Phase 2 implementations that only accept
        # ``(key_id, region)``.
        kms_client = EnclaveKMSClient(key_id, region)
    return InferenceEngine(kms_client)


def main() -> None:
    """Start the enclave RPC server."""
    bind_cid = int(os.getenv("ENCLAVE_BIND_CID", str(VMADDR_CID_ANY)))
    port = int(os.getenv("ENCLAVE_PORT", "5005"))
    server = VsockRPCServer(
        bind_cid=bind_cid,
        port=port,
        engine=_create_inference_engine(),
    )

    def _handle_shutdown(signum: int, _frame: Any) -> None:
        logger.info("Received shutdown signal %s", signum)
        server.stop()

    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    server.serve_forever()


if __name__ == "__main__":
    main()
