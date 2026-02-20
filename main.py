"""
Wedding App for Landsky Catering

This FastAPI application implements a simple workflow for managing wedding
requests (registrations), sending catering offers, tracking acceptance and
decline of offers, and sending reminder emails to the catering team a
couple of days before the event. It uses a PostgreSQL database (for
example, a Neon database) to persist event records and scheduled jobs.

Key features:

* Couples (the bride and groom) submit a registration with their names,
  wedding date, venue, number of guests, email address and phone number.
* Upon registration the application generates a unique token, stores the
  request in the database and sends an offer email from
  ``catering@landskybar.com`` to the couple’s email address.  The email
  contains personalised links to accept or decline the offer.
* When the couple clicks the acceptance link, the application records
  the acceptance, sends a confirmation email both to the couple and to
  the catering team and schedules a reminder email two days before the
  wedding.  If the decline link is clicked, the request is removed.
* A background scheduler (APScheduler) backed by the same database
  persists reminder jobs.  This ensures reminders survive application
  restarts.

Configuration is performed via environment variables.  At a minimum you
should set ``DATABASE_URL`` to point at your Neon database and
``SMTP_HOST``, ``SMTP_PORT``, ``SMTP_USER`` and ``SMTP_PASSWORD`` for
sending email.  ``BASE_URL`` defines the public URL where the
application is reachable (used to construct accept/decline links).

The application defaults to a local SQLite database and prints emails to
standard output if the email configuration is not provided.  This makes
development and testing easier without requiring live email
credentials.

Usage:

    uvicorn main:app --reload

The application exposes the following endpoints:

* ``POST /register`` – Accepts a JSON body with registration details and
  sends an offer email.
* ``GET /accept`` – Accepts the ``token`` query parameter to record
  acceptance and schedule reminders.
* ``GET /decline`` – Accepts the ``token`` query parameter to remove a
  pending request.
"""

from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timedelta
from typing import Optional

from email.mime.text import MIMEText
import smtplib

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr

from sqlalchemy import Boolean, Column, Date, Integer, String, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session, sessionmaker

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore


# ---------------------------------------------------------------------------
# Configuration and global objects
#
# Several aspects of the application are configured through environment
# variables.  If not provided, sensible defaults are used to allow local
# development without external dependencies.

# Base URL of the public server (used to construct links in the emails).
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")

# Email settings for sending messages.  The sender address is fixed per
# specification.  If SMTP_* variables are missing the application will
# log email contents to stdout instead of sending.
SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", "0") or 0)
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
SENDER_EMAIL = "catering@landskybar.com"

# Database connection URL.  Falls back to a local SQLite file for
# development if ``DATABASE_URL`` is not set.  Neon users should point this
# to their Neon Postgres connection string.  SQLAlchemy automatically
# determines the appropriate driver based on the URL scheme.
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./wedding_app.db")

# Time zone for scheduling reminders.  Europe/Zagreb is used because the
# user lives in Zagreb.  The zoneinfo module is available in Python 3.9+
# and will throw a KeyError if the zone name is invalid.  We use UTC as
# fallback.
try:
    from zoneinfo import ZoneInfo  # type: ignore[import]

    TZ = ZoneInfo("Europe/Zagreb")
except Exception:
    TZ = None

# ---------------------------------------------------------------------------
# Database setup

# SQLAlchemy base and session factory.  If using SQLite, the ``check_same_thread``
# flag must be disabled to allow connections across threads.  When
# connecting to Postgres (e.g. Neon) this flag is not needed.

connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


class Event(Base):
    """SQLAlchemy model representing a wedding registration.

    The ``token`` field links email actions (accept/decline) to a particular
    record.  When the couple accepts the offer, ``accepted`` is set to
    ``True``.  Additional fields store the couple’s names, wedding date and
    time, venue, number of guests, contact details and internal notes.
    """

    __tablename__ = "events"

    id: int = Column(Integer, primary_key=True, index=True)
    token: str = Column(String, unique=True, nullable=False, index=True)
    first_name: str = Column(String, nullable=False)
    last_name: str = Column(String, nullable=False)
    wedding_date: date = Column(Date, nullable=False)
    venue: str = Column(String, nullable=True)
    guest_count: int = Column(Integer, nullable=True)
    email: str = Column(String, nullable=False)
    phone: str = Column(String, nullable=True)
    accepted: bool = Column(Boolean, default=False)


# Create tables if they do not already exist.  This call is safe to run
# multiple times and will only create missing tables.
Base.metadata.create_all(bind=engine)


# ---------------------------------------------------------------------------
# Email utility functions

def send_email(to_address: str, subject: str, body: str) -> None:
    """Send a plain‑text email or log to stdout if SMTP is not configured.

    This helper encapsulates SMTP login and message composition.  If
    ``SMTP_HOST`` is not set the function simply prints the email
    parameters to standard output, which is useful for development or
    demonstration without external dependencies.

    Parameters
    ----------
    to_address: str
        Recipient email address.
    subject: str
        Email subject line.
    body: str
        Plain text body of the email.
    """
    msg = MIMEText(body, _charset="utf-8")
    msg["Subject"] = subject
    msg["From"] = SENDER_EMAIL
    msg["To"] = to_address

    if SMTP_HOST and SMTP_PORT and SMTP_USER and SMTP_PASSWORD:
        # Connect using SSL if port 465, otherwise start TLS.
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
        # Development fallback: log the email instead of sending.
        print("--- Email Debug ---")
        print(f"From: {SENDER_EMAIL}")
        print(f"To: {to_address}")
        print(f"Subject: {subject}")
        print(body)
        print("--- End Email Debug ---")


def send_offer_email(event: Event) -> None:
    """Compose and send the initial offer email to the couple.

    The offer email thanks the couple for submitting their details and
    provides personalised links to accept or decline the catering offer.

    Parameters
    ----------
    event: Event
        The newly created event record.
    """
    accept_link = f"{BASE_URL}/accept?token={event.token}"
    decline_link = f"{BASE_URL}/decline?token={event.token}"
    subject = "Ponuda za vaše vjenčanje"
    body = (
        f"Dragi {event.first_name} {event.last_name},\n\n"
        f"Hvala vam što ste nam poslali upit za catering usluge.\n"
        f"Vaše vjenčanje je planirano za {event.wedding_date:%d.%m.%Y.} u {event.venue} "
        f"za {event.guest_count or 'nepoznat broj'} gostiju.\n\n"
        "Kako bismo vam poslali ponudu, molimo vas da potvrdite ili odbijete "
        "ponudu putem sljedećih poveznica:\n"
        f"Prihvaćam ponudu: {accept_link}\n"
        f"Odbijam ponudu: {decline_link}\n\n"
        "Srdačan pozdrav,\n"
        "Landsky Catering"
    )
    send_email(event.email, subject, body)


def send_confirmation_email(event: Event) -> None:
    """Send confirmation emails when an offer is accepted.

    One message is sent to the couple confirming the acceptance and
    summarising the event details; another message is sent to the
    catering team with the same information.  Any missing email
    configuration will cause messages to be logged to stdout.
    """
    # Email to couple
    subject_couple = "Potvrda prihvaćanja ponude"
    body_couple = (
        f"Dragi {event.first_name} {event.last_name},\n\n"
        f"Zahvaljujemo na prihvaćanju naše ponude za vaše vjenčanje.\n"
        f"Evidentirali smo vaš događaj za {event.wedding_date:%d.%m.%Y.} u {event.venue} "
        f"s {event.guest_count or 'nepoznatim brojem'} gostiju.\n\n"
        "Ukoliko imate dodatnih pitanja slobodno nam se obratite.\n\n"
        "Srdačan pozdrav,\n"
        "Landsky Catering"
    )
    send_email(event.email, subject_couple, body_couple)

    # Email to internal catering team
    subject_internal = (
        f"[Prihvaćen] {event.first_name} {event.last_name} – {event.wedding_date:%d.%m.%Y.}"
    )
    body_internal = (
        f"Sljedeći događaj je potvrđen:\n\n"
        f"Ime: {event.first_name} {event.last_name}\n"
        f"Datum: {event.wedding_date:%d.%m.%Y.}\n"
        f"Mjesto: {event.venue}\n"
        f"Broj gostiju: {event.guest_count or 'N/A'}\n"
        f"E-mail: {event.email}\n"
        f"Telefon: {event.phone or 'N/A'}\n"
    )
    send_email(SENDER_EMAIL, subject_internal, body_internal)


def send_reminder_email(event: Event) -> None:
    """Send a reminder to the catering team two days before the wedding.

    This function is intended to be called by the scheduler.  It queries
    the database for the event by id and, if found, sends a reminder
    email to the catering team.  Missing email configuration falls back
    to logging.
    """
    # A new session is created here because the scheduler runs outside
    # normal request scope.  Always close the session to free resources.
    db = SessionLocal()
    try:
        refreshed_event = db.query(Event).get(event.id)  # type: ignore[attr-defined]
        if refreshed_event and refreshed_event.accepted:
            subject = (
                f"[Podsjetnik] {refreshed_event.first_name} {refreshed_event.last_name} "
                f"– {refreshed_event.wedding_date:%d.%m.%Y.}"
            )
            body = (
                f"Podsjetnik: vjenčanje {refreshed_event.first_name} "
                f"{refreshed_event.last_name} održat će se za dva dana, "
                f"{refreshed_event.wedding_date:%d.%m.%Y.} u {refreshed_event.venue}.\n"
                f"Broj gostiju: {refreshed_event.guest_count or 'N/A'}.\n"
            )
            send_email(SENDER_EMAIL, subject, body)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Scheduler setup

# APScheduler keeps track of reminders and stores job metadata in the
# database so jobs persist across restarts.  If the database is SQLite
# the job store will create a table in the same file.
jobstores = {
    "default": SQLAlchemyJobStore(url=DATABASE_URL)
}

scheduler = BackgroundScheduler(jobstores=jobstores, timezone=TZ)
# Start the scheduler as soon as module is imported.  This is safe
# because BackgroundScheduler starts in a separate thread.
scheduler.start()


def schedule_reminder(event: Event) -> None:
    """Schedule a reminder email two days before the wedding date.

    If the scheduled time has already passed (e.g., the event is within
    two days), the reminder will be scheduled for one minute from now.
    Duplicate jobs are replaced by specifying ``replace_existing=True``.

    Parameters
    ----------
    event: Event
        The event for which to schedule the reminder.
    """
    # Determine the naive date/time for midnight on the wedding day.
    event_datetime = datetime.combine(event.wedding_date, datetime.min.time())
    # Convert to timezone-aware datetime if a zone is available.
    if TZ is not None:
        event_datetime = event_datetime.replace(tzinfo=TZ)
    run_date = event_datetime - timedelta(days=2)
    # If the calculated run_date is in the past schedule a short delay.
    now = datetime.now(TZ) if TZ is not None else datetime.now()
    if run_date < now:
        run_date = now + timedelta(minutes=1)
    scheduler.add_job(
        func=send_reminder_email,
        trigger="date",
        run_date=run_date,
        args=[event],
        id=f"reminder_{event.id}",
        replace_existing=True,
    )


# ---------------------------------------------------------------------------
# FastAPI application and endpoints

app = FastAPI(title="Landsky Wedding App")

# Enable CORS for all origins (adjust as needed).  This allows browser
# frontends hosted on different domains to interact with the API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class RegistrationRequest(BaseModel):
    """Pydantic model for incoming registration JSON."""

    first_name: str
    last_name: str
    wedding_date: date
    venue: Optional[str] = None
    guest_count: Optional[int] = None
    email: EmailStr
    phone: Optional[str] = None


@app.post("/register")
def register(request: RegistrationRequest):
    """Handle a new wedding registration.

    Creates a new ``Event`` record, sends an offer email to the couple
    and returns a generic acknowledgement.  Duplicate tokens are highly
    unlikely because UUID4 is used.  Any database errors will raise a
    server error which FastAPI will convert to a JSON response.
    """
    db: Session = SessionLocal()
    try:
        token = str(uuid.uuid4())
        event = Event(
            token=token,
            first_name=request.first_name.strip(),
            last_name=request.last_name.strip(),
            wedding_date=request.wedding_date,
            venue=request.venue,
            guest_count=request.guest_count,
            email=request.email,
            phone=request.phone,
            accepted=False,
        )
        db.add(event)
        db.commit()
        db.refresh(event)
        send_offer_email(event)
        return {
            "message": "Vaš upit je zaprimljen. Ponuda će vam biti poslana na e-mail."
        }
    finally:
        db.close()


@app.get("/accept")
def accept(token: str = Query(..., description="Jedinstveni token u ponudi")):
    """Accept an offer identified by its token.

    When the couple clicks the acceptance link the event record is
    retrieved and its ``accepted`` flag is set.  Confirmation emails
    are sent and a reminder is scheduled.  If the token is invalid or
    the event has already been accepted the endpoint responds
    accordingly.
    """
    db: Session = SessionLocal()
    try:
        event = db.query(Event).filter_by(token=token).first()
        if not event:
            raise HTTPException(status_code=404, detail="Nevažeći token.")
        if event.accepted:
            return {"message": "Ova ponuda je već prihvaćena. Hvala!"}
        event.accepted = True
        db.commit()
        send_confirmation_email(event)
        schedule_reminder(event)
        return {
            "message": "Hvala vam na prihvaćanju ponude. Poslali smo vam potvrdu e-mailom."
        }
    finally:
        db.close()


@app.get("/decline")
def decline(token: str = Query(..., description="Jedinstveni token u ponudi")):
    """Decline an offer identified by its token.

    When the couple clicks the decline link the event is removed from
    the database.  If the token is invalid a 404 error is returned.  If
    the event was previously accepted the endpoint notes that removal is
    not allowed.  (Alternatively you could simply mark it as declined.)
    """
    db: Session = SessionLocal()
    try:
        event = db.query(Event).filter_by(token=token).first()
        if not event:
            raise HTTPException(status_code=404, detail="Nevažeći token.")
        if event.accepted:
            return {
                "message": "Ova ponuda je već prihvaćena i ne može se odbiti."
            }
        db.delete(event)
        db.commit()
        return {
            "message": "Žao nam je što odbijate ponudu. Hvala na interesu."
        }
    finally:
        db.close()


@app.get("/")
def root():
    """Simple root endpoint for quick testing."""
    return {"status": "OK", "message": "Welcome to the Landsky Wedding API"}