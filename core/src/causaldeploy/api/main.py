import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from falkordb.asyncio import FalkorDB as AsyncFalkorDB
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis
from sqlalchemy import text

from causaldeploy import __version__
from causaldeploy.api.auth import router as auth_router
from causaldeploy.api.errors import install_error_handlers
from causaldeploy.api.routes import v1, webhooks
from causaldeploy.config import get_settings
from causaldeploy.db.session import get_engine
from causaldeploy.logging import configure_logging, request_id_var
from causaldeploy.observability import (
    HTTP_SECONDS,
    QUEUE_DEPTH,
    init_tracing,
    render_metrics,
)


async def _check(coro_fn) -> bool:  # type: ignore[no-untyped-def]
    try:
        await asyncio.wait_for(coro_fn(), timeout=2.0)
    except Exception:
        return False
    return True


async def _postgres() -> None:
    async with get_engine().connect() as c:
        await c.execute(text("SELECT 1"))


async def _redis() -> None:
    r = Redis.from_url(get_settings().redis_url)
    try:
        await r.ping()
    finally:
        await r.aclose()


async def _falkordb() -> None:
    s = get_settings()
    db = AsyncFalkorDB(host=s.falkordb_host, port=s.falkordb_port)
    try:
        await db.connection.ping()
    finally:
        await db.connection.aclose()


def create_app() -> FastAPI:
    settings = (
        get_settings()
    )  # fail fast: invalid config raises a validation error naming the field
    configure_logging(settings)
    init_tracing(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await get_engine().dispose()

    app = FastAPI(title="Kaizen", version=__version__, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_id(request: Request, call_next):  # type: ignore[no-untyped-def]
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        started = time.perf_counter()
        token = request_id_var.set(rid)
        try:
            response: Response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["x-request-id"] = rid
        route = request.scope.get("route")
        HTTP_SECONDS.labels(
            request.method, getattr(route, "path", "unmatched"), str(response.status_code)
        ).observe(time.perf_counter() - started)
        return response

    install_error_handlers(app)
    app.include_router(auth_router)
    app.include_router(v1)
    app.include_router(webhooks)

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        r = Redis.from_url(get_settings().redis_url)
        try:
            QUEUE_DEPTH.set(await r.zcard("arq:queue"))
        except Exception:
            pass  # a metrics scrape must never fail because Redis is down
        finally:
            await r.aclose()
        return Response(render_metrics(), media_type="text/plain; version=0.0.4")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/readyz")
    async def readyz(response: Response) -> dict[str, bool]:
        pg, rd, fk = await asyncio.gather(_check(_postgres), _check(_redis), _check(_falkordb))
        checks = {"postgres": pg, "redis": rd, "falkordb": fk}
        if not all(checks.values()):
            response.status_code = 503
        return checks

    if settings.otlp_endpoint:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
    return app


app = create_app()
