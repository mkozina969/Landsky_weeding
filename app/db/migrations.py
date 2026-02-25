from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.core.logging import logger


def run_additive_migrations(engine: Engine) -> None:
    """Best-effort, additive-only migrations for MVP deployment."""
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
    except Exception:
        logger.exception("MIGRATIONS skipped/failed")
