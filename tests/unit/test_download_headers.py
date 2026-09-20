"""A downloaded file must declare its type once.

Why this exists
---------------
The live response for the statistics CSV read `Content-Type: text/csv;
charset=utf-8; charset=utf-8`. Nobody sees that on screen, and every parser takes
the first `charset` and carries on — it is here because the second one is the
fingerprint of a route saying something `send_file` already says for it, and
because the same line had been written in three places while only one of them was
ever looked at.

`send_file` appends the charset to a `text/*` mimetype on its own, so a route that
writes `text/csv` plus its own charset gets both halves. The sweep is over every
route rather than the one that was wrong: the mistake is a habit, not an incident.
"""
import re
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def app():
    from app import create_app
    return create_app("app.config.TestingConfig")

ROOT = Path(__file__).resolve().parents[2]
ROUTES = ROOT / "app" / "routes"

#: `mimetype="<something text/…>"` carrying its own charset.
DOUBLE_CHARSET = re.compile(
    r"""mimetype\s*=\s*["'](?P<mime>text/[^"']*?;?\s*charset\s*=\s*[a-zA-Z0-9-]+)["']""")


def _code(path):
    """A route file without its comments.

    A comment that names the bad shape — "not `text/csv; charset=utf-8`, which
    Flask would finish twice" — is an explanation, not a header, and a test that
    cannot tell them apart deletes the explanation. Line-based on purpose: a `#`
    inside a string is left alone, and these files do not carry one.
    """
    return "\n".join(line for line in path.read_text(encoding="utf-8",
                                                    errors="replace").splitlines()
                     if not line.lstrip().startswith("#"))


def _route_files():
    return sorted(path for path in ROUTES.glob("*.py") if path.name != "__init__.py")


@pytest.mark.parametrize("path", _route_files(), ids=lambda p: p.name)
def test_no_route_names_a_charset_flask_adds_itself(path):
    offenders = [match.group("mime") for match in DOUBLE_CHARSET.finditer(_code(path))]
    assert not offenders, (
        f"{path.name} names a charset Flask will add itself: {offenders}. "
        f"Write the bare mimetype — `mimetype=\"text/csv\"` — and let `send_file` "
        f"append `; charset=utf-8` once."
    )


def test_the_sweep_can_see_the_mistake_it_is_looking_for():
    """A pattern that matches nothing passes everything, including the defect."""
    assert DOUBLE_CHARSET.search('send_file(f, mimetype="text/csv; charset=utf-8")')
    assert DOUBLE_CHARSET.search('send_file(f, mimetype="text/plain;charset=utf-8")')
    # The fixed shape, and the shapes that were never wrong.
    assert not DOUBLE_CHARSET.search('send_file(f, mimetype="text/csv")')
    assert not DOUBLE_CHARSET.search('mimetype="application/pdf"')
    assert not DOUBLE_CHARSET.search('mimetype="application/vnd.ms-excel"')


def test_flask_is_the_one_that_adds_the_charset(app):
    """The mechanism the fix leans on, measured rather than assumed.

    A bare `text/csv` comes back with exactly one `charset`; the mimetype that
    names its own gets a second one appended. If Flask ever stops doing this, the
    sweep above would keep passing while the routes lost their charset — so the
    assumption is a test and not a comment.
    """
    import io

    from flask import helpers

    with app.test_request_context("/"):
        bare = helpers.send_file(io.BytesIO(b"a,b\n"), mimetype="text/csv")
        doubled = helpers.send_file(io.BytesIO(b"a,b\n"),
                                    mimetype="text/csv; charset=utf-8")
    assert bare.headers["Content-Type"].count("charset") == 1
    assert bare.headers["Content-Type"] == "text/csv; charset=utf-8"
    assert doubled.headers["Content-Type"].count("charset") == 2, (
        "Flask stopped appending the charset, so the bare mimetype is no longer "
        "equivalent — revisit the routes rather than this assertion")


def test_the_csv_routes_send_utf8_exactly_once():
    """The property, not the pattern: both CSV downloads of a report.

    Each writes a BOM, so the file has to be declared UTF-8 — the point is that it
    is declared once, and that Flask is the one declaring it.
    """
    code = _code(ROUTES / "teacher.py")
    assert code.count('mimetype="text/csv"') >= 2, (
        "a CSV download route lost its bare text/csv mimetype")
    assert "text/csv; charset=utf-8" not in code
