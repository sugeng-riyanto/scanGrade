"""One catalog for customer offers, admin activation and server-side limits."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

PLANS = (
    {"code": "trial", "name": "Uji coba", "name_en": "Trial", "price": 0,
     "minutes": 30, "participants": 5, "sessions": 3, "days": 7,
     "description": "Kenali alur pengawasan bersama kelompok kecil.",
     "description_en": "Try the supervision workflow with a small group."},
    {"code": "kelas", "name": "Kelas", "name_en": "Class", "price": 149000,
     "minutes": 60, "participants": 10, "sessions": 5, "days": 30,
     "description": "Untuk kelompok belajar dan pengawasan rutin.",
     "description_en": "For study groups and regular supervision."},
    {"code": "sekolah", "name": "Sekolah", "name_en": "School", "price": 399000,
     "minutes": 120, "participants": 35, "sessions": 3, "days": 30,
     "description": "Waktu lebih panjang untuk ujian satu kelas.",
     "description_en": "Longer sessions for a full classroom exam."},
)


def plan(code):
    return next((dict(p) for p in PLANS if p["code"] == code), None)


def parse_time(value):
    if not value:
        return None
    try:
        moment = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return moment if moment.tzinfo else None
    except (ValueError, TypeError):
        return None


def describe(row, *, now=None):
    row = {"school_id": None, "updated_at": "", "plan_code": None,
           "trial_started_at": None, "participant_limit": None,
           "session_limit_minutes": None, "session_allowance": None,
           "sessions_used": 0, **dict(row or {})}
    now = now or datetime.now(timezone.utc)
    expiry = parse_time(row.get("valid_until"))
    if row.get("archived_at"):
        state = "archived"
    elif not row.get("enabled") or row.get("status") not in ("trial", "paid"):
        state = "inactive"
    elif not expiry or expiry <= now:
        state = "expired"
    else:
        state = "trial" if row["status"] == "trial" else "active"
    row["effective_status"] = state
    row["days_left"] = max(0, int((expiry - now).total_seconds() // 86400)) if expiry else 0
    row["remaining_sessions"] = max(0, int(row.get("session_allowance") or 0) - int(row.get("sessions_used") or 0))
    row["expiry_label"] = expiry.astimezone(timezone(timedelta(hours=7))).strftime("%d %b %Y, %H.%M WIB") if expiry else "—"
    row["plan"] = plan(row.get("plan_code"))
    return row


def activation(existing, code, action, payment_verified, *, now=None):
    """Return validated server-owned values; client-supplied prices/caps are ignored."""
    package = plan(code)
    if not package or action not in ("activate", "extend"):
        raise ValueError("Pilih paket dan tindakan yang tersedia.")
    existing = existing or {}
    now = now or datetime.now(timezone.utc)
    if code == "trial" and existing.get("trial_started_at"):
        raise ValueError("Uji coba hanya tersedia satu kali per sekolah. Pilih paket lanjutan.")
    if code != "trial" and not payment_verified:
        raise ValueError("Verifikasi pembayaran sebelum mengaktifkan paket berbayar.")
    expiry = parse_time(existing.get("valid_until"))
    carry = action == "extend" and expiry and expiry > now and existing.get("plan_code") == code
    if action == "extend" and code != existing.get("plan_code"):
        raise ValueError("Perpanjangan harus memakai paket yang sama. Gunakan aktivasi untuk mengganti paket.")
    start = expiry if carry else now
    return {"enabled": True, "status": "trial" if code == "trial" else "paid",
            "plan_code": code, "valid_until": (start + timedelta(days=package["days"])).isoformat(),
            "session_limit_minutes": package["minutes"], "participant_limit": package["participants"],
            "session_allowance": int(existing.get("session_allowance") or 0) + package["sessions"] if carry else package["sessions"],
            "sessions_used": int(existing.get("sessions_used") or 0) if carry else 0,
            "trial_started_at": existing.get("trial_started_at") or (now.isoformat() if code == "trial" else None),
            "archived_at": None}


def session_open(session, *, now=None):
    if not session or session.get("status") != "active":
        return False
    if not session.get("expires_at"):
        return True  # licenses issued before timed packages retain their contract
    expiry = parse_time(session["expires_at"])
    return expiry is not None and expiry > (now or datetime.now(timezone.utc))
