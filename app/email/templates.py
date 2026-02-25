import html
from pathlib import Path

from app.core.config import BASE_URL
from app.db.models import Event

PACKAGE_LABELS = {
    "classic": "Classic",
    "premium": "Premium",
    "signature": "Signature",
}

# Project root = .../<repo_root>
# This file is .../<repo_root>/app/email/templates.py
ROOT = Path(__file__).resolve().parents[2]


def _nl2br_escaped(text: str) -> str:
    """Escape user text and convert newlines to <br> for HTML emails."""
    return html.escape(text).replace("\n", "<br>")


def render_offer_html(e: Event) -> str:
    """Render the offer email HTML.

    Primary source: frontend/offer.html (simple placeholder template).
    Fallback: inline HTML defined below.

    IMPORTANT: We use an absolute path to avoid working-directory issues on Render.
    """
    template_path = ROOT / "frontend" / "offer.html"

    if template_path.exists():
        tpl = template_path.read_text(encoding="utf-8")

        # Keep message formatting pleasant in HTML emails
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
            # Keep both placeholders usable depending on your template:
            .replace("{{MESSAGE}}", msg_html or html.escape((e.message or "").strip()))
            .replace("{{ACCEPT_URL}}", f"{BASE_URL}/accept?token={e.token}")
            .replace("{{DECLINE_URL}}", f"{BASE_URL}/decline?token={e.token}")
            .replace("{{BASE_URL}}", BASE_URL)
        )

    # -------- Inline fallback (keep minimal & safe) --------
    logo_url = f"{BASE_URL}/frontend/logo.png"
    cocktails_pdf = f"{BASE_URL}/frontend/cocktails.pdf"
    accept_link = f"{BASE_URL}/accept?token={e.token}"
    decline_link = f"{BASE_URL}/decline?token={e.token}"

    return f"""
<div style="font-family: Arial, sans-serif; color:#111; line-height:1.5;">
  <div style="max-width:720px; margin:0 auto; border:1px solid #eee; border-radius:14px; overflow:hidden;">
    <!-- PREMIUM HEADER -->
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

    <div style="padding:22px 22px 10px 22px;">
      <p>Poštovani/Poštovana <b>{html.escape(e.first_name)} {html.escape(e.last_name)}</b>,</p>
      <p>zahvaljujemo na Vašem upitu. U nastavku dostavljamo informacije vezane za cocktail catering.</p>

      <div style="border:1px solid #eee; border-radius:12px; padding:14px; background:#fafafa;">
        <div style="font-weight:700; margin-bottom:8px;">Sažetak upita</div>
        <div>📅 <b>Datum:</b> {html.escape(str(e.wedding_date))}</div>
        <div>📍 <b>Lokacija / sala:</b> {html.escape(e.venue)}</div>
        <div>👥 <b>Broj gostiju:</b> {e.guest_count}</div>
        <div>✉️ <b>Email:</b> {html.escape(e.email)}</div>
        <div>📞 <b>Telefon:</b> {html.escape(e.phone)}</div>
        <div style="margin-top:10px;"><b>Napomena / pitanja:</b><br>{html.escape((e.message or '(nema)').strip())}</div>
      </div>

      <div style="margin-top:16px;">
        <div style="font-weight:700;">U ponudi su sljedeći paketi:</div>
        <ul>
          <li><b>Classic</b> — osnovna ponuda</li>
          <li><b>Premium</b> — proširena ponuda</li>
          <li><b>Signature</b> — premium experience</li>
        </ul>
      </div>

      <div style="margin-top:16px;">
        <a href="{cocktails_pdf}" style="color:#2b6cb0;">Pogledajte listu cocktaila (PDF)</a>
      </div>

      <div style="margin-top:16px;">
        ✅ <a href="{accept_link}">Prihvaćam ponudu</a><br>
        ❌ <a href="{decline_link}">Odbijam ponudu</a>
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
    <li><b>Poruka:</b> {msg_html}</li>
  </ul>
  <p><a href="{preview_link}">Offer preview</a> • <a href="{admin_link}">Admin</a></p>
</div>
"""


def reminder_email_body(e: Event) -> str:
    accept_link = f"{BASE_URL}/accept?token={e.token}"
    decline_link = f"{BASE_URL}/decline?token={e.token}"
    return f"""
<div style="font-family: Arial, sans-serif; color:#111; line-height:1.5; max-width:700px; margin:0 auto;">
  <h2>Podsjetnik — Landsky Cocktail Catering ponuda</h2>
  <p>Poštovani {html.escape(e.first_name)} {html.escape(e.last_name)},</p>
  <p>Samo kratki podsjetnik vezano za našu ponudu za datum <b>{html.escape(str(e.wedding_date))}</b> ({html.escape(e.venue)}).</p>
  <p>✅ <a href="{accept_link}">Prihvaćam ponudu</a><br>
     ❌ <a href="{decline_link}">Odbijam ponudu</a></p>
</div>
"""


def event_2d_email_body(e: Event) -> str:
    return f"""
<div style="font-family: Arial, sans-serif; color:#111; line-height:1.5; max-width:700px; margin:0 auto;">
  <h2>Podsjetnik — Vaš događaj je uskoro</h2>
  <p>Poštovani {html.escape(e.first_name)} {html.escape(e.last_name)},</p>
  <p>Veselimo se Vašem događaju <b>{html.escape(str(e.wedding_date))}</b> ({html.escape(e.venue)}).</p>
  <p>Za bilo kakva pitanja slobodno odgovorite na ovaj email.</p>
</div>
"""
