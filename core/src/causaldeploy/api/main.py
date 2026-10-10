import asyncio
from urllib.parse import urlparse

from fastapi import FastAPI, Response

from causaldeploy import __version__
from causaldeploy.config import get_settings

app = FastAPI(title="CausalDeploy", version=__version__)


async def _tcp_ok(host: str, port: int) -> bool:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=1.0)
    except (OSError, TimeoutError):
        return False
    writer.close()
    await writer.wait_closed()
    return True


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.get("/readyz")
async def readyz(response: Response) -> dict[str, bool]:
    s = get_settings()
    pg, rd = urlparse(s.postgres_dsn), urlparse(s.redis_url)
    checks = {
        "falkordb": await _tcp_ok(s.falkordb_host, s.falkordb_port),
        "postgres": await _tcp_ok(pg.hostname or "localhost", pg.port or 5432),
        "redis": await _tcp_ok(rd.hostname or "localhost", rd.port or 6379),
    }
    if not all(checks.values()):
        response.status_code = 503
    return checks
