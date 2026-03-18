"""In-enclave model loading and inference utilities.

The inference engine expects a self-contained encrypted model bundle encoded as JSON
bytes. The bundle format is:

{
  "tenant_id": "tenant-a",
  "model_name": "fraud-model",
  "algorithm": "AESGCM",
  "encrypted_model_key": "<base64 KMS-wrapped data key>",
  "encrypted_model_blob": "<base64 AES-GCM ciphertext envelope>",
  "aad_b64": "<optional base64 associated data>",
  "feature_dimension": 4
}

The ``encrypted_model_blob`` value itself is a JSON document produced by the host
training pipeline and contains the AES-GCM nonce, ciphertext, and optional model
metadata. The engine decrypts the KMS-wrapped data key via ``EnclaveKMSClient``,
decrypts the model bytes in memory, deserializes them with ``joblib``, and caches
loaded models in an LRU map.
"""

from __future__ import annotations

import base64
import json
import os
from collections import OrderedDict
from dataclasses import dataclass
from io import BytesIO
from threading import RLock
from time import perf_counter
from typing import Any, Protocol, cast

import joblib
import numpy as np
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class InferenceEngineError(RuntimeError):
    """Base class for enclave inference errors."""


class ModelLoadError(InferenceEngineError):
    """Raised when an encrypted model bundle cannot be loaded."""


class ModelNotLoadedError(InferenceEngineError):
    """Raised when a prediction is requested for an unloaded model."""


class FeatureDimensionMismatchError(InferenceEngineError):
    """Raised when the feature vector size does not match model expectations."""


class KeyDecryptor(Protocol):
    """Protocol implemented by enclave-side KMS clients."""

    def decrypt_data_key(self, ciphertext_blob: bytes, tenant_id: str, **kwargs: Any) -> bytes:
        """Decrypt a KMS-wrapped data key.

        Args:
            ciphertext_blob: The KMS-wrapped data key.
            tenant_id: Tenant identifier used in the encryption context.
            **kwargs: Optional implementation-specific arguments.

        Returns:
            The plaintext data key bytes.
        """


@dataclass(slots=True)
class _LoadedModel:
    """Represents a model cached inside the enclave."""

    model_name: str
    model: Any
    feature_dimension: int


class InferenceEngine:
    """Loads encrypted model artifacts and serves predictions from memory."""

    def __init__(
        self,
        kms_client: KeyDecryptor,
        *,
        max_models_in_memory: int | None = None,
    ) -> None:
        """Initialize the inference engine.

        Args:
            kms_client: Enclave-side KMS client used to unwrap data keys.
            max_models_in_memory: Optional in-memory model cache limit.
        """
        self._kms_client = kms_client
        self._max_models = max_models_in_memory or int(
            os.getenv("ENCLAVE_MAX_LOADED_MODELS", "8"),
        )
        if self._max_models <= 0:
            raise ValueError("max_models_in_memory must be greater than 0.")

        self._models: OrderedDict[str, _LoadedModel] = OrderedDict()
        self._lock = RLock()

    def is_model_loaded(self, model_name: str) -> bool:
        """Return whether a model is already cached.

        Args:
            model_name: Model identifier.

        Returns:
            ``True`` when the model is already loaded.
        """
        with self._lock:
            return model_name in self._models

    def load_model(self, model_name: str, encrypted_weights: bytes) -> None:
        """Load an encrypted model bundle into enclave memory.

        Args:
            model_name: Model identifier.
            encrypted_weights: JSON bundle containing the encrypted model payload,
                encrypted data key, and tenant context.

        Raises:
            ModelLoadError: If decryption or deserialization fails.
        """
        normalized_model_name = model_name.strip()
        if not normalized_model_name:
            raise ModelLoadError("model_name must not be empty.")
        if not encrypted_weights:
            raise ModelLoadError("encrypted_weights must not be empty.")

        with self._lock:
            cached = self._models.get(normalized_model_name)
            if cached is not None:
                self._models.move_to_end(normalized_model_name)
                return

        bundle = self._parse_load_request(encrypted_weights)
        if bundle["model_name"] != normalized_model_name:
            raise ModelLoadError(
                "The model bundle name does not match the requested model name.",
            )

        encrypted_key = base64.b64decode(bundle["encrypted_model_key"], validate=True)
        encrypted_blob_json = base64.b64decode(bundle["encrypted_model_blob"], validate=True)
        tenant_id = bundle["tenant_id"]

        try:
            plaintext_data_key = self._decrypt_data_key(encrypted_key, tenant_id)
        except Exception as exc:  # noqa: BLE001
            raise ModelLoadError("Failed to decrypt the model data key.") from exc

        model_payload = self._parse_model_payload(encrypted_blob_json)
        nonce = base64.b64decode(model_payload["nonce_b64"], validate=True)
        ciphertext = base64.b64decode(model_payload["ciphertext_b64"], validate=True)
        aad = self._build_aad(
            tenant_id=tenant_id,
            model_name=normalized_model_name,
            aad_b64=model_payload.get("aad_b64"),
        )

        try:
            plaintext_model = AESGCM(plaintext_data_key).decrypt(nonce, ciphertext, aad)
        except (InvalidTag, ValueError) as exc:
            raise ModelLoadError("Failed to decrypt the model artifact.") from exc

        try:
            model = joblib.load(BytesIO(plaintext_model))
        except Exception as exc:  # noqa: BLE001
            raise ModelLoadError("Failed to deserialize the model artifact.") from exc

        feature_dimension = self._extract_feature_dimension(model, model_payload)
        loaded_model = _LoadedModel(
            model_name=normalized_model_name,
            model=model,
            feature_dimension=feature_dimension,
        )

        with self._lock:
            self._models[normalized_model_name] = loaded_model
            self._models.move_to_end(normalized_model_name)
            while len(self._models) > self._max_models:
                self._models.popitem(last=False)

    def predict(self, model_name: str, features: list[float]) -> dict[str, Any]:
        """Run inference with a loaded model.

        Args:
            model_name: Model identifier.
            features: Ordered feature vector.

        Returns:
            A prediction payload containing the prediction, confidence, latency,
            and a cache-hit marker.

        Raises:
            ModelNotLoadedError: If the model has not been loaded.
            FeatureDimensionMismatchError: If the input shape is invalid.
        """
        normalized_model_name = model_name.strip()
        if not normalized_model_name:
            raise ModelNotLoadedError("model_name must not be empty.")

        with self._lock:
            loaded_model = self._models.get(normalized_model_name)
            if loaded_model is None:
                raise ModelNotLoadedError(
                    f"Model '{normalized_model_name}' is not loaded in enclave memory.",
                )
            self._models.move_to_end(normalized_model_name)

        validated_features = self._validate_features(features, loaded_model.feature_dimension)
        feature_matrix = np.asarray([validated_features], dtype=np.float64)

        started_at = perf_counter()
        try:
            raw_prediction = loaded_model.model.predict(feature_matrix)
        except Exception as exc:  # noqa: BLE001
            raise InferenceEngineError("Model prediction failed.") from exc
        latency_ms = (perf_counter() - started_at) * 1000.0

        prediction = float(np.asarray(raw_prediction).reshape(-1)[0])
        confidence = self._compute_confidence(loaded_model.model, feature_matrix)

        return {
            "prediction": prediction,
            "confidence": confidence,
            "latency_ms": latency_ms,
            "served_from_cache": True,
        }

    def list_loaded_models(self) -> list[str]:
        """Return the names of models currently cached in memory.

        Returns:
            Loaded model names in LRU order from oldest to newest.
        """
        with self._lock:
            return list(self._models.keys())

    def unload_model(self, model_name: str) -> None:
        """Remove a model from the in-memory cache.

        Args:
            model_name: Model identifier.
        """
        normalized_model_name = model_name.strip()
        with self._lock:
            self._models.pop(normalized_model_name, None)

    def _decrypt_data_key(self, encrypted_key: bytes, tenant_id: str) -> bytes:
        """Decrypt a wrapped data key using the configured KMS client.

        This helper supports both the richer Phase 2 ``decrypt_data_key`` API and
        the earlier scaffold-style ``decrypt_ciphertext`` method so the engine can
        run against either implementation during incremental development.

        Args:
            encrypted_key: Wrapped data key bytes.
            tenant_id: Tenant identifier bound into the encryption context.

        Returns:
            Plaintext data key bytes.

        Raises:
            ModelLoadError: If the KMS client does not expose a supported decrypt method.
        """
        decrypt_data_key = getattr(self._kms_client, "decrypt_data_key", None)
        if callable(decrypt_data_key):
            return decrypt_data_key(encrypted_key, tenant_id)

        decrypt_ciphertext = getattr(self._kms_client, "decrypt_ciphertext", None)
        if callable(decrypt_ciphertext):
            return decrypt_ciphertext(encrypted_key)

        raise ModelLoadError(
            "The configured enclave KMS client does not support data-key decrypts."
        )

    def _parse_load_request(self, encrypted_weights: bytes) -> dict[str, str]:
        """Parse the encrypted load request bundle.

        Args:
            encrypted_weights: JSON bundle bytes.

        Returns:
            The decoded mapping.

        Raises:
            ModelLoadError: If the bundle is malformed.
        """
        try:
            payload = json.loads(encrypted_weights.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ModelLoadError("The model bundle is not valid UTF-8 JSON.") from exc

        if not isinstance(payload, dict):
            raise ModelLoadError("The model bundle must be a JSON object.")

        required_fields = {
            "tenant_id",
            "model_name",
            "encrypted_model_key",
            "encrypted_model_blob",
        }
        missing_fields = [
            field for field in required_fields if not isinstance(payload.get(field), str)
        ]
        if missing_fields:
            raise ModelLoadError(
                "The model bundle is missing required fields: "
                f"{', '.join(sorted(missing_fields))}.",
            )

        return cast(dict[str, str], payload)

    def _parse_model_payload(self, encrypted_blob_json: bytes) -> dict[str, Any]:
        """Parse the encrypted model artifact payload.

        Args:
            encrypted_blob_json: JSON payload bytes from ``encrypted_model.bin``.

        Returns:
            A mapping containing ciphertext, nonce, and optional metadata.

        Raises:
            ModelLoadError: If the payload is malformed.
        """
        try:
            payload = json.loads(encrypted_blob_json.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ModelLoadError("The encrypted model artifact is not valid UTF-8 JSON.") from exc

        if not isinstance(payload, dict):
            raise ModelLoadError("The encrypted model artifact must be a JSON object.")
        if payload.get("algorithm") != "AESGCM":
            raise ModelLoadError("Only AESGCM-encrypted model artifacts are supported.")
        if not isinstance(payload.get("nonce_b64"), str) or not isinstance(
            payload.get("ciphertext_b64"),
            str,
        ):
            raise ModelLoadError("The encrypted model artifact is missing ciphertext fields.")
        return payload

    def _extract_feature_dimension(self, model: Any, model_payload: dict[str, Any]) -> int:
        """Determine the model's expected feature dimension.

        Args:
            model: Deserialized scikit-learn model.
            model_payload: Parsed encrypted model payload.

        Returns:
            Expected feature dimension.

        Raises:
            ModelLoadError: If the feature dimension cannot be determined.
        """
        if hasattr(model, "n_features_in_"):
            n_features = int(model.n_features_in_)
            if n_features <= 0:
                raise ModelLoadError("The model reported an invalid feature dimension.")
            return n_features

        feature_dimension = model_payload.get("feature_dimension")
        if isinstance(feature_dimension, int) and feature_dimension > 0:
            return feature_dimension

        raise ModelLoadError(
            "Unable to determine the model feature dimension from metadata or the model object.",
        )

    @staticmethod
    def _build_aad(*, tenant_id: str, model_name: str, aad_b64: str | None) -> bytes:
        """Build AES-GCM associated data.

        Args:
            tenant_id: Tenant identifier.
            model_name: Model identifier.
            aad_b64: Optional base64-encoded associated data.

        Returns:
            Associated-data bytes.
        """
        if aad_b64:
            return base64.b64decode(aad_b64, validate=True)
        return f"{tenant_id}:{model_name}".encode()

    @staticmethod
    def _validate_features(features: list[float], expected_dimension: int) -> list[float]:
        """Validate the provided feature vector.

        Args:
            features: Feature vector from the host.
            expected_dimension: Expected feature dimension.

        Returns:
            A normalized list of floats.

        Raises:
            FeatureDimensionMismatchError: If the vector size is invalid.
        """
        if not isinstance(features, list) or not features:
            raise FeatureDimensionMismatchError("A non-empty feature vector is required.")

        normalized_features = [float(value) for value in features]
        if len(normalized_features) != expected_dimension:
            raise FeatureDimensionMismatchError(
                "Feature dimension mismatch: "
                f"expected {expected_dimension}, received {len(normalized_features)}.",
            )
        return normalized_features

    @staticmethod
    def _compute_confidence(
        model: Any,
        feature_matrix: np.ndarray[Any, np.dtype[np.float64]],
    ) -> float:
        """Compute a confidence score for a prediction.

        Args:
            model: Deserialized scikit-learn model.
            feature_matrix: 2D feature matrix.

        Returns:
            Confidence in the range ``[0.0, 1.0]`` when possible.
        """
        if hasattr(model, "predict_proba"):
            probabilities = np.asarray(model.predict_proba(feature_matrix), dtype=np.float64)
            return float(np.max(probabilities[0]))
        return 1.0
