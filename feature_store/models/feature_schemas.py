"""Request and response schemas for the feature store API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class FeatureSetCreate(BaseModel):
    """Payload for creating a feature set."""

    tenant_id: str
    feature_set_name: str
    features: dict[str, float]


class FeatureSetResponse(BaseModel):
    """Response model for a stored feature set."""

    tenant_id: str
    feature_set_name: str
    features: dict[str, float]
    created_at: datetime
    updated_at: datetime
    version: int


class InferenceRequest(BaseModel):
    """Payload for an inference request."""

    tenant_id: str
    feature_set_name: str
    model_name: str


class InferenceResponse(BaseModel):
    """Response model for an inference result."""

    prediction: float
    confidence: float
    latency_ms: float
    served_from_cache: bool
