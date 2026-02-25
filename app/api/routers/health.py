from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import REMINDERS_ENABLED
from app.db.session import get_db

router = APIRouter()


@router.get("/health")
def health(request: Request, db: Session = Depends(get_db)):
    # DB connectivity check
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    sched = getattr(request.app.state, "scheduler", None)
    sched_running = bool(getattr(sched, "running", False)) if sched is not None else False

    ok = bool(db_ok)
    return {
        "ok": ok,
        "db": "ok" if db_ok else "error",
        "scheduler": {
            "enabled": bool(REMINDERS_ENABLED),
            "running": sched_running,
        },
    }
