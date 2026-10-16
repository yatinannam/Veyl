import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response

from app import health
from app.audit import router as audit_router
from app.auth import router as auth_router
from app.auth.ratelimit import LoginLimiters
from app.config import Settings
from app.conversations import router as conversations_router
from app.db.engine import create_db_engine
from app.documents import router as documents_router
from app.documents.limits import BodySizeLimit
from app.documents.storage import LocalStorage
from app.errors import register_error_handlers
from app.llm.client import LLMClient, OpenAICompatibleLLM
from app.rag import router as rag_router
from app.retrieval.embeddings import Embedder, FastEmbedEmbedder, LazyEmbedder
from app.tenants import router as tenants_router
from app.users import router as users_router

API_PREFIX = "/api/v1"
MULTIPART_OVERHEAD_BYTES = 64 * 1024


def _default_embedder(settings: Settings) -> Embedder:
    return LazyEmbedder(
        lambda: FastEmbedEmbedder(settings.embedding_model, settings.embedding_cache_dir),
        settings.embedding_model,
    )


def _default_llm(settings: Settings) -> LLMClient:
    key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else None
    return OpenAICompatibleLLM(
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        api_key=key,
        timeout_seconds=settings.llm_timeout_seconds,
        max_tokens=settings.llm_max_tokens,
    )


def create_app(
    settings: Settings | None = None,
    *,
    embedder: Embedder | None = None,
    llm: LLMClient | None = None,
) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_db_engine(settings.database_url.get_secret_value())
        app.state.engine = engine
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(
        title="Veyl API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=f"{API_PREFIX}/docs",
        openapi_url=f"{API_PREFIX}/openapi.json",
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.login_limiters = LoginLimiters.create(
        max_failures=settings.login_max_failures,
        window_seconds=settings.login_window_seconds,
        max_concurrent_hashes=settings.login_max_concurrent_hashes,
    )
    app.state.storage = LocalStorage(settings.upload_dir)
    app.state.embedder = embedder or _default_embedder(settings)
    app.state.llm = llm or _default_llm(settings)
    register_error_handlers(app)

    @app.middleware("http")
    async def trace_id_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Server-generated only; a client-supplied trace id is never trusted.
        trace_id = str(uuid.uuid4())
        request.state.trace_id = trace_id
        response = await call_next(request)
        response.headers["X-Trace-Id"] = trace_id
        return response

    app.add_middleware(
        BodySizeLimit,
        max_bytes=settings.max_upload_mb * 1024 * 1024 + MULTIPART_OVERHEAD_BYTES,
        path_prefix=f"{API_PREFIX}/documents",
    )

    app.include_router(health.router, prefix=API_PREFIX)
    app.include_router(auth_router.router, prefix=API_PREFIX)
    app.include_router(tenants_router.router, prefix=API_PREFIX)
    app.include_router(users_router.router, prefix=API_PREFIX)
    app.include_router(documents_router.router, prefix=API_PREFIX)
    app.include_router(audit_router.router, prefix=API_PREFIX)
    app.include_router(rag_router.router, prefix=API_PREFIX)
    app.include_router(conversations_router.router, prefix=API_PREFIX)
    return app
