from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routers import admin as admin_router
from app.api.routers import health as health_router
from app.api.routers import public as public_router
from app.core.config import REMINDERS_ENABLED
from app.core.logging import logger
from app.db.migrations import run_additive_migrations
from app.db.models import Base
from app.db.session import engine

# Optional scheduler
try:
    from apscheduler.schedulers.background import BackgroundScheduler  # type: ignore
except Exception:
    BackgroundScheduler = None

from app.services.reminders import reminder_job


def create_app() -> FastAPI:
    app = FastAPI(title="Landsky Wedding App")

    # Routers
    app.include_router(health_router.router)
    app.include_router(public_router.router)
    app.include_router(admin_router.router)

    # Static frontend
    app.mount("/frontend", StaticFiles(directory="frontend", html=True), name="frontend")

    @app.on_event("startup")
    def _startup():
        # Create tables + additive migrations
        Base.metadata.create_all(bind=engine)
        run_additive_migrations(engine)

        # Scheduler
        if REMINDERS_ENABLED and BackgroundScheduler is not None:
            scheduler = BackgroundScheduler()
            app.state.scheduler = scheduler
            scheduler.add_job(reminder_job, "interval", hours=1)
            scheduler.start()
            logger.info("Reminder scheduler started.")
        else:
            logger.info("Reminder scheduler disabled or APScheduler not installed.")

    return app


app = create_app()
