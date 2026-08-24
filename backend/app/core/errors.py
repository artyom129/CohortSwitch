from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError


class APIError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


def _payload(request: Request, code: str, message: str, **details: Any) -> dict[str, Any]:
    error = {
        "code": code,
        "message": message,
        "request_id": getattr(request.state, "request_id", None),
    }
    error.update(details)
    return {"error": error}


async def api_error_handler(request: Request, exc: APIError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=_payload(request, exc.code, exc.message, **exc.details),
    )


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    safe_errors = [
        {"location": list(error["loc"]), "message": error["msg"], "type": error["type"]}
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content=_payload(
            request, "validation_error", "Request validation failed.", details=safe_errors
        ),
    )


async def integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content=_payload(request, "resource_conflict", "A resource with these values exists."),
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content=_payload(request, "internal_error", "An unexpected error occurred."),
    )
