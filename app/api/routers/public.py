import html
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.api.schemas import RegistrationRequest
from app.core.config import BASE_URL, TEST_MODE
from app.core.logging import logger
from app.db.models import Event
from app.db.session import get_db
from app.email.templates import PACKAGE_LABELS, render_offer_html
from app.services.offers import send_offer_flow
from app.services.status_audit import log_status_change

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
    request: Request,
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

        old_status = e.status
        e.accepted = True
        e.status = "accepted"
        e.selected_package = p
        log_status_change(db, e, old_status, e.status, source="guest_accept_link", request=request)
        e.updated_at = datetime.utcnow()
        db.commit()

        chosen = PACKAGE_LABELS.get(p, p)
        return HTMLResponse(
            f"<h2>Hvala! Ponuda je prihvaćena.</h2><p>Odabrani paket: <b>{html.escape(chosen)}</b></p>"
        )

    # Show selection UI (RICH / PREMIUM)
    logo_url = f"{BASE_URL}/frontend/logo.png"

    return HTMLResponse(
        f"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Odabir paketa</title>
</head>
<body style="margin:0;background:#f5f6f8;font-family:Arial,sans-serif;color:#111;">
  <div style="max-width:760px;margin:30px auto;padding:0 14px;">
    <div style="border:1px solid #e8e8e8;border-radius:16px;overflow:hidden;background:#fff;box-shadow:0 10px 30px rgba(0,0,0,.06);">

      <!-- Header -->
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#221E27;">
        <tr>
          <td width="110" align="left" style="padding:18px;">
            <img src="{logo_url}" width="74" height="74"
              alt="Landsky Cocktail Catering"
              style="display:block;width:74px;height:74px;object-fit:contain;border-radius:14px;background:#ffffff;padding:8px;border:0;">
          </td>
          <td align="center" style="padding:18px 10px;">
            <div style="color:#fff;">
              <div style="font-size:20px;font-weight:700;letter-spacing:.2px;line-height:1.2;">
                Landsky Cocktail Catering
              </div>
              <div style="font-size:13px;opacity:.85;margin-top:4px;">Potvrda ponude</div>
            </div>
          </td>
          <td width="110"></td>
        </tr>
      </table>

      <div style="padding:22px;">
        <div style="font-size:18px;font-weight:700;margin-bottom:6px;">Odaberite paket</div>
        <div style="font-size:13px;color:#666;margin-bottom:18px;">
          Molimo odaberite jedan od paketa za potvrdu ponude.
        </div>

        <!-- Classic -->
        <div style="border:1px solid #eee;border-radius:14px;padding:16px;margin-bottom:14px;background:#fafafa;">
          <div style="font-weight:700;">Classic</div>
          <div style="font-size:13px;color:#666;margin-top:4px;">
            Osnovna ponuda — idealno za kratka događanja i većim brojem uzvanika.
          </div>
          <div style="margin-top:10px;">
            <a href="{BASE_URL}/accept?token={e.token}&package=classic"
               style="background:#1b5e20;color:#fff;text-decoration:none;padding:8px 14px;border-radius:8px;font-weight:700;display:inline-block;">
              Odaberi Classic
            </a>
          </div>
        </div>

        <!-- Premium -->
        <div style="border:1px solid #ffe8c2;border-radius:14px;padding:16px;margin-bottom:14px;background:#fff7ea;">
          <div style="font-weight:700;">Premium</div>
          <div style="font-size:13px;color:#666;margin-top:4px;">
            Proširena ponuda — elegantnija i ekskluzivnija događanja.
          </div>
          <div style="margin-top:10px;">
            <a href="{BASE_URL}/accept?token={e.token}&package=premium"
               style="background:#1b5e20;color:#fff;text-decoration:none;padding:8px 14px;border-radius:8px;font-weight:700;display:inline-block;">
              Odaberi Premium
            </a>
          </div>
        </div>

        <!-- Signature -->
        <div style="border:1px solid #e8e8ff;border-radius:14px;padding:16px;background:#f5f5ff;">
          <div style="font-weight:700;">Signature</div>
          <div style="font-size:13px;color:#666;margin-top:4px;">
            Premium experience — potpuni wow efekt.
          </div>
          <div style="margin-top:10px;">
            <a href="{BASE_URL}/accept?token={e.token}&package=signature"
               style="background:#1b5e20;color:#fff;text-decoration:none;padding:8px 14px;border-radius:8px;font-weight:700;display:inline-block;">
              Odaberi Signature
            </a>
          </div>
        </div>

        <div style="margin-top:20px;font-size:12px;color:#777;text-align:center;">
          Ako trebate pomoć, kontaktirajte
          <a href="mailto:catering@landskybar.com" style="color:#666;text-decoration:underline;">
            catering@landskybar.com
          </a>
        </div>
      </div>

    </div>
  </div>
</body>
</html>
        """
    )


@router.get("/decline", response_class=HTMLResponse)
def decline_get(
    token: str = Query(...),
    db: Session = Depends(get_db),
):
    e = db.query(Event).filter_by(token=token).first()
    if not e:
        return HTMLResponse("<h3>Neispravan token.</h3>", status_code=404)

    return HTMLResponse(
        """
        <div style='font-family:Arial,sans-serif;max-width:720px;margin:30px auto;'>
          <h2>Odbijanje ponude</h2>
          <p>
            Radi sigurnosti, odbijanje ponude više nije moguće putem email linka.
          </p>
          <p>
            Molimo odgovorite na email i napišite da želite odbiti ponudu,
            a naš tim će ručno ažurirati status.
          </p>
        </div>
        """
    )


@router.post("/decline/confirm", response_class=HTMLResponse)
def decline_confirm_post(
    token: str = Form(...),
    db: Session = Depends(get_db),
):
    e = db.query(Event).filter_by(token=token).first()
    if not e:
        return HTMLResponse("<h3>Neispravan token.</h3>", status_code=404)

    if e.status == "declined":
        return HTMLResponse("<h3>Ponuda je već odbijena.</h3>")

    e.accepted = False
    e.status = "declined"
    e.updated_at = datetime.utcnow()
    db.commit()
    return HTMLResponse("<h2>Ponuda odbijena.</h2><p>Hvala na povratnoj informaciji.</p>")
