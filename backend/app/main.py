import json
import logging
import re

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from starlette.exceptions import HTTPException
from starlette.middleware.cors import CORSMiddleware

from app import models as m
from app.api.openapi import install_openapi
from app.api.routes import respond, router
from app.config import Settings
from app.db import make_engine, make_session_factory
from app.errors import DomainError
from app.services.business import Business, Result


def create_app(settings=None, session_factory=None):
    settings = settings or Settings()
    session_factory = session_factory or make_session_factory(make_engine(settings.database_url))
    app = FastAPI(title="SIMULATION Fleet Recovery", version="1.0")
    app.state.settings, app.state.session_factory = settings, session_factory
    app.state.business = Business(settings, session_factory)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_methods=["GET", "POST", "PUT", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "X-Request-ID",
            "X-Trace-ID",
            "X-Contract-Version",
        ],
        expose_headers=["Content-Type"],
        allow_credentials=False,
    )

    @app.middleware("http")
    async def headers(request: Request, call_next):
        request.state.request_id = m.new_id()
        for name in ("x-request-id", "x-trace-id"):
            value = request.headers.get(name)
            if value and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
                return respond(
                    request,
                    Result(
                        None,
                        422,
                        error=DomainError(
                            "INVALID_INPUT",
                            "Trace identifiers must be 1-64 letters/digits/underscore/hyphen",
                            422,
                        ).as_dict(),
                    ),
                )
        request.state.request_id = request.headers.get("x-request-id", request.state.request_id)
        if request.headers.get("x-contract-version", "1.0") != "1.0":
            return respond(
                request,
                Result(
                    None,
                    426,
                    error=DomainError(
                        "CONTRACT_VERSION_UNSUPPORTED", "Contract version must be 1.0", 426
                    ).as_dict(),
                ),
            )
        response = await call_next(request)
        logging.getLogger("app.http").info(
            json.dumps(
                {
                    "request_id": request.state.request_id,
                    "trace_id": request.headers.get("x-trace-id"),
                    "status": response.status_code,
                }
            )
        )
        return response

    @app.exception_handler(DomainError)
    async def domain_error(request, error):
        return respond(request, Result(None, error.status_code, error=error.as_dict()))

    async def validation_error(request, error):
        # Pydantic errors contain submitted values; never include credential inputs.
        detail = [
            {"field": ".".join(map(str, e["loc"])), "type": e["type"]} for e in error.errors()
        ]
        missing = sorted(
            {field for e in error.errors() for field in e.get("ctx", {}).get("missing_fields", [])}
        )
        missing += [str(e["loc"][-1]) for e in error.errors() if e["type"] == "missing"]
        return respond(
            request,
            Result(
                None,
                422,
                error=DomainError(
                    "MISSING_FIELDS" if missing else "INVALID_INPUT",
                    "Request validation failed",
                    422,
                    details={"fields": detail},
                    missing_fields=sorted(set(missing)),
                    next_action="ASK_USER",
                ).as_dict(),
            ),
        )

    app.add_exception_handler(RequestValidationError, validation_error)
    app.add_exception_handler(ValidationError, validation_error)

    @app.exception_handler(HTTPException)
    async def http_error(request, error):
        return respond(
            request,
            Result(
                None,
                error.status_code,
                error=DomainError(
                    "NOT_FOUND" if error.status_code == 404 else "INVALID_INPUT",
                    "Request rejected",
                    error.status_code,
                ).as_dict(),
            ),
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request, error):
        return respond(
            request,
            Result(
                None,
                500,
                error=DomainError("INTERNAL_ERROR", "Unexpected service failure", 500).as_dict(),
            ),
        )

    app.include_router(router)
    install_openapi(app)
    return app


app = create_app()
