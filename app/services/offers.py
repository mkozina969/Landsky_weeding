from datetime import datetime
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import CATERING_TEAM_EMAIL, TEST_MODE
from app.core.logging import log_evt
from app.db.models import Event
from app.email.sender import send_email, send_email_logged
from app.email.templates import internal_email_body, render_offer_html


def send_offer_flow(e: Event, db: Optional[Session] = None):
    """Send internal notification + customer offer.

    Idempotency: when db is provided, we atomically *claim* offer_sent_at to prevent duplicates.
    """
    claim_ts: Optional[datetime] = None

    if db is not None:
        # Atomically claim the initial offer send (prevents retries / double-clicks).
        claim_ts = datetime.utcnow()
        res = db.execute(
            text(
                "UPDATE events SET offer_sent_at=:now, last_email_sent_at=:now, updated_at=:now "
                "WHERE id=:id AND offer_sent_at IS NULL"
            ),
            {"now": claim_ts, "id": e.id},
        )
        db.commit()
        if res.rowcount != 1:
            log_evt("info", "offer_skipped", event_id=e.id, email_type="offer", reason="already_sent")
            return

    try:
        # internal notification
        subject_internal = f"Novi upit: {e.first_name} {e.last_name}{' (TEST)' if TEST_MODE else ''}"
        body_internal = internal_email_body(e)
        if db is not None:
            send_email_logged(db, e.id, "internal_new_inquiry", CATERING_TEAM_EMAIL, subject_internal, body_internal)
        else:
            send_email(CATERING_TEAM_EMAIL, subject_internal, body_internal)

        # offer email
        offer_recipient = CATERING_TEAM_EMAIL if TEST_MODE else e.email
        subject_offer = f"Ponuda – {e.first_name} {e.last_name}{' (TEST)' if TEST_MODE else ''}"
        body_offer = render_offer_html(e)
        if db is not None:
            send_email_logged(db, e.id, "offer", offer_recipient, subject_offer, body_offer)
        else:
            send_email(offer_recipient, subject_offer, body_offer)

        if db is not None:
            now = datetime.utcnow()
            # Keep these resets for the reminder flow
            e.last_email_sent_at = now
            e.reminder_count = 0
            e.reminder_3d_sent_at = None
            e.reminder_7d_sent_at = None
            e.updated_at = now
            db.commit()

        log_evt("info", "offer_sent", event_id=e.id, email_type="offer", recipient=offer_recipient)

    except Exception:
        # If we claimed but failed to send, revert claim so the system can retry safely.
        if db is not None and claim_ts is not None:
            try:
                db.execute(
                    text(
                        "UPDATE events SET offer_sent_at=NULL "
                        "WHERE id=:id AND offer_sent_at=:ts"
                    ),
                    {"id": e.id, "ts": claim_ts},
                )
                db.commit()
            except Exception:
                try:
                    db.rollback()
                except Exception:
                    pass
        log_evt("error", "offer_failed", event_id=e.id, email_type="offer")
        raise
