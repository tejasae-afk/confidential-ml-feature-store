"""vsock RPC server scaffold intended to run inside the enclave."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class VsockRPCServer:
    """Skeleton server for enclave-side vsock RPC handling."""

    port: int

    def serve(self) -> None:
        """Start accepting requests inside the enclave."""
        raise NotImplementedError(
            "Enclave vsock RPC server implementation is not available in the scaffold phase.",
        )


def main() -> None:
    """Entrypoint for the enclave server process."""
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    port = int(os.getenv("ENCLAVE_PORT", "5005"))
    logger.info("Starting enclave server scaffold on port %s", port)
    server = VsockRPCServer(port=port)
    server.serve()


if __name__ == "__main__":
    main()
