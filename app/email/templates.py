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

    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#221E27;">
      <tr>
        <td width="110" align="left" valign="middle" style="padding:18px;">
          <img src="{logo_url}" alt="Landsky Cocktail Catering"
               width="80" height="80"
               style="display:block; width:80px; height:80px; object-fit:contain; border-radius:14px; background:#ffffff; padding:8px; border:0;" />
        </td>
        <td align="center" valign="middle" style="padding:18px 10px;">
          <div style="font-family:Arial, sans-serif; color:#ffffff;">
            <div style="font-size:20px; font-weight:700;">
              Landsky Cocktail Catering
            </div>
            <div style="font-size:13px; opacity:.85;">Ponuda</div>
          </div>
        </td>
        <td width="110"></td>
      </tr>
    </table>

    <div style="padding:18px;">
      <p>
        Poštovani/Poštovana <b>{html.escape(e.first_name)} {html.escape(e.last_name)}</b>,
      </p>

      <p>Zahvaljujemo na Vašem upitu.</p>

      <div style="margin-top:14px; padding:14px; border:1px solid #eee; border-radius:12px; background:#fafafa;">
        <b>Sažetak upita:</b><br><br>
        Datum: {html.escape(str(e.wedding_date))}<br>
        Lokacija: {html.escape(e.venue)}<br>
        Gosti: {e.guest_count}<br>
        Email: {html.escape(e.email)}<br>
        Telefon: {html.escape(e.phone)}<br><br>
        Napomena:<br>{msg_html}
      </div>

      <div style="margin-top:16px;">
        📎 Detalji paketa:
        <a href="{cocktails_pdf}" target="_blank">{cocktails_pdf}</a>
      </div>

      <div style="margin-top:16px;">
        📎 Fotografija bara:
        <a href="{bar_img}" target="_blank">{bar_img}</a>
      </div>

      <div style="margin-top:16px;">
        📎 Popis cigara:
        <a href="{cigare_img}" target="_blank">{cigare_img}</a>
      </div>

      <div style="margin-top:20px;">
        <a href="{accept_link}" style="background:#1b5e20; color:#fff; text-decoration:none; padding:10px 14px; border-radius:10px; font-weight:700;">
          ✅ Prihvaćam
        </a>
        &nbsp;
        <a href="{decline_link}" style="background:#b71c1c; color:#fff; text-decoration:none; padding:10px 14px; border-radius:10px; font-weight:700;">
          ❌ Odbijam
        </a>
      </div>

      <div style="margin-top:16px; font-size:12px; color:#666; text-align:center;">
        Ako trebate pomoć, kontaktirajte
        <a href="mailto:catering@landskybar.com">catering@landskybar.com</a>
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
