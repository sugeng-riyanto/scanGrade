from datetime import datetime, timezone

from flask import jsonify


def success_response(data=None, message=None, status_code=200):
    resp = {"success": True, "timestamp": datetime.now(timezone.utc).isoformat()}
    if message:
        resp["message"] = message
    if data is not None:
        resp["data"] = data
    return jsonify(resp), status_code


def error_response(error_code, message, details=None, status_code=400):
    resp = {
        "success": False,
        "error": error_code,
        "message": message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if details:
        resp["details"] = details
    return jsonify(resp), status_code
