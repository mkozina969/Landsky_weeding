import smtplib
import ssl
from datetime import datetime
from email.mime.text import MIMEText

import requests
from sqlalchemy.orm import Session

from app.core.config import (
    EMAIL_PROVIDER,
    RESEND_API_KEY,
    SENDER_EMAIL,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_USER
)
from app.core.logging import logger
from app.db.models import EmailLog


def send_email_resend(to_email: str, subject: str, body_html: str):
    if not RESEND_API_KEY:
        raise RuntimeError("RESEND_API_KEY is not set")
    r = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "from": SENDER_EMAIL,
            "to": [to_email],
            "subject": subject,
            "html": body_html,
        },
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()
    return data.get("id")


def send_email_smtp(to_email: str, subject: str, body_html: str):
    if not SMTP_HOST:
        raise RuntimeError("SMTP_HOST is not set")
    msg = MIMEText(body_html, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = SENDER_EMAIL
    msg["To"] = to_email

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context) as server:
        if SMTP_USER:
            server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SENDER_EMAIL, [to_email], msg.as_string())
    return None


def send_email(to_email: str, subject: str, body_html: str):
    if EMAIL_PROVIDER == "smtp":
        return send_email_smtp(to_email, subject, body_html)
    return send_email_resend(to_email, subject, body_html)


def send_email_logged(db: Session, event_id: int, email_type: str, to_email: str, subject: str, body_html: str) -> None:
    """Send email and persist an audit log row.

    Best-effort: we always attempt to write a log row (sent or failed).
    Caller can decide whether to retry / revert flags on failure.
    """
    provider_message_id = None
    status = "sent"
    error = None
    try:
        provider_message_id = send_email(to_email, subject, body_html)
    except Exception as ex:
        status = "failed"
        error = str(ex)
        logger.exception("email_send_failed", extra={"event_id": event_id, "email_type": email_type, "to": to_email})
        raise
    finally:
        try:
            db.add(
                EmailLog(
                    event_id=event_id,
                    email_type=email_type,
                    to_email=to_email,
                    subject=subject[:255],
                    provider=EMAIL_PROVIDER,
                    provider_message_id=provider_message_id,
                    status=status,
                    error=error,
                    created_at=datetime.utcnow(),
                )
            )
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("email_log_write_failed", extra={"event_id": event_id, "email_type": email_type})
