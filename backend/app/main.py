from contextlib import asynccontextmanager
import asyncio
import json
import logging
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.api import admin, auth, feedback, integrations, metrics as metrics_api, notifications, push
from app.api import timetable_links
from app.calendar.catalog import TimetableCatalog
from app.calendar.service import CalendarService
from app.config import load_settings
from app.crypto.envelope import EnvelopeCrypto
from app.db.database import create_database
from app.db.repository import Repository
from app.jobs.runner import start_jobs
from app.jobs.queue import InMemoryJobQueue
from app.jobs.handlers import build_job_handlers
from app.limits.admission import BoundedAdmissionMiddleware
from app.limits.work import WorkLimits
from app.metrics.store import MetricsStore
from app.telemetry import ApplicationMetricsSampler
from app.logging_config import configure_logging
from app.push import PushService

LOG = logging.getLogger("umb_syncer")
STATIC_DIR = Path(__file__).resolve().parent / "static"
SOURCE_FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if not (STATIC_DIR / "index.html").is_file() and (SOURCE_FRONTEND_DIR / "index.html").is_file():
    STATIC_DIR = SOURCE_FRONTEND_DIR


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.umask(0o077)
    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=16, thread_name_prefix="kiedymam-blocking"))
    settings = load_settings()
    configure_logging(settings.log_level)
    database = create_database(settings)
    database.initialize()
    metrics = MetricsStore(settings.metrics_database_path, settings.service_version)
    application_metrics = ApplicationMetricsSampler(metrics)
    database.metrics = metrics
    master_key = await asyncio.to_thread(EnvelopeCrypto.load_master_key, settings.oracle_secret_ocid)
    crypto = EnvelopeCrypto(database, master_key)
    repository = Repository(database)
    metrics.add_counter("process.start.count", value=1)
    push_service = await asyncio.to_thread(PushService, repository, crypto, settings, metrics)
    work_limits = WorkLimits()
    calendar_service = CalendarService(repository, crypto, push_service, work_limits, metrics)
    timetable_catalog = TimetableCatalog(repository, settings.timetable_directory_pages)
    job_queue = InMemoryJobQueue(
        build_job_handlers(repository, calendar_service),
        workers=4, max_pending=512, metrics=metrics,
    )
    await job_queue.start()
    calendar_service.job_queue = job_queue
    app.state.settings = settings
    app.state.database = database
    app.state.crypto = crypto
    app.state.repository = repository
    app.state.calendar_service = calendar_service
    app.state.push_service = push_service
    app.state.timetable_catalog = timetable_catalog
    app.state.job_queue = job_queue
    app.state.work_limits = work_limits
    app.state.metrics = metrics
    app.state.admin_test_push_last = {}
    app.state.last_source_poll_success = None

    async def metrics_loop():
        while True:
            application_metrics.collect(
                queue_stats=job_queue.stats(),
                source_poll_time=calendar_service.last_successful_source_poll,
                db_paths=[database.path, metrics.database.path],
            )
            await asyncio.to_thread(metrics.flush)
            await asyncio.sleep(5)

    metrics_task = asyncio.create_task(metrics_loop(), name="metrics-flush")

    scheduler = start_jobs(calendar_service, repository, timetable_catalog, job_queue, loop)
    LOG.info("KiedyMam UMB service started")
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        await job_queue.stop()
        metrics_task.cancel()
        await asyncio.gather(metrics_task, return_exceptions=True)
        application_metrics.collect(queue_stats=job_queue.stats(),
                                    source_poll_time=calendar_service.last_successful_source_poll,
                                    db_paths=[database.path, metrics.database.path])
        await asyncio.to_thread(metrics.flush)
        LOG.info("KiedyMam UMB service stopped")


app = FastAPI(title="KiedyMam", docs_url=None, redoc_url=None, lifespan=lifespan)
app.add_middleware(BoundedAdmissionMiddleware, workers=16, queue_size=128)
app.include_router(auth.router)
app.include_router(integrations.router)
app.include_router(feedback.router)
app.include_router(notifications.router)
app.include_router(timetable_links.router)
app.include_router(push.router)
app.include_router(admin.router)
app.include_router(metrics_api.router)


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
