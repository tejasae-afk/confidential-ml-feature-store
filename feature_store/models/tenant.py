"""Tenant domain model."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class Tenant(BaseModel):
    """Represents an authenticated tenant."""

    tenant_id: str
    api_key: str
    created_at: datetime
    is_active: bool
    allowed_models: list[str]
