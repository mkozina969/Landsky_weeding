import os
import uuid
import smtplib
import requests
from datetime import datetime
from email.mime.text import MIMEText

from fastapi import FastAPI, Depends, Query, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from sqlalchemy import create_engine, Column, String, Integer, Boolean, DateTime, text
from sqlalchemy.orm import sessionmaker, declarative_base, Session

from pydantic import BaseModel, EmailStr
from dotenv import load_dotenv

load_dotenv()

# ======================
# ENV
# ======================

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./test.db")

SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")

EMAIL_PROVIDER = os.getenv("EMAIL_PROVIDER", "smtp")  # "resend" or "smtp"
RESEND_API_KEY = os.getenv("RESEND_API_KEY")

SENDER_EMAIL = os.getenv("SENDER_EMAIL", "onboarding@resend.dev")

# Internal inbox (you)
CATERING_TEAM_EMAIL = os.getenv("CATERING_TEAM_EMAIL", "mkozina31@gmail.com")

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")

ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "change-me")

# If you set this, it overrides who receives the "couple offer" email (useful later).
# In Resend test mode it will still fail for non-owner emails, so we keep it optional.
TEST_COUPLE_EMAIL = os.getenv("TEST_COUPLE_EMAIL")  # e.g. mkozina@intercars.eu

# ======================
# DB
# ======================

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


class Event(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, index=True)
    token = Column(String, unique=True, index=True)

    first_name = Column(String)
    last_name = Column(String)
    wedding_date = Column(String)
    venue = Column(String)
    guest_count = Column(Integer)

    email = Column(String)
    phone = Column(String)

    # NEW: message/questions from couple
    message = Column(String, default="")

    status = Column(String)  # pending / accepted / declined
    accepted = Column(Boolean, default=False)

    created_at = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(bind=engine)

# --- MVP migration: add message column if missing (SQLite/Postgres) ---
try:
    with engine.begin() as conn:
        if "sqlite" in DATABASE_URL:
            cols = conn.execute(text("PRAGMA table_info(events);")).fetchall()
            names = [c[1] for c in cols]
            if "message" not in names:
                conn.execute(text("ALTER TABLE events ADD COLUMN message TEXT DEFAULT ''"))
        else:
            res = conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='events' AND column_name='message';"
                )
            ).fetchone()
            if not res:
                conn.execute(text("ALTER TABLE events ADD COLUMN message VARCHAR DEFAULT ''"))
except Exception as ex:
    print("MIGRATION message skipped/failed:", repr(ex))

# ======================
# SCHEMA
# ======================


class RegistrationRequest(BaseModel):
    first_name: str
    last_name: str
    wedding_date: str
    venue: str
    guest_count: int
    email: EmailStr
    phone: str
    message: str | None = None  # NEW


# ======================
# APP
# ======================

app = FastAPI(title="Landsky Wedding App")

app.mount("/frontend", StaticFiles(directory="frontend", html=True), name="frontend")

# ======================
# DEP
# ======================


def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ======================
# ADMIN AUTH
# ======================

security = HTTPBasic()


def require_admin(credentials: HTTPBasicCredentials = Depends(security)):
    if credentials.username != ADMIN_USER or credentials.password != ADMIN_PASSWORD:
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )


# ======================
# EMAIL
# ======================


def send_email(to_email: str, subject: str, body_html: str):
    # RESEND
    if EMAIL_PROVIDER == "resend":
        if not RESEND_API_KEY:
            raise RuntimeError("RESEND_API_KEY missing")

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

        if r.status_code >= 400:
            raise RuntimeError(f"Resend error: {r.text}")

        return

    # SMTP fallback (local only)
    msg = MIMEText(body_html, "html")
    msg["Subject"] = subject
    msg["From"] = SENDER_EMAIL
    msg["To"] = to_email

    server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT)
    server.login(SMTP_USER, SMTP_PASSWORD)
    server.sendmail(SENDER_EMAIL, [to_email], msg.as_string())
    server.quit()


def offer_email_body(e: Event) -> str:
    accept_link = f"{BASE_URL}/accept?token={e.token}"
    decline_link = f"{BASE_URL}/decline?token={e.token}"

    msg_block = ""
    if (e.message or "").strip():
        msg_block = f"""
        <p><b>Napomena / Pitanja mladenaca:</b><br>
        {e.message}</p>
        """

    return f"""
    <h2>Ponuda za vjenčanje</h2>
    <p>Poštovani {e.first_name} {e.last_name},</p>

    <p>Zaprimili smo vaš upit:</p>
    <ul>
      <li><b>Datum:</b> {e.wedding_date}</li>
      <li><b>Lokacija / sala:</b> {e.venue}</li>
      <li><b>Broj gostiju:</b> {e.guest_count}</li>
    </ul>

    {msg_block}

    <p>Molimo potvrdite ponudu:</p>
    <a href="{accept_link}">Prihvaćam</a><br>
    <a href="{decline_link}">Odbijam</a>
    """


def internal_email_body(e: Event) -> str:
    preview_link = f"{BASE_URL}/offer-preview?token={e.token}"
    accept_link = f"{BASE_URL}/accept?token={e.token}"
    decline_link = f"{BASE_URL}/decline?token={e.token}"

    return f"""
    <h2>Novi upit (TEST)</h2>
    <ul>
      <li><b>Mladenci:</b> {e.first_name} {e.last_name}</li>
      <li><b>Email mladenaca:</b> {e.email}</li>
      <li><b>Telefon:</b> {e.phone}</li>
      <li><b>Datum:</b> {e.wedding_date}</li>
      <li><b>Sala:</b> {e.venue}</li>
      <li><b>Gosti:</b> {e.guest_count}</li>
      <li><b>Status:</b> {e.status}</li>
    </ul>

    <p><b>Napomena / Pitanja:</b><br>{(e.message or "").strip() or "(nema)"}</p>

    <p><b>Preview ponude (što bi mladenac vidio):</b><br>
      <a href="{preview_link}">{preview_link}</a>
    </p>

    <p><b>Direktni linkovi:</b><br>
      <a href="{accept_link}">accept</a><br>
      <a href="{decline_link}">decline</a>
    </p>
    """


def send_offer_flow(e: Event):
    """
    Test flow:
    1) Always email you (internal) so you have everything.
    2) Try to email couple (will fail on Resend test mode for non-owner emails).
    """
    # 1) Always internal
    send_email(CATERING_TEAM_EMAIL, f"Novi upit: {e.first_name} {e.last_name} (TEST)", internal_email_body(e))

    # 2) Couple offer (best effort)
    couple_target = TEST_COUPLE_EMAIL or e.email
    send_email(couple_target, "Ponuda za vaše vjenčanje", offer_email_body(e))


# ======================
# PUBLIC ROUTES
# ======================


@app.get("/", response_class=HTMLResponse)
def home():
    return '<a href="/frontend/">Otvori aplikaciju</a> | <a href="/admin">Admin</a>'


@app.get("/offer-preview", response_class=HTMLResponse)
def offer_preview(token: str = Query(...), db: Session = Depends(db_session)):
    e = db.query(Event).filter_by(token=token).first()
    if not e:
        return HTMLResponse("<h1>Token ne postoji</h1>", status_code=404)
    return HTMLResponse(offer_email_body(e))


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
        message=(payload.message or "").strip(),
        status="pending",
        accepted=False,
        created_at=datetime.utcnow(),
    )

    db.add(e)
    db.commit()
    db.refresh(e)

    # send emails (internal always, couple best effort)
    try:
        send_offer_flow(e)
    except Exception as ex:
        # We do NOT fail registration if email fails
        print("EMAIL SEND FAILED:", repr(ex))

    return {
        "message": "Vaš upit je zaprimljen.",
        "preview_url": f"{BASE_URL}/offer-preview?token={e.token}",  # useful in test
    }


@app.get("/accept", response_class=HTMLResponse)
def accept(token: str = Query(...), db: Session = Depends(db_session)):
    e = db.query(Event).filter_by(token=token).first()
    if not e:
        return "<h1>Token ne postoji</h1>"

    e.accepted = True
    e.status = "accepted"
    db.commit()

    return "<h1>Ponuda prihvaćena 🎉</h1>"


@app.get("/decline", response_class=HTMLResponse)
def decline(token: str = Query(...), db: Session = Depends(db_session)):
    e = db.query(Event).filter_by(token=token).first()
    if not e:
        return "<h1>Token ne postoji</h1>"

    e.accepted = False
    e.status = "declined"
    db.commit()

    return "<h1>Ponuda odbijena</h1>"


# ======================
# ADMIN UI + API
# ======================


@app.get("/admin", response_class=HTMLResponse)
def admin_page():
    path = os.path.join("frontend", "admin.html")
    if os.path.isfile(path):
        return FileResponse(path)
    return HTMLResponse("<h2>admin.html not found</h2>", status_code=404)


@app.get("/admin/api/events")
def admin_list_events(
    status: str | None = None,
    q: str | None = None,
    db: Session = Depends(db_session),
    _: None = Depends(require_admin),
):
    query = db.query(Event)

    if status:
        query = query.filter(Event.status == status)

    items = query.order_by(Event.id.desc()).all()

    if q:
        qq = q.lower()
        items = [
            e
            for e in items
            if (e.first_name and qq in e.first_name.lower())
            or (e.last_name and qq in e.last_name.lower())
            or (e.email and qq in e.email.lower())
        ]

    return {
        "items": [
            {
                "id": e.id,
                "token": e.token,
                "first_name": e.first_name,
                "last_name": e.last_name,
                "wedding_date": e.wedding_date,
                "venue": e.venue,
                "guest_count": e.guest_count,
                "email": e.email,
                "phone": e.phone,
                "message": e.message or "",
                "status": e.status,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in items
        ]
    }


@app.post("/admin/api/events/{event_id}/accept")
def admin_accept_event(
    event_id: int,
    db: Session = Depends(db_session),
    _: None = Depends(require_admin),
):
    e = db.query(Event).filter_by(id=event_id).first()
    if not e:
        raise HTTPException(404, "Not found")

    e.accepted = True
    e.status = "accepted"
    db.commit()

    return {"ok": True}


@app.post("/admin/api/events/{event_id}/decline")
def admin_decline_event(
    event_id: int,
    db: Session = Depends(db_session),
    _: None = Depends(require_admin),
):
    e = db.query(Event).filter_by(id=event_id).first()
    if not e:
        raise HTTPException(404, "Not found")

    e.accepted = False
    e.status = "declined"
    db.commit()

    return {"ok": True}


@app.post("/admin/api/events/{event_id}/resend")
def admin_resend_offer(
    event_id: int,
    db: Session = Depends(db_session),
    _: None = Depends(require_admin),
):
    e = db.query(Event).filter_by(id=event_id).first()
    if not e:
        raise HTTPException(404, "Not found")

    try:
        send_offer_flow(e)
    except Exception as ex:
        print("EMAIL SEND FAILED:", repr(ex))
        raise HTTPException(500, f"Email failed: {repr(ex)}")

    return {"ok": True}
