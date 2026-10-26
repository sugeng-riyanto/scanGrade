"""Print the host of a URL, never the URL.

The restore banner in `deploy/db_snapshot.py` was the first of these: it announced
the archive and then wrote rows, without ever naming the database it was writing
into. The fix there was to print the target — and the moment a target is printed,
the question becomes *what* of it, because it arrives as a URL from `.env` or from
an operator's command line, and a URL can carry a password.

`postgresql://postgres:<password>@db.example.edu:5432/postgres` is how a DSN is
written, not how it is printed: those tools write their output into a terminal, a
deploy journal and CI logs, and an operator pastes the refusal into a ticket. Any
of those is a copy of the credential the tool was only trying to identify.

So the rule this module implements: **a tool may name the host, and nothing else.**
Scheme, host and port — no userinfo, no path, no query, no fragment. A value it
cannot parse is reported as unparseable rather than echoed, because the fallback
is the one place where the raw string would go out.

    >>> host_only("postgresql://postgres:pw@db.example.edu:5432/postgres")
    'postgresql://db.example.edu:5432'
    >>> host_only("https://<user>:<password>@scan-grade.app/login?next=/x")
    'https://scan-grade.app'
    >>> host_only("")
    '(not set)'
"""
from __future__ import annotations

from urllib.parse import urlsplit

#: What a missing value prints as. A caller that has its own word for absence
#: (`'(missing)'` beside four other `(missing)` lines) passes it in.
NOT_SET = "(not set)"

#: What a value that is not a URL prints as. Deliberately not the value: an
#: unparseable string may be unparseable *because* it carries something odd.
UNPARSEABLE = "(unparseable URL)"


def host_only(raw: str | None, missing: str = NOT_SET) -> str:
    """`scheme://host[:port]` from a URL, or a placeholder — never the input.

    The userinfo is dropped rather than masked (`user:***@host` still names the
    account, and a password containing `@` would leave its tail visible), and so is
    everything after the host, which is where a DSN keeps its database and a link
    keeps its `?token=`.

    A bare `host:5432` with no scheme is accepted, because that is also how an
    operator writes a pooler address, and a URL without a scheme has no userinfo
    section to leak.
    """
    text = (raw or "").strip()
    if not text:
        return missing
    # One parse, and `//` only when the value carries no scheme of its own. Trying
    # the prefixed form as a *fallback* turned `https://` into the host `https`,
    # which is a placeholder-shaped value reported as a real host.
    candidate = text if "://" in text else "//" + text
    try:
        parts = urlsplit(candidate)
        host = parts.hostname
        port = parts.port
    except ValueError:
        # An invalid port (`host:99999`) or a malformed IPv6 literal raises.
        return UNPARSEABLE
    if not host:
        return UNPARSEABLE
    netloc = host if port is None else f"{host}:{port}"
    return f"{parts.scheme}://{netloc}" if parts.scheme else netloc
