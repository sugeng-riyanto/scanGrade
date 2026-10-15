"""Who could not get in, ranked — the school-wide reading of the SEB door log.

A teacher's SEB panel answers the question for one paper. An admin running a morning
of sittings needs it for the whole school, and *before* the room fills: which class
keeps being turned away, and which file reached nobody. Migration 064 wrote the index
for that read (`idx_seb_door_refusal_school_created`) and said the page did not exist
yet; `seb_door_log.rank_refusals` and `/admin-sekolah/seb-refusals` are that page.

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
               "windows": log.WINDOWS, "class_unknown": log.NO_CLASS_LABEL, **ranking}
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
        html = _render(app, [row()])
        tables = re.findall(r"<table[^>]*>", html)
        assert len(tables) == 3, f"expected three tables, found {len(tables)}"
        for table in tables:
            assert "min-w-[" in table, (
                "a table without a min-width squeezes instead of scrolling on a "
                "phone, which is the layout rule this suite keeps")
        assert html.count("overflow-x-auto") >= len(tables)

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

        needed = {"days", "windows", "class_unknown", "classes", "papers",
                  "reasons", "recent", "summary"}
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
                              r"class_unknown)\b", source))
        ranking = log.rank_refusals([row()], classes=CLASSES, exams=EXAMS)
        view_keys = {"days", "windows", "class_unknown"} | set(ranking)
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
