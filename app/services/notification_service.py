import os
import logging

import requests

logger = logging.getLogger(__name__)


def send_whatsapp(phone: str, message: str):
    """Optional WhatsApp channel — disabled unless FONNTE_API_KEY is set.

    Email is the active notification channel for this deployment.
    """
    api_key = os.getenv("FONNTE_API_KEY")
    if not api_key or not phone:
        logger.debug("WhatsApp channel not configured, skipping")
        return False
    try:
        requests.post(
            "https://api.fonnte.com/send",
            json={"target": phone, "message": message},
            headers={"Authorization": api_key},
            timeout=10,
        )
        return True
    except Exception as e:
        logger.error(f"Fonnte send failed: {e}")
        return False


def send_email(to_email: str, subject: str, body_html: str, text: str | None = None,
               important: bool = False):
    """Send one HTML mail through **the one sender**. Returns True on success.

    This used to open its own `smtplib` connection with its own `From`/`Reply-To`, a
    second implementation of everything `app/services/smtp_settings.py` decides: the
    credential resolver, the alias names an app password may arrive under, the display
    name, and the reply address. Two clients is two answers to one question, and only
    one of them was getting the fixes. So it delegates, and `text` is the plain-text
    alternative that rides along in the same `multipart/alternative`.
    """
    from app.services import smtp_settings

    ok, error = smtp_settings.send(to_email, subject, body_html, html=True, text=text,
                                   important=important)
    if not ok:
        logger.warning("Email to %s not sent: %s", to_email, error or "not configured")
    return bool(ok)


def notify_approval(email: str, phone: str, school_name: str, code: str, expires_at_str: str,
                    name: str = "Bapak/Ibu Admin Sekolah"):
    """Send approval notification via email and WhatsApp.

    The body comes from `app/services/email_bodies.py` like every other user-facing
    mail: bilingual, escaped (a school name is typed into a registration form), and
    with a plain-text half so a client that refuses HTML still reads the code.
    """
    from app.services import email_bodies

    mail = email_bodies.activation_code(name=name, school_name=school_name, code=code,
                                        expires_at=expires_at_str)
    sent = send_email(email, mail["subject"], mail["html"], text=mail["text"],
                      important=True)
    if not sent:
        logger.warning("Activation code for %s could not be emailed; code=%s", email, code)

    wa_msg = (
        f"*Aktivasi Akun ScanGrade*\n\n"
        f"Sekolah *{school_name}* telah disetujui!\n\n"
        f"Kode Aktivasi Anda: *{code}*\n"
        f"Berlaku hingga: {expires_at_str}\n\n"
        f"Aktivasi sekarang: {os.getenv('APP_URL', 'http://localhost:5000')}/auth/activate"
    )
    if phone:
        send_whatsapp(phone, wa_msg)
