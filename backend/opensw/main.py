from __future__ import annotations

import importlib
import os
import sqlite3
from contextlib import asynccontextmanager, closing
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import auth
from .config import APP_IDS, CATALOG, ROOT, Settings
from .db import connect, initialize
from .middleware import RequestBodyLimit


def create_app(app_id: str | None = None, data_dir: Path | str | None = None, config: dict | None = None) -> FastAPI:
    selected = app_id or os.environ.get("OPENSW_APP", "files")
    if selected not in APP_IDS:
        raise RuntimeError("Select one known OpenSW app.")
    directory = Path(data_dir or os.environ.get("OPENSW_DATA_DIR", "/data"))

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        settings = Settings.load(selected, directory, config)
        initialize(settings)
        application.state.settings = settings
        application.state.login_admission = auth.LoginAdmission()
        yield

    application = FastAPI(title=next(item["name"] for item in CATALOG["apps"] if item["id"] == selected), version=CATALOG["version"], lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    application.add_middleware(RequestBodyLimit)
    application.include_router(auth.router)
    application.include_router(importlib.import_module(f"opensw.apps.{selected}").router)

    @application.middleware("http")
    async def security_headers(request: Request, call_next):
        settings = request.app.state.settings
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            try:
                auth.require_origin(request)
            except HTTPException as exc:
                return JSONResponse({"detail": str(exc.detail)}, status_code=exc.status_code)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        if settings.secure_cookie:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @application.exception_handler(RequestValidationError)
    async def invalid_input(request: Request, exc: RequestValidationError):
        # Do not echo passwords, tokens, uploaded content or arbitrary input in
        # validation responses. The public API has one simple error shape.
        first = exc.errors()[0]
        field = ".".join(str(part) for part in first["loc"] if part not in {"body", "query", "path"})
        message = f"Check {field}: {first['msg']}" if field else "Check the request fields."
        return JSONResponse({"detail": message}, status_code=422)

    @application.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return JSONResponse({"detail": str(exc.detail)}, status_code=exc.status_code, headers=exc.headers)

    @application.exception_handler(sqlite3.OperationalError)
    async def storage_error(request: Request, exc: sqlite3.OperationalError):
        return JSONResponse({"detail": "The storage operation could not be confirmed. Refresh before trying another change."}, status_code=503)

    @application.get("/api/health")
    def health(request: Request):
        settings = request.app.state.settings
        with closing(connect(settings)) as conn:
            metadata = dict(conn.execute("SELECT key,value FROM metadata"))
            schema_version = conn.execute("PRAGMA user_version").fetchone()[0]
            admin = conn.execute("SELECT id FROM users WHERE active=1 AND role='admin' LIMIT 1").fetchone()
        if metadata != {"app": selected, "instance_name": settings.instance_name} or schema_version != 1 or admin is None:
            raise HTTPException(503, "The application has not finished initializing.")
        return {"status": "ok", "app": selected, "version": CATALOG["version"], "schema_version": schema_version}

    static = ROOT / "web" / "dist"
    assets = static / "assets"
    if assets.is_dir():
        application.mount("/assets", StaticFiles(directory=assets), name="assets")

    @application.api_route("/api", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"], include_in_schema=False)
    @application.api_route("/api/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"], include_in_schema=False)
    def unknown_api(path: str = ""):
        raise HTTPException(404, "This API is not available in this OpenSW app.")

    @application.get("/{path:path}")
    def frontend(path: str):
        # Unknown API paths must never become HTML success responses.
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, "This API is not available in this OpenSW app.")
        candidate = static / path
        if path and candidate.is_file() and candidate.resolve().is_relative_to(static.resolve()):
            return FileResponse(candidate)
        index = static / "index.html"
        if not index.is_file():
            raise HTTPException(503, "The interface has not been built. Build the selected OpenSW app first.")
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return application


app = create_app()
