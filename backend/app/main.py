from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib import import_module
from pathlib import Path
from typing import Any, cast

from arq.connections import ArqRedis
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.composition import Composition, configure_dependencies
from app.core.config import Settings, get_settings
from app.core.db import Database
from app.core.errors import AppError, ErrorCode
from app.core.logging import LogEvent, RequestContextMiddleware, configure_logging, logger
from app.core.response import Envelope


def mount_modules(application: FastAPI) -> None:
    directory = Path(__file__).parent / "modules"
    for api_file in sorted(directory.glob("*/api.py")):
        module = import_module(f"app.modules.{api_file.parent.name}.api")
        router = module.router
        application.include_router(
            router, prefix="" if router.prefix.startswith("/api/") else "/api"
        )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        configure_logging()
        if settings.environment == "production":
            settings.require_secrets()
        database = (
            Database(settings.database_url.get_secret_value()) if settings.database_url else None
        )
        application.state.database = database
        application.state.settings = settings
        redis = None
        try:
            if database:
                redis = cast(ArqRedis, ArqRedis.from_url(settings.redis_url.get_secret_value()))
                runtime = Composition(
                    settings,
                    database,
                    redis,
                    transport=getattr(application.state, "model_transport", None),
                )
                application.state.composition = runtime
                application.state.scope_cache = runtime.cache
                application.state.datasource_inspector = runtime.inspector
                application.state.sql_executor = runtime.executor
                application.state.task_manager = runtime.tasks
            yield
        finally:
            if redis:
                await redis.aclose()
            if database:
                await database.close()

    application = FastAPI(title="AskData", version="0.1.0", lifespan=lifespan)
    application.add_middleware(RequestContextMiddleware)

    @application.exception_handler(HTTPException)
    async def http_error_handler(request: Request, error: HTTPException) -> JSONResponse:
        code = {401: ErrorCode.AUTH_FAILED, 403: ErrorCode.FORBIDDEN, 404: ErrorCode.NOT_FOUND}.get(
            error.status_code, ErrorCode.VALIDATION_ERROR
        )
        return JSONResponse(
            status_code=error.status_code,
            content=Envelope[Any](
                code=code,
                message=code.value.replace("_", " ").capitalize(),
            ).model_dump(mode="json"),
        )

    @application.exception_handler(AppError)
    async def app_error_handler(request: Request, error: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=error.http_status,
            content=Envelope[Any](
                code=error.code,
                message=error.message,
            ).model_dump(mode="json"),
        )

    @application.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, error: RequestValidationError) -> JSONResponse:
        # Pydantic errors can contain submitted secrets; never serialize their input.
        return JSONResponse(
            status_code=422,
            content=Envelope[Any](
                code=ErrorCode.VALIDATION_ERROR,
                message="Invalid request",
            ).model_dump(mode="json"),
        )

    @application.exception_handler(Exception)
    async def internal_error_handler(request: Request, error: Exception) -> JSONResponse:
        logger.info(LogEvent.FAILURE, code=ErrorCode.INTERNAL_ERROR)
        return JSONResponse(
            status_code=500,
            content=Envelope[Any](
                code=ErrorCode.INTERNAL_ERROR,
                message="Internal error",
            ).model_dump(mode="json"),
        )

    @application.get("/healthz", response_model=Envelope[dict[str, str]])
    @application.get("/api/health", response_model=Envelope[dict[str, str]])
    async def health() -> Envelope[dict[str, str]]:
        return Envelope(data={"status": "ok"})

    configure_dependencies(application)
    mount_modules(application)
    return application


app = create_app()
