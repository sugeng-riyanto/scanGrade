import uuid
from datetime import datetime, timezone


def generate_uuid() -> str:
    return str(uuid.uuid4())


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def row_or_none(response):
    """Return ``response.data`` for a "fetch at most one row" query, or ``None``.

    postgrest's ``maybe_single().execute()`` returns ``None`` — not a response
    carrying ``data=None`` — when the query matches no row. Accessing ``.data``
    on that raises ``AttributeError``, so every such call site must go through
    here instead of touching ``.data`` directly.
    """
    return response.data if response is not None else None
