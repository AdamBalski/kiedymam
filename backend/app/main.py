from contextlib import asynccontextmanager
import asyncio
import json
import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.api import auth, integrations, notifications
from app.calendar.service import CalendarService
from app.config import load_settings
from app.crypto.envelope import EnvelopeCrypto
from app.db.database import create_database
from app.db.repository import Repository
from app.jobs.runner import start_jobs
from app.limits.admission import BoundedAdmissionMiddleware

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
    calendar_service = CalendarService(repository, crypto)
    app.state.settings = settings
    app.state.database = database
    app.state.crypto = crypto
    app.state.repository = repository
    app.state.calendar_service = calendar_service

    scheduler = start_jobs(calendar_service, repository)
    LOG.info("UMB Fizjoterapia service started")
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        LOG.info("UMB Fizjoterapia service stopped")


app = FastAPI(title="KiedyMam", docs_url=None, redoc_url=None, lifespan=lifespan)
app.add_middleware(BoundedAdmissionMiddleware, workers=16, queue_size=128)
app.include_router(auth.router)
app.include_router(integrations.router)
app.include_router(notifications.router)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


if STATIC_DIR.is_dir() and (STATIC_DIR / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")


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
    runtime_config = f"<script>window.UMB_BASE_PATH={json.dumps(base_path)};</script>"
    html = html.replace("</head>", f"{runtime_config}</head>", 1)
    return HTMLResponse(html)
