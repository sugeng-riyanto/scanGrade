"""Template filters, globals, and context processors for Jinja2."""
import json as _json
import math as _math
from datetime import datetime, timedelta, timezone
from flask import g, request, current_app

DEFAULT_TZ_OFFSET = 7


def from_json_filter(val):
    if val is None:
        return None
    if isinstance(val, (dict, list)):
        return val
    if isinstance(val, str):
        try:
            return _json.loads(val)
        except (TypeError, ValueError):
            return None
    return val


def tz_format_filter(val, fmt="%d %b %Y %H:%M"):
    if not val:
        return "-"
    try:
        if isinstance(val, str):
            for fmt_in in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
                try:
                    dt = datetime.strptime(val, fmt_in)
                    break
                except ValueError:
                    continue
            else:
                return val[:16].replace("T", " ")
        elif isinstance(val, datetime):
            dt = val
        else:
            return str(val)
        offset = getattr(g, "tz_offset", DEFAULT_TZ_OFFSET)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(timezone(timedelta(hours=offset)))
        return dt.strftime(fmt)
    except Exception:
        return str(val)[:16] if val else "-"


def tz_short_filter(val):
    offset = getattr(g, "tz_offset", DEFAULT_TZ_OFFSET)
    sign = "+" if offset >= 0 else ""
    return f"UTC{sign}{offset}"


def greeting():
    offset = getattr(g, "tz_offset", DEFAULT_TZ_OFFSET)
    now = datetime.now(timezone(timedelta(hours=offset)))
    h = now.hour
    if 5 <= h < 11:
        return "pagi"
    elif 11 <= h < 15:
        return "siang"
    elif 15 <= h < 18:
        return "sore"
    else:
        return "malam"


def greeting_en():
    offset = getattr(g, "tz_offset", DEFAULT_TZ_OFFSET)
    now = datetime.now(timezone(timedelta(hours=offset)))
    h = now.hour
    if 5 <= h < 11:
        return "morning"
    elif 11 <= h < 15:
        return "afternoon"
    elif 15 <= h < 18:
        return "evening"
    else:
        return "evening"


_demo_cache = {}
def get_demo_settings():
    req_key = f"demo_{id(request)}"
    if req_key in _demo_cache:
        return _demo_cache[req_key]
    try:
        supabase = current_app.extensions["supabase"]
        data = supabase.table("school_settings").select("demo_settings").eq("id", 1).single().execute().data or {}
        result = data.get("demo_settings") or {}
    except Exception:
        result = {}
    _demo_cache[req_key] = result
    return result


def get_whatsapp_number():
    try:
        supabase = current_app.extensions["supabase"]
        data = supabase.table("school_settings").select("whatsapp_number").eq("id", 1).single().execute().data or {}
        return data.get("whatsapp_number", "")
    except Exception:
        return ""


_features_cache = {}
def get_school_features(school_id=None):
    sid = school_id or getattr(g, "user_school_id", None)
    if not sid:
        return {}
    req_key = f"feat_{id(request)}_{sid}"
    if req_key in _features_cache:
        return _features_cache[req_key]
    try:
        supabase = current_app.extensions["supabase"]
        data = supabase.table("schools").select("features").eq("id", sid).single().execute().data or {}
        result = data.get("features") or {}
        if isinstance(result, str):
            result = _json.loads(result)
    except Exception:
        result = {}
    if not isinstance(result, dict):
        result = {}
    _features_cache[req_key] = result
    return result


def school_favicon(school_info=None):
    if school_info and school_info.get("logo_url"):
        return school_info["logo_url"]
    name = (school_info or {}).get("name", "SG")
    initials = "".join(w[0] for w in name.split()[:2]).upper()[:2] if len(name.split()) > 1 else name[:2].upper()
    color = "#4338CA"
    return f"data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' rx='6' fill='{color}'/><text x='16' y='22' text-anchor='middle' font-size='16' font-weight='bold' fill='white'>{initials}</text></svg>"


def register_template_filters(app):
    app.template_filter("from_json")(from_json_filter)
    app.template_filter("tz")(tz_format_filter)
    app.template_filter("tz_short")(tz_short_filter)

    app.jinja_env.globals["cos"] = _math.cos
    app.jinja_env.globals["sin"] = _math.sin

    from app.utils.csrf import generate_csrf_token
    app.jinja_env.globals["csrf_token"] = generate_csrf_token
    app.jinja_env.globals["get_demo_settings"] = get_demo_settings
    app.jinja_env.globals["get_whatsapp_number"] = get_whatsapp_number
    app.jinja_env.globals["get_school_features"] = get_school_features
    app.jinja_env.globals["school_favicon"] = school_favicon

    app.template_global()(greeting)
    app.template_global()(greeting_en)
