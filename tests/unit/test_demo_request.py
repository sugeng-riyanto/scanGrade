"""The demo form promised two things and did neither.

`POST /api/demo-request` wrote an `audit_logs` row and answered `{"success": true}`,
so a school that asked for a demo saw a green tick and the team found out only if
somebody thought to read an audit table. The landing page's own copy has always said
*"our team will give you a live demo"*, and the form has always looked like it told
them.

So these tests hold the two things a submit now does — mail the inbox that reads it,
and hand the visitor to WhatsApp with the request already written — plus the four
places this can silently stop being true: the number is national (`0812…` must become
`62812…` for a `wa.me` link), the message is built server-side (so a newline in a
school name cannot forge the rest of it), the inbox is resolved in a stated order
(a default that lands in an unread mailbox is the same defect one layer quieter), and
the audit row is written *before* the mail so a relay failure still leaves a lead.

The route is exercised through `app.test_client()` with the network edges patched:
the subject of the test is which shape each caller gets back, and that is only
visible in a real response.
"""
import html as html_mod
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

ROOT = Path(__file__).resolve().parents[2]

from app.services import demo_leads, email_bodies  # noqa: E402


# ── doubles: a database with no socket, and a relay that records ─────────────

class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, rows, log, name):
        self._rows = rows
        self._log = log
        self._name = name
        self._wanted = None

    def select(self, *cols):
        self._wanted = cols
        return self

    def eq(self, *args):
        return self

    def insert(self, row):
        self._log.append(("insert", self._name, row))
        return self

    def execute(self):
        self._log.append(("select", self._name, self._wanted))
        return _Result(self._rows)


class _DB:
    """`table(name)` → the canned rows for that name; inserts are recorded."""

    def __init__(self, tables=None, fail_insert=False):
        self.tables = tables or {}
        self.fail_insert = fail_insert
        self.log = []

    def table(self, name):
        q = _Query(self.tables.get(name, []), self.log, name)
        if self.fail_insert and name == "audit_logs":
            def boom(row):
                raise RuntimeError("supabase is down")
            q.insert = boom
        return q


class _Sent:
    """What `smtp_settings.send` was asked to do, or None."""

    def __init__(self, ok=True, error=None):
        self.calls = []
        self.ok = ok
        self.error = error

    def __call__(self, to_email, subject, body, html=False, text=None,
                 important=False, reply_to=None):
        self.calls.append({"to": to_email, "subject": subject, "html": body,
                           "text": text, "reply_to": reply_to})
        return self.ok, self.error


@pytest.fixture
def sent(monkeypatch):
    """A relay that records, and a mailbox that is configured unless a test says not."""
    recorder = _Sent()
    monkeypatch.setattr(demo_leads.smtp_settings, "send", recorder)
    monkeypatch.setattr(demo_leads.smtp_settings, "resolve",
                        lambda: {"user": "team@scan-grade.app",
                                 "reply_to": "noreply@scan-grade.app",
                                 "configured": True})
    return recorder


# ── the message the visitor is about to send ─────────────────────────────────

class TestThePrefilledMessage:

    def test_it_carries_what_the_school_typed(self):
        text = demo_leads.message("SMPN 1 Bandung", "kepsek@smpn1.sch.id", "0812 3456")
        assert "SMPN 1 Bandung" in text
        assert "kepsek@smpn1.sch.id" in text
        assert "0812 3456" in text

    def test_it_says_where_it_came_from(self):
        """The receiver has one line of context: this arrived from the site."""
        assert "scangrade.web.id" in demo_leads.message("SMA 3")

    def test_a_newline_in_a_name_cannot_add_a_line(self):
        """A name is a value, and a value must not be able to read as copy.

        The school name is the one field of the three that is free text, and the
        message is a sequence of lines: a name carrying `\\n` would otherwise append
        a line that looks like part of the request.
        """
        text = demo_leads.message("SMP\nN 1\tBandung")
        assert "\n" not in text.split("Sekolah / School: ")[1].split("\n")[0]
        assert "SMP N 1 Bandung" in text

    def test_an_empty_optional_field_is_omitted_rather_than_left_blank(self):
        text = demo_leads.message("SMA 3", "a@b.c")
        assert "WhatsApp:" not in text


class TestTheWhatsAppLink:

    def test_a_national_number_is_dialled_with_a_country_code(self, monkeypatch):
        """`0812…` is how people write it; `wa.me` answers nothing without `62`."""
        db = _DB({"school_settings": [{"whatsapp_number": "0812-3456-7890"}]})
        monkeypatch.setattr(demo_leads, "_supabase", lambda: db)
        assert demo_leads.whatsapp_number() == "6281234567890"

    def test_an_international_number_is_left_alone(self, monkeypatch):
        db = _DB({"school_settings": [{"whatsapp_number": "+62 812 3456 7890"}]})
        monkeypatch.setattr(demo_leads, "_supabase", lambda: db)
        assert demo_leads.whatsapp_number() == "6281234567890"

    def test_no_number_means_no_link(self, monkeypatch):
        """Nothing to send them to — the route then keeps them on the form."""
        monkeypatch.setattr(demo_leads, "_supabase", lambda: _DB({}))
        assert demo_leads.whatsapp_url("SMA 3") == ""

    def test_an_unreadable_row_is_no_link_rather_than_a_500(self, monkeypatch):
        class Broken:
            def table(self, name):
                raise RuntimeError("supabase is down")
        monkeypatch.setattr(demo_leads, "_supabase", lambda: Broken())
        assert demo_leads.whatsapp_number() == ""

    def test_the_link_encodes_the_whole_message(self, monkeypatch):
        db = _DB({"school_settings": [{"whatsapp_number": "081234567890"}]})
        monkeypatch.setattr(demo_leads, "_supabase", lambda: db)
        url = demo_leads.whatsapp_url("SMP 1 & 2", "a@b.c")
        parsed = urlparse(url)
        assert parsed.netloc == "api.whatsapp.com"
        assert parsed.path == "/send"
        query = parse_qs(parsed.query)
        assert query["phone"] == ["6281234567890"]
        # `&` in a school name must survive as data, not split the query
        assert "SMP 1 & 2" in query["text"][0]


# ── the inbox that reads it ──────────────────────────────────────────────────

class TestTheInbox:

    def test_a_stored_address_wins(self, monkeypatch, sent):
        monkeypatch.setattr(demo_leads, "_setting", lambda key: "leads@scan-grade.app")
        assert demo_leads.inbox() == "leads@scan-grade.app"

    def test_the_mailbox_that_sends_is_the_fallback(self, monkeypatch, sent):
        """Demonstrably watched: the deploy alerts already land there."""
        monkeypatch.setattr(demo_leads, "_setting", lambda key: "")
        assert demo_leads.inbox() == "team@scan-grade.app"

    def test_the_noreply_address_is_the_last_resort(self, monkeypatch, sent):
        monkeypatch.setattr(demo_leads, "_setting", lambda key: "")
        monkeypatch.setattr(demo_leads.smtp_settings, "resolve",
                            lambda: {"user": "", "reply_to": "noreply@scan-grade.app",
                                     "configured": True})
        assert demo_leads.inbox() == "noreply@scan-grade.app"

    def test_nothing_resolved_is_an_empty_string(self, monkeypatch, sent):
        monkeypatch.setattr(demo_leads, "_setting", lambda key: "")
        monkeypatch.setattr(demo_leads.smtp_settings, "resolve",
                            lambda: {"user": "", "reply_to": "", "configured": False})
        assert demo_leads.inbox() == ""

    def test_both_readers_of_the_contact_row_name_the_same_row(self):
        """The registration page and this link must not be able to disagree.

        `get_whatsapp_number` (a Jinja global) and `demo_leads` are two readers of one
        value. The display wants it as typed and the link wants digits, so there are
        two functions — but one table and one row, named here from both sides.
        """
        global_src = (ROOT / "app" / "__init__.py").read_text(encoding="utf-8")
        block = global_src.split("def get_whatsapp_number", 1)[1].split("jinja_env.globals", 1)[0]
        assert 'school_settings' in block and '.eq("id", 1)' in block
        assert demo_leads.WHATSAPP_TABLE == "school_settings"
        assert demo_leads.WHATSAPP_ROW == 1


# ── the four outcomes of one send ───────────────────────────────────────────

class TestNotify:

    def test_a_success_reports_who_was_told(self, monkeypatch, sent):
        monkeypatch.setattr(demo_leads, "inbox", lambda: "team@scan-grade.app")
        out = demo_leads.notify("SMPN 1", "kepsek@smpn1.sch.id", "0812")
        assert out == {"sent": True, "to": "team@scan-grade.app", "why": None}
        assert sent.calls and sent.calls[0]["to"] == "team@scan-grade.app"

    def test_the_schools_address_is_the_reply_to(self, monkeypatch, sent):
        """Answering is one press, not a lookup. This is the reason for the header."""
        monkeypatch.setattr(demo_leads, "inbox", lambda: "team@scan-grade.app")
        demo_leads.notify("SMPN 1", "kepsek@smpn1.sch.id", "")
        assert sent.calls[0]["reply_to"] == "kepsek@smpn1.sch.id"

    def test_no_inbox_is_its_own_answer(self, monkeypatch, sent):
        monkeypatch.setattr(demo_leads, "inbox", lambda: "")
        out = demo_leads.notify("SMPN 1", "a@b.c")
        assert out["sent"] is False and out["why"] == "no_inbox"
        assert sent.calls == [], "no mail can be built without a recipient"

    def test_no_credential_is_not_the_same_as_a_refused_relay(self, monkeypatch):
        monkeypatch.setattr(demo_leads, "inbox", lambda: "team@scan-grade.app")
        monkeypatch.setattr(demo_leads.smtp_settings, "send",
                            lambda *a, **k: (False, "no credential"))
        monkeypatch.setattr(demo_leads.smtp_settings, "resolve",
                            lambda: {"configured": False})
        out = demo_leads.notify("SMPN 1", "a@b.c")
        assert out["why"] == "not_configured"

    def test_a_refused_relay_carries_its_reason(self, monkeypatch):
        monkeypatch.setattr(demo_leads, "inbox", lambda: "team@scan-grade.app")
        monkeypatch.setattr(demo_leads.smtp_settings, "send",
                            lambda *a, **k: (False, "535 auth failed"))
        monkeypatch.setattr(demo_leads.smtp_settings, "resolve",
                            lambda: {"configured": True})
        out = demo_leads.notify("SMPN 1", "a@b.c")
        assert out["why"] == "send_failed" and out["detail"] == "535 auth failed"


# ── the mail the team reads ─────────────────────────────────────────────────

class TestTheBody:

    def build(self):
        return email_bodies.demo_request(
            school_name="SMPN 1 Bandung", email="kepsek@smpn1.sch.id",
            phone="0812 3456", message="Kami ingin demo untuk 600 murid",
            submitted_at="17 Okt 2026 09:30 WIB")

    def test_the_subject_names_the_school(self):
        out = self.build()
        assert "SMPN 1 Bandung" in out["subject"] and "ScanGrade" in out["subject"]

    def test_both_halves_are_content(self):
        out = self.build()
        assert "meminta demo" in out["html"] and "asked for a ScanGrade" in out["html"]
        assert "kepsek@smpn1.sch.id" in out["html"] and "0812 3456" in out["html"]
        assert "Kami ingin demo" in out["html"]

    def test_the_text_half_carries_the_same_facts(self):
        out = self.build()
        for fact in ("SMPN 1 Bandung", "kepsek@smpn1.sch.id", "0812 3456",
                     "600 murid", "ScanGrade"):
            assert fact in out["text"]

    def test_a_name_is_escaped_in_the_html_half(self):
        out = email_bodies.demo_request(school_name="SMA <b>1</b>", email="a@b.c")
        assert "<b>1</b>" not in out["html"]
        assert "&lt;b&gt;" in out["html"]


# ── the route: two callers, two shapes ──────────────────────────────────────

#: The app's `before_request` refuses a POST without a token, so a test that skips
#: this is not testing the route — it is testing the refusal.
TOKEN = "demo-request-token"


def _client(app):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_csrf_token"] = TOKEN
    return client


def _post(app, **fields):
    fields.setdefault("_csrf_token", TOKEN)
    return _client(app).post("/api/demo-request", data=fields,
                             follow_redirects=False)


def _post_json(app, payload):
    return _client(app).post("/api/demo-request", json=payload,
                             headers={"X-CSRF-Token": TOKEN})


class TestTheRoute:

    @pytest.fixture
    def wired(self, monkeypatch, app):
        """The route with its three network edges patched, and the calls recorded."""
        from app.routes import public

        state = {"notified": [], "db": _DB({"audit_logs": []})}
        monkeypatch.setattr(public, "get_supabase", lambda: state["db"])

        def notify(*args, **kwargs):
            state["notified"].append((args, kwargs, list(state["db"].log)))
            return {"sent": True, "to": "team@scan-grade.app", "why": None}

        monkeypatch.setattr(demo_leads, "notify", notify)
        return state

    def test_a_form_post_goes_straight_to_whatsapp(self, wired, monkeypatch, app):
        monkeypatch.setattr(demo_leads, "whatsapp_url",
                            lambda *a, **k: "https://api.whatsapp.com/send?phone=62812")
        res = _post(app, school_name="SMPN 1", email="a@b.c", phone="0812")
        assert res.status_code == 302
        assert res.headers["Location"] == "https://api.whatsapp.com/send?phone=62812"

    def test_without_a_number_the_visitor_lands_back_on_the_form(
            self, wired, monkeypatch, app):
        monkeypatch.setattr(demo_leads, "whatsapp_url", lambda *a, **k: "")
        res = _post(app, school_name="SMPN 1", email="a@b.c")
        assert res.status_code == 302
        assert res.headers["Location"] == "/?demo=sent#demo"

    def test_an_invalid_form_comes_back_with_its_reason(self, wired, app):
        res = _post(app, school_name="", email="not-an-address")
        assert res.status_code == 302
        assert res.headers["Location"] == "/?demo=invalid"
        assert wired["notified"] == [], "an invalid request must not mail anybody"

    def test_the_json_caller_gets_the_link_and_whether_the_team_was_told(
            self, wired, monkeypatch, app):
        """The demo page's own client, and the one shape an API caller needs."""
        monkeypatch.setattr(demo_leads, "whatsapp_url", lambda *a, **k: "https://x.test/wa")
        res = _post_json(app, {"school_name": "SMPN 1", "email": "a@b.c"})
        assert res.status_code == 200
        assert res.get_json() == {
            "success": True, "emailed": True,
            "message": "Terima kasih! Tim kami akan menghubungi Anda.",
            "whatsapp_url": "https://x.test/wa",
        }

    def test_an_invalid_json_request_answers_in_json(self, wired, app):
        res = _post_json(app, {"email": "a@b.c"})
        assert res.status_code == 400
        assert res.get_json()["success"] is False
        assert "sekolah" in res.get_json()["message"].lower()

    def test_the_request_is_recorded_before_the_mail_is_built(self, wired, app):
        """"The relay cannot send" must not mean "the lead is gone"."""
        _post(app, school_name="SMPN 1", email="a@b.c", phone="0812")
        assert len(wired["notified"]) == 1
        _, _, log_at_notify = wired["notified"][0]
        assert any(entry[0] == "insert" and entry[1] == "audit_logs"
                   for entry in log_at_notify), (
            "the audit row has to exist by the time the mail is attempted")

    def test_a_lost_audit_row_does_not_lose_the_lead(self, monkeypatch, sent, app):
        from app.routes import public
        state = {"notified": []}
        monkeypatch.setattr(public, "get_supabase", lambda: _DB(fail_insert=True))
        monkeypatch.setattr(demo_leads, "notify",
                            lambda *a, **k: state["notified"].append(1) or
                            {"sent": True, "to": "team@scan-grade.app", "why": None})
        monkeypatch.setattr(demo_leads, "whatsapp_url", lambda *a, **k: "")
        res = _post(app, school_name="SMPN 1", email="a@b.c")
        assert res.status_code == 302
        assert state["notified"] == [1]

    def test_the_endpoint_is_rate_limited(self):
        """It sends mail: an unthrottled loop fills somebody else's inbox."""
        text = (ROOT / "app" / "routes" / "public.py").read_text(encoding="utf-8")
        block = text.split('@public_bp.route("/api/demo-request"', 1)[1]
        block = block.split("def demo_request", 1)[0]
        assert "_rate_limit(" in block, (
            "the demo-request endpoint lost its limit — it is the one public route "
            "that sends mail on every POST")


# ── the form, as the visitor gets it ────────────────────────────────────────

def render_landing(app, query: str = "") -> str:
    from flask import g
    with app.test_request_context("/" + query):
        g.user = None
        return html_mod.unescape(
            app.jinja_env.get_template("landing.html").render(away_grace_seconds=10))


class TestTheForm:

    def test_it_posts_to_the_route_that_does_the_work(self, app):
        body = render_landing(app)
        assert re.search(r'<form id="demo-form" method="POST" action="/api/demo-request"', body), (
            "the form must be a plain POST: an async handler cannot open a tab any "
            "more, so the navigation itself has to carry the request")

    def test_it_carries_the_csrf_token_itself(self, app):
        """Not left to the DOMContentLoaded script: the refusal is a 403.

        base.html adds a token to every POST form on DOMContentLoaded, but the app
        enforces CSRF in a global `before_request` — so a submit that happens before
        that script runs is a lead answered with a 403. The hidden field is written
        here for the same reason the app's own forms write one.
        """
        form = render_landing(app).split('<form id="demo-form"', 1)[1].split("</form>", 1)[0]
        assert 'name="_csrf_token"' in form and form.count('name="_csrf_token"') == 1

    @pytest.mark.parametrize("field", ("school_name", "email", "phone"))
    def test_the_three_fields_the_route_reads_are_the_three_it_posts(self, app, field):
        """A renamed input is a field the route silently sees as empty."""
        body = render_landing(app)
        form = body.split('<form id="demo-form"', 1)[1].split("</form>", 1)[0]
        assert f'name="{field}"' in form

    def test_the_submit_button_and_its_halves(self, app):
        body = render_landing(app)
        assert "Minta Demo" in body and "Request a Demo" in body

    def test_the_anchor_the_redirect_returns_to_exists(self, app):
        """`/?demo=sent#demo` is a promise about an element."""
        assert 'id="demo"' in render_landing(app)

    def test_the_sent_state_is_there_when_the_redirect_says_so(self, app):
        body = render_landing(app, "?demo=sent")
        assert "already reached our team" in body
        assert "sudah terkirim ke tim kami" in body

    def test_the_refused_state_is_there_too(self, app):
        body = render_landing(app, "?demo=invalid")
        assert "wajib diisi" in body and "are required" in body

    def test_neither_state_is_printed_on_a_plain_visit(self, app):
        body = render_landing(app)
        assert "already reached our team" not in body
        assert "are required" not in body

    def test_the_old_fetch_handler_is_gone(self, app):
        """Two submit paths is how the form stops telling anybody anything.

        The removed `<script>` posted JSON and swapped in a success box; the route it
        posted to answers a *redirect* now, so a second handler would leave the
        visitor on a page whose form had quietly disappeared.
        """
        body = render_landing(app)
        assert "submitDemo" not in body
        assert "demo-success" not in body
