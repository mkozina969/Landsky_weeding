from datetime import datetime

from fastapi import Request
from sqlalchemy.orm import Session

from app.db.models import Event, StatusChangeLog


def _client_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    xff = (request.headers.get("x-forwarded-for") or "").strip()
    if xff:
        return xff.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


def log_status_change(
    db: Session,
    event: Event,
    old_status: str | None,
    new_status: str | None,
    source: str,
    request: Request | None = None,
    reason: str | None = None,
) -> None:
    if old_status == new_status:
        return

    row = StatusChangeLog(
        event_id=event.id,
        old_status=old_status,
        new_status=new_status,
        source=source,
        reason=reason,
        actor_ip=_client_ip(request),
        actor_user_agent=(request.headers.get("user-agent") if request is not None else None),
        actor_auth=(request.headers.get("authorization", "")[:32] if request is not None else None),
        created_at=datetime.utcnow(),
    )
    db.add(row)
