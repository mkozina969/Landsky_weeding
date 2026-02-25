# -*- coding: utf-8 -*-
import html
import os
from pathlib import Path

from app.core.config import BASE_URL
from app.db.models import Event

PACKAGE_LABELS = {
    "classic": "Classic",
    "premium": "Premium",
    "signature": "Signature",
}

# repo root: .../<repo_root>
# this file: .../<repo_root>/app/email/templates.py
ROOT = Path(__file__).resolve().parents[2]


def _nl2br_escaped(text: str) -> str:
    """Escape user text and convert newlines to <br> for HTML emails."""
    return html.escape(text).replace("\n", "<br>")


def render_offer_html(e: Event) -> str:
    """
    Offer email HTML.

    - If USE_FRONTEND_OFFER_TEMPLATE=true AND frontend/offer.html exists -> use it.
    - Otherwise use the rich inline template (your previous "fallback", with all sections/links).
    """
    use_frontend = os.getenv("USE_FRONTEND_OFFER_TEMPLATE", "").strip().lower() in ("1", "true", "yes", "on")

    template_path = ROOT / "frontend" / "offer.html"
    if use_frontend and template_path.exists():
        tpl = template_path.read_text(encoding="utf-8")
        msg = (e.message or "").strip()
        msg_html = _nl2br_escaped(msg) if msg else ""
        return (
            tpl.replace("{{FIRST_NAME}}", html.escape(e.first_name))
            .replace("{{LAST_NAME}}", html.escape(e.last_name))
            .replace("{{WEDDING_DATE}}", html.escape(str(e.wedding_date)))
            .replace("{{VENUE}}", html.escape(e.venue))
            .replace("{{GUEST_COUNT}}", str(e.guest_count))
            .replace("{{EMAIL}}", html.escape(e.email))
            .replace("{{PHONE}}", html.escape(e.phone))
            .replace("{{MESSAGE}}", msg_html or html.escape((e.message or "").strip()))
            .replace("{{ACCEPT_URL}}", f"{BASE_URL}/accept?token={e.token}")
            .replace("{{DECLINE_URL}}", f"{BASE_URL}/decline?token={e.token}")
            .replace("{{BASE_URL}}", BASE_URL)
        )

    # -------- Rich inline template (FULL version with links) --------
    logo_url = f"{BASE_URL}/frontend/logo.png"
    cocktails_pdf = f"{BASE_URL}/frontend/cocktails.pdf"
    bar_img = f"{BASE_URL}/frontend/bar.jpeg"
    cigare_img = f"{BASE_URL}/frontend/cigare.png"
    accept_link = f"{BASE_URL}/accept?token={e.token}"
    decline_link = f"{BASE_URL}/decline?token={e.token}"

    msg = (e.message or "").strip()
    msg_html = _nl2br_escaped(msg) if msg else "(nema)"

    return f"""
<div style="font-family: Arial, sans-serif; color:#111; line-height:1.5;">
  <div style="max-width:720px; margin:0 auto; border:1px solid #eee; border-radius:14px; overflow:hidden;">

    <!-- PREMIUM HEADER (email-safe table layout) -->
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#221E27;">
      <tr>
        <td width="110" align="left" valign="middle" style="padding:18px;">
          <img src="{logo_url}" alt="Landsky Cocktail Catering"
               width="80" height="80"
               style="display:block; width:80px; height:80px; object-fit:contain; border-radius:14px; background:#ffffff; padding:8px; border:0;" />
        </td>
        <td align="center" valign="middle" style="padding:18px 10px;">
          <div style="font-family:Arial, sans-serif; color:#ffffff;">
            <div style="font-size:20px; font-weight:700; letter-spacing:.2px; line-height:1.2;">
              Landsky Cocktail Catering
            </div>
            <div style="font-size:13px; opacity:.85; margin-top:4px;">Ponuda</div>
          </div>
        </td>
        <td width="110" valign="middle" style="padding:18px;">&nbsp;</td>
      </tr>
    </table>

    <div style="padding:18px;">
      <div style="font-size:14px;">
        Poštovani/Poštovana <b>{html.escape(e.first_name)} {html.escape(e.last_name)}</b>,<br>
        zahvaljujemo na Vašem upitu. U nastavku dostavljamo informacije vezane za cocktail catering.
      </div>

      <div style="margin-top:14px; padding:14px; border:1px solid #eee; border-radius:12px; background:#fafafa;">
        <div style="font-weight:700; margin-bottom:8px;">Sažetak upita</div>
        <div>📅 <b>Datum:</b> {html.escape(str(e.wedding_date))}</div>
        <div>📍 <b>Lokacija / sala:</b> {html.escape(e.venue)}</div>
        <div>👥 <b>Broj gostiju:</b> {e.guest_count}</div>
        <div>✉️ <b>Email:</b> {html.escape(e.email)}</div>
        <div>📞 <b>Telefon:</b> {html.escape(e.phone)}</div>
        <div style="margin-top:8px;"><b>Napomena / pitanja:</b><br>{msg_html}</div>
      </div>

      <div style="margin-top:14px; padding:14px; border:1px solid #ffe8c2; border-radius:12px; background:#fff7ea;">
        <div style="font-weight:700; margin-bottom:8px;">U ponudi su sljedeći paketi:</div>

        <div style="margin:8px 0 10px 0;">
          <div style="font-weight:700;">Ponuda uključuje:</div>
          <ul style="margin:6px 0 0 18px; padding:0;">
            <li>Profesionalnog barmena</li>
            <li>Event menu s koktelima prilagođen temi eventa (po želji)</li>
            <li>Alkoholne i bezalkoholne koktele</li>
            <li>Premium led / nefrumirani led</li>
            <li>Dekoracije</li>
            <li>Šank</li>
          </ul>
        </div>

        <div style="font-weight:700; margin:10px 0 6px 0;">Cijene paketa</div>
        <div>• <b>Classic:</b> 1.000 EUR + PDV (100 koktela) — dodatnih 100: 500 EUR + PDV</div>
        <div>• <b>Premium:</b> 1.200 EUR + PDV (100 koktela) — dodatnih 100: 600 EUR + PDV</div>
        <div>• <b>Signature:</b> 1.500 EUR + PDV (100 koktela) — dodatnih 100: 800 EUR + PDV</div>

        <div style="margin-top:10px;">* Preporučujemo 200 koktela.</div>

        <div style="margin-top:10px;">
          📎 Detalji paketa: <a href="{cocktails_pdf}" target="_blank" style="color:#0b57d0;">{cocktails_pdf}</a>
        </div>
      </div>

      <div style="margin-top:14px; padding:14px; border:1px solid #eee; border-radius:12px; background:#fff;">
        <div style="font-weight:700; margin-bottom:8px;">Premium cigare (opcionalno)</div>
        <div>Uz odabir cigara od nas dobivate humidor, rezač, upaljač i pepeljaru.</div>
        <div style="margin-top:8px;">📎 Popis cigara: <a href="{cigare_img}" target="_blank" style="color:#0b57d0;">{cigare_img}</a></div>
        <div style="margin-top:8px;">Za događaje izvan Zagreba naplaćuje se put <b>0,70 EUR/km</b>.</div>
        <div style="margin-top:8px;">
          Rado Vas pozivamo na prezentaciju koktela u našem LandSky Baru (Draškovićeva 144), gdje ćemo Vam detaljno predstaviti našu uslugu i odabrati najbolje za vaš event.
        </div>
        <div style="margin-top:8px;">📎 Fotografija bara: <a href="{bar_img}" target="_blank" style="color:#0b57d0;">{bar_img}</a></div>
      </div>

      <div style="margin-top:14px; padding:14px; border:1px solid #e8f5e9; border-radius:12px; background:#f2fbf3;">
        <div style="font-weight:700; margin-bottom:8px;">Potvrda ponude</div>
        <div>Molimo potvrdite ponudu klikom:</div>
        <div style="margin-top:10px;">
          <a href="{accept_link}" style="background:#1b5e20; color:#fff; text-decoration:none; padding:10px 14px; border-radius:10px; font-weight:700; display:inline-block;">✅ Prihvaćam</a>
          <span style="display:inline-block; width:10px;"></span>
          <a href="{decline_link}" style="background:#b71c1c; color:#fff; text-decoration:none; padding:10px 14px; border-radius:10px; font-weight:700; display:inline-block;">❌ Odbijam</a>
        </div>
        <div style="margin-top:10px; font-size:12px; color:#333;">
          Napomena: kod prihvaćanja ćete odabrati paket (Classic / Premium / Signature).
        </div>
      </div>

      <div style="margin-top:16px; font-size:12px; color:#666; text-align:center;">
        Ovaj email je generiran automatski. Ako trebate pomoć, odgovorite na ovaj email ili kontaktirajte
        <a href="mailto:catering@landskybar.com" style="color:#666; text-decoration:underline;">catering@landskybar.com</a>
      </div>
    </div>
  </div>
</div>
"""


def internal_email_body(e: Event) -> str:
    preview_link = f"{BASE_URL}/offer-preview?token={e.token}"
    admin_link = f"{BASE_URL}/admin"
    msg = (e.message or "").strip()
    msg_html = _nl2br_escaped(msg) if msg else "(nema)"

    return f"""
<div style="font-family: Arial, sans-serif; color:#111; line-height:1.5;">
  <h2>Novi upit</h2>
  <ul>
    <li><b>Klijent:</b> {html.escape(e.first_name)} {html.escape(e.last_name)}</li>
    <li><b>Email klijenta:</b> {html.escape(e.email)}</li>
    <li><b>Telefon:</b> {html.escape(e.phone)}</li>
    <li><b>Datum:</b> {html.escape(str(e.wedding_date))}</li>
    <li><b>Sala:</b> {html.escape(e.venue)}</li>
    <li><b>Gosti:</b> {e.guest_count}</li>
    <li><b>Status:</b> {html.escape(getattr(e, "status", ""))}</li>
    <li><b>Odabrani paket:</b> {html.escape(getattr(e, "selected_package", "") or "—")}</li>
  </ul>
  <p><b>Napomena / Pitanja:</b><br>{msg_html}</p>
  <p><b>Preview ponude:</b><br><a href="{preview_link}">{preview_link}</a></p>
  <p><b>Admin:</b> <a href="{admin_link}">{admin_link}</a></p>
</div>
"""


def reminder_email_body(e: Event) -> str:
    accept_link = f"{BASE_URL}/accept?token={e.token}"
    decline_link = f"{BASE_URL}/decline?token={e.token}"

    return f"""
<div style="font-family: Arial, sans-serif; color:#111; line-height:1.5; max-width:700px; margin:0 auto;">
  <h2>Podsjetnik - Landsky Cocktail Catering ponuda</h2>

  <p>Poštovani/Poštovana {html.escape(e.first_name)} {html.escape(e.last_name)},</p>

  <p>
    Samo kratki podsjetnik vezano za našu ponudu za datum
    <b>{html.escape(str(e.wedding_date))}</b> ({html.escape(e.venue)}).
  </p>

  <div style="margin-top:16px;">
    <a href="{accept_link}" style="background:#1b5e20; color:#fff; text-decoration:none; padding:8px 12px; border-radius:8px; font-weight:700; display:inline-block;">
      ✅ Prihvaćam ponudu
    </a>
    <span style="display:inline-block; width:10px;"></span>
    <a href="{decline_link}" style="background:#b71c1c; color:#fff; text-decoration:none; padding:8px 12px; border-radius:8px; font-weight:700; display:inline-block;">
      ❌ Odbijam ponudu
    </a>
  </div>

  <div style="margin-top:20px; font-size:12px; color:#666; text-align:center;">
    Ako trebate pomoć, odgovorite na ovaj email ili kontaktirajte
    <a href="mailto:catering@landskybar.com" style="color:#666; text-decoration:underline;">catering@landskybar.com</a>
  </div>
</div>
"""


def event_2d_email_body(e: Event) -> str:
    return f"""
<div style="font-family: Arial, sans-serif; color:#111; line-height:1.5; max-width:700px; margin:0 auto;">
  <h2>Podsjetnik - Vaš događaj je uskoro</h2>

  <p>Poštovani/Poštovana {html.escape(e.first_name)} {html.escape(e.last_name)},</p>

  <p>
    Veselimo se Vašem događaju <b>{html.escape(str(e.wedding_date))}</b> ({html.escape(e.venue)}).
  </p>

  <p>Za bilo kakva pitanja slobodno odgovorite na ovaj email.</p>
</div>
"""
