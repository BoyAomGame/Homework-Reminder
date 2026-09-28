"""Application entrypoint.

Run with: uvicorn main:app
One process hosts all three moving parts on a single asyncio loop:
the web app, the pool of per-user Discord bot clients, and the
reminder scheduler.
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app import runtime
from app.auth.deps import LoginRequired
from app.auth.routes import router as auth_router
from app.config import get_settings
from app.web.routes.assignments import router as assignments_router
from app.web.routes.dashboard import router as dashboard_router
from app.web.routes.settings import router as settings_router
from app.web.routes.timetable import router as timetable_router

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)-5.5s [%(name)s] %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Imported lazily so the web layer can be tested without discord/apscheduler.
    from app.bot_manager import BotManager
    from app.scheduler import ReminderScheduler
    from app.db.models import Base
    from app.db.session import engine
    from app.db.session import SessionFactory
    from app.providers.registry import reload_configuration

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with SessionFactory() as session:
        await reload_configuration(session)

    bot_manager = BotManager()
    runtime.set_bot_manager(bot_manager)
    await bot_manager.start_all_enabled()

    scheduler = ReminderScheduler()
    runtime.set_scheduler(scheduler)
    await scheduler.start()

    yield

    await scheduler.shutdown()
    await bot_manager.shutdown()
    runtime.set_scheduler(None)
    runtime.set_bot_manager(None)


app = FastAPI(title="เตือนการบ้าน", lifespan=lifespan, docs_url=None, redoc_url=None)

app.add_middleware(
    SessionMiddleware,
    secret_key=get_settings().session_secret,
    same_site="lax",
    https_only=False,  # HTTPS termination happens at the Nginx proxy
)

app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).parent / "app" / "web" / "static"),
    name="static",
)


@app.exception_handler(LoginRequired)
async def redirect_to_login(request: Request, exc: LoginRequired):
    return RedirectResponse("/login", status_code=303)


app.include_router(auth_router)
app.include_router(dashboard_router)
app.include_router(assignments_router)
app.include_router(timetable_router)
app.include_router(settings_router)
