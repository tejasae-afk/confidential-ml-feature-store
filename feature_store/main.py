"""FastAPI application entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from feature_store import __version__
from feature_store.config import Settings, get_settings
from feature_store.routers import features, health, inference
from feature_store.services.dynamo_service import DynamoDBService
from feature_store.services.feature_service import FeatureService
from feature_store.utils.exceptions import register_exception_handlers
from feature_store.utils.logger import (
    clear_log_context,
    configure_logging,
    get_logger,
    set_log_context,
)

logger = get_logger(__name__)
_REQUEST_ID_HEADER = "X-Request-ID"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialize and tear down application resources.

    Args:
        app: FastAPI application instance.

    Yields:
        ``None`` while the application is serving requests.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    app.state.settings = settings
    app.state.dynamo_service = DynamoDBService(
        table_name=settings.dynamodb_table_name,
        region_name=settings.aws_region,
        endpoint_url=settings.dynamodb_endpoint,
    )
    app.state.feature_service = FeatureService(app.state.dynamo_service)

    logger.info("Application startup complete")
    try:
        yield
    finally:
        logger.info("Application shutdown complete")
        clear_log_context()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Returns:
        A configured ``FastAPI`` application.
    """
    settings: Settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="Confidential ML Feature Store",
        version=__version__,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[_REQUEST_ID_HEADER],
    )

    @app.middleware("http")
    async def request_context_middleware(request: Request, call_next) -> Response:
        request_id = request.headers.get(_REQUEST_ID_HEADER, str(uuid4()))
        request.state.request_id = request_id
        set_log_context(
            request_id=request_id,
            tenant_id=request.headers.get("X-Tenant-ID"),
        )

        try:
            response: Response = await call_next(request)
        finally:
            clear_log_context()

        response.headers[_REQUEST_ID_HEADER] = request_id
        return response

    app.include_router(health.router)
    app.include_router(features.router)
    app.include_router(inference.router)
    register_exception_handlers(app)
    return app


app = create_app()
