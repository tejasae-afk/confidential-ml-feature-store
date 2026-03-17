"""KMS and attestation scaffold tests."""

import pytest

from feature_store.services.kms_service import KMSService


def test_spoofed_attestation_validation_path_is_not_yet_implemented() -> None:
    service = KMSService(key_id="test-key-id")

    with pytest.raises(NotImplementedError):
        service.validate_attestation_document(b"spoofed-attestation-document")
