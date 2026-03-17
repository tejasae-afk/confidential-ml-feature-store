"""Host-side enclave client scaffold tests."""

import pytest

from feature_store.services.enclave_client import EnclaveClient


def test_enclave_client_predict_contract_is_scaffolded() -> None:
    client = EnclaveClient(cid=16, port=5005, timeout_seconds=5.0)

    with pytest.raises(NotImplementedError):
        client.predict({"tenant_id": "tenant-alpha", "entity_id": "user-123"})
