import os
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import requests

logger = logging.getLogger(__name__)


def send_whatsapp(phone: str, message: str):
    api_key = os.getenv("FONNTE_API_KEY")
    if not api_key:
        logger.warning("FONNTE_API_KEY not set, skipping WhatsApp")
        return
    try:
        requests.post(
            "https://api.fonnte.com/send",
            json={"target": phone, "message": message},
            headers={"Authorization": api_key},
            timeout=10,
        )
    except Exception as e:
        logger.error(f"Fonnte send failed: {e}")


def send_email(to_email: str, subject: str, body_html: str):
    """Send email via SMTP. Falls back to logging if not configured."""
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = os.getenv("SMTP_PORT", "587")
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")
    from_email = os.getenv("SMTP_FROM", "noreply@scangrade.app")

    if not smtp_host or not smtp_user or not smtp_pass:
        logger.info(f"SMTP not configured, would send email to {to_email}: {subject}")
        return

    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = from_email
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(body_html, "html"))

        with smtplib.SMTP(smtp_host, int(smtp_port)) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(from_email, [to_email], msg.as_string())
    except Exception as e:
        logger.error(f"SMTP send failed to {to_email}: {e}")


def notify_approval(email: str, phone: str, school_name: str, code: str, expires_at_str: str):
    """Send approval notification via email and WhatsApp."""
    subject = f"Aktivasi Akun ScanGrade - {school_name}"
    body_html = f"""
    <div style="font-family:Inter,sans-serif;max-width:560px;margin:0 auto;padding:24px;background:#f8fafc;border-radius:16px;">
        <div style="text-align:center;padding:24px 0;">
            <div style="width:48px;height:48px;margin:0 auto;background:linear-gradient(135deg,#f97316,#f59e0b);border-radius:12px;display:flex;align-items:center;justify-content:center;">
                <svg width="24" height="24" fill="white" viewBox="0 0 24 24"><path d="M12 2L2 7v10l10 5 10-5V7L12 2z"/></svg>
            </div>
            <h1 style="color:#1e293b;font-size:24px;font-weight:800;margin:16px 0 4px;">Aktivasi Akun ScanGrade</h1>
            <p style="color:#64748b;font-size:14px;">Sekolah <strong>{school_name}</strong> telah disetujui</p>
        </div>
        <div style="background:white;border-radius:16px;padding:24px;border:1px solid #e2e8f0;">
            <p style="color:#1e293b;font-size:14px;font-weight:600;">Kode Aktivasi Anda:</p>
            <div style="background:#f8fafc;border:2px dashed #f97316;border-radius:12px;padding:16px;text-align:center;margin:12px 0;">
                <span style="font-size:32px;font-weight:800;letter-spacing:8px;color:#ea580c;">{code}</span>
            </div>
            <p style="color:#64748b;font-size:13px;">Gunakan kode di atas untuk mengaktifkan akun Anda.</p>
            <p style="color:#64748b;font-size:13px;">Berlaku hingga: <strong>{expires_at_str}</strong></p>
            <div style="margin-top:16px;text-align:center;">
                <a href="{os.getenv('APP_URL', 'http://localhost:5000')}/auth/activate"
                   style="display:inline-block;background:linear-gradient(135deg,#f97316,#f59e0b);color:white;padding:12px 32px;border-radius:12px;text-decoration:none;font-weight:700;font-size:14px;">
                    Aktivasi Sekarang
                </a>
            </div>
        </div>
    </div>
    """
    send_email(email, subject, body_html)

    wa_msg = (
        f"*Aktivasi Akun ScanGrade*\n\n"
        f"Sekolah *{school_name}* telah disetujui!\n\n"
        f"Kode Aktivasi Anda: *{code}*\n"
        f"Berlaku hingga: {expires_at_str}\n\n"
        f"Aktivasi sekarang: {os.getenv('APP_URL', 'http://localhost:5000')}/auth/activate"
    )
    if phone:
        send_whatsapp(phone, wa_msg)
