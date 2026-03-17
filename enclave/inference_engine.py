"""scikit-learn inference engine scaffold."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any


class InferenceEngine:
    """Load a protected model and execute predictions inside the enclave."""

    def __init__(self, model_path: str) -> None:
        self.model_path = Path(model_path)

    def load_model(self) -> None:
        raise NotImplementedError("Model loading is not implemented in the scaffold phase.")

    def predict(self, features: Mapping[str, Any]) -> Any:
        raise NotImplementedError("Prediction logic is not implemented in the scaffold phase.")
