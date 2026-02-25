from datetime import datetime

from sqlalchemy import text

from app.core.config import (
    CATERING_TEAM_EMAIL,
    REMINDER_DAY_1,
    REMINDER_DAY_2,
    TEST_MODE,
)
from app.core.logging import logger, log_evt
from app.db.models import Event
from app.db.session import SessionLocal, engine
from app.email.sender import send_email_logged
from app.email.templates import reminder_email_body, event_2d_email_body


def get_reminder_recipient(e: Event, kind: str) -> str:
    """Routing rules:
    - TEST_MODE: everything to catering
    - event_2d: always to catering
    - offer_3d/offer_7d: to client (pending only)
    """
    if TEST_MODE:
        return CATERING_TEAM_EMAIL
    if kind == "event_2d":
        return CATERING_TEAM_EMAIL
    return e.email


def reminder_job():
    """Hourly reminders with idempotency + Postgres advisory lock."""
    db = SessionLocal()
    lock_acquired = False
    try:
        now = datetime.utcnow()

        # Prevent duplicate scheduler runs (Postgres only)
        if not ("sqlite" in str(engine.url)):
            try:
                lock_acquired = bool(
                    db.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": 927341}).scalar()
                )
                if not lock_acquired:
                    log_evt("info", "reminder_job_skipped", reason="lock_not_acquired")
                    return
            except Exception:
                # Safer to skip than to risk duplicate sends.
                logger.exception("reminder_job: advisory lock error")
                log_evt("error", "reminder_job_skipped", reason="lock_error")
                return

        # 1) Offer reminders (pending)
        pending = db.query(Event).filter(Event.status == "pending").all()
        for e in pending:
            base = e.offer_sent_at or e.last_email_sent_at
            if not base:
                continue

            # 3-day reminder
            if e.reminder_3d_sent_at is None and (now - base).days >= REMINDER_DAY_1:
                claim_ts = now
                res = db.execute(
                    text(
                        "UPDATE events SET reminder_3d_sent_at=:now, last_email_sent_at=:now, "
                        "reminder_count=COALESCE(reminder_count,0)+1, updated_at=:now "
                        "WHERE id=:id AND reminder_3d_sent_at IS NULL"
                    ),
                    {"now": claim_ts, "id": e.id},
                )
                db.commit()
                if res.rowcount == 1:
                    try:
                        send_email_logged(
                            db,
                            e.id,
                            "offer_3d",
                            get_reminder_recipient(e, "offer_3d"),
                            "Podsjetnik — Landsky ponuda",
                            reminder_email_body(e),
                        )
                        log_evt("info", "reminder_sent", event_id=e.id, email_type="offer_3d")
                    except Exception:
                        logger.exception("reminder_job: send failed (offer_3d)", extra={"event_id": e.id})
                        # revert claim so it can retry
                        try:
                            db.execute(
                                text(
                                    "UPDATE events SET reminder_3d_sent_at=NULL, "
                                    "reminder_count=CASE WHEN reminder_count>0 THEN reminder_count-1 ELSE 0 END "
                                    "WHERE id=:id AND reminder_3d_sent_at=:ts"
                                ),
                                {"id": e.id, "ts": claim_ts},
                            )
                            db.commit()
                        except Exception:
                            db.rollback()
                        log_evt("error", "reminder_failed", event_id=e.id, email_type="offer_3d")

            # 7-day reminder
            if e.reminder_7d_sent_at is None and (now - base).days >= REMINDER_DAY_2:
                claim_ts = now
                res = db.execute(
                    text(
                        "UPDATE events SET reminder_7d_sent_at=:now, last_email_sent_at=:now, "
                        "reminder_count=COALESCE(reminder_count,0)+1, updated_at=:now "
                        "WHERE id=:id AND reminder_7d_sent_at IS NULL"
                    ),
                    {"now": claim_ts, "id": e.id},
                )
                db.commit()
                if res.rowcount == 1:
                    try:
                        send_email_logged(
                            db,
                            e.id,
                            "offer_7d",
                            get_reminder_recipient(e, "offer_7d"),
                            "Podsjetnik — Landsky ponuda",
                            reminder_email_body(e),
                        )
                        log_evt("info", "reminder_sent", event_id=e.id, email_type="offer_7d")
                    except Exception:
                        logger.exception("reminder_job: send failed (offer_7d)", extra={"event_id": e.id})
                        try:
                            db.execute(
                                text(
                                    "UPDATE events SET reminder_7d_sent_at=NULL, "
                                    "reminder_count=CASE WHEN reminder_count>0 THEN reminder_count-1 ELSE 0 END "
                                    "WHERE id=:id AND reminder_7d_sent_at=:ts"
                                ),
                                {"id": e.id, "ts": claim_ts},
                            )
                            db.commit()
                        except Exception:
                            db.rollback()
                        log_evt("error", "reminder_failed", event_id=e.id, email_type="offer_7d")

        # 2) Event 2-day reminders (accepted only)
        accepted = db.query(Event).filter(Event.status == "accepted").all()
        for e in accepted:
            if e.event_2d_sent_at is not None:
                continue
            days_until = (e.wedding_date - now.date()).days
            if days_until <= 2:
                claim_ts = now
                res = db.execute(
                    text(
                        "UPDATE events SET event_2d_sent_at=:now, last_email_sent_at=:now, updated_at=:now "
                        "WHERE id=:id AND event_2d_sent_at IS NULL"
                    ),
                    {"now": claim_ts, "id": e.id},
                )
                db.commit()
                if res.rowcount == 1:
                    try:
                        send_email_logged(
                            db,
                            e.id,
                            "event_2d",
                            get_reminder_recipient(e, "event_2d"),
                            "Podsjetnik — događaj za 2 dana",
                            event_2d_email_body(e),
                        )
                        log_evt("info", "reminder_sent", event_id=e.id, email_type="event_2d")
                    except Exception:
                        logger.exception("reminder_job: send failed (event_2d)", extra={"event_id": e.id})
                        try:
                            db.execute(
                                text("UPDATE events SET event_2d_sent_at=NULL WHERE id=:id AND event_2d_sent_at=:ts"),
                                {"id": e.id, "ts": claim_ts},
                            )
                            db.commit()
                        except Exception:
                            db.rollback()
                        log_evt("error", "reminder_failed", event_id=e.id, email_type="event_2d")

    finally:
        try:
            if lock_acquired and not ("sqlite" in str(engine.url)):
                try:
                    db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": 927341})
                    db.commit()
                except Exception:
                    db.rollback()
        finally:
            db.close()
