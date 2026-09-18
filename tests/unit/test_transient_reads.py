"""A read that fails at random, and a page that used to hide it.

Supabase's HTTP client keeps connections alive and the server closes them; the
library surfaces that as ``httpx.RemoteProtocolError: Server disconnected`` on a
query that is perfectly correct. Measured against the live project — ten
identical ``exams`` reads in a row:

    rows 5 · rows 5 · **failed** · rows 5 · rows 5 · **failed** · **failed** ·
    rows 5 · **failed** · rows 5

Four of ten. On the OMR test bench that read fills the batch dropdown, behind a
bare ``except Exception: pass``, so one run in three showed a select holding only
"— Tanpa grading —" and the operator had no way to tell *this bench has no exams*
from *this bench could not ask*.

Two rules now: the read is retried, because the failure is the transport and not
the query; and when it still fails the route passes ``None`` instead of ``[]`` so
the page can say so. An empty list is data; ``None`` is an unanswered question.
"""
import re
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from postgrest.exceptions import APIError

from app.utils.helpers import read_with_retry

ROOT = Path(__file__).resolve().parents[2]
ROUTE = ROOT / "app" / "routes" / "super_admin.py"
TEMPLATE = ROOT / "app" / "templates" / "super_admin" / "omr_test.html"


def _disconnected():
    return httpx.RemoteProtocolError("Server disconnected")


# ── the helper ────────────────────────────────────────────────

def test_a_transient_disconnect_is_retried_and_the_answer_returned():
    calls = []

    def query():
        calls.append(1)
        if len(calls) == 1:
            raise _disconnected()
        return "the rows"

    assert read_with_retry(query, delay=0) == "the rows"
    assert len(calls) == 2, "the first transport failure was not retried"


def test_a_persistent_failure_still_raises_so_a_caller_can_tell(tmp_path):
    """Retrying must not swallow the failure: a caller that cares still can.

    `None` (no rows) and a raised error (no answer) are different facts, and the
    OMR page's notice depends on the difference.
    """
    calls = []

    def query():
        calls.append(1)
        raise _disconnected()

    with pytest.raises(httpx.TransportError):
        read_with_retry(query, attempts=3, delay=0)
    assert len(calls) == 3, f"expected three attempts, made {len(calls)}"


def test_the_server_answering_is_not_retried():
    """`APIError` is a reply — a bad column, an RLS refusal, a 404 row.

    Asking again changes nothing and costs a second round trip, so a real error
    must surface on the first attempt.
    """
    calls = []

    def query():
        calls.append(1)
        raise APIError({"message": "column exams.title does not exist"})

    with pytest.raises(APIError):
        read_with_retry(query, attempts=3, delay=0)
    assert len(calls) == 1, "a server-side error was retried"


def test_the_retry_waits_between_attempts(monkeypatch):
    """An immediate re-ask is on the same closed connection half the time."""
    slept = []
    monkeypatch.setattr("app.utils.helpers.time.sleep", lambda s: slept.append(s))
    calls = []

    def query():
        calls.append(1)
        if len(calls) < 3:
            raise _disconnected()
        return "ok"

    assert read_with_retry(query, attempts=3, delay=0.2) == "ok"
    assert slept == [0.2, 0.4], (
        f"expected a growing wait between attempts, slept {slept}")


def test_a_result_carrying_no_attempt_is_still_returned():
    """The happy path is untouched — this wrapper must not change a good read."""
    sentinel = MagicMock()
    assert read_with_retry(lambda: sentinel, delay=0) is sentinel


# ── the page: the read is retried, and the failure is visible ──

def _view_body():
    src = ROUTE.read_text(encoding="utf-8")
    body = src.split("def omr_test_page", 1)[1].split("\n@super_bp.route", 1)[0]
    assert body, "the omr-test view is gone"
    return body


def test_the_bench_retries_the_list_it_renders():
    body = _view_body()
    assert "read_with_retry(" in body, (
        "the exam list is read once and once only, so a transient "
        "`Server disconnected` empties the dropdown again")


def test_a_failed_read_is_not_reported_as_an_empty_list():
    """`[]` says 'there are no exams'. `None` says 'the read failed'."""
    body = _view_body()
    except_branch = body.split("except Exception", 1)[1]
    assert re.search(r"exams\s*=\s*None", except_branch), (
        "the failure branch still leaves an empty list, which the page cannot "
        "distinguish from a school with no exams")
    assert not re.search(r"exams\s*=\s*\[\s*\]", except_branch), (
        "the failure branch assigns an empty list: an unanswered read and no "
        "data render identically")


def test_the_page_says_so_when_the_list_could_not_be_read():
    body = TEMPLATE.read_text(encoding="utf-8")
    # Jinja's test is lowercase `none`. `is None` is a Python-looking spelling
    # that raises `TemplateRuntimeError: No test named 'None'` — a 500 on the
    # page, which is how the first version of this notice shipped for a minute.
    assert "{% if exams is none %}" in body, (
        "the notice is not guarded by Jinja's `none` test — `is None` is a 500, "
        "not a false")
    notice = re.search(r"\{%-?\s*if exams is none\s*-?%\}(.*?)\{%-?\s*endif\s*-?%\}",
                       body, re.S)
    assert notice, (
        "the page renders an empty dropdown without a word, which is the silent "
        "failure the exam list was fixed to stop being")
    visible = re.sub(r"<!--.*?-->", " ", notice.group(1), flags=re.S)
    assert re.search(r">\s*\S[^<]*<", visible), (
        "the `exams is None` branch paints no text, so nothing is actually said")


def test_the_dropdown_still_renders_when_the_list_is_unknown():
    """`None` must not break the select: `for` over a missing list is a TypeError
    in Jinja, and a traceback is worse than an empty dropdown."""
    body = TEMPLATE.read_text(encoding="utf-8")
    assert "{% for e in exams or [] %}" in body, (
        "the select iterates `exams` directly, so `None` raises instead of "
        "rendering the placeholder option")
