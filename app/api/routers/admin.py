import os
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import or_, text
from sqlalchemy.orm import Session

from app.api.schemas import StatusUpdate
from app.core.config import CATERING_TEAM_EMAIL, TEST_MODE
from app.core.logging import log_evt
from app.core.security import require_admin, require_admin_request
from app.db.models import EmailLog, Event
from app.db.session import engine, get_db
from app.email.sender import send_email_logged
from app.email.templates import render_offer_html
from app.email.templates import reminder_email_body  # for manual send
from app.services.offers import send_offer_flow

router = APIRouter()


@router.get("/admin", response_class=HTMLResponse, include_in_schema=False)
def admin_page(request: Request):
    require_admin_request(request)
    path = os.path.join("frontend", "admin.html")
    if os.path.isfile(path):
        return HTMLResponse(open(path, "r", encoding="utf-8").read())
    return HTMLResponse("<h2>admin.html not found</h2>", status_code=404)


@router.post("/admin/logout", include_in_schema=False)
def admin_logout():
    return Response(status_code=204)


@router.get("/admin/api/events")
def admin_events(
    request: Request,
    status: str | None = None,
    q: str | None = None,
    date_sort: str = "asc",
    id_sort: str | None = None,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    require_admin_request(request)

    query = db.query(Event)

    if status:
        query = query.filter(Event.status == status)

    if q and q.strip():
        qq = f"%{q.strip()}%"
        query = query.filter(
            or_(
                Event.first_name.ilike(qq),
                Event.last_name.ilike(qq),
                Event.email.ilike(qq),
            )
        )

    if id_sort == "asc":
        query = query.order_by(Event.id.asc())
    elif id_sort == "desc":
        query = query.order_by(Event.id.desc())
    elif date_sort == "desc":
        query = query.order_by(Event.wedding_date.desc(), Event.id.desc())
    else:
        query = query.order_by(Event.wedding_date.asc(), Event.id.desc())

    rows = query.limit(500).all()

    items = []
    for e in rows:
        items.append(
            {
                "id": e.id,
                "token": e.token,
                "first_name": e.first_name,
                "last_name": e.last_name,
                "wedding_date": str(e.wedding_date),
                "venue": e.venue,
                "guest_count": e.guest_count,
                "email": e.email,
                "phone": e.phone,
                "message": e.message,
                "status": e.status,
                "accepted": bool(e.accepted),
                "selected_package": e.selected_package,
                "created_at": e.created_at.isoformat() if e.created_at else None,
                "updated_at": e.updated_at.isoformat() if e.updated_at else None,
                "last_email_sent_at": e.last_email_sent_at.isoformat() if e.last_email_sent_at else None,
                "reminder_count": e.reminder_count or 0,
                "offer_sent_at": e.offer_sent_at.isoformat() if e.offer_sent_at else None,
                "reminder_3d_sent_at": e.reminder_3d_sent_at.isoformat() if e.reminder_3d_sent_at else None,
                "reminder_7d_sent_at": e.reminder_7d_sent_at.isoformat() if e.reminder_7d_sent_at else None,
                "event_2d_sent_at": e.event_2d_sent_at.isoformat() if e.event_2d_sent_at else None,
            }
        )

    return {"items": items}


@router.get("/admin/api/events/{event_id}/email-logs")
def admin_email_logs(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    require_admin_request(request)
    rows = (
        db.query(EmailLog)
        .filter(EmailLog.event_id == event_id)
        .order_by(EmailLog.id.desc())
        .limit(200)
        .all()
    )
    return {
        "items": [
            {
                "id": r.id,
                "email_type": r.email_type,
                "to_email": r.to_email,
                "subject": r.subject,
                "provider": r.provider,
                "provider_message_id": r.provider_message_id,
                "status": r.status,
                "error": r.error,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    }


@router.post("/admin/api/events/{event_id}/status")
def admin_set_status(
    event_id: int,
    payload: StatusUpdate,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    require_admin_request(request)
    e = db.query(Event).filter_by(id=event_id).first()
    if not e:
        raise HTTPException(status_code=404, detail="Not found")
    e.status = payload.status
    e.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True}


@router.post("/admin/api/events/{event_id}/accept")
def admin_accept(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    require_admin_request(request)
    e = db.query(Event).filter_by(id=event_id).first()
    if not e:
        raise HTTPException(status_code=404, detail="Not found")
    e.accepted = True
    e.status = "accepted"
    e.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True}


@router.post("/admin/api/events/{event_id}/decline")
def admin_decline(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    require_admin_request(request)
    e = db.query(Event).filter_by(id=event_id).first()
    if not e:
        raise HTTPException(status_code=404, detail="Not found")
    e.accepted = False
    e.status = "declined"
    e.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True}


@router.post("/admin/api/events/{event_id}/resend")
def admin_resend_offer(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    """Resend offer (admin action).

    Idempotency: prevent duplicate sends from retries / double-clicks using:
    - per-event Postgres advisory lock (best-effort)
    - short-window dedupe via EmailLog
    """
    require_admin_request(request)
    e = db.query(Event).filter_by(id=event_id).first()
    if not e:
        raise HTTPException(status_code=404, detail="Not found")

    # Best-effort per-event lock (Postgres only). If not acquired, skip quietly.
    lock_acquired = False
    if not ("sqlite" in str(engine.url)):
        try:
            lock_key = 900000 + int(event_id)
            lock_acquired = bool(db.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": lock_key}).scalar())
            if not lock_acquired:
                log_evt("info", "resend_skipped", event_id=event_id, email_type="resend_offer", reason="lock_not_acquired")
                return {"ok": True, "skipped": True}
        except Exception:
            # safer to skip than to risk duplicate sends
            log_evt("error", "resend_skipped", event_id=event_id, email_type="resend_offer", reason="lock_error")
            return {"ok": True, "skipped": True}

    try:
        # Short-window dedupe (60s)
        cutoff = datetime.utcnow() - timedelta(seconds=60)
        recent = (
            db.query(EmailLog)
            .filter(EmailLog.event_id == event_id, EmailLog.email_type == "resend_offer", EmailLog.created_at >= cutoff)
            .first()
        )
        if recent:
            log_evt("info", "resend_skipped", event_id=event_id, email_type="resend_offer", reason="recent_dedupe")
            return {"ok": True, "skipped": True}

        # Send without touching offer_sent_at; this is an explicit resend.
        offer_recipient = CATERING_TEAM_EMAIL if TEST_MODE else e.email
        subject_offer = f"Ponuda – {e.first_name} {e.last_name}{' (TEST)' if TEST_MODE else ''}"
        body_offer = render_offer_html(e)
        send_email_logged(db, e.id, "resend_offer", offer_recipient, subject_offer, body_offer)

        now = datetime.utcnow()
        e.last_email_sent_at = now
        e.updated_at = now
        db.commit()

        log_evt("info", "resend_sent", event_id=event_id, email_type="resend_offer", recipient=offer_recipient)
        return {"ok": True}

    finally:
        if lock_acquired and not ("sqlite" in str(engine.url)):
            try:
                db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": 900000 + int(event_id)})
                db.commit()
            except Exception:
                pass


@router.post("/admin/api/events/{event_id}/send-reminder-now")
def admin_send_reminder_now(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    """Manual reminder send (admin action)."""
    require_admin_request(request)
    e = db.query(Event).filter_by(id=event_id).first()
    if not e:
        raise HTTPException(status_code=404, detail="Not found")

    offer_recipient = CATERING_TEAM_EMAIL if TEST_MODE else e.email
    send_email_logged(
        db,
        e.id,
        "manual_reminder",
        offer_recipient,
        "Podsjetnik — Landsky ponuda",
        reminder_email_body(e),
    )

    now = datetime.utcnow()
    e.last_email_sent_at = now
    e.updated_at = now
    db.commit()
    log_evt("info", "manual_reminder_sent", event_id=event_id, email_type="manual_reminder", recipient=offer_recipient)
    return {"ok": True}
