"""The exam builder's PDF fetch must hear a refusal in a language it reads.

`/teacher/exams/new` parses the reply to `POST /teacher/exams/parse-pdf` as JSON.
When a teacher's session has ended, the role guard's default refusal is a **302 to the
login page** — the fetch follows it, gets HTML, and `r.json()` throws the sentence the
teacher actually saw on production:

    Could not process the PDF
    Failed: Unexpected token '<', "<!DOCTYPE "... is not valid JSON

The guard already answers JSON for a caller that says it reads JSON
(`auth._wants_json`). These tests hold the two halves of the fix in place: the route
*does* answer JSON when the request carries `Accept: application/json`, and the
builder's two JSON fetches *do* carry it and read the body tolerantly.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "app" / "templates" / "teacher" / "exam_form.html"


# ── the route half ──────────────────────────────────────────────────────────

def _csrf_client(app):
    """A client past the CSRF wall, but with **no** session cookie — the state a
    teacher is in when their session has ended mid-edit."""
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_csrf_token"] = "test-csrf-token"
    client.environ_base["HTTP_X_CSRF_TOKEN"] = "test-csrf-token"
    return client


def test_a_dead_session_answering_a_json_reader_gets_json(app):
    """The refusal a teacher's expired session actually receives.

    No token cookie at all, and the request says it reads JSON — which is what the
    builder's `fetch` now sends. A 401 with an `error` string is what the page can
    turn into a sentence; an HTML login page is what it cannot.
    """
    client = _csrf_client(app)
    resp = client.post("/teacher/exams/parse-pdf",
                       headers={"Accept": "application/json"})

    assert resp.status_code == 401, resp.status_code
    assert resp.mimetype == "application/json", resp.mimetype
    body = resp.get_json()
    assert body and isinstance(body.get("error"), str) and body["error"], (
        "the refusal carried no sentence the page could show", body)
    assert "<!DOCTYPE" not in resp.get_data(as_text=True), (
        "a JSON reader was handed the login page's HTML")


def test_the_same_dead_session_without_the_header_still_redirects(app):
    """The *other* half of `_wants_json`: an ordinary browser navigation to the same
    route must keep getting the login page, not a JSON body it would render raw. This
    is what makes the header the fix rather than a change to the guard."""
    client = _csrf_client(app)
    resp = client.post("/teacher/exams/parse-pdf")

    assert resp.status_code == 302, resp.status_code
    assert "/auth/login" in resp.headers["Location"]


# ── the template half ───────────────────────────────────────────────────────

def _template() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def test_the_upload_fetch_says_it_reads_json():
    """The route only answers JSON because the request advertises it. Drop the header
    and the expired-session bug returns unchanged."""
    text = _template()
    upload = text.split("fetch('/teacher/exams/parse-pdf'", 1)[1].split("await", 1)[0]
    assert "'Accept': 'application/json'" in upload, (
        "the parse-pdf fetch stopped advertising that it reads JSON — an expired "
        "session will answer HTML again")


def test_both_json_fetches_read_the_body_tolerantly():
    """`.json()` on the reply is the failure mode itself: it throws the parser's
    sentence, naming `<!DOCTYPE` rather than the problem. Both fetches whose reply the
    page reads as an object go through `sgJsonReply`."""
    text = _template()
    assert "function sgJsonReply(" in text, "the tolerant reader is gone"
    for call in ("fetch('/teacher/exams/parse-pdf'", "fetch('/teacher/exams/generate-key'"):
        # The reply is read a few statements after the fetch, so the window is the
        # stretch until the next bracketed function — not the fetch statement alone.
        segment = text.split(call, 1)[1][:600]
        assert "sgJsonReply(" in segment, (
            f"the fetch at {call!r} no longer reads its reply through sgJsonReply")


def test_the_tolerant_reader_has_sentences_for_the_statuses_it_will_meet():
    """A refusal with no body of its own still has to give the teacher something to do.
    The two statuses this page really produces are 401 (session gone) and 413 (the PDF
    over the 50MB limit — the framework's own HTML error page)."""
    text = _template()
    reader = text.split("function sgHttpReason", 1)[1].split("function sgPct", 1)[0]
    assert "401" in reader and "413" in reader, (
        "sgHttpReason no longer names the two statuses this page produces")
    assert "'<!DOCTYPE'" not in reader and "Unexpected token" not in reader, (
        "the reader's copy quotes the parser error it exists to replace")
