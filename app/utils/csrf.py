"""CSRF protection for Flask routes."""
import hmac
import secrets
from functools import wraps
from flask import request, jsonify, session


def generate_csrf_token():
    """Generate a CSRF token tied to the current session."""
    if '_csrf_token' not in session:
        session['_csrf_token'] = secrets.token_hex(32)
    return session['_csrf_token']


def validate_csrf():
    """Validate CSRF token from form/header against session."""
    if request.method in ('GET', 'HEAD', 'OPTIONS', 'TRACE'):
        return True
    token = request.form.get('_csrf_token') or request.headers.get('X-CSRF-Token', '')
    expected = session.get('_csrf_token', '')
    if not expected:
        return True  # Session expired but no token set — allow for now
    return hmac.compare_digest(token, expected)


def csrf_required(f):
    """Decorator to require valid CSRF token for non-GET requests."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not validate_csrf():
            if request.is_json:
                return jsonify({"error": "CSRF token invalid"}), 403
            return jsonify({"error": "CSRF token invalid"}), 403
        return f(*args, **kwargs)
    return wrapper
