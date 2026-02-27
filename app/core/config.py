import os
from dotenv import load_dotenv

load_dotenv()

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

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

ALLOW_ADMIN_DECLINE = os.getenv("ALLOW_ADMIN_DECLINE", "0").lower() in ("1", "true", "yes", "on")
