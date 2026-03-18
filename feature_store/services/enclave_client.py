"""Host-side vsock RPC client abstraction."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class EnclaveClient:
    """Client responsible for host-to-enclave communication."""

    def __init__(self, cid: int, port: int, timeout_seconds: float = 5.0) -> None:
        self.cid = cid
        self.port = port
        self.timeout_seconds = timeout_seconds

    def ping(self) -> bool:
        """Check whether the enclave is reachable."""
        raise NotImplementedError(
            "Enclave health probing is not implemented in the scaffold phase.",
        )

    def send_request(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Send a raw RPC payload to the enclave."""
        raise NotImplementedError(
            "vsock RPC request handling is not implemented in the scaffold phase.",
        )

    def predict(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Submit an inference request to the enclave."""
        raise NotImplementedError(
            "Enclave prediction calls are not implemented in the scaffold phase.",
        )
