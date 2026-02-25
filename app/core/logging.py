import logging
from typing import Optional

from .config import LOG_LEVEL

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

logger = logging.getLogger("landsky")


def log_evt(level: str, action: str, event_id: Optional[int] = None, email_type: Optional[str] = None, **kw):
    """Lightweight structured-ish logging without external deps."""
    parts = [f"action={action}"]
    if event_id is not None:
        parts.append(f"event_id={event_id}")
    if email_type is not None:
        parts.append(f"email_type={email_type}")
    for k, v in kw.items():
        if v is None:
            continue
        parts.append(f"{k}={v}")
    msg = " ".join(parts)

    lvl = level.lower()
    if lvl == "debug":
        logger.debug(msg)
    elif lvl == "warning":
        logger.warning(msg)
    elif lvl == "error":
        logger.error(msg)
    else:
        logger.info(msg)
