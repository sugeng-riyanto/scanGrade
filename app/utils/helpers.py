import time
import uuid
from datetime import datetime, timezone

import httpx


def generate_uuid() -> str:
    return str(uuid.uuid4())


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_with_retry(query, attempts: int = 3, delay: float = 0.2):
    """Run a postgrest read, retrying the transport failure the client throws.

    Supabase's HTTP client keeps connections alive and the server closes them;
    the library surfaces that as ``httpx.RemoteProtocolError: Server disconnected``
    on a query that is perfectly correct. Measured against the live project: 4 of
    10 identical ``exams`` reads failed this way, which on screen is a dropdown
    that lists the exams one moment and is empty the next — with nothing anywhere
    to say why. The next attempt answers.

    Only *transport* errors are retried. ``postgrest.APIError`` is the server
    answering — a bad column, an RLS refusal — and asking again changes nothing.

    The last transport error is raised when every attempt fails, so a caller can
    still tell "no rows" from "could not ask".
    """
    last = None
    for attempt in range(attempts):
        try:
            return query()
        except httpx.TransportError as exc:
            last = exc
            if attempt + 1 < attempts:
                time.sleep(delay * (attempt + 1))
    raise last


def row_or_none(response):
    """Return ``response.data`` for a "fetch at most one row" query, or ``None``.

    postgrest's ``maybe_single().execute()`` returns ``None`` — not a response
    carrying ``data=None`` — when the query matches no row. Accessing ``.data``
    on that raises ``AttributeError``, so every such call site must go through
    here instead of touching ``.data`` directly.
    """
    return response.data if response is not None else None
