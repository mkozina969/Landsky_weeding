import os
import ssl
import uuid
from datetime import date, datetime

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr, Field

from sqlalchemy import Boolean, Column, Date, DateTime, Integer, String, Text, create_engine, select
from sqlalchemy.orm import Session, declarative_base, sessionmaker

import requests

# -----------------------------
# Environment
# -----------------------------
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")
TEST_MODE = os.getenv("TEST_MODE", "true").lower() in ("1", "true", "yes", "on")

# Email routing:
# - In TEST_MODE: all outgoing emails go only to CATERING_TEAM_EMAIL (e.g. you)
# - In PROD: offer emails go to couple's email, internal notifications can still go to CATERING_TEAM_EMAIL
SENDER_EMAIL = os.getenv("SENDER_EMAIL", "mkozina31@gmail.com")
CATERING_TEAM_EMAIL = os.getenv("CATERING_TEAM_EMAIL", SENDER_EMAIL)

# Provider toggle: "resend" or "smtp"
EMAIL_PROVIDER = os.getenv("EMAIL_PROVIDER", "resend").lower().strip()

# Resend
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()

# SMTP (optional, not recommended on Render Free)
SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "465").strip() or "465")
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "").strip()

# Admin basic auth
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "change-me")

# Optional Scheduler (we keep it optional; install APScheduler if you want reminders)
try:
    from apscheduler.schedulers.background import BackgroundScheduler  # type: ignore
except Exception:
    BackgroundScheduler = None


# -----------------------------
# Database (SQLAlchemy)
# -----------------------------
Base = declarative_base()


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
    notes = Column(Text, nullable=True)

    # Status + package choice
    status = Column(String(30), default="pending", nullable=False)   # pending/accepted/declined
    selected_package = Column(String(30), nullable=True)             # classic/premium/signature
    accepted = Column(Boolean, default=False, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)


def _sanitize_database_url(url: str) -> str:
    """
    Neon connection strings often include sslmode=require (psycopg2 style).
    pg8000 does NOT accept sslmode keyword in connect(). We remove query params and enforce SSL via connect_args.
    """
    if not url:
        return url
    # Drop query string entirely; we enforce SSL via ssl_context
    if "?" in url:
        url = url.split("?", 1)[0]
    return url


def _make_engine():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set")

    url = _sanitize_database_url(DATABASE_URL)

    connect_args = {}
    # If using pg8000 driver, enforce SSL to satisfy Neon.
    # URL example should be: postgresql+pg8000://user:pass@host/db
    if url.startswith("postgresql+pg8000://") or url.startswith("postgres://") or url.startswith("postgresql://"):
        # If user supplied postgres:// or postgresql://, SQLAlchemy may pick default driver.
        # We strongly recommend explicit "postgresql+pg8000://"
        ssl_ctx = ssl.create_default_context()
        connect_args["ssl_context"] = ssl_ctx

    return create_engine(url, pool_pre_ping=True, connect_args=connect_args)


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# -----------------------------
# Pydantic models
# -----------------------------
class RegistrationRequest(BaseModel):
    first_name: str = Field(..., min_length=1, max_length=120)
    last_name: str = Field(..., min_length=1, max_length=120)
    wedding_date: date
    venue: str = Field(..., min_length=1, max_length=255)
    guest_count: int = Field(..., ge=1, le=10000)
    email: EmailStr
    phone: str = Field(..., min_length=3, max_length=80)
    notes: str | None = Field(default=None, max_length=5000)


class StatusUpdate(BaseModel):
    status: str


# -----------------------------
# App init
# -----------------------------
app = FastAPI(title="Landsky Wedding App")

# Serve /frontend static site (index.html + assets)
app.mount("/frontend", StaticFiles(directory="frontend", html=True), name="frontend")


@app.on_event("startup")
def _startup():
    # Create tables
    Base.metadata.create_all(bind=engine)

    # Optional scheduler
    if BackgroundScheduler is not None:
        # NOTE: background schedulers on free instances can stop when instance sleeps.
        # When you upgrade, it's stable enough for light reminders.
        scheduler = BackgroundScheduler()
        scheduler.start()
        app.state.scheduler = scheduler


# -----------------------------
# Helpers: Email (Resend / SMTP)
# -----------------------------
def _load_offer_template_text() -> str:
    """
    Optional: if you keep a plain text file in frontend/offer_template.txt.
    If missing, we use built-in default template.
    """
    candidates = [
        os.path.join("frontend", "offer_template.txt"),
        os.path.join("frontend", "ponuda.txt"),
        os.path.join("frontend", "ponude.txt"),
    ]
    for p in candidates:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                return f.read()
    return ""


def _default_offer_html(e: Event) -> str:
    details = f"""
    <div style="border:1px solid #e5e7eb;border-radius:12px;padding:14px;background:#f9fafb">
      <b>Sažetak upita</b><br/>
      📅 <b>Datum:</b> {e.wedding_date}<br/>
      📍 <b>Lokacija / sala:</b> {e.venue}<br/>
      👥 <b>Broj gostiju:</b> {e.guest_count}<br/>
      ✉️ <b>Email:</b> {e.email}<br/>
      ☎️ <b>Telefon:</b> {e.phone}<br/>
      <br/>
      <b>Napomena / pitanja:</b><br/>
      <div style="white-space:pre-wrap">{(e.notes or '').strip() or '—'}</div>
    </div>
    """

    accept_url = f"{BASE_URL}/accept?token={e.token}"
    decline_url = f"{BASE_URL}/decline?token={e.token}"

    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Landsky Catering – Ponuda</title>
</head>
<body style="margin:0;padding:0;background:#ffffff;font-family:Arial,Helvetica,sans-serif;color:#111827">
  <div style="max-width:720px;margin:0 auto;padding:18px">
    <div style="background:#0b0f14;color:#fff;border-radius:14px;padding:18px 18px;display:flex;gap:14px;align-items:center">
      <div style="width:56px;height:56px;border-radius:12px;background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.15);display:flex;align-items:center;justify-content:center;overflow:hidden">
        <img src="{BASE_URL}/frontend/assets/logo.png" alt="Logo" style="width:42px;filter:invert(1) brightness(1.1) contrast(1.05);opacity:.95" />
      </div>
      <div>
        <div style="font-weight:700;font-size:18px;line-height:1">Landsky Catering</div>
        <div style="opacity:.85;font-size:13px;margin-top:4px">Ponuda za vjenčanje</div>
      </div>
    </div>

    <p style="margin:18px 0 10px">Poštovani {e.first_name} {e.last_name},</p>
    <p style="margin:0 0 14px">Zahvaljujemo na Vašem upitu. U nastavku dostavljamo informacije vezane za cocktail catering.</p>

    {details}

    <h3 style="margin:18px 0 8px">Potvrda ponude</h3>
    <p style="margin:0 0 10px;color:#374151">Molimo odaberite paket (Classic / Premium / Signature) i potvrdite ponudu klikom:</p>

    <div style="display:flex;gap:10px;flex-wrap:wrap;margin:12px 0 18px">
      <a href="{accept_url}" style="background:#16a34a;color:#fff;text-decoration:none;padding:10px 14px;border-radius:10px;font-weight:700">Prihvaćam</a>
      <a href="{decline_url}" style="background:#ef4444;color:#fff;text-decoration:none;padding:10px 14px;border-radius:10px;font-weight:700">Odbijam</a>
    </div>

    <p style="margin:0;color:#6b7280;font-size:12px">Ovaj e-mail je generiran automatski.</p>
  </div>
</body>
</html>
"""


def render_offer_html(e: Event) -> str:
    """
    If you have a custom HTML template in frontend/offer.html, we load it and replace placeholders.
    Otherwise, we use a good default.
    """
    template_path = os.path.join("frontend", "offer.html")
    if os.path.exists(template_path):
        html = open(template_path, "r", encoding="utf-8").read()
        html = html.replace("{{FIRST_NAME}}", e.first_name)
        html = html.replace("{{LAST_NAME}}", e.last_name)
        html = html.replace("{{WEDDING_DATE}}", str(e.wedding_date))
        html = html.replace("{{VENUE}}", e.venue)
        html = html.replace("{{GUEST_COUNT}}", str(e.guest_count))
        html = html.replace("{{EMAIL}}", e.email)
        html = html.replace("{{PHONE}}", e.phone)
        html = html.replace("{{NOTES}}", (e.notes or "").strip())
        html = html.replace("{{ACCEPT_URL}}", f"{BASE_URL}/accept?token={e.token}")
        html = html.replace("{{DECLINE_URL}}", f"{BASE_URL}/decline?token={e.token}")
        html = html.replace("{{BASE_URL}}", BASE_URL)
        return html

    return _default_offer_html(e)


def send_email_resend(to_email: str, subject: str, html: str):
    if not RESEND_API_KEY:
        raise RuntimeError("RESEND_API_KEY is not set")

    # Resend requires verified domain for arbitrary "from" addresses.
    # In testing: set from to "onboarding@resend.dev" or your verified domain.
    # We'll use SENDER_EMAIL but if not verified, Resend will error.
    # You can set SENDER_EMAIL to "onboarding@resend.dev" while testing.
    from_email = SENDER_EMAIL

    r = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "from": from_email,
            "to": [to_email],
            "subject": subject,
            "html": html,
        },
        timeout=30,
    )
    if r.status_code >= 300:
        raise RuntimeError(f"Resend error: {r.text}")


def send_email_smtp(to_email: str, subject: str, html: str):
    import smtplib
    from email.mime.text import MIMEText

    if not (SMTP_HOST and SMTP_USER and SMTP_PASSWORD):
        raise RuntimeError("SMTP settings missing (SMTP_HOST/SMTP_USER/SMTP_PASSWORD)")

    msg = MIMEText(html, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = SENDER_EMAIL
    msg["To"] = to_email

    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SENDER_EMAIL, [to_email], msg.as_string())


def send_offer_email(e: Event, couple_email: str):
    subject = "Ponuda za vaše vjenčanje"
    html = render_offer_html(e)

    if EMAIL_PROVIDER == "smtp":
        send_email_smtp(couple_email, subject, html)
    else:
        send_email_resend(couple_email, subject, html)


# -----------------------------
# Routes: Front
# -----------------------------
@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/frontend/")


@app.post("/register")
def register(payload: RegistrationRequest, db: Session = Depends(db_session)):
    token = str(uuid.uuid4())

    e = Event(
        token=token,
        first_name=payload.first_name.strip(),
        last_name=payload.last_name.strip(),
        wedding_date=payload.wedding_date,
        venue=payload.venue.strip(),
        guest_count=payload.guest_count,
        email=str(payload.email),
        phone=payload.phone.strip(),
        notes=(payload.notes or "").strip() or None,
        status="pending",
        accepted=False,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )

    db.add(e)
    db.commit()
    db.refresh(e)

    # Decide who receives offer email
    offer_recipient = CATERING_TEAM_EMAIL if TEST_MODE else e.email

    preview_url = None
    if TEST_MODE:
        preview_url = f"{BASE_URL}/offer-preview?token={e.token}"

    # Try sending email but NEVER fail registration in test mode.
    try:
        send_offer_email(e, offer_recipient)
    except Exception as ex:
        # Log on server; still return success so UI doesn't "look broken"
        print("EMAIL SEND FAILED:", repr(ex))

    return {"message": "Vaš upit je zaprimljen.", "preview_url": preview_url}


@app.get("/offer-preview", response_class=HTMLResponse)
def offer_preview(token: str = Query(...), db: Session = Depends(db_session)):
    e = db.query(Event).filter_by(token=token).first()
    if not e:
        raise HTTPException(status_code=404, detail="Token not found")
    return HTMLResponse(render_offer_html(e))


# -----------------------------
# Accept / Decline flow
# -----------------------------
PACKAGE_LABELS = {
    "classic": "Classic",
    "premium": "Premium",
    "signature": "Signature",
}


@app.get("/accept", response_class=HTMLResponse)
def accept_get(token: str = Query(...), db: Session = Depends(db_session)):
    e = db.query(Event).filter_by(token=token).first()
    if not e:
        return HTMLResponse("<h3>Neispravan token.</h3>", status_code=404)

    if e.status == "accepted":
        chosen = PACKAGE_LABELS.get((e.selected_package or "").lower(), e.selected_package or "—")
        return HTMLResponse(f"<h3>Ponuda je već prihvaćena.</h3><p>Odabrani paket: <b>{chosen}</b></p>")

    if e.status == "declined":
        return HTMLResponse("<h3>Ponuda je već odbijena.</h3>")

    options = ""
    for key, label in PACKAGE_LABELS.items():
        options += f"""
        <label style="display:block;margin:8px 0">
          <input type="radio" name="package" value="{key}" required />
          <b>{label}</b>
        </label>
        """

    html = f"""<!doctype html>
<html><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Prihvaćanje ponude</title>
</head>
<body style="font-family:Arial,Helvetica,sans-serif;background:#0b0f14;color:#e5e7eb;margin:0;padding:24px">
  <div style="max-width:720px;margin:0 auto;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12);border-radius:16px;padding:18px">
    <h2 style="margin:0 0 6px">Prihvaćanje ponude</h2>
    <p style="margin:0 0 14px;color:#9ca3af">Molimo odaberite paket i potvrdite.</p>

    <div style="padding:12px;border-radius:14px;background:rgba(0,0,0,.25);border:1px solid rgba(255,255,255,.10)">
      <b>{e.first_name} {e.last_name}</b><br/>
      Datum: {e.wedding_date}<br/>
      Lokacija: {e.venue}<br/>
      Gostiju: {e.guest_count}
    </div>

    <form method="post" action="/accept" style="margin-top:14px">
      <input type="hidden" name="token" value="{e.token}"/>
      <h3 style="margin:14px 0 8px">Odaberite paket</h3>
      {options}
      <button type="submit" style="margin-top:12px;background:#16a34a;color:white;border:none;padding:10px 14px;border-radius:12px;font-weight:700;cursor:pointer">Potvrdi prihvaćanje</button>
      <a href="/decline?token={e.token}" style="margin-left:10px;color:#fca5a5">Odbij ponudu</a>
    </form>
  </div>
</body></html>"""
    return HTMLResponse(html)


@app.post("/accept", response_class=HTMLResponse)
async def accept_post(request: Request, db: Session = Depends(db_session)):
    form = await request.form()
    token = str(form.get("token", "")).strip()
    package = str(form.get("package", "")).strip().lower()

    if package not in PACKAGE_LABELS:
        return HTMLResponse("<h3>Molimo odaberite paket.</h3>", status_code=400)

    e = db.query(Event).filter_by(token=token).first()
    if not e:
        return HTMLResponse("<h3>Neispravan token.</h3>", status_code=404)

    e.status = "accepted"
    e.accepted = True
    e.selected_package = package
    e.updated_at = datetime.utcnow()
    db.commit()

    chosen = PACKAGE_LABELS[package]
    return HTMLResponse(f"<h2>Ponuda prihvaćena ✅</h2><p>Odabrani paket: <b>{chosen}</b></p>")


@app.get("/decline", response_class=HTMLResponse)
def decline_get(token: str = Query(...), db: Session = Depends(db_session)):
    e = db.query(Event).filter_by(token=token).first()
    if not e:
        return HTMLResponse("<h3>Neispravan token.</h3>", status_code=404)

    if e.status == "declined":
        return HTMLResponse("<h3>Ponuda je već odbijena.</h3>")

    html = f"""<!doctype html>
<html><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Odbijanje ponude</title>
</head>
<body style="font-family:Arial,Helvetica,sans-serif;background:#0b0f14;color:#e5e7eb;margin:0;padding:24px">
  <div style="max-width:720px;margin:0 auto;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12);border-radius:16px;padding:18px">
    <h2 style="margin:0 0 6px">Odbijanje ponude</h2>
    <p style="margin:0 0 14px;color:#9ca3af">Potvrdite ako želite odbiti ponudu.</p>

    <form method="post" action="/decline">
      <input type="hidden" name="token" value="{e.token}"/>
      <button type="submit" style="background:#ef4444;color:white;border:none;padding:10px 14px;border-radius:12px;font-weight:700;cursor:pointer">Potvrdi odbijanje</button>
      <a href="/accept?token={e.token}" style="margin-left:10px;color:#86efac">Vrati se na prihvaćanje</a>
    </form>
  </div>
</body></html>"""
    return HTMLResponse(html)


@app.post("/decline", response_class=HTMLResponse)
async def decline_post(request: Request, db: Session = Depends(db_session)):
    form = await request.form()
    token = str(form.get("token", "")).strip()

    e = db.query(Event).filter_by(token=token).first()
    if not e:
        return HTMLResponse("<h3>Neispravan token.</h3>", status_code=404)

    e.status = "declined"
    e.accepted = False
    e.updated_at = datetime.utcnow()
    db.commit()
    return HTMLResponse("<h2>Ponuda odbijena ❌</h2>")


# -----------------------------
# Admin (Basic Auth)
# -----------------------------
def _check_basic_auth(request: Request) -> bool:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Basic "):
        return False
    import base64

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
    return HTMLResponse(open(os.path.join("frontend", "admin.html"), "r", encoding="utf-8").read())


@app.post("/admin/logout", include_in_schema=False)
def admin_logout():
    # Browsers keep basic auth cached; simplest is to respond 401 on next request.
    return Response(status_code=204)


@app.get("/admin/api/events")
def admin_events(
    request: Request,
    status: str | None = None,
    q: str | None = None,
    db: Session = Depends(db_session),
):
    _require_admin(request)

    query = db.query(Event)

    if status:
        query = query.filter(Event.status == status)

    if q:
        like = f"%{q.lower()}%"
        query = query.filter(
            (Event.first_name.ilike(like))
            | (Event.last_name.ilike(like))
            | (Event.email.ilike(like))
        )

    rows = query.order_by(Event.id.desc()).limit(500).all()

    return [
        {
            "id": r.id,
            "token": r.token,
            "status": r.status,
            "selected_package": r.selected_package,
            "first_name": r.first_name,
            "last_name": r.last_name,
            "wedding_date": str(r.wedding_date),
            "venue": r.venue,
            "guest_count": r.guest_count,
            "email": r.email,
            "phone": r.phone,
            "notes": r.notes or "",
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@app.post("/admin/api/events/{event_id}/status")
def admin_set_status(
    event_id: int,
    payload: StatusUpdate,
    request: Request,
    db: Session = Depends(db_session),
):
    _require_admin(request)

    status = payload.status.strip().lower()
    if status not in ("pending", "accepted", "declined"):
        raise HTTPException(status_code=400, detail="Invalid status")

    e = db.query(Event).filter_by(id=event_id).first()
    if not e:
        raise HTTPException(status_code=404, detail="Not found")

    e.status = status
    e.accepted = status == "accepted"
    e.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True}
