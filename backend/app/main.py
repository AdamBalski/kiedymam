from contextlib import asynccontextmanager
import asyncio
import json
import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.api import auth, feedback, integrations, notifications, push
from app.api import timetable_links
from app.calendar.catalog import TimetableCatalog
from app.calendar.service import CalendarService
from app.config import load_settings
from app.crypto.envelope import EnvelopeCrypto
from app.db.database import create_database
from app.db.repository import Repository
from app.jobs.runner import start_jobs
from app.limits.admission import BoundedAdmissionMiddleware
from app.push import PushService

LOG = logging.getLogger("umb_syncer")
STATIC_DIR = Path(__file__).resolve().parent / "static"
SOURCE_FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if not (STATIC_DIR / "index.html").is_file() and (SOURCE_FRONTEND_DIR / "index.html").is_file():
    STATIC_DIR = SOURCE_FRONTEND_DIR


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.umask(0o077)
    settings = load_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    database = create_database(settings)
    database.initialize()
    master_key = await asyncio.to_thread(EnvelopeCrypto.load_master_key, settings.oracle_secret_ocid)
    crypto = EnvelopeCrypto(database, master_key)
    repository = Repository(database)
    push_service = await asyncio.to_thread(PushService, repository, crypto, settings)
    calendar_service = CalendarService(repository, crypto, push_service)
    timetable_catalog = TimetableCatalog(repository, settings.timetable_directory_pages)
    app.state.settings = settings
    app.state.database = database
    app.state.crypto = crypto
    app.state.repository = repository
    app.state.calendar_service = calendar_service
    app.state.push_service = push_service
    app.state.timetable_catalog = timetable_catalog

    scheduler = start_jobs(calendar_service, repository, timetable_catalog)
    LOG.info("KiedyMam UMB service started")
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        LOG.info("KiedyMam UMB service stopped")


app = FastAPI(title="KiedyMam", docs_url=None, redoc_url=None, lifespan=lifespan)
app.add_middleware(BoundedAdmissionMiddleware, workers=16, queue_size=128)
app.include_router(auth.router)
app.include_router(integrations.router)
app.include_router(feedback.router)
app.include_router(notifications.router)
app.include_router(timetable_links.router)
app.include_router(push.router)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


if STATIC_DIR.is_dir() and (STATIC_DIR / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")


@app.get("/service-worker.js", include_in_schema=False)
async def service_worker(request: Request):
    worker = STATIC_DIR / "service-worker.js"
    if not worker.is_file():
        raise HTTPException(status_code=404, detail="Service worker is unavailable")
    return FileResponse(
        worker, media_type="application/javascript",
        headers={"Service-Worker-Allowed": (request.app.state.settings.app_base_path or "") + "/"},
    )


@app.get("/manifest.webmanifest", include_in_schema=False)
@app.get("/kiedymam-icon.svg", include_in_schema=False)
async def public_asset(request: Request):
    asset_name = request.url.path.rsplit("/", 1)[-1]
    path = STATIC_DIR / asset_name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Static asset is unavailable")
    media_type = "application/manifest+json" if asset_name.endswith(".webmanifest") else (
        "image/png" if asset_name.endswith(".png") else "image/svg+xml"
    )
    return FileResponse(path, media_type=media_type)


@app.get("/{path:path}", include_in_schema=False)
async def frontend(path: str, request: Request):
    if path == "api" or path.startswith("api/"):
        raise HTTPException(status_code=404, detail="API route not found")
    reserved_document = path.strip("/")
    if reserved_document in {"privacy", "terms"}:
        document = STATIC_DIR / f"{reserved_document}.html"
        if document.is_file():
            return FileResponse(document)
    index = STATIC_DIR / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=404, detail="Frontend build is not present")
    html = index.read_text(encoding="utf-8")
    base_path = request.app.state.settings.app_base_path
    runtime_config = (
        f"<base href={json.dumps(base_path + '/')}>"
        f"<script>window.UMB_BASE_PATH={json.dumps(base_path)};</script>"
    )
    html = html.replace("<head>", f"<head>{runtime_config}", 1)
    return HTMLResponse(html)
