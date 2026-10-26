"""One error shape for every failure: {"error": {"code", "message", "request_id"}}. Messages are written for
people; internals (SQL, stack traces) never leave the server."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError
from starlette.exceptions import HTTPException as StarletteHTTPException

log = structlog.get_logger()


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, extra: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra or {}


# Custom SQLSTATEs raised by astro.booking_guard() and constraint names, mapped to API errors.
SQLSTATE = {
    "AV001": (409, "hold_expired", "That hold has lapsed. Please check the slot again."),
    "AV002": (402, "allocation_exhausted", "You have used all of your observing hours for this semester."),
    "AV003": (409, "illegal_transition", "That booking can no longer be changed that way."),
    "AV004": (400, "immutable_field", "That part of a booking cannot be changed."),
    "23P01": (409, "slot_conflict", "Someone else has just taken that slot."),
    "23505": (409, "duplicate", "That already exists."),
    "42501": (403, "forbidden", "You are not allowed to do that."),
}


def sqlstate(err: BaseException) -> str | None:
    orig = getattr(err, "orig", None)
    for candidate in (orig, getattr(orig, "__cause__", None)):
        code = getattr(candidate, "sqlstate", None) or getattr(candidate, "pgcode", None)
        if code:
            return str(code)
    return None


def from_db(err: DBAPIError) -> ApiError | None:
    code = sqlstate(err)
    if code in SQLSTATE:
        status, c, msg = SQLSTATE[code]
        return ApiError(status, c, msg)
    return None


def _body(request: Request, status: int, code: str, message: str, extra: dict[str, Any] | None = None) -> JSONResponse:
    rid = getattr(request.state, "request_id", None)
    payload: dict[str, Any] = {"code": code, "message": message, "request_id": rid, **(extra or {})}
    headers = {"Retry-After": str(extra["retry_after"])} if extra and "retry_after" in extra else None
    return JSONResponse({"error": payload}, status_code=status, headers=headers)


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api(request: Request, exc: ApiError) -> JSONResponse:
        return _body(request, exc.status, exc.code, exc.message, exc.extra)

    @app.exception_handler(DBAPIError)
    async def _db(request: Request, exc: DBAPIError) -> JSONResponse:
        mapped = from_db(exc)
        if mapped:
            return _body(request, mapped.status, mapped.code, mapped.message)
        log.error("db_error", sqlstate=sqlstate(exc), error=str(exc.orig)[:300])
        return _body(request, 500, "internal", "Something went wrong on our side.")

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [{"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]
        return _body(request, 422, "invalid_request", "Some fields need attention.", {"fields": fields[:20]})

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        codes = {404: "not_found", 405: "method_not_allowed", 413: "too_large", 429: "rate_limited"}
        return _body(request, exc.status_code, codes.get(exc.status_code, "http_error"), str(exc.detail))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled", path=request.url.path)
        return _body(request, 500, "internal", "Something went wrong on our side.")
