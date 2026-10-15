"""A teacher who belongs to two NPSNs can choose which one they are working in.

The keystone this file is about was **missing**, and missing the quiet way: every
part of the feature existed except the one that makes it reachable.

* `app.utils.auth` resolves the school of every request from the verified active
  membership (`school_membership.resolve_for_request`), and that resolution honours a
  per-token choice (`get_active_school`) — re-checked against an active row on every
  request, dropped when the row is gone, and carried onto the new token when the
  session is refreshed.
* `school_membership.set_active_school` stores that choice, server-side, keyed by the
  session token.

What did not exist was a **caller**: nothing in the app ever wrote a choice, and
nothing let a teacher make one. So a teacher whose second NPSN had accepted them
stayed resolved into the school their profile names — the destination school could
not find them on its roster, could not give them a class, and their papers never
followed the school that had accepted them. Being treated as acting elsewhere
required *leaving* the home school, which is what this feature exists to stop being
necessary.

What is pinned here:

* **the reader** (`switchable_schools`) — the schools this account may work in, from
  the same memoised membership read the request's own resolution already made, with
  the empty payload for a reader who has nothing to choose and for a read that failed;
* **the door** (`POST /teacher/school/switch`) — the only writer of the choice, and
  the two refusals that make a form a claim rather than an authority: a school the
  caller is not an **active** member of is refused, and `home` *clears* the choice
  instead of naming a school;
* **the chrome** — the control, rendered by the context processor, which reads nothing
  at all for a pupil or a school admin.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import g
from werkzeug.exceptions import HTTPException

from app.services import school_membership as membership

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "app" / "services" / "school_membership.py"
DOOR = ROOT / "app" / "routes" / "membership_routes.py"
INIT = ROOT / "app" / "__init__.py"
BASE = ROOT / "app" / "templates" / "base.html"

TEACHER, OTHER = "sc-A", "sc-B"


# ── fakes ────────────────────────────────────────────────────────────────────

class _Query:
    """One `teacher_school_membership` read, filtered the way PostgREST is."""

    def __init__(self, rows, fail=False):
        self.rows = list(rows)
        self.fail = fail

    def select(self, *a, **k):
        if self.fail:
            raise RuntimeError("connection reset by peer")
        return self

    def eq(self, col, val):
        self.rows = [r for r in self.rows if str(r.get(col)) == str(val)]
        return self

    def execute(self):
        if self.fail:
            raise RuntimeError("connection reset by peer")
        return SimpleNamespace(data=self.rows)


class _Sb:
    def __init__(self, rows=None, fail=False):
        self.rows = list(rows or [])
        self.fail = fail
        self.reads = 0

    def table(self, name):
        assert name == "teacher_school_membership", name
        self.reads += 1
        return _Query(list(self.rows), fail=self.fail)


def _rows(*pairs):
    """`("sc-A", "active"), ("sc-B", "closed")` → membership rows."""
    return [{"user_id": "u-1", "school_id": school, "status": status}
            for school, status in pairs]


def _no_reads(monkeypatch):
    """Fail the test if the membership rows are read at all."""
    def _boom(*a, **k):
        pytest.fail("a reader with one school must not read the membership table")

    monkeypatch.setattr(membership, "_rows_for_request", _boom)


def _names(monkeypatch, mapping):
    """The per-school row cache, without a database."""
    from app.utils import req_cache
    monkeypatch.setattr(req_cache, "school_row",
                        lambda school_id, columns="name": {"name": mapping.get(school_id, "")})


def _choice(monkeypatch, chosen):
    monkeypatch.setattr(membership, "get_active_school", lambda token: chosen)


# ── the reader: what may this account switch between? ────────────────────────

def test_a_pupil_or_a_school_admin_reads_nothing_at_all(monkeypatch):
    """The chrome is on every page, so the partition is asked before any query."""
    _no_reads(monkeypatch)
    for role in ("murid", "admin_sekolah", "super_admin"):
        out = membership.switchable_schools("u-1", "tok", role, active_school_id=TEACHER)
        assert out == {"schools": [], "away": False}, role


def test_no_signed_in_account_answers_the_empty_payload(monkeypatch):
    _no_reads(monkeypatch)
    assert membership.switchable_schools(None, None, "guru") == {"schools": [], "away": False}


def test_one_school_and_in_it_is_nothing_to_choose(monkeypatch):
    """The control is not rendered, so a teacher with one NPSN sees no header clutter."""
    _choice(monkeypatch, None)
    monkeypatch.setattr(membership, "_rows_for_request",
                        lambda user_id, supabase=None: _rows((TEACHER, "active")))
    out = membership.switchable_schools("u-1", "tok", "guru", active_school_id=TEACHER)
    assert out == {"schools": [], "away": False}


def test_two_active_memberships_are_both_offered(monkeypatch):
    """The rows arrive in one order and the control shows another, on purpose.

    The membership read is unordered and PostgREST promises nothing about the order
    it answers in, so the list has to be sorted here or two pages can show two
    different orders. Two things about this fixture are load-bearing: the rows are
    the **reverse** of the sorted order (a fixture that happens to arrive sorted
    cannot tell a sorted list from an unsorted one), and the second name is
    lower-case on purpose — ASCII puts every capital before every lower-case letter,
    so an order that does not fold case would put `SMPN 1` first here.
    """
    _names(monkeypatch, {TEACHER: "SMPN 1", OTHER: "smk negeri 2"})
    _choice(monkeypatch, None)
    monkeypatch.setattr(membership, "_rows_for_request",
                        lambda user_id, supabase=None: _rows((TEACHER, "active"), (OTHER, "active")))
    out = membership.switchable_schools("u-1", "tok", "guru", active_school_id=TEACHER)
    assert [s["school_id"] for s in out["schools"]] == [OTHER, TEACHER], (
        "sorted by name, case-insensitively — 'smk negeri 2' comes before 'SMPN 1'")
    assert [s["active"] for s in out["schools"]] == [False, True], (
        "the school this request is already in is the one marked active")
    assert out["away"] is False


def test_a_membership_that_was_closed_is_not_offered(monkeypatch):
    """A school a teacher was removed from may not be reachable from the header either."""
    _names(monkeypatch, {TEACHER: "SMP 1", OTHER: "SMA 2"})
    _choice(monkeypatch, None)
    monkeypatch.setattr(membership, "_rows_for_request",
                        lambda user_id, supabase=None: _rows((TEACHER, "active"), (OTHER, "closed")))
    out = membership.switchable_schools("u-1", "tok", "guru", active_school_id=TEACHER)
    assert out == {"schools": [], "away": False}, (
        "one active row, and the request is in it: nothing to switch to")


def test_a_closed_school_is_not_offered_even_when_it_would_be_the_choice(monkeypatch):
    _names(monkeypatch, {"sc-Closed": "SMA Tutup", OTHER: "SMA 2"})
    _choice(monkeypatch, None)
    monkeypatch.setattr(membership, "_rows_for_request",
                        lambda user_id, supabase=None: _rows(("sc-Closed", "closed"), (OTHER, "active")))
    out = membership.switchable_schools("u-1", "tok", "guru", active_school_id=TEACHER)
    assert [s["school_id"] for s in out["schools"]] == [OTHER]


def test_an_unreadable_membership_hides_the_control_instead_of_raising(monkeypatch):
    """A read that failed may not be the reason a page does not render."""
    _choice(monkeypatch, TEACHER)
    monkeypatch.setattr(membership, "_rows_for_request",
                        lambda user_id, supabase=None: None)
    assert membership.switchable_schools("u-1", "tok", "guru") == {"schools": [], "away": False}

    def _boom(*a, **k):
        raise RuntimeError("nobody could answer")

    monkeypatch.setattr(membership, "_rows_for_request", _boom)
    assert membership.switchable_schools("u-1", "tok", "guru") == {"schools": [], "away": False}


def test_a_school_that_cannot_be_named_still_appears(monkeypatch):
    """An id is an answer; a name is presentation."""
    from app.utils import req_cache
    _choice(monkeypatch, None)
    monkeypatch.setattr(membership, "_rows_for_request",
                        lambda user_id, supabase=None: _rows((TEACHER, "active"), (OTHER, "active")))

    def _boom(school_id, columns="name"):
        raise RuntimeError("no such school in the cache")

    monkeypatch.setattr(req_cache, "school_row", _boom)
    out = membership.switchable_schools("u-1", "tok", "guru", active_school_id=TEACHER)
    assert [s["school_id"] for s in out["schools"]] == [TEACHER, OTHER]
    assert all(s["name"] == "" for s in out["schools"])


def test_away_is_the_per_session_choice_and_that_is_what_brings_the_option_home(monkeypatch):
    """A teacher migrated into another school has no row for their own.

    Which is why the list cannot be built only from rows: while the choice is in
    force (`away`), the control offers the way back to the school the profile names —
    and that is the only route home for an account created after migration 044.
    """
    _names(monkeypatch, {OTHER: "SMA 2"})
    _choice(monkeypatch, OTHER)
    monkeypatch.setattr(membership, "_rows_for_request",
                        lambda user_id, supabase=None: _rows((OTHER, "active")))
    out = membership.switchable_schools("u-1", "tok", "guru", active_school_id=OTHER)
    assert out["away"] is True, "the choice is in force"
    assert [s["school_id"] for s in out["schools"]] == [OTHER]
    # …and with no choice stored, the same account reads as being in its home school,
    # so the control appears (there is somewhere to go) without the way back.
    _choice(monkeypatch, None)
    out = membership.switchable_schools("u-1", "tok", "guru", active_school_id=TEACHER)
    assert out["away"] is False
    assert [s["school_id"] for s in out["schools"]] == [OTHER]


def test_the_switcher_adds_no_query_to_a_page_that_already_resolved_its_school(app, monkeypatch):
    """One read per request, shared with the resolution — the chrome is on every page.

    `resolve_for_request` runs at the start of *every* authenticated request and
    `switchable_schools` runs again at render time; a control in the header may not be
    the reason a page pays a second round-trip for a row it already holds.
    """
    _names(monkeypatch, {TEACHER: "SMP 1", OTHER: "SMA 2"})
    sb = _Sb(_rows((TEACHER, "active"), (OTHER, "active")))
    monkeypatch.setattr(membership, "get_active_school", lambda token: None)
    monkeypatch.setattr(membership, "clear_active_school", lambda token: None)
    with app.test_request_context("/teacher/dashboard"):
        resolved = membership.resolve_for_request("tok", "u-1", TEACHER, "guru", supabase=sb)
        out = membership.switchable_schools("u-1", "tok", "guru",
                                            active_school_id=resolved, supabase=sb)
    assert resolved == TEACHER
    assert len(out["schools"]) == 2
    assert sb.reads == 1, f"the membership table was read {sb.reads} times, not once"


# ── the door: the only writer of the choice ──────────────────────────────────

def _call_door(app, monkeypatch, *, role="guru", form=None, member=True, calls=None):
    """Run the view past its decorators, recording what it wrote where."""
    from app.routes import membership_routes

    calls = calls if calls is not None else {}
    monkeypatch.setattr(membership_routes, "get_supabase", lambda: "sb")
    monkeypatch.setattr(membership, "is_active_member",
                        lambda sb, user_id, school_id: member)
    monkeypatch.setattr(membership, "set_active_school",
                        lambda token, school_id: calls.__setitem__("set", (token, school_id)))
    monkeypatch.setattr(membership, "clear_active_school",
                        lambda token: calls.__setitem__("clear", token))
    monkeypatch.setattr(membership_routes, "log_activity",
                        lambda *a, **k: calls.__setitem__("logged", k.get("new_data")))
    with app.test_request_context("/teacher/school/switch", method="POST",
                                  data=form if form is not None else {}):
        g.user_id, g.user_role, g.user_token = "u-1", role, "tok-1"
        g.user_school_id, g.user_home_school_id = TEACHER, TEACHER
        return inspect.unwrap(membership_routes.switch_school)()


def test_a_teacher_switches_into_a_school_they_are_an_active_member_of(app, monkeypatch):
    calls = {}
    response = _call_door(app, monkeypatch, form={"school_id": OTHER}, calls=calls)
    assert calls["set"] == ("tok-1", OTHER), "the choice is written for this session"
    assert calls["logged"] == {"school_id": OTHER}
    assert response.status_code == 303, "kept as a POST-redirect-GET, not a replayable POST"
    assert response.headers["Location"].endswith("/teacher/dashboard")


def test_the_choice_this_door_writes_is_the_school_the_next_request_resolves(monkeypatch):
    """The keystone, end to end: write the choice, then ask the resolver.

    The two halves were written months apart, and this is the claim that joins them —
    the choice the header posts is the school every following page, mark and paper is
    scoped to.
    """
    stored = {}
    monkeypatch.setattr(membership, "get_active_school", lambda token: stored.get(token))
    monkeypatch.setattr(membership, "set_active_school",
                        lambda token, school_id: stored.__setitem__(token, school_id))
    monkeypatch.setattr(membership, "clear_active_school", lambda token: stored.pop(token, None))
    rows = _rows((TEACHER, "active"), (OTHER, "active"))
    monkeypatch.setattr(membership, "_rows_for_request",
                        lambda user_id, supabase=None: rows)
    # Before: the profile's school, which is where every request already went.
    assert membership.resolve_for_request("tok-1", "u-1", TEACHER, "guru") == TEACHER
    membership.set_active_school("tok-1", OTHER)
    # After: the chosen one — and back again, which is what makes it a choice.
    assert membership.resolve_for_request("tok-1", "u-1", TEACHER, "guru") == OTHER
    membership.clear_active_school("tok-1")
    assert membership.resolve_for_request("tok-1", "u-1", TEACHER, "guru") == TEACHER


def test_a_school_that_is_not_an_active_membership_is_refused(app, monkeypatch):
    """A form names an intention; the membership row is the authority."""
    calls = {}
    with pytest.raises(HTTPException) as refusal:
        _call_door(app, monkeypatch, form={"school_id": "sc-Somebody-Else"}, member=False,
                   calls=calls)
    assert refusal.value.code == 403
    assert "set" not in calls, "a refused switch wrote a choice"


def test_a_pupil_or_a_school_admin_cannot_switch_at_all(app, monkeypatch):
    calls = {}
    for role in ("murid", "admin_sekolah"):
        with pytest.raises(HTTPException) as refusal:
            _call_door(app, monkeypatch, role=role, form={"school_id": OTHER}, calls=calls)
        assert refusal.value.code == 403, role
    assert "set" not in calls


def test_home_clears_the_choice_instead_of_naming_a_school(app, monkeypatch):
    """Going back has to be possible without knowing which school home is."""
    for form in ({"school_id": "home"}, {"school_id": ""}, {}):
        calls = {}
        response = _call_door(app, monkeypatch, form=form, calls=calls)
        assert calls.get("clear") == "tok-1", form
        assert "set" not in calls, form
        assert calls["logged"] == {"school_id": None, "choice": "home"}
        assert response.status_code == 303


def test_the_door_asks_the_membership_before_it_writes(app):
    """Order, not merely presence: the refusal has to precede the write."""
    body = _door_body()
    assert body.index("is_active_member(") < body.index("set_active_school("), (
        "a school was written into the session before the row that grants it was read")


def test_the_return_path_is_a_local_page_and_nothing_else():
    """A switch may return to the page it was used from — and to no other page.

    `//evil.example` is protocol-relative, so a browser follows it off-site; a
    backslash is read as a slash by more than one client; an absolute URL turns a
    school choice into an open redirect; and a tab or a newline is the one shape that
    looks local and is not, because a browser strips those out of a URL **before** it
    parses it — `/<TAB>/evil.example` arrives at the parser as `//evil.example`.
    """
    from app.routes.membership_routes import _safe_next

    assert _safe_next("/teacher/exams?page=2") == "/teacher/exams?page=2"
    for hostile in ("//evil.example/x", "https://evil.example", "http://evil.example",
                    "/\\evil.example", "\\\\evil.example", "javascript:alert(1)",
                    "/\t/evil.example", "/\r/evil.example", "/\n/evil.example",
                    "/teacher\t/../evil.example"):
        assert _safe_next(hostile) == "", hostile
    assert _safe_next(None) == "" and _safe_next("   ") == ""


def test_a_hostile_next_cannot_take_a_switch_off_site(app, monkeypatch):
    calls = {}
    response = _call_door(app, monkeypatch, form={"school_id": OTHER, "next": "//evil.example"},
                          calls=calls)
    assert calls["set"] == ("tok-1", OTHER)
    assert "evil.example" not in response.headers["Location"]


def test_the_landing_page_follows_the_role_that_switched(app, monkeypatch):
    """Three cross-school roles, three dashboards: a redirect may not be a dead end."""
    for role, expected in (("guru", "/teacher/dashboard"),
                           ("principal", "/principal/dashboard"),
                           ("vice_principal", "/vice-principal/dashboard")):
        calls = {}
        response = _call_door(app, monkeypatch, role=role, form={"school_id": OTHER},
                              calls=calls)
        assert response.headers["Location"] == expected, role


def test_the_door_is_the_only_writer_of_the_choice_in_the_whole_app():
    """One writer. A second one is how the two halves of a session drift apart.

    `app/utils/auth.py` is the one other caller, and it is not a *writer* of the
    choice — it carries an existing choice onto the token a refresh mints, which is
    what stops an hourly refresh from silently returning a teacher to their home
    school. Every other mention is a read, storage or prose.
    """
    writers = []
    for path in (ROOT / "app").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for _match in re.finditer(r"^\s*(?:membership\.)?set_active_school\(", text, re.M):
            writers.append(path.relative_to(ROOT))
    # Compared by file *name*, not by a joined path: this suite runs on Windows too,
    # and an assertion about where a call lives must not be an assertion about `\`.
    assert writers, "nothing writes the choice any more — the control is decoration"
    assert {p.name for p in writers} <= {"membership_routes.py", "auth.py"}, \
        sorted({str(p) for p in writers})
    assert any(p.name == "membership_routes.py" for p in writers), (
        "the door stopped writing the choice")


def test_an_unsigned_request_never_reaches_the_view(app):
    """`@login_required` is what fills `g`, so it has to be the outermost decorator."""
    source = DOOR.read_text(encoding="utf-8")
    assert "@school_switch_bp.route(\"/teacher/school/switch\", methods=[\"POST\"])" in source
    assert source.index("@login_required") < source.index("def switch_school()")
    rule = next(r for r in app.url_map.iter_rules() if "school/switch" in str(r))
    assert rule.rule == "/teacher/school/switch"
    assert rule.methods == {"POST", "OPTIONS"}, (
        "a GET must not be able to change the school a session is in")
    assert rule.endpoint == "school_switch.switch_school"


# ── the chrome: the control, rendered ────────────────────────────────────────

def _processor(app):
    """The shipped context processor that fills the header's switcher payload."""
    for fn in app.template_context_processors[None]:
        if fn.__name__ == "inject_school_switcher":
            return fn
    raise AssertionError("the header's switcher payload is not injected at all")


def test_the_chrome_reads_nothing_for_a_pupil_or_an_anonymous_page(app, monkeypatch):
    """Executed, not grepped: the processor is the thing every page runs."""
    def _boom(*a, **k):
        pytest.fail("the chrome must not read memberships for this reader")

    monkeypatch.setattr(membership, "switchable_schools", _boom)
    processor = _processor(app)
    with app.test_request_context("/student/dashboard"):
        assert processor() == {"school_switcher": {"schools": [], "away": False}}
        g.user_id, g.user_role = "u-1", "murid"
        assert processor() == {"school_switcher": {"schools": [], "away": False}}
        g.user_role = "admin_sekolah"
        assert processor() == {"school_switcher": {"schools": [], "away": False}}


def test_the_chrome_hands_the_service_the_session_it_resolved(app, monkeypatch):
    """The active school in the payload is the *request's*, not the profile's."""
    seen = {}

    def _spy(user_id, token, role, active_school_id=None, supabase=None):
        seen.update(dict(user_id=user_id, token=token, role=role,
                         active=active_school_id))
        return {"schools": [{"school_id": OTHER, "name": "SMA 2", "active": True}],
                "away": True}

    monkeypatch.setattr(membership, "switchable_schools", _spy)
    with app.test_request_context("/teacher/dashboard"):
        g.user_id, g.user_role = "u-1", "guru"
        g.user_token, g.user_school_id = "tok-1", OTHER
        payload = _processor(app)()["school_switcher"]
    assert seen == {"user_id": "u-1", "token": "tok-1", "role": "guru", "active": OTHER}
    assert payload["away"] is True and payload["schools"][0]["school_id"] == OTHER


def test_the_header_gets_its_payload_with_no_page_to_hand_it_one(app, monkeypatch):
    """The wiring, end to end: processor → template, on a page that passes nothing.

    A hundred and fifteen templates render `base.html` and not one of them passes
    `school_switcher` — the header would have to be handed the payload by every page
    that wants it. So this renders through `render_template`, which is what runs the
    context processors, rather than `template.render()` (which does not), and asserts
    the control for a teacher and *nothing* for the pupil whose page is the reason the
    payload has an empty shape at all.
    """
    from flask import render_template

    _names(monkeypatch, {TEACHER: "SMPN 1", OTHER: "smk negeri 2"})
    _choice(monkeypatch, None)
    monkeypatch.setattr(membership, "_rows_for_request",
                        lambda user_id, supabase=None: _rows((TEACHER, "active"), (OTHER, "active")))

    def _page(role, school):
        with app.test_request_context("/teacher/dashboard"):
            g.user_id, g.user_role = "u-1", role
            g.user_name, g.user_email = "Budi", "budi@example.com"
            g.user_token, g.user_school_id, g.tz_offset = "tok-1", school, 7
            return render_template("base.html")

    teacher_page = _page("guru", TEACHER)
    assert "/teacher/school/switch" in teacher_page, (
        "the header's control never got the payload the processor builds")
    assert re.search(r'<option value="sc-B"[^>]*>smk negeri 2</option>', teacher_page), (
        "the control rendered without the schools the reader may choose between")
    # The same page for a pupil: the processor answers the empty payload and the
    # header draws nothing — this is the Chrome-is-on-every-page case.
    pupil_page = _page("murid", TEACHER)
    assert "/teacher/school/switch" not in pupil_page


def test_the_chrome_survives_a_service_that_raises(app, monkeypatch):
    """The chrome is on every page: it may not be why one of them does not render."""
    monkeypatch.setattr(membership, "switchable_schools",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("nope")))
    with app.test_request_context("/teacher/dashboard"):
        g.user_id, g.user_role = "u-1", "guru"
        assert _processor(app)() == {"school_switcher": {"schools": [], "away": False}}


def _render_chrome(app, payload) -> str:
    """`base.html` as a signed-in teacher sees it, with the payload already decided."""
    with app.test_request_context("/teacher/dashboard"):
        g.user_id, g.user_role = "u-1", "guru"
        g.user_name, g.user_email = "Budi", "budi@example.com"
        g.user_token, g.user_school_id, g.tz_offset = "tok-1", TEACHER, 7
        return app.jinja_env.get_template("base.html").render(school_switcher=payload)


TWO = {"schools": [{"school_id": TEACHER, "name": "SMP Negeri 1", "active": True},
                   {"school_id": OTHER, "name": "SMA Negeri 2", "active": False}],
       "away": False}


def test_a_teacher_with_two_schools_gets_a_control_that_posts_both(app):
    html = _render_chrome(app, TWO)
    form = re.search(r"<form method=\"POST\" action=\"/teacher/school/switch\".*?</form>",
                     html, re.S)
    assert form, "the header has no school control at all"
    markup = form.group(0)
    for school in TWO["schools"]:
        assert f'<option value="{school["school_id"]}"' in markup, school
        assert school["name"] in markup
    assert markup.count("selected") == 1
    assert f'<option value="{TEACHER}" selected>' in markup, (
        "the school this request is in is the one shown as current")
    assert 'name="next"' in markup, (
        "the switch has to be able to return to the page it was used from")
    assert "_csrf_token" in markup, "a POST without a token is refused by the app"


def test_the_way_back_to_the_accounts_own_school_is_a_word_not_an_id(app, monkeypatch):
    """`home` cannot be invented into another school by a client."""
    html = _render_chrome(app, {"schools": [{"school_id": OTHER, "name": "SMA 2",
                                             "active": True}], "away": True})
    assert '<option value="home"' in html
    assert "My own school" in html and "Sekolah asal saya" in html


def test_a_teacher_with_one_school_sees_no_control_at_all(app):
    html = _render_chrome(app, {"schools": [], "away": False})
    assert "sgSchoolSwitcher" not in html
    assert "/teacher/school/switch" not in html


def test_the_control_is_in_the_chrome_every_page_rides_on(app):
    """Not on a settings page: the answer has to be visible where it is used."""
    source = BASE.read_text(encoding="utf-8")
    assert 'action="/teacher/school/switch"' in source
    assert "school_switcher|default({})" in source, (
        "the control must render from the injected payload, not from a page's context")
    # …and inside a region that declares its language, since the header follows the
    # language toggle and this copy is a pair like every other string in it.
    header = source[source.index("<header :lang=\"lang\""):]
    assert 'action="/teacher/school/switch"' in header[:header.index("</header>")]


def test_the_control_is_big_enough_to_tap_a_papers_device_with(app):
    """A phone is the device this app is used on; the control may not hide from it."""
    html = _render_chrome(app, TWO)
    markup = re.search(r"<select id=\"sgSchoolSwitcher\".*?>", html, re.S).group(0)
    assert "tap-44" in markup, "the school picker is smaller than a finger"


def test_no_page_reads_the_choice_out_of_the_browser(app):
    """The choice lives server-side, per token; nothing about it travels in a form."""
    source = BASE.read_text(encoding="utf-8")
    form = re.search(r"<form method=\"POST\" action=\"/teacher/school/switch\".*?</form>",
                     source, re.S).group(0)
    assert form.count('name="school_id"') == 1
    assert "chosen" not in form, (
        "the browser must not be able to echo the current choice back as an input")


# ── helpers ──────────────────────────────────────────────────────────────────

def _door_body() -> str:
    """The view's own source, from its `def` — decorators and prose above it aside."""
    from app.routes import membership_routes
    source = inspect.getsource(membership_routes.switch_school)
    return source[source.index("def switch_school"):]
