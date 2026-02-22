from __future__ import annotations

import os
import uuid
import base64
from datetime import date, datetime, timedelta
from typing import Optional, List, Dict, Any

import smtplib
from email.mime.text import MIMEText

from fastapi import FastAPI, HTTPException, Query, Depends, Request, Header
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from pydantic import BaseModel, EmailStr

from sqlalchemy import (
    Column,
    Integer,
    String,
    Date,
    DateTime,
    Boolean,
    create_engine,
    text,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session


# =========================
# CONFIG (.env / env vars)
# =========================

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./wedding_app.db")

SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", "0") or 0)
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")

# Landsky mailbox (fixed by spec)
SENDER_EMAIL = os.getenv("SENDER_EMAIL", "mkozina31@gmail.com")

# Where internal notifications go (defaults to same)
CATERING_TEAM_EMAIL = os.getenv("CATERING_TEAM_EMAIL", SENDER_EMAIL)

# Admin access (Basic Auth)
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "change-me")

# Protect cron endpoint
CRON_SECRET = os.getenv("CRON_SECRET", "change-me")


# =========================
# DB SETUP
# =========================

connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

Base = declarative_base()


class Event(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, index=True)
    token = Column(String, unique=True, nullable=False, index=True)

    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)

    wedding_date = Column(Date, nullable=False)
    venue = Column(String, nullable=True)
    guest_count = Column(Integer, nullable=True)

    email = Column(String, nullable=False)
    phone = Column(String, nullable=True)

    # lifecycle
    status = Column(String, nullable=False, default="pending")  # pending/accepted/declined
    accepted = Column(Boolean, default=False)  # kept for backward compatibility
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    accepted_at = Column(DateTime, nullable=True)
    declined_at = Column(DateTime, nullable=True)


def ensure_schema() -> None:
    """Create base tables and attempt lightweight schema upgrades."""
    Base.metadata.create_all(bind=engine)

    # Best-effort ALTERs for Postgres (Neon). If already exists, ignore.
    # SQLite might not support all ALTER patterns; we try and ignore errors.
    alters = [
        "ALTER TABLE events ADD COLUMN IF NOT EXISTS status VARCHAR DEFAULT 'pending'",
        "ALTER TABLE events ADD COLUMN IF NOT EXISTS created_at TIMESTAMP",
        "ALTER TABLE events ADD COLUMN IF NOT EXISTS accepted_at TIMESTAMP",
        "ALTER TABLE events ADD COLUMN IF NOT EXISTS declined_at TIMESTAMP",
    ]
    with engine.begin() as conn:
        for stmt in alters:
            try:
                conn.execute(text(stmt))
            except Exception:
                pass


ensure_schema()


def db_session() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# =========================
# EMAIL HELPERS
# =========================

def send_email(to_address: str, subject: str, body: str) -> None:
    msg = MIMEText(body, _charset="utf-8")
    msg["Subject"] = subject
    msg["From"] = SENDER_EMAIL
    msg["To"] = to_address

    if SMTP_HOST and SMTP_PORT and SMTP_USER and SMTP_PASSWORD:
        try:
            if SMTP_PORT == 465:
                server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT)
            else:
                server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
                server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        finally:
            try:
                server.quit()
            except Exception:
                pass
    else:
        # dev fallback
        print("--- Email Debug ---")
        print(f"From: {SENDER_EMAIL}")
        print(f"To: {to_address}")
        print(f"Subject: {subject}")
        print(body)
        print("--- End Email Debug ---")


def offer_email_body(e: Event) -> str:
    accept_link = f"{BASE_URL}/accept?token={e.token}"
    decline_link = f"{BASE_URL}/decline?token={e.token}"
    return (
        f"Dragi {e.first_name} {e.last_name},\n\n"
        f"Hvala vam što ste nam poslali upit za catering usluge.\n"
        f"Vaše vjenčanje je planirano za {e.wedding_date:%d.%m.%Y.}"
        + (f" u {e.venue}" if e.venue else "")
        + (f" za {e.guest_count} gostiju." if e.guest_count else ".")
        + "\n\n"
        "Molimo vas da potvrdite ili odbijete ponudu putem poveznica:\n"
        f"Prihvaćam ponudu: {accept_link}\n"
        f"Odbijam ponudu: {decline_link}\n\n"
        "Srdačan pozdrav,\n"
        "Landsky Catering"
    )


def send_offer_email(e: Event) -> None:
    # You can choose who receives what. For now:
    # - couple receives offer with accept/decline links
    # - catering team gets notification about new inquiry
    send_email(e.email, "Ponuda za vaše vjenčanje", offer_email_body(e))

    internal_subject = f"[Novi upit] {e.first_name} {e.last_name} – {e.wedding_date:%d.%m.%Y.}"
    internal_body = (
        "Zaprimljen je novi upit:\n\n"
        f"Ime: {e.first_name} {e.last_name}\n"
        f"Datum: {e.wedding_date:%d.%m.%Y.}\n"
        f"Sala: {e.venue or 'N/A'}\n"
        f"Broj gostiju: {e.guest_count or 'N/A'}\n"
        f"E-mail: {e.email}\n"
        f"Telefon: {e.phone or 'N/A'}\n"
        f"Status: {e.status}\n"
    )
    send_email(CATERING_TEAM_EMAIL, internal_subject, internal_body)


def send_confirmation_emails(e: Event) -> None:
    # Couple
    send_email(
        e.email,
        "Potvrda prihvaćanja ponude",
        (
            f"Dragi {e.first_name} {e.last_name},\n\n"
            "Zahvaljujemo na prihvaćanju naše ponude.\n"
            f"Evidentirali smo događaj za {e.wedding_date:%d.%m.%Y.}"
            + (f" u {e.venue}" if e.venue else "")
            + (f" za {e.guest_count} gostiju." if e.guest_count else ".")
            + "\n\n"
            "Srdačan pozdrav,\n"
            "Landsky Catering"
        ),
    )

    # Internal team
    send_email(
        CATERING_TEAM_EMAIL,
        f"[Prihvaćeno] {e.first_name} {e.last_name} – {e.wedding_date:%d.%m.%Y.}",
        (
            "Ponuda je prihvaćena.\n\n"
            f"Ime: {e.first_name} {e.last_name}\n"
            f"Datum: {e.wedding_date:%d.%m.%Y.}\n"
            f"Sala: {e.venue or 'N/A'}\n"
            f"Broj gostiju: {e.guest_count or 'N/A'}\n"
            f"E-mail: {e.email}\n"
            f"Telefon: {e.phone or 'N/A'}\n"
        ),
    )


def send_decline_internal(e: Event) -> None:
    send_email(
        CATERING_TEAM_EMAIL,
        f"[Odbijeno] {e.first_name} {e.last_name} – {e.wedding_date:%d.%m.%Y.}",
        (
            "Ponuda je odbijena.\n\n"
            f"Ime: {e.first_name} {e.last_name}\n"
            f"Datum: {e.wedding_date:%d.%m.%Y.}\n"
            f"Sala: {e.venue or 'N/A'}\n"
            f"Broj gostiju: {e.guest_count or 'N/A'}\n"
            f"E-mail: {e.email}\n"
            f"Telefon: {e.phone or 'N/A'}\n"
        ),
    )


# =========================
# AUTH (ADMIN)
# =========================

security = HTTPBasic()


def require_admin(creds: HTTPBasicCredentials = Depends(security)) -> None:
    # Simple constant-time-ish compare is enough for MVP
    if creds.username != ADMIN_USER or creds.password != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Basic"})


# =========================
# FASTAPI APP
# =========================

app = FastAPI(title="Landsky Wedding App")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve /frontend static
if os.path.isdir("frontend"):
    app.mount("/frontend", StaticFiles(directory="frontend", html=True), name="frontend")


# =========================
# MODELS (API)
# =========================

class RegistrationRequest(BaseModel):
    first_name: str
    last_name: str
    wedding_date: date
    venue: Optional[str] = None
    guest_count: Optional[int] = None
    email: EmailStr
    phone: Optional[str] = None


# =========================
# PUBLIC ROUTES
# =========================

@app.get("/", response_class=HTMLResponse)
def home():
    # Make public site at / (not /frontend/)
    path = os.path.join("frontend", "index.html")
    if os.path.isfile(path):
        return FileResponse(path)
    return HTMLResponse("<h1>Frontend not found</h1><p>Create frontend/index.html</p>", status_code=404)

from fastapi.responses import FileResponse

@app.get("/admin", response_class=HTMLResponse)
def admin_ui():
    path = os.path.join("frontend", "admin.html")
    if os.path.isfile(path):
        return FileResponse(path)
    return HTMLResponse("<h2>admin.html not found</h2>", status_code=404)

@app.get("/admin/", response_class=HTMLResponse)
def admin_ui_slash():
    return admin_ui()

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/register")
def register(payload: RegistrationRequest, db: Session = Depends(db_session)):
    token = str(uuid.uuid4())
    e = Event(
        token=token,
        first_name=payload.first_name.strip(),
        last_name=payload.last_name.strip(),
        wedding_date=payload.wedding_date,
        venue=payload.venue,
        guest_count=payload.guest_count,
        email=str(payload.email),
        phone=payload.phone,
        status="pending",
        accepted=False,
        created_at=datetime.utcnow(),
    )
    db.add(e)
    db.commit()
    db.refresh(e)

   try:
    send_offer_email(e)
except Exception as ex:
    print("EMAIL SEND FAILED:", repr(ex))

return {"message": "Vaš upit je zaprimljen. (Email je privremeno u test modu)"}


@app.get("/accept", response_class=HTMLResponse)
def accept(token: str = Query(...), db: Session = Depends(db_session)):
    e = db.query(Event).filter_by(token=token).first()
    if not e:
        return HTMLResponse("<h2>Nevažeći token.</h2>", status_code=404)

    if e.status == "accepted":
        return HTMLResponse("<h2>Ponuda je već prihvaćena. Hvala!</h2>")

    if e.status == "declined":
        return HTMLResponse("<h2>Ponuda je već odbijena.</h2>")

    e.status = "accepted"
    e.accepted = True
    e.accepted_at = datetime.utcnow()
    db.commit()

    send_confirmation_emails(e)

    return HTMLResponse(
        "<h2>Hvala! Ponuda je prihvaćena.</h2><p>Poslali smo potvrdu e-mailom.</p>"
    )


@app.get("/decline", response_class=HTMLResponse)
def decline(token: str = Query(...), db: Session = Depends(db_session)):
    e = db.query(Event).filter_by(token=token).first()
    if not e:
        return HTMLResponse("<h2>Nevažeći token.</h2>", status_code=404)

    if e.status == "accepted":
        return HTMLResponse("<h2>Ponuda je već prihvaćena i ne može se odbiti.</h2>")

    if e.status == "declined":
        return HTMLResponse("<h2>Ponuda je već odbijena.</h2>")

    e.status = "declined"
    e.declined_at = datetime.utcnow()
    db.commit()

    send_decline_internal(e)

    return HTMLResponse("<h2>Hvala na odgovoru. Ponuda je odbijena.</h2>")


# =========================
# ADMIN API
# =========================

def event_to_dict(e: Event) -> Dict[str, Any]:
    return {
        "id": e.id,
        "token": e.token,
        "first_name": e.first_name,
        "last_name": e.last_name,
        "wedding_date": e.wedding_date.isoformat(),
        "venue": e.venue,
        "guest_count": e.guest_count,
        "email": e.email,
        "phone": e.phone,
        "status": e.status,
        "created_at": e.created_at.isoformat() if e.created_at else None,
        "accepted_at": e.accepted_at.isoformat() if e.accepted_at else None,
        "declined_at": e.declined_at.isoformat() if e.declined_at else None,
    }


@app.get("/admin/events")
def admin_list_events(
    status: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    db: Session = Depends(db_session),
    _: None = Depends(require_admin),
):
    q = db.query(Event)

    if status:
        q = q.filter(Event.status == status)

    if date_from:
        q = q.filter(Event.wedding_date >= date_from)

    if date_to:
        q = q.filter(Event.wedding_date <= date_to)

    q = q.order_by(Event.wedding_date.asc())
    events = q.all()
    return {"items": [event_to_dict(e) for e in events]}


@app.post("/admin/events/{event_id}/accept")
def admin_accept_event(
    event_id: int,
    db: Session = Depends(db_session),
    _: None = Depends(require_admin),
):
    e = db.query(Event).get(event_id)
    if not e:
        raise HTTPException(404, "Not found")

    if e.status != "accepted":
        e.status = "accepted"
        e.accepted = True
        e.accepted_at = datetime.utcnow()
        db.commit()
        send_confirmation_emails(e)

    return {"ok": True, "event": event_to_dict(e)}


@app.post("/admin/events/{event_id}/decline")
def admin_decline_event(
    event_id: int,
    db: Session = Depends(db_session),
    _: None = Depends(require_admin),
):
    e = db.query(Event).get(event_id)
    if not e:
        raise HTTPException(404, "Not found")

    if e.status == "accepted":
        raise HTTPException(400, "Already accepted; cannot decline.")

    if e.status != "declined":
        e.status = "declined"
        e.declined_at = datetime.utcnow()
        db.commit()
        send_decline_internal(e)

    return {"ok": True, "event": event_to_dict(e)}


# =========================
# REMINDERS (CRON)
# =========================

@app.post("/tasks/send-reminders")
def send_reminders(
    x_cron_secret: Optional[str] = Header(default=None, alias="X-Cron-Secret"),
    db: Session = Depends(db_session),
):
    """
    Call this endpoint from a cron job (e.g. Render Cron / GitHub Action).
    It sends reminders for accepted events that happen in 2 days.
    """
    if not x_cron_secret or x_cron_secret != CRON_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")

    target_date = (date.today() + timedelta(days=2))

    events = (
        db.query(Event)
        .filter(Event.status == "accepted")
        .filter(Event.wedding_date == target_date)
        .all()
    )

    sent = 0
    for e in events:
        subject = f"[Podsjetnik] {e.first_name} {e.last_name} – {e.wedding_date:%d.%m.%Y.}"
        body = (
            "Podsjetnik: vjenčanje je za 2 dana.\n\n"
            f"Ime: {e.first_name} {e.last_name}\n"
            f"Datum: {e.wedding_date:%d.%m.%Y.}\n"
            f"Sala: {e.venue or 'N/A'}\n"
            f"Broj gostiju: {e.guest_count or 'N/A'}\n"
            f"E-mail: {e.email}\n"
            f"Telefon: {e.phone or 'N/A'}\n"
        )
        send_email(CATERING_TEAM_EMAIL, subject, body)
        sent += 1

    return {"ok": True, "target_date": target_date.isoformat(), "sent": sent}
