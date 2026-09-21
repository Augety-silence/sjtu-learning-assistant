"""仅监听本机回环地址的 Dashboard API 与同源静态站点。"""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from sjtu_learning_assistant.archive_service import DEFAULT_ARCHIVE_ROOT, ArchiveError
from sjtu_learning_assistant.dashboard_service import (
    DashboardError,
    DashboardService,
    NotFoundError,
)
from sjtu_learning_assistant.database import create_database_engine

PROJECT_ROOT = Path(__file__).resolve().parent
STATIC_DIR = PROJECT_ROOT / "dashboard-web" / "dist"
ALLOWED_HOSTNAMES = {"127.0.0.1", "localhost"}
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
        "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    ),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


class StrictDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceIdRequest(StrictDTO):
    source_id: str = Field(min_length=1, max_length=255, pattern=r"^[^\x00-\x1f\x7f]+$")


def _host_name(host_header: str) -> str:
    try:
        parsed = urlsplit(f"//{host_header}")
    except ValueError:
        return ""
    return (parsed.hostname or "").lower()


def _same_origin(origin: str, host_header: str) -> bool:
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    return (
        parsed.scheme == "http"
        and parsed.username is None
        and parsed.password is None
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
        and parsed.netloc.lower() == host_header.lower()
        and (parsed.hostname or "").lower() in ALLOWED_HOSTNAMES
    )


def create_app(
    *,
    engine: Engine | None = None,
    service: DashboardService | None = None,
    static_dir: Path | None = None,
    csrf_token: str | None = None,
) -> FastAPI:
    database_engine = engine or create_database_engine()
    dashboard = service or DashboardService(
        database_engine,
        archive_root=Path(
            os.environ.get("SJTU_ARCHIVE_ROOT", str(DEFAULT_ARCHIVE_ROOT))
        ),
    )
    app = FastAPI(
        title="SJTU Learning Assistant Dashboard",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.dashboard = dashboard
    app.state.csrf_token = csrf_token or secrets.token_urlsafe(32)

    @app.middleware("http")
    async def local_security(request: Request, call_next):
        host_header = request.headers.get("host", "")
        if _host_name(host_header) not in ALLOWED_HOSTNAMES:
            response = JSONResponse({"detail": "Host 不被允许。"}, status_code=400)
        elif request.method == "POST":
            origin = request.headers.get("origin", "")
            token = request.headers.get("x-csrf-token", "")
            if not _same_origin(origin, host_header):
                response = JSONResponse({"detail": "Origin 不被允许。"}, status_code=403)
            elif not secrets.compare_digest(token, app.state.csrf_token):
                response = JSONResponse({"detail": "CSRF 校验失败。"}, status_code=403)
            else:
                response = await call_next(request)
        else:
            response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers[name] = value
        response.headers["Cache-Control"] = "no-store"
        return response

    def get_service() -> DashboardService:
        return app.state.dashboard


    @app.exception_handler(NotFoundError)
    async def not_found_handler(_request: Request, exc: NotFoundError):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(DashboardError)
    async def dashboard_error_handler(_request: Request, exc: DashboardError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(ArchiveError)
    async def archive_error_handler(_request: Request, exc: ArchiveError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(SQLAlchemyError)
    async def database_error_handler(_request: Request, _exc: SQLAlchemyError):
        return JSONResponse({"detail": "本地数据库暂时不可用。"}, status_code=503)

    @app.get("/api/csrf")
    def csrf() -> dict[str, str]:
        return {"csrf_token": app.state.csrf_token}

    @app.get("/api/health")
    def health(dashboard_service: DashboardService = Depends(get_service)):
        return dashboard_service.health()

    @app.get("/api/overview")
    def overview(dashboard_service: DashboardService = Depends(get_service)):
        return dashboard_service.overview()

    @app.get("/api/deadlines")
    def deadlines(
        dashboard_service: DashboardService = Depends(get_service),
        window: Literal["24h", "7d", "14d"] = "7d",
    ):
        return {"items": dashboard_service.deadlines({"24h": 24, "7d": 168, "14d": 336}[window])}

    @app.get("/api/messages")
    def messages(
        dashboard_service: DashboardService = Depends(get_service),
        kind: Literal["all", "email", "announcement"] = "all",
    ):
        return {"items": dashboard_service.messages(kind)}

    @app.get("/api/materials/filters")
    def material_filters(dashboard_service: DashboardService = Depends(get_service)):
        return dashboard_service.material_filters()

    @app.get("/api/materials")
    def materials(
        dashboard_service: DashboardService = Depends(get_service),
        term: str | None = Query(default=None, max_length=128),
        course_id: str | None = Query(default=None, max_length=128),
        status: Literal["all", "downloaded", "pending", "failed"] = "all",
    ):
        return {
            "items": dashboard_service.materials(
                term=term,
                course_id=course_id,
                status_filter=status,
            )
        }

    @app.get("/api/sync-status")
    def sync_status(dashboard_service: DashboardService = Depends(get_service)):
        return dashboard_service.sync_status()

    @app.post("/api/sync-trigger", status_code=202)
    def sync_trigger(dashboard_service: DashboardService = Depends(get_service)):
        return dashboard_service.trigger_sync()

    @app.post("/api/materials/download")
    def material_download(payload: SourceIdRequest, dashboard_service: DashboardService = Depends(get_service)):
        return dashboard_service.download_material(payload.source_id.strip())

    @app.post("/api/materials/open")
    def material_open(payload: SourceIdRequest, dashboard_service: DashboardService = Depends(get_service)):
        return dashboard_service.open_material(payload.source_id.strip())

    web_root = Path(static_dir) if static_dir is not None else STATIC_DIR
    assets = web_root / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    resolved_web_root = web_root.resolve()

    @app.api_route("/api", methods=["GET", "POST"], include_in_schema=False)
    @app.api_route("/api/{path:path}", methods=["GET", "POST"], include_in_schema=False)
    def api_not_found(path: str = ""):
        raise HTTPException(status_code=404, detail="Not Found")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        requested: Path | None = None
        try:
            candidate = (web_root / path).resolve()
            candidate.relative_to(resolved_web_root)
            requested = candidate
        except (OSError, ValueError):
            pass
        if path and requested is not None and requested.is_file():
            return FileResponse(requested)
        index = web_root / "index.html"
        if index.is_file():
            return FileResponse(index)
        raise HTTPException(status_code=404, detail="前端尚未构建。")

    return app


app = create_app()
