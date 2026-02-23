import os
import ssl
import uuid
import html
import base64
import smtplib
from datetime import datetime, date, timedelta, time
from email.mime.text import MIMEText
from typing import Generator, Optional

import requests
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import Boolean, Column, Date, DateTime, Integer, String, Text, create_engine, text
from sqlalchemy.orm import Session, declarative_base, sessionmaker

# Optional scheduler
try:
    from apscheduler.schedulers.background import BackgroundScheduler  # type: ignore
except Exception:
    BackgroundScheduler = None

load_dotenv()

import logging

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

logger = logging.getLogger("landsly")

# ======================
# ENV
# ======================

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")

# Email provider: "resend" or "smtp"
EMAIL_PROVIDER = os.getenv("EMAIL_PROVIDER", "resend").lower().strip()

# Resend
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
# Sender: for Resend testing you can use onboarding@resend.dev
SENDER_EMAIL = os.getenv("SENDER_EMAIL", "onboarding@resend.dev").strip()

# Internal inbox (you)
CATERING_TEAM_EMAIL = os.getenv("CATERING_TEAM_EMAIL", SENDER_EMAIL).strip()

# SMTP (optional)
SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT = int((os.getenv("SMTP_PORT", "465").strip() or "465"))
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "").strip()

# Admin basic auth
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "change-me")

# TEST MODE: if "1" or "true" -> offer emails go only to CATERING_TEAM_EMAIL
TEST_MODE = os.getenv("TEST_MODE", "1").lower() in ("1", "true", "yes", "on")

# Reminders
REMINDERS_ENABLED = os.getenv("REMINDERS_ENABLED", "0").lower() in ("1", "true", "yes", "on")
REMINDER_DAY_1 = int(os.getenv("REMINDER_DAY_1", "3"))
REMINDER_DAY_2 = int(os.getenv("REMINDER_DAY_2", "7"))

# ======================
# DB
# ======================

Base = declarative_base()


def _sanitize_database_url(url: str) -> str:
    """
    Neon connection strings often include sslmode=require in the query string.
    Some drivers don't accept sslmode in connect args, so we strip query params
    and enforce SSL via connect_args.
    """
    if not url:
        return url
    if "?" in url:
        url = url.split("?", 1)[0]
    return url


def _make_engine():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set")

    url = _sanitize_database_url(DATABASE_URL)

    connect_args = {}
    if url.startswith("postgresql") or url.startswith("postgres://"):
        ssl_ctx = ssl.create_default_context()
        connect_args["ssl_context"] = ssl_ctx

    if "sqlite" in url:
        connect_args = {"check_same_thread": False}

    return create_engine(url, pool_pre_ping=True, connect_args=connect_args)


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Event(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, index=True)
    token = Column(String(64), unique=True, index=True, nullable=False)

    first_name = Column(String(120), nullable=False)
    last_name = Column(String(120), nullable=False)
    wedding_date = Column(Date, nullable=False)
    venue = Column(String(255), nullable=False)
    guest_count = Column(Integer, nullable=False)

    email = Column(String(255), nullable=False)
    phone = Column(String(80), nullable=False)

    # Couple notes / questions
    message = Column(Text, nullable=True)

    # Status + package choice
    status = Column(String(30), default="pending", nullable=False)  # pending/accepted/declined
    selected_package = Column(String(30), nullable=True)  # classic/premium/signature
    accepted = Column(Boolean, default=False, nullable=False)

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
except Exception as ex:
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
    first_name: str = Field(..., min_length=1, max_length=120)
    last_name: str = Field(..., min_length=1, max_length=120)
    wedding_date: date
    venue: str = Field(..., min_length=1, max_length=255)
    guest_count: int = Field(..., ge=1, le=10000)
    email: EmailStr
    phone: str = Field(..., min_length=3, max_length=80)
    message: Optional[str] = Field(default=None, max_length=5000)


class StatusUpdate(BaseModel):
    status: str


# ======================
# EMAIL
# ======================

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
        timeout=30,
    )
    if r.status_code >= 300:
        raise RuntimeError(f"Resend error: {r.text}")


def send_email_smtp(to_email: str, subject: str, body_html: str):
    if not (SMTP_HOST and SMTP_USER and SMTP_PASSWORD):
        raise RuntimeError("SMTP settings missing (SMTP_HOST/SMTP_USER/SMTP_PASSWORD)")

    msg = MIMEText(body_html, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = SENDER_EMAIL
    msg["To"] = to_email

    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SENDER_EMAIL, [to_email], msg.as_string())


def send_email(to_email: str, subject: str, body_html: str):
    if EMAIL_PROVIDER == "smtp":
        return send_email_smtp(to_email, subject, body_html)
    return send_email_resend(to_email, subject, body_html)


def get_reminder_recipient(e: "Event", kind: str) -> str:
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


PACKAGE_LABELS = {
    "classic": "Classic",
    "premium": "Premium",
    "signature": "Signature",
}


def render_offer_html(e: Event) -> str:
    template_path = os.path.join("frontend", "offer.html")
    if os.path.exists(template_path):
        tpl = open(template_path, "r", encoding="utf-8").read()
        return (
            tpl.replace("{{FIRST_NAME}}", html.escape(e.first_name))
            .replace("{{LAST_NAME}}", html.escape(e.last_name))
            .replace("{{WEDDING_DATE}}", html.escape(str(e.wedding_date)))
            .replace("{{VENUE}}", html.escape(e.venue))
            .replace("{{GUEST_COUNT}}", str(e.guest_count))
            .replace("{{EMAIL}}", html.escape(e.email))
            .replace("{{PHONE}}", html.escape(e.phone))
            .replace("{{MESSAGE}}", html.escape((e.message or "").strip()))
            .replace("{{ACCEPT_URL}}", f"{BASE_URL}/accept?token={e.token}")
            .replace("{{DECLINE_URL}}", f"{BASE_URL}/decline?token={e.token}")
            .replace("{{BASE_URL}}", BASE_URL)
        )

    logo_url = f"{BASE_URL}/frontend/logo.png"
    cocktails_pdf = f"{BASE_URL}/frontend/cocktails.pdf"
    bar_img = f"{BASE_URL}/frontend/bar.jpeg"
    cigare_img = f"{BASE_URL}/frontend/cigare.png"
    accept_link = f"{BASE_URL}/accept?token={e.token}"
    decline_link = f"{BASE_URL}/decline?token={e.token}"
    msg = (e.message or "").strip()
    msg_html = html.escape(msg).replace("\\n", "<br>") if msg else "(nema)"

    return f"""
<div style="font-family: Arial, sans-serif; color:#111; line-height:1.5;">
  <div style="max-width:720px; margin:0 auto; border:1px solid #eee; border-radius:14px; overflow:hidden;">
    <div style="background:#0b0f14; padding:18px 18px 12px 18px;">
      <div style="display:flex; align-items:center; gap:14px;">
        <img src="{logo_url}" alt="Landsky Cocktail Catering"
          style="width:68px; height:68px; object-fit:contain;
                 background:#ffffff; border:1px solid rgba(0,0,0,.08);
                 border-radius:14px; padding:10px;">
        <div>
          <div style="color:#fff; font-size:18px; font-weight:700;">Landsky Cocktail Catering</div>
          <div style="color:rgba(255,255,255,.7); font-size:12px;">Ponuda</div>
        </div>
      </div>
    </div>

    <div style="padding:18px;">
      <p style="margin:0 0 10px 0;"><b>Poštovani {html.escape(e.first_name)} {html.escape(e.last_name)},</b></p>
      <p style="margin:0 0 14px 0;">Zahvaljujemo na Vašem upitu. U nastavku dostavljamo informacije vezane za cocktail catering.</p>

      <div style="background:#fafafa; border:1px solid #eee; border-radius:12px; padding:12px 14px; margin:14px 0;">
        <div style="font-weight:700; margin-bottom:6px;">Sažetak upita</div>
        <div>📅 <b>Datum:</b> {html.escape(str(e.wedding_date))}</div>
        <div>📍 <b>Lokacija / sala:</b> {html.escape(e.venue)}</div>
        <div>👥 <b>Broj gostiju:</b> {e.guest_count}</div>
        <div>✉️ <b>Email:</b> {html.escape(e.email)}</div>
        <div>📞 <b>Telefon:</b> {html.escape(e.phone)}</div>
        <div style="margin-top:8px;"><b>Napomena / pitanja:</b><br>{msg_html}</div>
      </div>

      <div style="background:#fff7e6; border:1px solid #f3e3bf; border-radius:12px; padding:12px 14px; margin:14px 0;">
        <div style="font-weight:700; margin-bottom:6px;">Cijene paketa</div>
        <div>• <b>Classic:</b> 1.000 EUR + PDV (100 koktela) — dodatnih 100: 500 EUR + PDV</div>
        <div>• <b>Premium:</b> 1.200 EUR + PDV (100 koktela) — dodatnih 100: 600 EUR + PDV</div>
        <div>• <b>Signature:</b> 1.500 EUR + PDV (100 koktela) — dodatnih 100: 800 EUR + PDV</div>
        <div style="margin-top:8px; color:#6b5a2a;">* Preporučujemo 200 koktela.</div>
        <div style="margin-top:10px;">
          📎 Detalji paketa: <a href="{cocktails_pdf}">{cocktails_pdf}</a>
        </div>
      </div>

      <div style="margin:14px 0;">
        <div style="font-weight:700; margin-bottom:6px;">Premium cigare (opcionalno)</div>
        <p style="margin:0 0 8px 0;">
          Uz odabir cigara od nas dobivate humidor, rezač, upaljač i pepeljare.
          Nudimo i <b>Cigar Connoisseur</b> uslugu — <b>450 EUR + PDV</b> (3 sata).
        </p>
        📎 Popis cigara: <a href="{cigare_img}">{cigare_img}</a>
      </div>

      <p style="margin:0 0 10px 0;">
        Za događaje izvan Zagreba naplaćuje se put <b>0,70 EUR/km</b>.
      </p>

      <div style="border-top:1px solid #eee; margin-top:16px; padding-top:14px;">
        <div style="font-weight:700; margin-bottom:6px;">Potvrda ponude</div>
        <p style="margin:0 0 10px 0;">Molimo potvrdite ponudu klikom:</p>
        <p style="margin:0;">
          ✅ <a href="{accept_link}">Prihvaćam</a><br>
          ❌ <a href="{decline_link}">Odbijam</a>
        </p>
        <p style="margin:10px 0 0; color:#6b7280; font-size:12px;">
          Napomena: kod prihvaćanja ćete odabrati paket (Classic / Premium / Signature).
        </p>
      </div>
    </div>
  </div>
</div>
"""


def internal_email_body(e: Event) -> str:
    preview_link = f"{BASE_URL}/offer-preview?token={e.token}"
    admin_link = f"{BASE_URL}/admin"
    msg = (e.message or "").strip()
    msg_html = html.escape(msg).replace("\\n", "<br>") if msg else "(nema)"
    return f"""
<div style="font-family: Arial, sans-serif; color:#111; line-height:1.5;">
  <h2>Novi upit</h2>
  <ul>
    <li><b>Mladenci:</b> {html.escape(e.first_name)} {html.escape(e.last_name)}</li>
    <li><b>Email mladenaca:</b> {html.escape(e.email)}</li>
    <li><b>Telefon:</b> {html.escape(e.phone)}</li>
    <li><b>Datum:</b> {html.escape(str(e.wedding_date))}</li>
    <li><b>Sala:</b> {html.escape(e.venue)}</li>
    <li><b>Gosti:</b> {e.guest_count}</li>
    <li><b>Status:</b> {html.escape(e.status)}</li>
    <li><b>Odabrani paket:</b> {html.escape(e.selected_package or "—")}</li>
  </ul>
  <p><b>Napomena / Pitanja:</b><br>{msg_html}</p>
  <p><b>Preview ponude:</b><br><a href="{preview_link}">{preview_link}</a></p>
  <p><b>Admin:</b> <a href="{admin_link}">{admin_link}</a></p>
</div>
"""


def send_offer_flow(e: Event, db: Optional[Session] = None):
    # internal notification
    send_email(
        CATERING_TEAM_EMAIL,
        f"Novi upit: {e.first_name} {e.last_name}{' (TEST)' if TEST_MODE else ''}",
        internal_email_body(e),
    )

    # offer email
    offer_recipient = CATERING_TEAM_EMAIL if TEST_MODE else e.email
    send_email(
        offer_recipient,
        f"Ponuda – {e.first_name} {e.last_name}{' (TEST)' if TEST_MODE else ''}",
        render_offer_html(e),
    )

    if db is not None:
        now = datetime.utcnow()
        e.last_email_sent_at = now
        e.offer_sent_at = now
        e.reminder_count = 0
        e.reminder_3d_sent_at = None
        e.reminder_7d_sent_at = None
        e.updated_at = now
        db.commit()


def reminder_email_body(e: Event) -> str:
    accept_link = f"{BASE_URL}/accept?token={e.token}"
    decline_link = f"{BASE_URL}/decline?token={e.token}"
    return f"""
<div style="font-family: Arial, sans-serif; color:#111; line-height:1.5; max-width:700px; margin:0 auto;">
  <h2>Podsjetnik — Landsky Cocktail Catering ponuda</h2>
  <p>Poštovani {html.escape(e.first_name)} {html.escape(e.last_name)},</p>
  <p>Samo kratki podsjetnik vezano za našu ponudu za datum <b>{html.escape(str(e.wedding_date))}</b> ({html.escape(e.venue)}).</p>
  <p>✅ <a href="{accept_link}">Prihvaćam ponudu</a><br>
     ❌ <a href="{decline_link}">Odbijam ponudu</a></p>
</div>
"""



def event_2d_email_body(e: Event) -> str:
    return f"""
<div style="font-family: Arial, sans-serif; color:#111; line-height:1.5; max-width:700px; margin:0 auto;">
  <h2>Podsjetnik — Vaše vjenčanje je uskoro</h2>
  <p>Poštovani {html.escape(e.first_name)} {html.escape(e.last_name)},</p>
  <p>Samo kratka potvrda da smo sve spremni za vaš datum <b>{html.escape(str(e.wedding_date))}</b> na lokaciji <b>{html.escape(e.venue)}</b>.</p>
  <p>Ako imate bilo kakve promjene oko broja gostiju ili detalja, slobodno nam se javite.</p>
  <p>Srdačno,<br>Landsky Cocktail Catering</p>
</div>
"""


def compute_next_reminder(e: Event) -> tuple[Optional[str], Optional[datetime]]:
    """Returns (kind, due_at) where kind is: offer_3d, offer_7d, event_2d."""
    # Offer reminders for pending events
    if e.status == "pending":
        base = e.offer_sent_at or e.last_email_sent_at
        if base:
            if e.reminder_3d_sent_at is None:
                return ("offer_3d", base + timedelta(days=REMINDER_DAY_1))
            if e.reminder_7d_sent_at is None:
                return ("offer_7d", base + timedelta(days=REMINDER_DAY_2))

    # 2 days before wedding date for accepted
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
                lock_acquired = bool(
                    db.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": 927341}).scalar()
                )
                if not lock_acquired:
                    logger.info("reminder_job: lock not acquired, skipping")
                    return
            except Exception:
                # If lock fails for any reason, proceed without it (still idempotent by DB flags)
                logger.exception("reminder_job: advisory lock error")

        # 1) Offer reminders (pending)
        pending = db.query(Event).filter(Event.status == "pending").all()
        for e in pending:
            base = e.offer_sent_at or e.last_email_sent_at
            if not base:
                continue

            # 3-day (or REMINDER_DAY_1) reminder
            if e.reminder_3d_sent_at is None and (now - base).days >= REMINDER_DAY_1:
                # Claim atomically
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
                        send_email(get_reminder_recipient(e, "offer_3d"), "Podsjetnik — Landsky ponuda", reminder_email_body(e))
                    except Exception:
                        logger.exception("reminder_job: send failed (offer_3d)", extra={"event_id": e.id})
                continue

            # 7-day (or REMINDER_DAY_2) reminder
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
                        send_email(get_reminder_recipient(e, "offer_7d"), "Podsjetnik — Landsky ponuda", reminder_email_body(e))
                    except Exception:
                        logger.exception("reminder_job: send failed (offer_7d)", extra={"event_id": e.id})

        # 2) Accepted events: 2 days before wedding date (only once)
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

            # internal recipient (catering team)
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
                    send_email(recipient, "Podsjetnik — uskoro vjenčanje", event_2d_email_body(e))
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

app = FastAPI(title="Landsky Wedding App")
app.mount("/frontend", StaticFiles(directory="frontend", html=True), name="frontend")


@app.on_event("startup")
def _startup():
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
    except Exception as ex:
        logger.exception("EMAIL SEND FAILED")

    return {"message": "Vaš upit je zaprimljen.", "preview_url": preview_url}


@app.get("/offer-preview", response_class=HTMLResponse)
def offer_preview(token: str = Query(...), db: Session = Depends(db_session)):
    e = db.query(Event).filter_by(token=token).first()
    if not e:
        raise HTTPException(status_code=404, detail="Token not found")
    return HTMLResponse(render_offer_html(e))


# ======================
# ACCEPT / DECLINE (couple) - GET only (no python-multipart needed)
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


# OLD endpoints
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
    # recipient determined per reminder kind (offer_3d/offer_7d)

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
        send_email(recipient, "Podsjetnik — Landsky ponuda", reminder_email_body(e))
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
        send_email(recipient, "Podsjetnik — Landsky ponuda", reminder_email_body(e))
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
        send_email(recipient, "Podsjetnik — uskoro vjenčanje", event_2d_email_body(e))
        return {"ok": True}

    raise HTTPException(status_code=400, detail="Invalid reminder type")
