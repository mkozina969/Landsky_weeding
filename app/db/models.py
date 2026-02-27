from datetime import datetime

from sqlalchemy import Boolean, Column, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import declarative_base

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

    # Optional free-text message
    message = Column(Text, nullable=True)

    status = Column(String(20), nullable=False, default="pending")  # pending/accepted/declined
    accepted = Column(Boolean, default=False, nullable=False)

    selected_package = Column(String(50), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    last_email_sent_at = Column(DateTime, nullable=True)
    reminder_count = Column(Integer, default=0, nullable=False)

    # Idempotency / reminder flags (additive)
    offer_sent_at = Column(DateTime, nullable=True)
    reminder_3d_sent_at = Column(DateTime, nullable=True)
    reminder_7d_sent_at = Column(DateTime, nullable=True)
    event_2d_sent_at = Column(DateTime, nullable=True)


class EmailLog(Base):
    __tablename__ = "email_logs"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=False)

    email_type = Column(String(50), nullable=False)  # offer / internal_notify / reminder_3d / reminder_7d / event_2d / resend_offer
    to_email = Column(String(255), nullable=False)
    subject = Column(String(255), nullable=False)

    provider = Column(String(40), nullable=False, default="unknown")
    provider_message_id = Column(String(255), nullable=True)

    status = Column(String(20), nullable=False, default="sent")  # sent/failed
    error = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class StatusChangeLog(Base):
    __tablename__ = "status_change_logs"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=False, index=True)

    old_status = Column(String(20), nullable=True)
    new_status = Column(String(20), nullable=True)
    source = Column(String(50), nullable=False)
    reason = Column(String(255), nullable=True)

    actor_ip = Column(String(120), nullable=True)
    actor_user_agent = Column(Text, nullable=True)
    actor_auth = Column(String(32), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
