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

from sqlalchemy import create_engine, Column, String, Integer, Boolean, DateTime
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

EMAIL_PROVIDER = os.getenv("EMAIL_PROVIDER", "smtp")
RESEND_API_KEY = os.getenv("RESEND_API_KEY")

SENDER_EMAIL = os.getenv("SENDER_EMAIL", "onboarding@resend.dev")

# TEST MODE: all offer emails go here (your email)
CATERING_TEAM_EMAIL = os.getenv("CATERING_TEAM_EMAIL", "mkozina31@gmail.com")

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")

ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "change-me")

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

    status = Column(String)     # pending / accepted / declined
    accepted = Column(Boolean, default=False)

    created_at = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(bind=engine)

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


def send_email(to_email: str, subject: str, body: str):
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
                "html": body,
            },
            timeout=20,
        )

        if r.status_code >= 400:
            raise RuntimeError(f"Resend error: {r.text}")

        return

    # SMTP fallback (local only)
    msg = MIMEText(body, "html")
    msg["Subject"] = subject
    msg["From"] = SENDER_EMAIL
    msg["To"] = to_email

    server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT)
    server.login(SMTP_USER, SMTP_PASSWORD)
    server.sendmail(SENDER_EMAIL, [to_email], msg.as_string())
    server.quit()


def offer_email_body(e: Event):
    accept_link = f"{BASE_URL}/accept?token={e.token}"
    decline_link = f"{BASE_URL}/decline?token={e.token}"

    return f"""
    <h2>Ponuda za vjenčanje</h2>
    <p>Poštovani {e.first_name} {e.last_name},</p>

    <p><b>TEST INFO:</b> Upit poslan od (email mladenaca): <b>{e.email}</b></p>

    <p>Vaš upit je zaprimljen.</p>

    <p>Molimo potvrdite ponudu:</p>

    <a href="{accept_link}">Prihvaćam</a><br>
    <a href="{decline_link}">Odbijam</a>
    """


def send_offer_email(e: Event):
    # TEST MODE: šalji ponudu samo na tvoj email
    send_email(CATERING_TEAM_EMAIL, "Ponuda za vaše vjenčanje (TEST)", offer_email_body(e))


# ======================
# PUBLIC ROUTES
# ======================


@app.get("/", response_class=HTMLResponse)
def home():
    return '<a href="/frontend/">Otvori aplikaciju</a> | <a href="/admin">Admin</a>'


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

    return {"message": "Vaš upit je zaprimljen."}


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

    # simple search filter in python (ok for MVP)
    if q:
        qq = q.lower()
        items = [
            e for e in items
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
