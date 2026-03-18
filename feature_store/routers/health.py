"""Health-check router."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from feature_store import __version__
from feature_store.services.dynamo_service import DynamoDBService

router = APIRouter(tags=["health"])


class HealthCheckResponse(BaseModel):
    """Response model for the health endpoint."""

    status: str
    version: str
    dynamodb: dict[str, Any]


@router.get("/health", response_model=HealthCheckResponse)
async def health(request: Request) -> HealthCheckResponse:
    """Return application health and DynamoDB connectivity state.

    Args:
        request: Incoming FastAPI request.

    Returns:
        A health summary.
    """
    dynamo_service: DynamoDBService = request.app.state.dynamo_service
    reachable = dynamo_service.check_connectivity()
    return HealthCheckResponse(
        status="ok" if reachable else "degraded",
        version=__version__,
        dynamodb={
            "reachable": reachable,
            "table_name": dynamo_service.table_name,
        },
    )
