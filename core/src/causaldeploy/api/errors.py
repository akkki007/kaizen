"""Uniform error envelope: {"error": {"code", "message", "request_id", "details"?}}."""

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from causaldeploy.logging import request_id_var

_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    422: "validation_error",
    429: "rate_limited",
    503: "unavailable",
}


def envelope(status: int, message: str, details: object = None) -> JSONResponse:
    body: dict[str, object] = {
        "code": _CODES.get(status, "error"),
        "message": message,
        "request_id": request_id_var.get(),
    }
    if details is not None:
        body["details"] = details
    return JSONResponse({"error": body}, status_code=status)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        resp = envelope(exc.status_code, str(exc.detail))
        if isinstance(exc, HTTPException) and exc.headers:
            resp.headers.update(exc.headers)
        return resp

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        return envelope(422, "request validation failed", details)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        import logging

        logging.getLogger("causaldeploy.api").exception("unhandled error")
        return envelope(500, "internal server error")
