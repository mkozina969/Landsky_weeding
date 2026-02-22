import os
import uuid
import smtplib
import requests
import html
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

# Resend testing sender (works without domain verification, but only allows sending to your own email)
SENDER_EMAIL = os.getenv("SENDER_EMAIL", "onboarding@resend.dev")

# Internal inbox (you)
CATERING_TEAM_EMAIL = os.getenv("CATERING_TEAM_EMAIL", "mkozina31@gmail.com")

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")

ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "change-me")

# TEST MODE: when "1" we send the offer ONLY to CATERING_TEAM_EMAIL (you)
TEST_MODE = os.getenv("TEST_MODE", "1") == "1"

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

    # NEW: note/questions from couple
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
    message: str | None = None


# ======================
# APP
# ======================

app = FastAPI(title="Landsky Wedding App")

# Static frontend (/frontend/index.html, /frontend/admin.html, /frontend/logo.png, etc.)
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
    """
    This is the HTML offer email that couples receive.
    We host logo + attachments on our own site and link them here.
    """
    logo_url = f"{BASE_URL}/frontend/logo.png"
    cocktails_pdf = f"{BASE_URL}/frontend/cocktails.pdf"
    bar_img = f"{BASE_URL}/frontend/bar.jpeg"
    cigare_img = f"{BASE_URL}/frontend/cigare.png"

    accept_link = f"{BASE_URL}/accept?token={e.token}"
    decline_link = f"{BASE_URL}/decline?token={e.token}"

    msg = (e.message or "").strip()
    msg_html = html.escape(msg).replace("\n", "<br>") if msg else "(nema)"

    return f"""
    <div style="font-family: Arial, sans-serif; color:#111; line-height:1.5;">
      <div style="max-width:720px; margin:0 auto; border:1px solid #eee; border-radius:14px; overflow:hidden;">
        <div style="background:#0b0f14; padding:18px 18px 12px 18px;">
          <div style="display:flex; align-items:center; gap:14px;">
            <img src="{logo_url}" alt="Landsky Catering" style="width:68px; height:68px; object-fit:contain; background:rgba(255,255,255,.06); border:1px solid rgba(255,255,255,.15); border-radius:14px; padding:10px;">
            <div>
              <div style="color:#fff; font-size:18px; font-weight:700;">Landsky Catering</div>
              <div style="color:rgba(255,255,255,.7); font-size:12px;">Ponuda za vjenčanje</div>
            </div>
          </div>
        </div>

        <div style="padding:18px;">
          <p style="margin:0 0 10px 0;"><b>Poštovani {html.escape(e.first_name)} {html.escape(e.last_name)},</b></p>
          <p style="margin:0 0 14px 0;">Zahvaljujemo na Vašem upitu. U nastavku dostavljamo informacije vezane za cocktail catering.</p>

          <div style="background:#fafafa; border:1px solid #eee; border-radius:12px; padding:12px 14px; margin:14px 0;">
            <div style="font-weight:700; margin-bottom:6px;">Sažetak upita</div>
            <div>📅 <b>Datum:</b> {html.escape(e.wedding_date)}</div>
            <div>📍 <b>Lokacija / sala:</b> {html.escape(e.venue)}</div>
            <div>👥 <b>Broj gostiju:</b> {e.guest_count}</div>
            <div>✉️ <b>Email:</b> {html.escape(e.email)}</div>
            <div>📞 <b>Telefon:</b> {html.escape(e.phone)}</div>
            <div style="margin-top:8px;"><b>Napomena / pitanja:</b><br>{msg_html}</div>
          </div>

          <p style="margin:0 0 10px 0;">
            U ponudi su omiljeni klasici kao i pristup osmišljavanja koktela sukladno vašem događanju.
          </p>

          <div style="margin:12px 0;">
            <div style="font-weight:700; margin-bottom:6px;">Ponuda uključuje</div>
            <ul style="margin:0; padding-left:18px;">
              <li>Profesionalnog barmena</li>
              <li>Event menu s koktelima prilagođen temi eventa (po želji)</li>
              <li>Staklene čaše + piće (alkoholno i bezalkoholno)</li>
              <li>Premium led / konzumni led</li>
              <li>Dekoracije</li>
              <li>Šank</li>
            </ul>
          </div>

          <div style="background:#fff7e6; border:1px solid #f3e3bf; border-radius:12px; padding:12px 14px; margin:14px 0;">
            <div style="font-weight:700; margin-bottom:6px;">Cijene paketa</div>
            <div>• <b>Klasični koktel paket:</b> 1.000 EUR + PDV (100 koktela) — svakih dodatnih 100: 500 EUR + PDV</div>
            <div>• <b>Premium koktel paket:</b> 1.200 EUR + PDV (100 koktela) — svakih dodatnih 100: 600 EUR + PDV</div>
            <div>• <b>Signature koktel paket:</b> 1.500 EUR + PDV (100 koktela) — svakih dodatnih 100: 800 EUR + PDV</div>
            <div style="margin-top:8px; color:#6b5a2a;">* Preporučujemo 200 koktela.</div>
            <div style="margin-top:10px;">
              📎 Detalji paketa: <a href="{cocktails_pdf}">{cocktails_pdf}</a>
            </div>
          </div>

          <div style="margin:14px 0;">
            <div style="font-weight:700; margin-bottom:6px;">Premium cigare (opcionalno)</div>
            <p style="margin:0 0 8px 0;">
              Uz odabir cigara od nas dobivate humidor, rezač, upaljač i pepeljare.
              Nudimo i <b>Cigar Connoisseur</b> uslugu (stručno vođenje, rezanje i paljenje) — <b>450 EUR + PDV</b> (3 sata).
            </p>
            📎 Popis cigara: <a href="{cigare_img}">{cigare_img}</a>
          </div>

          <p style="margin:0 0 10px 0;">
            Za događaje izvan Zagreba naplaćuje se put <b>0,70 EUR/km</b>.
          </p>

          <p style="margin:0 0 14px 0;">
            Rado Vas pozivamo i na prezentaciju koktela u našem Landsky Baru (Ozaljska 146),
            gdje ćemo Vam detaljno predstaviti našu uslugu i odabrati najbolje za vaš event.
          </p>

          <div style="margin:14px 0;">
            📸 Fotografija bara: <a href="{bar_img}">{bar_img}</a>
          </div>

          <div style="border-top:1px solid #eee; margin-top:16px; padding-top:14px;">
            <div style="font-weight:700; margin-bottom:6px;">Potvrda ponude</div>
            <p style="margin:0 0 10px 0;">Molimo potvrdite ponudu klikom:</p>
            <p style="margin:0;">
              ✅ <a href="{accept_link}">Prihvaćam</a><br>
              ❌ <a href="{decline_link}">Odbijam</a>
            </p>
          </div>

          <div style="margin-top:18px; color:#333;">
            Srdačan pozdrav,<br>
            <b>Landsky Catering</b><br>
            E-mail: <a href="mailto:catering@landskybar.com">catering@landskybar.com</a><br>
            Telefon: 091/594/6515
          </div>
        </div>
      </div>
    </div>
    """


def internal_email_body(e: Event) -> str:
    preview_link = f"{BASE_URL}/offer-preview?token={e.token}"
    admin_link = f"{BASE_URL}/admin"
    return f"""
    <div style="font-family: Arial, sans-serif; color:#111; line-height:1.5;">
      <h2>Novi upit (TEST)</h2>
      <ul>
        <li><b>Mladenci:</b> {html.escape(e.first_name)} {html.escape(e.last_name)}</li>
        <li><b>Email mladenaca:</b> {html.escape(e.email)}</li>
        <li><b>Telefon:</b> {html.escape(e.phone)}</li>
        <li><b>Datum:</b> {html.escape(e.wedding_date)}</li>
        <li><b>Sala:</b> {html.escape(e.venue)}</li>
        <li><b>Gosti:</b> {e.guest_count}</li>
        <li><b>Status:</b> {html.escape(e.status)}</li>
      </ul>

      <p><b>Napomena / Pitanja:</b><br>{html.escape((e.message or "").strip() or "(nema)").replace("\\n", "<br>")}</p>

      <p><b>Preview ponude (što bi mladenac vidio):</b><br>
        <a href="{preview_link}">{preview_link}</a>
      </p>

      <p><b>Admin:</b> <a href="{admin_link}">{admin_link}</a></p>
    </div>
    """


def send_offer_flow(e: Event):
    """
    Always:
      - send internal email to you
    In TEST_MODE:
      - send offer only to you
    Later (production):
      - send offer to couple email
    """
    # internal always
    send_email(
        CATERING_TEAM_EMAIL,
        f"Novi upit: {e.first_name} {e.last_name} (TEST)",
        internal_email_body(e),
    )

    offer_html = offer_email_body(e)

    if TEST_MODE:
        # TEST MODE: offer goes only to you
        send_email(
            CATERING_TEAM_EMAIL,
            f"Ponuda (TEST) – {e.first_name} {e.last_name}",
            offer_html,
        )
    else:
        # Production: offer goes to couple
        send_email(e.email, "Ponuda za vaše vjenčanje", offer_html)


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

    try:
        send_offer_flow(e)
    except Exception as ex:
        # we do not fail registration if email fails
        print("EMAIL SEND FAILED:", repr(ex))

    return {
        "message": "Vaš upit je zaprimljen.",
        "preview_url": f"{BASE_URL}/offer-preview?token={e.token}",
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
