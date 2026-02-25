import html
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.api.schemas import RegistrationRequest
from app.core.config import BASE_URL, TEST_MODE
from app.core.logging import logger
from app.db.models import Event
from app.db.session import get_db
from app.email.templates import PACKAGE_LABELS, render_offer_html
from app.services.offers import send_offer_flow

router = APIRouter()


@router.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/frontend/")


@router.post("/register")
def register(payload: RegistrationRequest, db: Session = Depends(get_db)):
    e = Event(
        token=uuid.uuid4().hex,
        first_name=payload.first_name.strip(),
        last_name=payload.last_name.strip(),
        wedding_date=payload.wedding_date,
        venue=payload.venue.strip(),
        guest_count=payload.guest_count,
        email=str(payload.email),
        phone=payload.phone.strip(),
        message=(payload.message or "").strip() or None,
        status="pending",
        accepted=False,
        selected_package=None,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        last_email_sent_at=None,
        reminder_count=0,
    )

    db.add(e)
    db.commit()
    db.refresh(e)

    preview_url = f"{BASE_URL}/offer-preview?token={e.token}" if TEST_MODE else None

    try:
        send_offer_flow(e, db=db)
    except Exception:
        logger.exception("EMAIL SEND FAILED")

    return {"message": "Vaš upit je zaprimljen.", "preview_url": preview_url}


@router.get("/offer-preview", response_class=HTMLResponse)
def offer_preview(token: str = Query(...), db: Session = Depends(get_db)):
    e = db.query(Event).filter_by(token=token).first()
    if not e:
        raise HTTPException(status_code=404, detail="Token not found")
    return HTMLResponse(render_offer_html(e))


@router.get("/accept", response_class=HTMLResponse)
def accept_get(
    token: str = Query(...),
    package: str | None = Query(None),
    db: Session = Depends(get_db),
):
    e = db.query(Event).filter_by(token=token).first()
    if not e:
        return HTMLResponse("<h3>Neispravan token.</h3>", status_code=404)

    if e.status == "accepted":
        chosen = PACKAGE_LABELS.get((e.selected_package or "").lower(), e.selected_package or "—")
        return HTMLResponse(
            f"<h3>Ponuda je već prihvaćena.</h3><p>Odabrani paket: <b>{html.escape(chosen)}</b></p>"
        )

    if e.status == "declined":
        return HTMLResponse("<h3>Ponuda je već odbijena.</h3>")

    if package:
        p = package.strip().lower()
        if p not in PACKAGE_LABELS:
            return HTMLResponse("<h3>Neispravan paket.</h3>", status_code=400)

        e.accepted = True
        e.status = "accepted"
        e.selected_package = p
        e.updated_at = datetime.utcnow()
        db.commit()

        chosen = PACKAGE_LABELS.get(p, p)
        return HTMLResponse(
            f"<h2>Hvala! Ponuda je prihvaćena.</h2><p>Odabrani paket: <b>{html.escape(chosen)}</b></p>"
        )

    # Show selection UI (simple)
    return HTMLResponse(
        f"""
        <div style='font-family:Arial,sans-serif;max-width:720px;margin:30px auto;'>
          <h2>Odaberite paket</h2>
          <p>Molimo odaberite jedan od paketa:</p>
          <ul>
            <li><a href="{BASE_URL}/accept?token={e.token}&package=classic">Classic</a></li>
            <li><a href="{BASE_URL}/accept?token={e.token}&package=premium">Premium</a></li>
            <li><a href="{BASE_URL}/accept?token={e.token}&package=signature">Signature</a></li>
          </ul>
        </div>
        """
    )


@router.get("/decline", response_class=HTMLResponse)
def decline_get(
    token: str = Query(...),
    confirm: str | None = Query(None),
    db: Session = Depends(get_db),
):
    e = db.query(Event).filter_by(token=token).first()
    if not e:
        return HTMLResponse("<h3>Neispravan token.</h3>", status_code=404)

    if e.status == "declined":
        return HTMLResponse("<h3>Ponuda je već odbijena.</h3>")

    if confirm == "1":
        e.accepted = False
        e.status = "declined"
        e.updated_at = datetime.utcnow()
        db.commit()
        return HTMLResponse("<h2>Ponuda odbijena.</h2><p>Hvala na povratnoj informaciji.</p>")

    return HTMLResponse(
        f"""
        <div style='font-family:Arial,sans-serif;max-width:720px;margin:30px auto;'>
          <h2>Odbijanje ponude</h2>
          <p>Jeste li sigurni da želite odbiti ponudu?</p>
          <a href="{BASE_URL}/decline?token={e.token}&confirm=1">Da, odbijam</a>
        </div>
        """
    )
