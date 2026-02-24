import os
import ssl
import uuid
import html
import base64
import smtplib
from datetime import datetime, date, timedelta, time
from email.mime.text import MIMEText
from typing import Generator, Optional, Tuple

import requests
from fastapi import FastAPI, Depends, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, EmailStr
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Date,
    DateTime,
    Boolean,
    Text,
    ForeignKey,
    text,
)
from sqlalchemy.orm import sessionmaker, declarative_base, Session

try:
    from apscheduler.schedulers.background import BackgroundScheduler
except Exception:
    BackgroundScheduler = None  # type: ignore

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("landski")

# ======================
# ENV
# ======================

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./app.db")
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")

ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")

CATERING_TEAM_EMAIL = os.getenv("CATERING_TEAM_EMAIL", "")
SENDER_EMAIL = os.getenv("SENDER_EMAIL", "")
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")

TEST_MODE = os.getenv("TEST_MODE", "1") == "1"
REMINDERS_ENABLED = os.getenv("REMINDERS_ENABLED", "1") == "1"

REMINDER_DAY_1 = int(os.getenv("REMINDER_DAY_1", "3"))  # 3 days
REMINDER_DAY_2 = int(os.getenv("REMINDER_DAY_2", "7"))  # 7 days

PACKAGE_LABELS = {
    "classic": "Classic",
    "premium": "Premium",
    "signature": "Signature",
}

# ======================
# DB
# ======================

connect_args = {}

if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}
elif DATABASE_URL.startswith("postgres"):
    # Neon TLS fix (vracamo staro ponasanje)
    import ssl
    connect_args = {"ssl_context": ssl.create_default_context()}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Event(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, index=True)
    token = Column(String(64), unique=True, index=True, nullable=False)

    first_name = Column(String(120), nullable=False)
    last_name = Column(String(120), nullable=False)
    wedding_date = Column(Date, nullable=False)  # keep DB name stable (prod-safe)
    venue = Column(String(255), nullable=False)
    guest_count = Column(Integer, nullable=False)

    email = Column(String(255), nullable=False)
    phone = Column(String(80), nullable=False)

    # Client notes / questions
    message = Column(Text, nullable=True)

    # Status + package choice
    status = Column(String(30), default="pending", nullable=False)  # pending/accepted/declined
    selected_package = Column(String(30), nullable=True)  # classic/premium/signature
    accepted = Column(Boolean, default=False, nullable=False)

    # Event type (wedding/corporate/private)
    event_type = Column(String(30), nullable=True, default="wedding")

    # Offer expiry (e.g. 14 days)
    offer_expires_at = Column(DateTime, nullable=True)

    # Reminder tracking
    last_email_sent_at = Column(DateTime, nullable=True)
    reminder_count = Column(Integer, default=0, nullable=False)
    # Offer / reminder tracking (idempotent flags)
    offer_sent_at = Column(DateTime, nullable=True)
    reminder_3d_sent_at = Column(DateTime, nullable=True)
    reminder_7d_sent_at = Column(DateTime, nullable=True)
    event_2d_sent_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class EmailLog(Base):
    __tablename__ = "email_logs"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey("events.id", ondelete="CASCADE"), index=True, nullable=False)

    email_type = Column(String(50), nullable=False)  # internal_new_inquiry/offer/offer_3d/offer_7d/event_2d/resend_offer/admin_manual
    to_email = Column(String(255), nullable=False)
    subject = Column(String(255), nullable=False)

    provider = Column(String(30), nullable=False, default="resend")
    provider_message_id = Column(String(120), nullable=True)

    status = Column(String(20), nullable=False, default="sent")  # sent/failed
    error = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class EventNote(Base):
    __tablename__ = "event_notes"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey("events.id", ondelete="CASCADE"), index=True, nullable=False)
    text = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


Base.metadata.create_all(bind=engine)

# --- MVP migrations (best-effort) ---
try:
    with engine.begin() as conn:
        if "sqlite" in str(engine.url):
            cols = conn.execute(text("PRAGMA table_info(events);")).fetchall()
            names = [c[1] for c in cols]

            def add_sqlite(col: str, ddl: str):
                if col not in names:
                    conn.execute(text(ddl))

            add_sqlite("message", "ALTER TABLE events ADD COLUMN message TEXT")
            add_sqlite("selected_package", "ALTER TABLE events ADD COLUMN selected_package TEXT")
            add_sqlite("last_email_sent_at", "ALTER TABLE events ADD COLUMN last_email_sent_at DATETIME")
            add_sqlite("reminder_count", "ALTER TABLE events ADD COLUMN reminder_count INTEGER DEFAULT 0")
            add_sqlite("updated_at", "ALTER TABLE events ADD COLUMN updated_at DATETIME")

            # New additive fields
            add_sqlite("offer_sent_at", "ALTER TABLE events ADD COLUMN offer_sent_at DATETIME")
            add_sqlite("reminder_3d_sent_at", "ALTER TABLE events ADD COLUMN reminder_3d_sent_at DATETIME")
            add_sqlite("reminder_7d_sent_at", "ALTER TABLE events ADD COLUMN reminder_7d_sent_at DATETIME")
            add_sqlite("event_2d_sent_at", "ALTER TABLE events ADD COLUMN event_2d_sent_at DATETIME")
            add_sqlite("event_type", "ALTER TABLE events ADD COLUMN event_type TEXT")
            add_sqlite("offer_expires_at", "ALTER TABLE events ADD COLUMN offer_expires_at DATETIME")
        else:
            def col_exists(col: str) -> bool:
                r = conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name='events' AND column_name=:c"
                    ),
                    {"c": col},
                ).fetchone()
                return bool(r)

            if not col_exists("message"):
                conn.execute(text("ALTER TABLE events ADD COLUMN message TEXT"))
            if not col_exists("selected_package"):
                conn.execute(text("ALTER TABLE events ADD COLUMN selected_package VARCHAR"))
            if not col_exists("last_email_sent_at"):
                conn.execute(text("ALTER TABLE events ADD COLUMN last_email_sent_at TIMESTAMP NULL"))
            if not col_exists("reminder_count"):
                conn.execute(text("ALTER TABLE events ADD COLUMN reminder_count INTEGER DEFAULT 0"))
            if not col_exists("updated_at"):
                conn.execute(text("ALTER TABLE events ADD COLUMN updated_at TIMESTAMP NULL"))

            # New additive fields
            if not col_exists("offer_sent_at"):
                conn.execute(text("ALTER TABLE events ADD COLUMN offer_sent_at TIMESTAMP NULL"))
            if not col_exists("reminder_3d_sent_at"):
                conn.execute(text("ALTER TABLE events ADD COLUMN reminder_3d_sent_at TIMESTAMP NULL"))
            if not col_exists("reminder_7d_sent_at"):
                conn.execute(text("ALTER TABLE events ADD COLUMN reminder_7d_sent_at TIMESTAMP NULL"))
            if not col_exists("event_2d_sent_at"):
                conn.execute(text("ALTER TABLE events ADD COLUMN event_2d_sent_at TIMESTAMP NULL"))
            if not col_exists("event_type"):
                conn.execute(text("ALTER TABLE events ADD COLUMN event_type VARCHAR NULL"))
            if not col_exists("offer_expires_at"):
                conn.execute(text("ALTER TABLE events ADD COLUMN offer_expires_at TIMESTAMP NULL"))
except Exception:
    logger.exception("MIGRATIONS skipped/failed")


def db_session() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ======================
# SCHEMA
# ======================

class RegistrationRequest(BaseModel):
    event_type: Optional[str] = Field(default="wedding")
    first_name: str = Field(..., min_length=1, max_length=120)
    last_name: str = Field(..., min_length=1, max_length=120)
    wedding_date: date
    venue: str = Field(..., min_length=1, max_length=255)
    guest_count: int = Field(..., ge=1, le=1000)
    email: EmailStr
    phone: str = Field(..., min_length=3, max_length=80)
    message: Optional[str] = None


class StatusUpdate(BaseModel):
    status: str


# ======================
# EMAIL
# ======================

def send_email(to_email: str, subject: str, html_body: str) -> Optional[str]:
    """Send via Resend if configured, otherwise try SMTP (legacy), return provider msg id if any."""
    if RESEND_API_KEY:
        try:
            resp = requests.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
                json={
                    "from": SENDER_EMAIL,
                    "to": [to_email],
                    "subject": subject,
                    "html": html_body,
                },
                timeout=20,
            )
            if resp.status_code >= 400:
                raise RuntimeError(f"Resend error {resp.status_code}: {resp.text}")
            data = resp.json()
            return data.get("id")
        except Exception:
            logger.exception("Resend send failed")
            raise

    # fallback SMTP (if you had it configured)
    smtp_host = os.getenv("SMTP_HOST", "")
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASS", "")
    smtp_port = int(os.getenv("SMTP_PORT", "465"))

    if not smtp_host:
        logger.warning("No RESEND_API_KEY and no SMTP_HOST; skipping email send")
        return None

    msg = MIMEText(html_body, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = SENDER_EMAIL
    msg["To"] = to_email

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context) as server:
        server.login(smtp_user, smtp_pass)
        server.sendmail(SENDER_EMAIL, [to_email], msg.as_string())
    return None


def send_email_logged(db: Session, event_id: int, email_type: str, to_email: str, subject: str, html_body: str):
    provider_id = None
    status = "sent"
    err = None
    try:
        provider_id = send_email(to_email, subject, html_body)
    except Exception as ex:
        status = "failed"
        err = str(ex)

    log_row = EmailLog(
        event_id=event_id,
        email_type=email_type,
        to_email=to_email,
        subject=subject,
        provider="resend" if RESEND_API_KEY else "smtp",
        provider_message_id=provider_id,
        status=status,
        error=err,
        created_at=datetime.utcnow(),
    )
    db.add(log_row)
    db.commit()

    if status != "sent":
        raise RuntimeError(f"Email failed: {err}")


def internal_email_body(e: Event) -> str:
    return f"""
<div style="font-family:Arial,Helvetica,sans-serif;background:#0b0f14;color:#e5e7eb;padding:18px">
  <h2 style="margin:0 0 10px">Novi upit za catering / event</h2>
  <div style="padding:12px;border-radius:14px;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12)">
    <b>Klijent:</b> {html.escape(e.first_name)} {html.escape(e.last_name)}<br/>
    <b>Tip:</b> {html.escape((e.event_type or "wedding").upper())}<br/>
    <b>Datum:</b> {html.escape(str(e.wedding_date))}<br/>
    <b>Lokacija:</b> {html.escape(e.venue)}<br/>
    <b>Gostiju:</b> {e.guest_count}<br/>
    <b>Email:</b> {html.escape(e.email)}<br/>
    <b>Telefon:</b> {html.escape(e.phone)}<br/>
    <b>Napomena:</b> {html.escape(e.message or "")}
  </div>
</div>
"""


def render_offer_html(e: Event) -> str:
    accept_link = f"{BASE_URL}/accept?token={e.token}"
    decline_link = f"{BASE_URL}/decline?token={e.token}"

    # hosted attachments from your frontend
    cocktails_url = f"{BASE_URL}/frontend/cocktails.pdf"
    bar_url = f"{BASE_URL}/frontend/bar.jpeg"
    cigare_url = f"{BASE_URL}/frontend/cigare.png"

    return f"""
<div style="font-family:Arial,Helvetica,sans-serif;background:#0b0f14;color:#e5e7eb;padding:18px">
  <div style="max-width:760px;margin:0 auto;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12);border-radius:18px;padding:18px">
    <div style="display:flex;align-items:center;gap:12px">
      <img src="{BASE_URL}/frontend/logo.png" alt="Landsky" style="height:44px;border-radius:10px"/>
      <div>
        <h2 style="margin:0">Ponuda — Landsky Catering</h2>
        <div style="opacity:.8;font-size:13px">Sažetak upita: {html.escape(e.first_name)} {html.escape(e.last_name)} • {html.escape(str(e.wedding_date))} • {html.escape(e.venue)}</div>
      </div>
    </div>

    <hr style="border:none;border-top:1px solid rgba(255,255,255,.10);margin:14px 0"/>

    <h3 style="margin:0 0 8px">Ponuda uključuje</h3>
    <ul style="margin:0;padding-left:18px;opacity:.95">
      <li>Profesionalno osoblje i organizacija</li>
      <li>Bar / cocktail setup prema dogovoru</li>
      <li>Dogovor oko logistike i termina</li>
    </ul>

    <p style="margin:12px 0 0;opacity:.9">
      Put: <b>0.70 EUR/km</b>
    </p>

    <h3 style="margin:16px 0 8px">Premium cigare (opcionalno)</h3>
    <p style="margin:0;opacity:.9">
      Pregled opcija: <a style="color:#93c5fd" href="{cigare_url}" target="_blank" rel="noopener">cigare.png</a>
    </p>

    <h3 style="margin:16px 0 8px">Cocktails (PDF)</h3>
    <p style="margin:0;opacity:.9">
      <a style="color:#93c5fd" href="{cocktails_url}" target="_blank" rel="noopener">cocktails.pdf</a>
    </p>

    <h3 style="margin:16px 0 8px">Paketi</h3>
    <div style="display:grid;grid-template-columns:1fr;gap:10px">
      <div style="padding:12px;border-radius:14px;background:rgba(0,0,0,.25);border:1px solid rgba(255,255,255,.10)">
        <b>Classic</b><div style="opacity:.85">Osnovni paket</div>
      </div>
      <div style="padding:12px;border-radius:14px;background:rgba(0,0,0,.25);border:1px solid rgba(255,255,255,.10)">
        <b>Premium</b><div style="opacity:.85">Napredni paket</div>
      </div>
      <div style="padding:12px;border-radius:14px;background:rgba(0,0,0,.25);border:1px solid rgba(255,255,255,.10)">
        <b>Signature</b><div style="opacity:.85">Najbolji paket</div>
      </div>
    </div>

    <div style="margin-top:16px;padding:12px;border-radius:14px;background:rgba(0,0,0,.25);border:1px solid rgba(255,255,255,.10)">
      <p style="margin:0 0 8px;opacity:.9">
        Ako želite, možete doći na prezentaciju u LandSky bar:
        <a style="color:#93c5fd" href="{bar_url}" target="_blank" rel="noopener">bar.jpeg</a>
      </p>
      <div style="display:flex;gap:10px;flex-wrap:wrap">
        <a href="{accept_link}" style="background:#16a34a;color:white;text-decoration:none;padding:10px 14px;border-radius:12px;font-weight:700">Prihvati ponudu</a>
        <a href="{decline_link}" style="background:#ef4444;color:white;text-decoration:none;padding:10px 14px;border-radius:12px;font-weight:700">Odbij ponudu</a>
      </div>
    </div>
  </div>
</div>
"""


def reminder_email_body(e: Event) -> str:
    accept_link = f"{BASE_URL}/accept?token={e.token}"
    decline_link = f"{BASE_URL}/decline?token={e.token}"
    return f"""
<div style="font-family:Arial,Helvetica,sans-serif;background:#0b0f14;color:#e5e7eb;padding:18px">
  <div style="max-width:760px;margin:0 auto;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12);border-radius:18px;padding:18px">
    <h2 style="margin:0 0 8px">Podsjetnik — Ponuda</h2>
    <p style="margin:0 0 14px;opacity:.9">Podsjetnik vezano za ponudu za vaš event.</p>
    <div style="display:flex;gap:10px;flex-wrap:wrap">
      <a href="{accept_link}" style="background:#16a34a;color:white;text-decoration:none;padding:10px 14px;border-radius:12px;font-weight:700">Prihvati ponudu</a>
      <a href="{decline_link}" style="background:#ef4444;color:white;text-decoration:none;padding:10px 14px;border-radius:12px;font-weight:700">Odbij ponudu</a>
    </div>
  </div>
</div>
"""


def event_2d_email_body(e: Event) -> str:
    return f"""
<div style="font-family:Arial,Helvetica,sans-serif;background:#0b0f14;color:#e5e7eb;padding:18px">
  <div style="max-width:760px;margin:0 auto;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12);border-radius:18px;padding:18px">
    <h2 style="margin:0 0 8px">Interni podsjetnik — event uskoro</h2>
    <p style="margin:0 0 14px;opacity:.9">Event je za 2 dana. Provjerite logistiku i pripremu.</p>
    <div style="padding:12px;border-radius:14px;background:rgba(0,0,0,.25);border:1px solid rgba(255,255,255,.10)">
      <b>{html.escape(e.first_name)} {html.escape(e.last_name)}</b><br/>
      Tip: {html.escape((e.event_type or "wedding").upper())}<br/>
      Datum: {html.escape(str(e.wedding_date))}<br/>
      Lokacija: {html.escape(e.venue)}<br/>
      Gostiju: {e.guest_count}
    </div>
  </div>
</div>
"""


def get_reminder_recipient(e: Event, kind: str) -> str:
    # TEST_MODE: sve ide cateringu
    if TEST_MODE:
        return CATERING_TEAM_EMAIL

    # 2 dana prije eventa: samo interno
    if kind == "event_2d":
        return CATERING_TEAM_EMAIL

    # offer reminders idu klijentu
    return e.email


def send_offer_flow(e: Event, db: Optional[Session] = None):
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
        e.last_email_sent_at = now
        e.offer_sent_at = now
        e.offer_expires_at = now + timedelta(days=14)
        e.reminder_count = 0
        e.reminder_3d_sent_at = None
        e.reminder_7d_sent_at = None
        e.updated_at = now
        db.commit()


def compute_next_reminder(e: Event) -> tuple[Optional[str], Optional[datetime]]:
    """Returns (kind, due_at) where kind is: offer_3d, offer_7d, event_2d."""
    # Stop offer reminders after expiry
    if e.status == "pending" and e.offer_expires_at and e.offer_expires_at < datetime.utcnow():
        return (None, None)

    # Offer reminders for pending events
    if e.status == "pending":
        base = e.offer_sent_at or e.last_email_sent_at
        if base:
            if e.reminder_3d_sent_at is None:
                return ("offer_3d", base + timedelta(days=REMINDER_DAY_1))
            if e.reminder_7d_sent_at is None:
                return ("offer_7d", base + timedelta(days=REMINDER_DAY_2))

    # 2 days before event date for accepted
    if e.status == "accepted" and e.event_2d_sent_at is None and e.wedding_date:
        event_dt = datetime.combine(e.wedding_date, time(12, 0))
        return ("event_2d", event_dt - timedelta(days=2))

    return (None, None)


def reminder_job():
    """Hourly reminders with idempotency + Postgres advisory lock."""
    db = SessionLocal()
    lock_acquired = False
    try:
        now = datetime.utcnow()

        # Prevent duplicate scheduler runs (Postgres only)
        if not ("sqlite" in str(engine.url)):
            try:
                lock_acquired = bool(db.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": 927341}).scalar())
                if not lock_acquired:
                    logger.info("reminder_job: lock not acquired; skipping")
                    return
            except Exception:
                logger.exception("reminder_job: lock check failed")

        # 1) Pending events: 3/7 days after offer_sent_at
        pending = db.query(Event).filter(Event.status == "pending").all()
        for e in pending:
            # stop if expired
            if e.offer_expires_at and e.offer_expires_at < now:
                continue

            base = e.offer_sent_at or e.last_email_sent_at
            if not base:
                continue

            # 3-day reminder
            if e.reminder_3d_sent_at is None and (now - base).days >= REMINDER_DAY_1:
                res = db.execute(
                    text(
                        "UPDATE events SET reminder_3d_sent_at=:now, last_email_sent_at=:now, "
                        "reminder_count=COALESCE(reminder_count,0)+1, updated_at=:now "
                        "WHERE id=:id AND reminder_3d_sent_at IS NULL"
                    ),
                    {"now": now, "id": e.id},
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
                    except Exception:
                        logger.exception("reminder_job: send failed (offer_3d)", extra={"event_id": e.id})
                continue

            # 7-day reminder
            if e.reminder_3d_sent_at is not None and e.reminder_7d_sent_at is None and (now - base).days >= REMINDER_DAY_2:
                res = db.execute(
                    text(
                        "UPDATE events SET reminder_7d_sent_at=:now, last_email_sent_at=:now, "
                        "reminder_count=COALESCE(reminder_count,0)+1, updated_at=:now "
                        "WHERE id=:id AND reminder_7d_sent_at IS NULL"
                    ),
                    {"now": now, "id": e.id},
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
                    except Exception:
                        logger.exception("reminder_job: send failed (offer_7d)", extra={"event_id": e.id})

        # 2) Accepted events: 2 days before date (only once)
        accepted = db.query(Event).filter(Event.status == "accepted").all()
        for e in accepted:
            if e.event_2d_sent_at is not None:
                continue
            if not e.wedding_date:
                continue
            due_at = datetime.combine(e.wedding_date, time(12, 0)) - timedelta(days=2)
            if now < due_at:
                continue

            recipient = get_reminder_recipient(e, "event_2d")

            res = db.execute(
                text(
                    "UPDATE events SET event_2d_sent_at=:now, last_email_sent_at=:now, updated_at=:now "
                    "WHERE id=:id AND event_2d_sent_at IS NULL"
                ),
                {"now": now, "id": e.id},
            )
            db.commit()
            if res.rowcount == 1:
                try:
                    send_email_logged(
                        db,
                        e.id,
                        "event_2d",
                        recipient,
                        "Interni podsjetnik — uskoro događaj",
                        event_2d_email_body(e),
                    )
                except Exception:
                    logger.exception("reminder_job: send failed (event_2d)", extra={"event_id": e.id})

    except Exception:
        logger.exception("REMINDER JOB ERROR")
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        if lock_acquired and not ("sqlite" in str(engine.url)):
            try:
                db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": 927341})
                db.commit()
            except Exception:
                pass
        db.close()


# ======================
# APP
# ======================

app = FastAPI(title="Landsky Catering – Inquiries & Offers")
app.mount("/frontend", StaticFiles(directory="frontend", html=True), name="frontend")


@app.on_event("startup")
def _startup():
    # ensure tables exist
    Base.metadata.create_all(bind=engine)

    if REMINDERS_ENABLED and BackgroundScheduler is not None:
        scheduler = BackgroundScheduler()
        scheduler.add_job(reminder_job, "interval", hours=1)
        scheduler.start()
        app.state.scheduler = scheduler
        logger.info("Reminder scheduler started.")
    else:
        logger.info("Reminder scheduler disabled or APScheduler not installed.")


# ======================
# PUBLIC ROUTES
# ======================

@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/frontend/")


@app.post("/register")
def register(payload: RegistrationRequest, db: Session = Depends(db_session)):
    e = Event(
        token=uuid.uuid4().hex,
        first_name=payload.first_name.strip(),
        last_name=payload.last_name.strip(),
        event_type=(payload.event_type or "wedding").strip().lower(),
        wedding_date=payload.wedding_date,
        venue=payload.venue.strip(),
        guest_count=payload.guest_count,
        email=str(payload.email),
        phone=payload.phone.strip(),
        message=(payload.message or "").strip() or None,
        status="pending",
        accepted=False,
        selected_package=None,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        last_email_sent_at=None,
        reminder_count=0,
    )

    db.add(e)
    db.commit()
    db.refresh(e)

    preview_url = f"{BASE_URL}/offer-preview?token={e.token}" if TEST_MODE else None

    try:
        send_offer_flow(e, db=db)
    except Exception:
        logger.exception("EMAIL SEND FAILED")

    return {"message": "Vaš upit je zaprimljen.", "preview_url": preview_url}


@app.get("/offer-preview", response_class=HTMLResponse)
def offer_preview(token: str = Query(...), db: Session = Depends(db_session)):
    e = db.query(Event).filter_by(token=token).first()
    if not e:
        raise HTTPException(status_code=404, detail="Token not found")
    return HTMLResponse(render_offer_html(e))


# ======================
# ACCEPT / DECLINE (client)
# ======================

@app.get("/accept", response_class=HTMLResponse)
def accept_get(
    token: str = Query(...),
    package: str | None = Query(None),
    db: Session = Depends(db_session),
):
    e = db.query(Event).filter_by(token=token).first()
    if not e:
        return HTMLResponse("<h3>Neispravan token.</h3>", status_code=404)

    if e.status == "accepted":
        chosen = PACKAGE_LABELS.get((e.selected_package or "").lower(), e.selected_package or "—")
        return HTMLResponse(f"<h3>Ponuda je već prihvaćena.</h3><p>Odabrani paket: <b>{html.escape(chosen)}</b></p>")

    if e.status == "declined":
        return HTMLResponse("<h3>Ponuda je već odbijena.</h3>")

    if not package:
        options = ""
        for key, label in PACKAGE_LABELS.items():
            options += f"""
            <label style="display:block;margin:10px 0;padding:10px;border:1px solid rgba(255,255,255,.12);border-radius:12px;background:rgba(0,0,0,.15)">
              <input type="radio" name="package" value="{key}" required />
              <b style="margin-left:6px">{label}</b>
            </label>
            """

        page = f"""<!doctype html>
<html><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Prihvaćanje ponude</title></head>
<body style="font-family:Arial,Helvetica,sans-serif;background:#0b0f14;color:#e5e7eb;margin:0;padding:24px">
  <div style="max-width:720px;margin:0 auto;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12);border-radius:16px;padding:18px">
    <h2 style="margin:0 0 6px">Prihvaćanje ponude</h2>
    <p style="margin:0 0 14px;color:#9ca3af">Molimo odaberite paket i potvrdite.</p>
    <div style="padding:12px;border-radius:14px;background:rgba(0,0,0,.25);border:1px solid rgba(255,255,255,.10)">
      <b>{html.escape(e.first_name)} {html.escape(e.last_name)}</b><br/>
      Tip: {html.escape((e.event_type or "wedding").upper())}<br/>
      Datum: {html.escape(str(e.wedding_date))}<br/>
      Lokacija: {html.escape(e.venue)}<br/>
      Gostiju: {e.guest_count}
    </div>

    <form method="get" action="/accept" style="margin-top:14px">
      <input type="hidden" name="token" value="{html.escape(e.token)}"/>
      <h3 style="margin:14px 0 8px">Odaberite paket</h3>
      {options}
      <button type="submit" style="margin-top:12px;background:#16a34a;color:white;border:none;padding:10px 14px;border-radius:12px;font-weight:700;cursor:pointer">
        Potvrdi prihvaćanje
      </button>
      <a href="/decline?token={html.escape(e.token)}" style="margin-left:10px;color:#fca5a5">Odbij ponudu</a>
    </form>
  </div>
</body></html>"""
        return HTMLResponse(page)

    package_key = package.strip().lower()
    if package_key not in PACKAGE_LABELS:
        return HTMLResponse("<h3>Neispravan paket. Molimo odaberite Classic/Premium/Signature.</h3>", status_code=400)

    e.accepted = True
    e.status = "accepted"
    e.selected_package = package_key
    e.updated_at = datetime.utcnow()
    db.commit()

    chosen = PACKAGE_LABELS[package_key]
    return HTMLResponse(f"<h2>Ponuda prihvaćena ✅</h2><p>Odabrani paket: <b>{html.escape(chosen)}</b></p>")


@app.get("/decline", response_class=HTMLResponse)
def decline_get(
    token: str = Query(...),
    confirm: str | None = Query(None),
    db: Session = Depends(db_session),
):
    e = db.query(Event).filter_by(token=token).first()
    if not e:
        return HTMLResponse("<h3>Neispravan token.</h3>", status_code=404)

    if e.status == "declined":
        return HTMLResponse("<h3>Ponuda je već odbijena.</h3>")

    if confirm == "1":
        e.accepted = False
        e.status = "declined"
        e.updated_at = datetime.utcnow()
        db.commit()
        return HTMLResponse("<h2>Ponuda odbijena ❌</h2>")

    page = f"""<!doctype html>
<html><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Odbijanje ponude</title></head>
<body style="font-family:Arial,Helvetica,sans-serif;background:#0b0f14;color:#e5e7eb;margin:0;padding:24px">
  <div style="max-width:720px;margin:0 auto;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12);border-radius:16px;padding:18px">
    <h2 style="margin:0 0 6px">Odbijanje ponude</h2>
    <p style="margin:0 0 14px;color:#9ca3af">Potvrdite ako želite odbiti ponudu.</p>
    <form method="get" action="/decline">
      <input type="hidden" name="token" value="{html.escape(e.token)}"/>
      <input type="hidden" name="confirm" value="1"/>
      <button type="submit" style="background:#ef4444;color:white;border:none;padding:10px 14px;border-radius:12px;font-weight:700;cursor:pointer">
        Potvrdi odbijanje
      </button>
      <a href="/accept?token={html.escape(e.token)}" style="margin-left:10px;color:#86efac">Vrati se na prihvaćanje</a>
    </form>
  </div>
</body></html>"""
    return HTMLResponse(page)


# ======================
# ADMIN AUTH + UI + API
# ======================

from fastapi.security import HTTPBasic, HTTPBasicCredentials

security = HTTPBasic()


def require_admin(credentials: HTTPBasicCredentials = Depends(security)):
    if credentials.username != ADMIN_USER or credentials.password != ADMIN_PASSWORD:
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )


def _check_basic_auth(request: Request) -> bool:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(auth.split(" ", 1)[1]).decode("utf-8")
        username, password = decoded.split(":", 1)
    except Exception:
        return False
    return username == ADMIN_USER and password == ADMIN_PASSWORD


def _require_admin(request: Request):
    if not _check_basic_auth(request):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": 'Basic realm="Landsky Admin"'},
        )


@app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
def admin_page(request: Request):
    _require_admin(request)
    path = os.path.join("frontend", "admin.html")
    if os.path.isfile(path):
        return HTMLResponse(open(path, "r", encoding="utf-8").read())
    return HTMLResponse("<h2>admin.html not found</h2>", status_code=404)


@app.post("/admin/logout", include_in_schema=False)
def admin_logout():
    return Response(status_code=204)


@app.get("/admin/api/events")
def admin_events(
    request: Request,
    status: str | None = None,
    q: str | None = None,
    db: Session = Depends(db_session),
    _: None = Depends(require_admin),
):
    _require_admin(request)

    query = db.query(Event)

    if status:
        query = query.filter(Event.status == status)

    rows = query.order_by(Event.id.desc()).limit(500).all()

    if q:
        qq = q.lower()
        rows = [
            e for e in rows
            if (e.first_name and qq in e.first_name.lower())
            or (e.last_name and qq in e.last_name.lower())
            or (e.email and qq in e.email.lower())
        ]

    items = []
    for e in rows:
        kind, due_at = compute_next_reminder(e)
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
                "message": e.message or "",
                "status": e.status,
                "selected_package": e.selected_package or "",
                "event_type": e.event_type or "wedding",
                "offer_expires_at": e.offer_expires_at.isoformat() if e.offer_expires_at else None,
                "is_expired": bool(e.offer_expires_at and e.offer_expires_at < datetime.utcnow()) if e.status == "pending" else False,
                "reminder_count": int(e.reminder_count or 0),
                "last_email_sent_at": e.last_email_sent_at.isoformat() if e.last_email_sent_at else None,
                "next_reminder_kind": kind,
                "next_reminder_due": due_at.isoformat() if due_at else None,
                "offer_sent_at": e.offer_sent_at.isoformat() if e.offer_sent_at else None,
                "reminder_3d_sent_at": e.reminder_3d_sent_at.isoformat() if e.reminder_3d_sent_at else None,
                "reminder_7d_sent_at": e.reminder_7d_sent_at.isoformat() if e.reminder_7d_sent_at else None,
                "event_2d_sent_at": e.event_2d_sent_at.isoformat() if e.event_2d_sent_at else None,
                "created_at": e.created_at.isoformat() if e.created_at else None,
                "updated_at": e.updated_at.isoformat() if e.updated_at else None,
            }
        )
    return {"items": items}


def _apply_status(e: Event, status: str):
    status = status.strip().lower()
    if status not in ("pending", "accepted", "declined"):
        raise HTTPException(status_code=400, detail="Invalid status")
    e.status = status
    e.accepted = status == "accepted"
    e.updated_at = datetime.utcnow()


@app.get("/admin/api/events/{event_id}/email-logs")
def admin_email_logs(
    event_id: int,
    db: Session = Depends(db_session),
    _: None = Depends(require_admin),
):
    logs = (
        db.query(EmailLog)
        .filter(EmailLog.event_id == event_id)
        .order_by(EmailLog.id.desc())
        .limit(100)
        .all()
    )
    return [
        {
            "id": l.id,
            "event_id": l.event_id,
            "email_type": l.email_type,
            "to_email": l.to_email,
            "subject": l.subject,
            "provider": l.provider,
            "provider_message_id": l.provider_message_id,
            "status": l.status,
            "error": l.error,
            "created_at": l.created_at.isoformat() if l.created_at else None,
        }
        for l in logs
    ]


@app.post("/admin/api/events/{event_id}/notes")
def admin_add_note(
    event_id: int,
    payload: dict,
    request: Request,
    db: Session = Depends(db_session),
    _: None = Depends(require_admin),
):
    _require_admin(request)
    text_val = (payload.get("text") or "").strip()
    if not text_val:
        raise HTTPException(status_code=400, detail="Text is required")
    note = EventNote(event_id=event_id, text=text_val, created_at=datetime.utcnow())
    db.add(note)
    db.commit()
    return {"ok": True}


@app.get("/admin/api/events/{event_id}/timeline")
def admin_timeline(
    event_id: int,
    request: Request,
    db: Session = Depends(db_session),
    _: None = Depends(require_admin),
):
    _require_admin(request)

    emails = (
        db.query(EmailLog)
        .filter(EmailLog.event_id == event_id)
        .order_by(EmailLog.created_at.desc())
        .limit(200)
        .all()
    )
    notes = (
        db.query(EventNote)
        .filter(EventNote.event_id == event_id)
        .order_by(EventNote.created_at.desc())
        .limit(200)
        .all()
    )

    items = []
    for e in emails:
        items.append(
            {
                "type": "email",
                "created_at": e.created_at.isoformat() if e.created_at else None,
                "label": f"{e.email_type} → {e.to_email}",
                "status": e.status,
                "subject": e.subject,
            }
        )
    for n in notes:
        items.append(
            {
                "type": "note",
                "created_at": n.created_at.isoformat() if n.created_at else None,
                "label": n.text,
            }
        )

    items.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return items


@app.post("/admin/api/events/{event_id}/status")
def admin_set_status(
    event_id: int,
    payload: StatusUpdate,
    request: Request,
    db: Session = Depends(db_session),
    _: None = Depends(require_admin),
):
    _require_admin(request)
    e = db.query(Event).filter_by(id=event_id).first()
    if not e:
        raise HTTPException(status_code=404, detail="Not found")
    _apply_status(e, payload.status)
    db.commit()
    return {"ok": True}


@app.post("/admin/api/events/{event_id}/accept")
def admin_accept_event(
    event_id: int,
    request: Request,
    db: Session = Depends(db_session),
    _: None = Depends(require_admin),
):
    _require_admin(request)
    e = db.query(Event).filter_by(id=event_id).first()
    if not e:
        raise HTTPException(status_code=404, detail="Not found")
    _apply_status(e, "accepted")
    if not e.selected_package:
        e.selected_package = "—"
    db.commit()
    return {"ok": True}


@app.post("/admin/api/events/{event_id}/decline")
def admin_decline_event(
    event_id: int,
    request: Request,
    db: Session = Depends(db_session),
    _: None = Depends(require_admin),
):
    _require_admin(request)
    e = db.query(Event).filter_by(id=event_id).first()
    if not e:
        raise HTTPException(status_code=404, detail="Not found")
    _apply_status(e, "declined")
    db.commit()
    return {"ok": True}


@app.post("/admin/api/events/{event_id}/resend")
def admin_resend_offer(
    event_id: int,
    request: Request,
    db: Session = Depends(db_session),
    _: None = Depends(require_admin),
):
    _require_admin(request)
    e = db.query(Event).filter_by(id=event_id).first()
    if not e:
        raise HTTPException(status_code=404, detail="Not found")
    send_offer_flow(e, db=db)
    return {"ok": True}


@app.post("/admin/api/events/{event_id}/send-reminder-now")
def admin_send_reminder_now(
    event_id: int,
    request: Request,
    db: Session = Depends(db_session),
    _: None = Depends(require_admin),
):
    _require_admin(request)
    e = db.query(Event).filter_by(id=event_id).first()
    if not e:
        raise HTTPException(status_code=404, detail="Not found")

    kind, due_at = compute_next_reminder(e)
    if not kind:
        raise HTTPException(status_code=400, detail="No reminder due for this event")

    now = datetime.utcnow()
    recipient = get_reminder_recipient(e, kind)

    if kind == "offer_3d":
        res = db.execute(
            text(
                "UPDATE events SET reminder_3d_sent_at=:now, last_email_sent_at=:now, "
                "reminder_count=COALESCE(reminder_count,0)+1, updated_at=:now "
                "WHERE id=:id AND reminder_3d_sent_at IS NULL"
            ),
            {"now": now, "id": e.id},
        )
        db.commit()
        if res.rowcount != 1:
            return {"ok": True, "skipped": True}
        send_email_logged(db, e.id, "offer_3d", recipient, "Podsjetnik — Landsky ponuda", reminder_email_body(e))
        return {"ok": True}

    if kind == "offer_7d":
        res = db.execute(
            text(
                "UPDATE events SET reminder_7d_sent_at=:now, last_email_sent_at=:now, "
                "reminder_count=COALESCE(reminder_count,0)+1, updated_at=:now "
                "WHERE id=:id AND reminder_7d_sent_at IS NULL"
            ),
            {"now": now, "id": e.id},
        )
        db.commit()
        if res.rowcount != 1:
            return {"ok": True, "skipped": True}
        send_email_logged(db, e.id, "offer_7d", recipient, "Podsjetnik — Landsky ponuda", reminder_email_body(e))
        return {"ok": True}

    if kind == "event_2d":
        res = db.execute(
            text(
                "UPDATE events SET event_2d_sent_at=:now, last_email_sent_at=:now, updated_at=:now "
                "WHERE id=:id AND event_2d_sent_at IS NULL"
            ),
            {"now": now, "id": e.id},
        )
        db.commit()
        if res.rowcount != 1:
            return {"ok": True, "skipped": True}
        send_email_logged(db, e.id, "event_2d", recipient, "Interni podsjetnik — uskoro događaj", event_2d_email_body(e))
        return {"ok": True}

    raise HTTPException(status_code=400, detail="Invalid reminder type")
