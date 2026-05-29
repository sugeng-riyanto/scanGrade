import re
import time
import html
from flask import request, jsonify


def sanitize_input(value: str) -> str:
    if value is None:
        return ""
    return html.escape(str(value).strip())


def sanitize_dict(data: dict) -> dict:
    return {k: sanitize_input(v) if isinstance(v, str) else v for k, v in data.items()}


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[-\s]+", "-", text)
    return text.strip("-")


def validate_timestamp(ts: float, tolerance: int = 300) -> bool:
    return abs(time.time() - ts) <= tolerance


def validate_uuid(value: str) -> bool:
    if not value or not isinstance(value, str):
        return False
    pattern = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    return bool(re.match(pattern, value.lower()))


def validate_nisn(value: str) -> bool:
    return bool(re.match(r"^\d{8,12}$", value.strip())) if value else False


def validate_phone(value: str) -> bool:
    return bool(re.match(r"^\+?[\d\s\-()]{7,20}$", value.strip())) if value else True


def validate_email(value: str) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value.strip())) if value else False


def validate_password_strength(password: str) -> tuple:
    errors = []
    if len(password) < 6:
        errors.append("Password minimal 6 karakter")
    if len(password) > 128:
        errors.append("Password maksimal 128 karakter")
    if not re.search(r"[A-Za-z]", password):
        errors.append("Password harus mengandung huruf")
    if not re.search(r"\d", password):
        errors.append("Password harus mengandung angka")
    return (len(errors) == 0, errors)


def require_json():
    if not request.is_json:
        return jsonify({"error": "Content-Type must be application/json"}), 415
    return None
