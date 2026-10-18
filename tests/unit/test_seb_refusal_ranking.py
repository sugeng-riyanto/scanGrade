"""Who could not get in, and who is about to — the school-wide reading of the log.

A teacher's SEB panel answers the question for one paper. An admin running a morning
of sittings needs it for the whole school, in two halves that share one window:

* **what keeps happening.** Which class keeps being turned away, and which file
  reached nobody. Migration 064 wrote the index for that read
  (`idx_seb_door_refusal_school_created`) and said the page did not exist yet;
  `seb_door_log.rank_refusals` is that half.
* **what to do this morning.** One paper that has not finished, its roster, and the
  pupils on it who already have a refusal against their name — each with the device
  they were refused on, so the answer is a name and a thing to check rather than a
  trend. `seb_door_log.at_risk`, `papers_to_prepare` and `pick_paper` are that half,
  and `/admin-sekolah/seb-refusals` is both.

The second half is where the honesty is load-bearing. The log records refusals and
nothing else, so a pupil with no row is **unknown and not ready**: the panel counts
them separately as `no_record` and says so in as many words, because a page that read
an empty at-risk list as a green tick would clear a room on the strength of never
having looked.

What is guarded here is the part a render cannot show and a reader cannot check:

* **the two numbers are two facts.** Five attempts by one pupil is a mistyped link;
  one attempt each by five pupils is a file that reached nobody. A single total tells
  those apart from nothing, so `pupils` is distinct `student_id` and `attempts` is
  rows, and the ordering puts pupils first.
* **the order is total.** A ranking whose ties resolve by dict insertion order is a
  page that changes between two reads of the same log.
* **nothing here is a charge.** The writers already assert that in
  `test_seb_door_log`'s own terms; the *ranking* has to keep it too — a reading that
  starts consulting the ladder would make this page a second, silent penalty path.

The page itself is rendered for real (through `base.html`, as a signed-in school
admin), because a body asserted from source can be a body the layout threw away.
"""
from __future__ import annotations

import ast
import pathlib
import re
from datetime import datetime, timedelta, timezone

import pytest

from app.services import seb_door_log as log

ROOT = pathlib.Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "app" / "templates" / "admin_sekolah" / "seb_refusals.html"
ROUTES = ROOT / "app" / "routes" / "admin_sekolah.py"
SERVICE = ROOT / "app" / "services" / "seb_door_log.py"

CLASSES = [{"id": "c1", "name": "7A"}, {"id": "c2", "name": "8B"}]
EXAMS = {"e1": "Ulangan Biologi", "e2": "UTS Matematika"}


def row(student="s1", exam="e1", reason=log.REASON_NO_CLIENT, cls="c1",
        at="2026-10-15T07:00:00+00:00", name="Ahmad"):
    """One refusal as `refusals_for_school` hands it over: the row plus its class."""
    return {"student_id": student, "exam_id": exam, "reason": reason, "at": at,
            "class_id": cls, "name": name, "user_agent": "Chrome",
            "label": log.REASON_LABELS.get(reason, (reason, reason))}


def rank(rows, classes=CLASSES, exams=EXAMS):
    return log.rank_refusals(rows, classes=classes, exams=exams)


# ── the two numbers, and why one total is not enough ────────────────────────────


class TestTheTwoNumbers:
    def test_one_pupil_five_times_is_one_pupil(self):
        out = rank([row(at=f"2026-10-15T07:0{i}:00+00:00") for i in range(5)])
        assert out["summary"]["refused_pupils"] == 1
        assert out["summary"]["refused_attempts"] == 5
        assert out["classes"][0]["pupils"] == 1 and out["classes"][0]["attempts"] == 5

    def test_five_pupils_once_each_is_five_pupils(self):
        out = rank([row(student=f"s{i}") for i in range(5)])
        assert out["summary"]["refused_pupils"] == 5
        assert out["summary"]["refused_attempts"] == 5

    def test_a_refusal_with_no_pupil_is_an_attempt_and_not_a_pupil(self):
        """The migration set `student_id` NULL on purpose: a deleted profile keeps
        its refusal as a record."""
        out = rank([row(student=None), row(student="s9")])
        assert out["summary"]["refused_attempts"] == 2
        assert out["summary"]["refused_pupils"] == 1

    def test_the_papers_are_counted_and_not_the_attempts(self):
        out = rank([row(exam="e1"), row(exam="e1"), row(exam="e2")])
        assert out["summary"]["papers"] == 2


# ── the order is the page's whole claim ─────────────────────────────────────────


class TestTheOrdering:
    def test_pupils_decide_before_attempts(self):
        """Two pupils, once each, outranks one pupil who tried nine times: the first
        is a file that reached nobody."""
        rows = [row(student="a", cls="c1")] * 9 + [row(student="b", cls="c2"),
                                                   row(student="c", cls="c2")]
        out = rank(rows)
        assert [c["class_id"] for c in out["classes"]] == ["c2", "c1"]

    def test_attempts_break_a_tie(self):
        rows = [row(student="a", cls="c1")] * 3 + [row(student="b", cls="c2")]
        out = rank(rows)
        assert [c["class_id"] for c in out["classes"]] == ["c1", "c2"]

    def test_the_order_does_not_depend_on_the_order_rows_arrived(self):
        rows = [row(student="a", cls="c1"), row(student="b", cls="c2"),
                row(student="d", cls="c2"), row(student="c", cls="c3")]
        forward = rank(rows)["classes"]
        backward = rank(list(reversed(rows)))["classes"]
        assert [(c["class_id"], c["pupils"], c["attempts"]) for c in forward] == \
               [(c["class_id"], c["pupils"], c["attempts"]) for c in backward]

    def test_the_papers_are_ordered_the_same_way(self):
        rows = [row(student="a", exam="e1")] * 4 + [row(student="b", exam="e2"),
                                                    row(student="c", exam="e2")]
        out = rank(rows)
        assert [p["exam_id"] for p in out["papers"]] == ["e2", "e1"]


# ── names: resolved, or left for the page to pair ───────────────────────────────


class TestTheNames:
    def test_a_class_is_named_from_the_schools_own_list(self):
        assert rank([row(cls="c2")])["classes"][0]["label"] == "8B"

    def test_a_named_class_with_no_row_gets_no_label(self):
        """Not a sentence baked in the service: the page owns the pair, because only
        the page knows which language the reader chose."""
        out = rank([row(cls="c9")])
        assert out["classes"][0]["label"] is None

    def test_a_refusal_with_no_class_at_all_is_still_listed(self):
        out = rank([row(cls=None)])
        assert len(out["classes"]) == 1 and out["classes"][0]["label"] is None
        assert out["classes"][0]["attempts"] == 1

    def test_a_paper_is_named_from_the_titles_read(self):
        assert rank([row(exam="e2")])["papers"][0]["label"] == "UTS Matematika"

    def test_a_paper_whose_title_could_not_be_read_falls_back_to_its_id(self):
        assert rank([row(exam="e9")])["papers"][0]["label"] == "e9"


# ── the summary, the causes, and the newest rows ────────────────────────────────


class TestTheSummaryAndTheCauses:
    def test_an_empty_window_claims_no_moment(self):
        out = rank([])
        assert out["summary"]["refused_last_at"] is None
        assert out["summary"]["refused_attempts"] == 0
        assert out["classes"] == [] and out["papers"] == [] and out["recent"] == []

    def test_the_last_moment_is_the_newest_one_whatever_order_it_arrived(self):
        rows = [row(at="2026-10-15T07:00:00+00:00"), row(at="2026-10-16T09:30:00+00:00"),
                row(at="2026-10-14T05:00:00+00:00")]
        assert rank(rows)["summary"]["refused_last_at"] == "2026-10-16T09:30:00+00:00"

    def test_the_causes_are_counted_biggest_first_and_labelled(self):
        rows = [row(reason=log.REASON_NO_CLIENT)] * 3 + [row(reason=log.REASON_KEY_MISMATCH)]
        causes = rank(rows)["reasons"]
        assert [c["reason"] for c in causes] == [log.REASON_NO_CLIENT,
                                                 log.REASON_KEY_MISMATCH]
        assert causes[0]["count"] == 3
        assert causes[0]["label"] == log.REASON_LABELS[log.REASON_NO_CLIENT]

    def test_a_cause_this_release_does_not_know_is_shown_and_not_dropped(self):
        causes = rank([row(reason="something_new")])["reasons"]
        assert causes[0]["reason"] == "something_new"
        assert causes[0]["label"] == ("something_new", "something_new")

    def test_the_most_common_cause_of_a_group_is_pairs_not_a_sentence(self):
        """Each ranking row carries the pair too, so a table cell cannot print one
        language's words on a page that follows the toggle."""
        rows = [row(reason=log.REASON_NO_KEY), row(reason=log.REASON_NO_KEY),
                row(reason=log.REASON_KEY_MISMATCH)]
        bucket = rank(rows)["classes"][0]
        assert bucket["reason"] == log.REASON_NO_KEY
        assert bucket["reason_label"] == log.REASON_LABELS[log.REASON_NO_KEY]

    def test_the_recent_list_is_capped(self):
        rows = [row(student=f"s{i:04d}", at=f"2026-10-15T07:{i % 60:02d}:00+00:00")
                for i in range(log.RECENT_ROWS + 20)]
        out = rank(rows)
        assert len(out["recent"]) == log.RECENT_ROWS
        assert out["recent"][0]["student_id"] == "s0000", (
            "the newest row of the read has to be the first one listed")

    def test_a_recent_row_names_its_class_paper_and_cause(self):
        recent = rank([row(cls="c2", exam="e2")])["recent"][0]
        assert recent["class_label"] == "8B" and recent["class_missing"] is False
        assert recent["exam_label"] == "UTS Matematika"
        assert recent["label"] == log.REASON_LABELS[log.REASON_NO_CLIENT]

    def test_a_pupil_whose_name_is_gone_is_still_a_row(self):
        recent = rank([row(name=None)])["recent"][0]
        assert recent["name"] is None and recent["student_id"] == "s1"


# ── the window is a whitelist, not a number off the query string ────────────────


class TestTheWindow:
    @pytest.mark.parametrize("days", log.WINDOWS)
    def test_the_offered_windows_are_accepted(self, days):
        assert log.window_days(str(days)) == days

    @pytest.mark.parametrize("raw", (None, "", "90d", "999999", "-1", "abc"))
    def test_anything_else_falls_back_to_the_default(self, raw):
        assert log.window_days(raw) == log.DEFAULT_WINDOW

    def test_a_days_parameter_that_is_not_offered_cannot_widen_the_read(self):
        assert log.window_days("3650") == log.DEFAULT_WINDOW, (
            "the reader turns this into a timestamp, so an arbitrary window is a "
            "scan nobody asked for")


# ── the read itself: scoped to one school, and survivable ──────────────────────


class _Query:
    """The narrow slice of PostgREST this reads, recording what it was asked."""

    def __init__(self, rows, calls, fail):
        self._rows, self._calls, self._fail = rows, calls, fail

    def select(self, columns):
        self._calls.append(("select", columns))
        return self

    def eq(self, column, value):
        self._calls.append(("eq", column, value))
        return self

    def gte(self, column, value):
        self._calls.append(("gte", column, value))
        return self

    def in_(self, column, values):
        self._calls.append(("in_", column, sorted(values)))
        return self

    def order(self, column, **kwargs):
        self._calls.append(("order", column, kwargs.get("desc")))
        return self

    def limit(self, count):
        self._calls.append(("limit", count))
        return self

    def execute(self):
        if self._fail:
            raise RuntimeError("postgrest said no")
        return type("_Result", (), {"data": self._rows})()


class _Supabase:
    def __init__(self, rows=(), fail=False):
        self.rows, self.fail, self.calls, self.tables = rows, fail, [], []

    def table(self, name):
        self.tables.append(name)
        return _Query(self.rows, self.calls, self.fail)


class TestTheReadIsScopedAndSurvivesFailure:
    def test_it_is_filtered_by_school_and_by_window(self):
        fake = _Supabase([])
        log.refusals_for_school(fake, "sch-1", days=7)
        assert ("eq", "school_id", "sch-1") in fake.calls
        assert any(c[0] == "gte" and c[1] == "created_at" for c in fake.calls), (
            "an unfiltered read is the whole table, for every school")
        assert ("order", "created_at", True) in fake.calls
        assert ("limit", log.MAX_SCHOOL_ROWS) in fake.calls

    def test_it_reads_the_class_this_page_groups_by(self):
        fake = _Supabase([])
        log.refusals_for_school(fake, "sch-1")
        columns = [c for c in fake.calls if c[0] == "select"][0][1]
        assert "class_id" in columns, (
            "the grouping column has to be in the select, or every refusal lands in "
            "the unknown-class bucket")

    def test_no_school_id_reads_nothing_at_all(self):
        fake = _Supabase([row()])
        assert log.refusals_for_school(fake, None) == []
        assert fake.tables == [], "a read with no school is a read of every school"

    def test_a_failing_read_is_an_empty_list_not_an_exception(self):
        assert log.refusals_for_school(_Supabase(fail=True), "sch-1") == []

    def test_the_titles_are_one_read_and_a_missing_one_is_absent(self):
        fake = _Supabase([{"id": "e1", "title": "Ulangan Biologi"}])
        assert log.exam_titles(fake, {"e1", "e2"}) == {"e1": "Ulangan Biologi"}
        assert fake.tables == ["exams"], "one query for the page, not one per row"

    def test_no_papers_means_no_query(self):
        fake = _Supabase([])
        assert log.exam_titles(fake, set()) == {} and fake.tables == []


# ── the page, rendered for a signed-in school admin ────────────────────────────


def _decorators(name: str) -> str:
    """The decorator text above one view in `admin_sekolah.py`."""
    source = ROUTES.read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == name)
    lines = source.splitlines()
    first = min(d.lineno for d in node.decorator_list)
    return "\n".join(lines[first - 1:node.lineno - 1])


def _render(app, rows, **overrides):
    """The page as the route renders it, for an admin whose school has refusals."""
    from flask import g, render_template

    ranking = rank(rows, classes=overrides.pop("classes", CLASSES),
                   exams=overrides.pop("exams", EXAMS))
    context = {"days": overrides.pop("days", log.DEFAULT_WINDOW),
               "windows": log.WINDOWS, "class_unknown": log.NO_CLASS_LABEL,
               "readiness": overrides.pop("readiness", _readiness()), **ranking}
    context.update(overrides)
    with app.test_request_context("/admin-sekolah/seb-refusals"):
        g.user_id = "adm-1"
        g.user_name = "Admin Uji"
        g.user_email = "admin@example.test"
        g.user_role = "admin_sekolah"
        g.user_school_id = "sch-1"
        g.tz_offset = 7
        return render_template("admin_sekolah/seb_refusals.html", **context)


class TestThePageRenders:
    def test_it_renders_with_the_rankings_on_it(self, app):
        html = _render(app, [row(cls="c2", exam="e2"), row(student="s2", cls="c1")])
        assert "8B" in html and "UTS Matematika" in html, (
            "the page lost its rankings — or base.html threw the whole body away")
        assert "data-seb-refusal-not-penalty" in html, (
            "the body did not render at all, which passes any assertion made from "
            "source alone")

    def test_a_refusal_is_stated_to_be_neither_violation_nor_penalty(self, app):
        html = _render(app, [row()])
        assert "bukan pelanggaran dan bukan penalti" in html
        assert "not a violation and not a penalty" in html, (
            "the one sentence that stops this page being read as an accusation has "
            "to be a pair like every other string")

    def test_an_unnameable_class_is_a_pair_and_not_indonesian(self, app):
        html = _render(app, [row(cls="c9")])
        assert "t('Kelas tidak diketahui','Class unknown')" in html, (
            "a class the school no longer lists must read in the reader's language")

    def test_every_table_can_scroll_on_a_phone(self, app):
        """Every table in the body, the readiness panel's own included.

        The rule is about tables and not about the ranking, so it has to be counted
        on the page as it now reads — which is two halves, and the panel's tables
        only exist when there is a paper to prepare for.
        """
        html = _render(app, [row()])
        tables = re.findall(r"<table[^>]*>", html)
        assert len(tables) == 3, f"expected three tables, found {len(tables)}"
        for table in tables:
            assert "min-w-[" in table, (
                "a table without a min-width squeezes instead of scrolling on a "
                "phone, which is the layout rule this suite keeps")
        assert html.count("overflow-x-auto") >= len(tables)

        shown = _render(app, [row()], readiness=ready_readiness())
        tables = re.findall(r"<table[^>]*>", shown)
        assert len(tables) == 6, (
            f"expected six tables with the readiness panel, found {len(tables)}")
        for table in tables:
            assert "min-w-[" in table
        assert shown.count("overflow-x-auto") >= len(tables)

    def test_the_empty_window_denies_nothing_and_explains_itself(self, app):
        html = _render(app, [])
        assert "Tidak ada penolakan di pintu SEB" in html, (
            "an empty page has to say that an empty log means the door worked")
        # Both halves, with the window interpolated by Jinja into the pair — which is
        # also the proof that the sentence follows the toggle rather than sitting in
        # the template as one language's copy.
        assert "in the last 30 days" in html and "dalam 30 hari terakhir" in html, (
            "the empty state has to name the window it is speaking about, in both "
            "languages")
        assert "<table" not in html, (
            "an empty window still draws the ranking tables, which reads as zeros "
            "rather than as no refusals")

    def test_it_declares_no_language_of_its_own(self, app):
        """It is on `TRANSLATED`, so its copy follows the reader: a pinned page or a
        fixed `lang=` would be the toggle switched off for the admin who needs it."""
        source = TEMPLATE.read_text(encoding="utf-8")
        assert "content_lang" not in source
        assert not re.search(r"\blang=\"", source)

    def test_the_active_window_is_the_one_the_reader_asked_for(self, app):
        html = _render(app, [row()], days=7)
        assert "/admin-sekolah/seb-refusals?days=7" in html
        assert "bg-primary-600 text-white" in html, (
            "nothing on the page marks which window is being read")


# ── the route: who may open it, and what it must not become ────────────────────


class TestTheRoute:
    def test_it_is_on_the_admin_blueprint_at_its_own_address(self, app):
        rules = {str(rule.rule) for rule in app.url_map.iter_rules()}
        assert "/admin-sekolah/seb-refusals" in rules
        assert '@admin_sekolah_bp.route("/seb-refusals")' in ROUTES.read_text(
            encoding="utf-8")

    def test_the_door_answers_and_is_locked_for_an_anonymous_visitor(self, app):
        """The interface the admin actually uses is the address, not the template.

        A route that is not mounted answers 404 — which the sidebar would link into
        — and one that is mounted but unguarded answers 200 with the school's
        pupils on it. This is the only check here that goes through HTTP.
        """
        with app.test_client() as client:
            response = client.get("/admin-sekolah/seb-refusals")
        assert response.status_code in (301, 302, 401, 403), (
            f"an anonymous request answered {response.status_code}, not a refusal")
        location = response.headers.get("Location", "")
        assert location.startswith("/auth/"), (
            f"the school admin's own door answers the refusal, not {location!r}")

    def test_only_a_school_admin_may_open_it(self):
        block = _decorators("seb_refusals_page")
        assert "admin_sekolah_required" in block, (
            "a page that names pupils and their classes is not for every signed-in "
            f"member of the school\n{block}")

    def test_the_view_passes_every_key_the_page_reads(self, app, monkeypatch):
        """The gap between "the template renders" and "the route renders it".

        A hand-built context proves the page's own copy; it cannot prove the *view*
        supplies it. `class_unknown[0]` on an undefined name raises rather than
        rendering a blank cell — Undefined is not subscriptable — so a key the view
        forgets is a 500 in front of the admin, and this is the check that sees it.
        """
        from flask import g

        from app.routes import admin_sekolah as routes

        captured = {}
        monkeypatch.setattr(routes, "_school_id", lambda: "sch-1")
        monkeypatch.setattr(routes, "get_supabase", lambda: _Supabase([]))
        monkeypatch.setattr(routes, "school_classes", lambda school_id: CLASSES)
        monkeypatch.setattr(routes, "render_template",
                            lambda name, **context: captured.update(context) or "")
        with app.test_request_context("/admin-sekolah/seb-refusals?days=7"):
            g.user_id = "adm-1"
            g.user_role = "admin_sekolah"
            g.user_school_id = "sch-1"
            g.tz_offset = 7
            # The role guard wraps the view and answers a redirect of its own before
            # the body runs (it is the door, and it behaves as one). The question
            # here is the *body*: unwrap the guard rather than fake a session, so a
            # forgotten context key cannot hide behind an early return.
            view = getattr(routes.seb_refusals_page, "__wrapped__",
                           routes.seb_refusals_page)
            assert view is not routes.seb_refusals_page, (
                "the view is not wrapped by a guard, so nothing was refused above it")
            view()

        needed = {"days", "windows", "class_unknown", "readiness", "classes",
                  "papers", "reasons", "recent", "summary"}
        assert needed <= set(captured), (
            f"the view does not pass {sorted(needed - set(captured))} — the page reads "
            f"those names and an undefined one raises")
        assert captured["days"] == 7, "the window the reader asked for is not the one asked for"
        assert captured["class_unknown"] == log.NO_CLASS_LABEL

    def test_the_page_reads_no_key_the_view_does_not_pass(self, app):
        """The same question from the other side, read off the template itself.

        `summary`, `classes`, `papers`, `reasons` and `recent` are the ranking dict's
        own keys, so a new one added to the service and used in the page has to reach
        the view too.
        """
        source = TEMPLATE.read_text(encoding="utf-8")
        read = set(re.findall(r"\b(summary|classes|papers|reasons|recent|days|windows|"
                              r"class_unknown|readiness)\b", source))
        ranking = log.rank_refusals([row()], classes=CLASSES, exams=EXAMS)
        view_keys = {"days", "windows", "class_unknown", "readiness"} | set(ranking)
        assert read <= view_keys, (
            f"the page names {sorted(read - view_keys)}, which the route never passes")

    def test_the_school_comes_from_the_session_and_never_from_the_request(self):
        source = ROUTES.read_text(encoding="utf-8")
        body = source.split("def seb_refusals_page(", 1)[1].split("\n@admin_sekolah_bp", 1)[0]
        assert "sid = _school_id()" in body, (
            "the school is read from the session, or a query string could ask for "
            "another school's refusals")
        assert "request.args.get(\"school_id\")" not in body and "request.form" not in body

    def test_the_window_goes_through_the_whitelist(self):
        source = ROUTES.read_text(encoding="utf-8")
        body = source.split("def seb_refusals_page(", 1)[1].split("\n@admin_sekolah_bp", 1)[0]
        assert "seb_door_log.window_days(request.args.get(\"days\"))" in body, (
            "a raw `days` off the query string is a timestamp built from a stranger")

    def test_the_ranking_writes_nothing_and_reads_no_penalty_source(self):
        """The writers assert this for the log; the *reading* has to keep it too: a
        ranking that consulted the ladder would make this page a silent second
        penalty path, and a write on a GET is a page that changes what it reports."""
        source = SERVICE.read_text(encoding="utf-8")
        ranking = source.split("# ── the school-wide question", 1)[1] \
                        .split("def attach_refusals", 1)[0]
        for write in (".insert(", ".update(", ".delete("):
            assert write not in ranking, f"the ranking calls {write}"
        assert "violation_logs" not in ranking
        assert "PENALIZED_VIOLATION_TYPES" not in ranking
        assert "penalty" not in ranking.lower(), (
            "nothing about this page may touch the penalty path")


# ── the paper that has not started yet ────────────────────────────────────────
#
# The half of the page that acts before a sitting. It is graded twice: the
# arithmetic on its own (no database, no template), and then the panel rendered
# through base.html, because a body asserted from source can be a body the layout
# threw away.

ANDROID = ("Mozilla/5.0 (Linux; Android 12; SM-A125F) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36")
IPAD = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
WINDOWS_EDGE = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0 Safari/537.36 Edg/120.0")
SEB_UA = "SEB/3.6.0 (Windows NT 10.0; Win64; x64)"

LATER = "2026-11-01T01:00:00+00:00"
# Relative to the clock the code reads, not to the day the test was written: a
# window literal goes stale the moment the wall clock passes it, and then a test
# about *ordering* fails as if the ordering were wrong.
_NOW = datetime.now(timezone.utc).replace(microsecond=0)
SOON = (_NOW + timedelta(hours=2)).isoformat()
LONG_AGO = (_NOW - timedelta(days=30)).isoformat()
T1 = "2026-10-14T07:00:00+00:00"
T2 = "2026-10-15T07:00:00+00:00"
T3 = "2026-10-13T07:00:00+00:00"

ROSTER = [{"student_id": "s1", "class_id": "c1", "full_name": "Ahmad"},
          {"student_id": "s2", "class_id": "c1", "full_name": "Bila"},
          {"student_id": "s3", "class_id": "c2", "full_name": "Cita"},
          {"student_id": "s4", "class_id": "c2", "full_name": "Dedi"}]


def refuse(student="s1", at=T1, ua=ANDROID, exam="e9", reason=log.REASON_NO_CLIENT,
           cls="c9"):
    """One refusal as the school read hands it over, device and all.

    ``cls`` defaults to a class the school does not list on purpose: the panel must
    take a pupil's class from the *roster*, and a row whose profile says something
    else is how that is proved rather than assumed.
    """
    return {**row(student=student, exam=exam, reason=reason, cls=cls, at=at),
            "user_agent": ua}


def paper(pid="p1", title="UTS Biologi", start=SOON, end=None, require_seb=True,
          classes=("c1", "c2"), mode="class"):
    return {"id": pid, "title": title, "start_at": start, "end_at": end,
            "require_seb": require_seb, "class_ids": list(classes),
            "target_mode": mode}


def ready_paper(pid="p1", **kwargs):
    """The paper dict the *view* holds: `papers_to_prepare` own output."""
    return log.papers_to_prepare([paper(pid, **kwargs)])[0]


def _readiness(papers=None, paper=None, roster=None, refusals=None,
               targets=None, truncated=False, classes=CLASSES):
    """The readiness half, assembled the way `_seb_readiness` assembles it."""
    out = {"papers": papers or [], "paper": paper, "truncated": truncated}
    out.update(log.at_risk(roster or [], refusals or [], classes=classes,
                           targets=targets))
    return out


def ready_readiness():
    """A page with a paper, a roster and one pupil already refused."""
    chosen = ready_paper()
    return _readiness(papers=[chosen], paper=chosen, roster=ROSTER,
                      refusals=[refuse("s1")])


# ── the device, which is the half of the panel a name alone does not act on ────


class TestTheDeviceNames:
    def test_an_android_phone_reads_as_a_phone_and_a_browser(self):
        assert log.device_label(ANDROID) == ("Chrome di Android", "Chrome on Android")

    def test_an_ipad_is_not_reported_as_a_mac(self):
        """An iPad's own string says Macintosh *and* Mobile; that pair is the tell,
        and a shared computer laboratory full of iPads is the case this exists for."""
        assert log.device_label(IPAD) == ("Safari di iPad", "Safari on iPad")
        assert log.device_key(IPAD) == "safari|ipad"

    def test_edge_is_not_reported_as_chrome(self):
        """Edge's string carries Chrome/ and Safari/ as well, so the table's order is
        the rule rather than a preference."""
        assert log.device_label(WINDOWS_EDGE)[0].startswith("Edge")
        assert log.device_label(WINDOWS_EDGE)[0].endswith("Windows")

    def test_seb_names_itself_and_keeps_its_platform(self):
        """The device and the cause together are what a teacher acts on: SEB on a
        laptop that was turned away is a stale file, not a pupil who needs the link."""
        assert log.device_label(SEB_UA) == ("SEB di Windows", "SEB on Windows")
        assert log.device_key(SEB_UA) == "seb|windows"

    @pytest.mark.parametrize("ua", ("", None, "curl/8.0", "Mozilla/5.0"))
    def test_an_unusable_string_is_named_rather_than_left_blank(self, ua):
        """The label and the key answer the same thing about the same string: a row
        grouped under a device it does not name is a row an admin cannot look for."""
        assert log.device_label(ua) == log.DEVICE_UNKNOWN
        assert log.device_key(ua) == "unknown"

    def test_the_key_groups_the_same_device_and_separates_a_different_one(self):
        assert log.device_key(ANDROID) == log.device_key(ANDROID.replace("SM-A125F", "Z"))
        assert log.device_key(ANDROID) != log.device_key(IPAD)
        assert log.device_key(ANDROID) != log.device_key(ANDROID.replace("Android", "iPhone"))

    def test_a_row_without_a_device_is_still_a_row(self):
        assert log.device_of({})["device_ua"] is None
        assert log.device_of({})["device_label"] == log.DEVICE_UNKNOWN

    def test_the_stored_string_is_cut_to_what_a_cell_can_hold(self):
        long_ua = "Mozilla/5.0 " + ("x" * 900)
        assert len(log.device_of({"user_agent": long_ua})["device_ua"]) == 300


# ── which paper to open on ────────────────────────────────────────────────────


class TestThePapersWorthPreparingFor:
    def test_the_scheduled_paper_comes_before_the_one_already_open(self):
        out = log.papers_to_prepare([paper("p1", start=LONG_AGO),
                                     paper("p2", start=SOON)])
        assert [p["id"] for p in out] == ["p2", "p1"]
        assert [p["state"] for p in out] == ["before", "open"]

    def test_a_closed_paper_is_not_offered(self):
        out = log.papers_to_prepare([paper("p1", start=LONG_AGO,
                                           end=LONG_AGO)])
        assert out == [], "an admin cannot prepare for a window that has shut"

    def test_a_paper_with_no_window_is_not_called_upcoming(self):
        """It reads as open for ever on `exam_window`'s own clock, so offering it
        would put last term's paper at the top of a list about this morning."""
        assert log.papers_to_prepare([paper("p1", start=None)]) == []

    def test_a_paper_that_does_not_require_seb_is_not_a_door(self):
        assert log.papers_to_prepare([paper("p1", require_seb=False)]) == []

    def test_the_state_is_a_pair_the_page_can_translate(self):
        out = log.papers_to_prepare([paper("p1")])
        assert out[0]["state_label"] == log.STATE_LABELS["before"]
        assert log.STATE_LABELS["before"] != log.STATE_LABELS["open"]
        assert all(len(pair) == 2 and all(pair) for pair in log.STATE_LABELS.values())

    def test_the_offered_list_is_capped(self):
        rows = [paper(f"p{i}") for i in range(log.PAPER_CHOICES + 3)]
        assert len(log.papers_to_prepare(rows)) == log.PAPER_CHOICES

    def test_the_order_is_total(self):
        rows = [paper("b", start=SOON), paper("a", start=SOON)]
        first = [p["id"] for p in log.papers_to_prepare(rows)]
        assert first == [p["id"] for p in log.papers_to_prepare(list(reversed(rows)))]

    def test_the_title_falls_back_to_the_id_rather_than_vanishing(self):
        out = log.papers_to_prepare([{**paper("p1"), "title": None}])
        assert out[0]["title"] == "p1"


class TestWhichPaperThePageOpensOn:
    def test_the_one_the_reader_asked_for(self):
        papers = log.papers_to_prepare([paper("p1"), paper("p2", start=LATER)])
        assert log.pick_paper(papers, "p2")["id"] == "p2"

    def test_a_paper_that_is_no_longer_offered_falls_back_to_the_first(self):
        """A bookmark to a paper that has since closed lands on the next one rather
        than on a panel reading a roster nobody can sit."""
        papers = log.papers_to_prepare([paper("p1")])
        assert log.pick_paper(papers, "gone")["id"] == "p1"

    @pytest.mark.parametrize("rows,wanted", [([], "p1"), (None, None), ([], None)])
    def test_nothing_to_offer_is_none(self, rows, wanted):
        assert log.pick_paper(rows, wanted) is None


class TestThePaperRead:
    def test_it_reads_one_schools_gated_papers_in_one_query(self):
        fake = _Supabase([])
        log.upcoming_papers(fake, "sch-1")
        assert fake.tables == ["exams"], "one query for the picker, not one per paper"
        assert ("eq", "school_id", "sch-1") in fake.calls
        assert ("eq", "require_seb", True) in fake.calls, (
            "a paper nobody gates is not a door and has nothing to prepare for")
        assert ("order", "start_at", True) in fake.calls, (
            "ordering by start date descending is what keeps the papers near today "
            "inside the cap")
        assert ("limit", log.PAPER_LIMIT) in fake.calls

    def test_the_columns_the_roster_and_the_clock_need_are_selected(self):
        fake = _Supabase([])
        log.upcoming_papers(fake, "sch-1")
        columns = [c for c in fake.calls if c[0] == "select"][0][1]
        for name in ("class_ids", "target_mode", "require_seb", "start_at", "end_at"):
            assert name in columns, (
                f"{name} is left out of the select, and PostgREST reads a missing "
                f"column as absent rather than as an error")

    def test_no_school_reads_nothing_at_all(self):
        fake = _Supabase([paper()])
        assert log.upcoming_papers(fake, None)["papers"] == []
        assert fake.tables == [], "a read with no school is a read of every school"

    def test_a_failing_read_offers_nothing_and_does_not_raise(self):
        out = log.upcoming_papers(_Supabase(fail=True), "sch-1")
        assert out["papers"] == [] and out["truncated"] is False

    def test_a_capped_read_says_it_was_capped(self):
        rows = [paper(f"p{i}") for i in range(3)]
        assert log.upcoming_papers(_Supabase(rows), "sch-1", limit=3)["truncated"] is True
        assert log.upcoming_papers(_Supabase(rows), "sch-1", limit=9)["truncated"] is False


# ── who, in this room, is at risk ─────────────────────────────────────────────


class TestWhoIsAtRiskOnOnePaper:
    def test_only_the_pupils_on_this_paper_are_named(self):
        out = log.at_risk(ROSTER, [refuse("s1"), refuse("sx")], classes=CLASSES)
        assert [p["student_id"] for p in out["pupils"]] == ["s1"]
        assert out["summary"] == {"roster": 4, "at_risk": 1, "attempts": 1,
                                  "devices": 1, "no_record": 3, "papers": 1}

    def test_a_pupil_left_off_a_per_student_paper_is_not_on_it(self):
        out = log.at_risk(ROSTER, [refuse("s1")], classes=CLASSES, targets={"s2"})
        assert out["pupils"] == []
        assert out["summary"]["roster"] == 1 and out["summary"]["no_record"] == 1

    def test_a_class_paper_passes_no_targets_and_keeps_the_whole_roster(self):
        out = log.at_risk(ROSTER, [], classes=CLASSES, targets=None)
        assert out["summary"]["roster"] == 4

    def test_the_newest_refusal_names_the_cause_and_the_device(self):
        rows = [refuse("s1", at=T1, ua=ANDROID),
                refuse("s1", at=T2, ua=IPAD, reason=log.REASON_KEY_MISMATCH),
                refuse("s1", at=T1, ua=SEB_UA)]
        pupil = log.at_risk(ROSTER, rows, classes=CLASSES)["pupils"][0]
        assert pupil["attempts"] == 3
        assert pupil["reason"] == log.REASON_KEY_MISMATCH, (
            "the cause shown has to be the newest one, or the panel sends an admin to "
            "fix a file that was already fixed")
        assert pupil["reason_label"] == log.REASON_LABELS[log.REASON_KEY_MISMATCH]
        assert pupil["device_label"] == log.device_label(IPAD)
        assert pupil["papers"] == 1, "three attempts on one paper is one paper"

    def test_the_newest_row_wins_whatever_order_it_arrived_in(self):
        rows = [refuse("s1", at=T1, ua=ANDROID), refuse("s1", at=T2, ua=IPAD)]
        forward = log.at_risk(ROSTER, rows, classes=CLASSES)["pupils"][0]
        backward = log.at_risk(ROSTER, list(reversed(rows)), classes=CLASSES)["pupils"][0]
        assert forward["device_label"] == backward["device_label"] == log.device_label(IPAD)

    def test_a_pupil_refused_on_two_papers_counts_both(self):
        rows = [refuse("s1", exam="e1"), refuse("s1", exam="e2", at=T2)]
        assert log.at_risk(ROSTER, rows, classes=CLASSES)["pupils"][0]["papers"] == 2

    def test_the_class_label_comes_from_the_roster(self):
        """The panel is about the paper, so the room the pupil is sitting in is the
        room the class name has to come from — not whatever the refusal's profile said.
        """
        pupil = log.at_risk(ROSTER, [refuse("s3", cls="c9")], classes=CLASSES)["pupils"][0]
        assert pupil["class_id"] == "c2"
        assert pupil["class_label"] == "8B"

    def test_an_unnameable_class_is_none_and_never_a_sentence(self):
        """A sentence baked here would be one language's words on a page that follows
        the reader's toggle; the template is the only place that knows which half."""
        out = log.at_risk([{"student_id": "s1", "class_id": "cX", "full_name": "A"}],
                          [refuse("s1")], classes=CLASSES)
        assert out["pupils"][0]["class_label"] is None

    def test_the_devices_are_grouped_by_what_they_are(self):
        rows = [refuse("s1", at=T1, ua=ANDROID),
                refuse("s2", at=T2, ua=ANDROID.replace("SM-A125F", "Z")),
                refuse("s3", at=T3, ua=IPAD)]
        out = log.at_risk(ROSTER, rows, classes=CLASSES)
        assert [d["pupils"] for d in out["devices"]] == [2, 1]
        assert [d["attempts"] for d in out["devices"]] == [2, 1]
        assert out["devices"][0]["label"] == log.device_label(ANDROID)
        assert out["devices"][0]["last_at"] == T2, (
            "the device that was most recently refused is the one to look at first")

    def test_a_shared_string_on_a_different_platform_is_a_different_device(self):
        rows = [refuse("s1", at=T1, ua=ANDROID), refuse("s2", at=T2, ua=IPAD)]
        assert len(log.at_risk(ROSTER, rows, classes=CLASSES)["devices"]) == 2

    def test_the_order_is_total_and_does_not_follow_the_input(self):
        rows = [refuse("s2", at=T1), refuse("s1", at=T1), refuse("s1", at=T2)]
        forward = log.at_risk(ROSTER, rows, classes=CLASSES)["pupils"]
        backward = log.at_risk(ROSTER, list(reversed(rows)), classes=CLASSES)["pupils"]
        assert [p["student_id"] for p in forward] == ["s1", "s2"]
        assert [p["student_id"] for p in forward] == [p["student_id"] for p in backward]

    def test_a_pupil_in_two_of_the_papers_classes_is_still_one_pupil(self):
        roster = [*ROSTER, {"student_id": "s1", "class_id": "c2", "full_name": ""}]
        out = log.at_risk(roster, [refuse("s1")], classes=CLASSES)
        assert out["summary"]["roster"] == 4 and out["summary"]["at_risk"] == 1
        assert out["pupils"][0]["class_id"] == "c1", (
            "the first class that names one wins, so a blank duplicate cannot move a "
            "pupil to the unknown-class bucket")

    def test_a_refusal_with_no_pupil_is_not_a_pupil_here_either(self):
        out = log.at_risk(ROSTER, [{**refuse("s1"), "student_id": None}], classes=CLASSES)
        assert out["summary"]["at_risk"] == 0 and out["summary"]["no_record"] == 4

    def test_an_empty_roster_claims_nothing_at_all(self):
        out = log.at_risk([], [refuse("s1")], classes=CLASSES)
        assert out["pupils"] == [] and out["devices"] == [] and out["classes"] == []
        assert out["summary"] == {"roster": 0, "at_risk": 0, "attempts": 0,
                                  "devices": 0, "no_record": 0, "papers": 0}

    def test_the_per_class_rollup_counts_the_room_and_not_the_refusals(self):
        out = log.at_risk(ROSTER, [refuse("s1"), refuse("s1", at=T2)], classes=CLASSES)
        by_class = {c["label"]: c for c in out["classes"]}
        assert by_class["7A"] == {"class_id": "c1", "label": "7A", "roster": 2,
                                  "at_risk": 1}
        assert by_class["8B"]["roster"] == 2 and by_class["8B"]["at_risk"] == 0

    def test_the_raw_string_travels_beside_the_label(self):
        out = log.at_risk(ROSTER, [refuse("s1", ua=ANDROID)], classes=CLASSES)
        assert out["pupils"][0]["device_ua"] == ANDROID, (
            "two Android phones are told apart by the string, and the label is only "
            "the decision")

    def test_a_pupil_with_no_name_is_still_a_row(self):
        out = log.at_risk([{"student_id": "s1", "class_id": "c1", "full_name": None}],
                          [refuse("s1")], classes=CLASSES)
        assert out["pupils"][0]["name"] is None
        assert out["summary"]["at_risk"] == 1

    def test_the_roster_is_counted_without_the_refusals(self):
        """The three numbers that keep the panel honest: `at_risk` is who has a
        record, `no_record` is who does not, and `roster` is both of them together.
        Rendering the last as the first is how an empty table reads as a green tick.
        """
        out = log.at_risk(ROSTER, [refuse("s1")], classes=CLASSES)
        assert (out["summary"]["at_risk"] + out["summary"]["no_record"]
                == out["summary"]["roster"] == len(ROSTER))


# ── the panel, rendered for a signed-in school admin ─────────────────────────


class TestTheReadinessPanel:
    def test_it_names_the_pupil_the_class_and_the_device(self, app):
        html = _render(app, [], readiness=ready_readiness())
        assert "data-seb-readiness" in html, (
            "the panel did not render, which passes any assertion made from source")
        assert "data-seb-at-risk-pupil" in html
        assert "Ahmad" in html and "7A" in html
        assert "t('Chrome di Android','Chrome on Android')" in html, (
            "the device is named as a pair, so it follows the reader's language")
        assert ANDROID in html, "the raw string is the tooltip, and it has to be there"

    def test_the_paper_it_is_about_is_on_the_page(self, app):
        html = _render(app, [], readiness=ready_readiness())
        assert "UTS Biologi" in html
        assert "t('Terjadwal','Scheduled')" in html

    def test_no_record_is_never_rendered_as_ready(self, app):
        """The one sentence the panel depends on: the log records refusals, so a
        pupil with no row is unknown, and a page that read that as a green tick would
        clear a room on the strength of never having looked."""
        html = _render(app, [], readiness=ready_readiness())
        assert "Tanpa catatan bukan berarti sudah siap" in html
        assert "No record does not mean ready" in html

    def test_the_summary_shows_the_three_numbers(self, app):
        html = _render(app, [], readiness=ready_readiness())
        assert "Punya catatan ditolak" in html and "With a refusal record" in html
        assert "Tanpa catatan apa pun" in html and "With no record at all" in html
        assert "Murid di ujian ini" in html and "Pupils on this paper" in html

    def test_the_picker_carries_the_paper_and_the_window_both_ways(self, app):
        """A picker link that dropped the window would answer a different question
        than the ranking below it; a window chip that dropped the paper would throw
        the admin back to the first paper on every click."""
        html = _render(app, [row()], days=7, readiness=ready_readiness())
        link = "/admin-sekolah/seb-refusals?days=7&exam=p1"
        assert html.count(link) >= 2, (
            "expected the window chip and the picker link to agree, found "
            f"{html.count(link)} occurrences")

    def test_a_school_with_nothing_scheduled_says_so_and_shows_no_table(self, app):
        html = _render(app, [row()], readiness=_readiness())
        assert "Belum ada ujian SEB berjadwal" in html
        assert "No SEB paper with a schedule is still to come" in html
        assert "data-seb-at-risk-pupil" not in html

    def test_a_paper_nobody_has_been_refused_on_says_so_without_promising(self, app):
        chosen = ready_paper()
        readiness = _readiness(papers=[chosen], paper=chosen, roster=ROSTER)
        html = _render(app, [], readiness=readiness)
        assert "Tidak ada murid di ujian ini yang punya catatan ditolak" in html
        assert "That is not a promise the door will work" in html, (
            "an empty at-risk list is the most dangerous thing this panel can say, and "
            "it has to say what it is worth")

    def test_a_capped_picker_says_it_was_capped(self, app):
        chosen = ready_paper()
        html = _render(app, [], readiness=_readiness(papers=[chosen], paper=chosen,
                                                     roster=ROSTER, truncated=True))
        assert "Daftar dibatasi ke ujian SEB terbaru" in html

    def test_the_panel_does_not_pin_a_language(self, app):
        html = _render(app, [], readiness=ready_readiness())
        assert "lang=\"id\"" not in html and "content_lang" not in html


class TestTheReadinessRoute:
    def test_the_roster_comes_from_the_reader_the_teacher_form_writes(self):
        """`exam_targets` owns both directions — the year table first, then
        `profiles.class_id` — so a panel that read the roster itself would show a
        different room than the form that assigns it."""
        source = ROUTES.read_text(encoding="utf-8")
        body = source.split("def _seb_readiness(", 1)[1].split("\n@admin_sekolah_bp", 1)[0]
        assert "exam_targets.roster_for_classes(" in body
        assert "exam_targets.included_student_ids(" in body
        assert "seb_door_log.upcoming_papers(" in body
        assert "seb_door_log.pick_paper(" in body

    def test_a_per_student_papers_target_list_is_read_only_then(self):
        source = ROUTES.read_text(encoding="utf-8")
        body = source.split("def _seb_readiness(", 1)[1].split("\n@admin_sekolah_bp", 1)[0]
        assert 'if paper.get("target_mode") == "students":' in body, (
            "a class paper is its roster; asking for targets it does not have would "
            "narrow the panel to nobody")

    def test_the_readiness_half_passes_every_key_the_page_reads(self, app, monkeypatch):
        """The same question the ranking half is asked, about the new half: a key the
        view forgets is a 500 in front of the admin, and `readiness.summary.roster`
        on an undefined `readiness` raises rather than rendering a blank cell."""
        from flask import g

        from app.routes import admin_sekolah as routes

        captured = {}
        monkeypatch.setattr(routes, "_school_id", lambda: "sch-1")
        monkeypatch.setattr(routes, "get_supabase", lambda: _Supabase([]))
        monkeypatch.setattr(routes, "school_classes", lambda school_id: CLASSES)
        monkeypatch.setattr(routes, "render_template",
                            lambda name, **context: captured.update(context) or "")
        with app.test_request_context("/admin-sekolah/seb-refusals"):
            g.user_id = "adm-1"
            g.user_role = "admin_sekolah"
            g.user_school_id = "sch-1"
            g.tz_offset = 7
            view = getattr(routes.seb_refusals_page, "__wrapped__",
                           routes.seb_refusals_page)
            view()

        template = TEMPLATE.read_text(encoding="utf-8")
        readiness = captured.get("readiness")
        assert isinstance(readiness, dict), "the view passes no readiness at all"
        for name in set(re.findall(r"readiness\.(\w+)", template)):
            assert name in readiness, (
                f"the page reads readiness.{name}, which the view never builds")
        for name in set(re.findall(r"readiness\.summary\.(\w+)", template)):
            assert name in readiness.get("summary", {}), (
                f"the page reads readiness.summary.{name}, which the summary lacks")

    def test_the_page_asks_for_no_key_the_readiness_half_does_not_build(self):
        """The other direction, read off the template: every `readiness.x` name it
        uses has to exist in the shape the view builds even when there is no paper."""
        from app.services import seb_door_log as service

        empty = {"papers": [], "paper": None, "truncated": False,
                 **service.at_risk([], [], classes=CLASSES)}
        template = TEMPLATE.read_text(encoding="utf-8")
        read = set(re.findall(r"readiness\.(\w+)", template))
        assert read <= set(empty), f"the template reads {sorted(read - set(empty))}"
