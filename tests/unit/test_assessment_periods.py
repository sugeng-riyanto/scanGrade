"""The four assessment periods a school actually runs a year on.

Reported from `/vice-principal/dashboard`: the deputy needs to schedule **Mid
Semester, Final Semester, Mock Test / Try Out and School Assessment**, and the
choice has to reach every role — a teacher filing a paper, a pupil reading their
list, a head of school reading the report.

What was already there, and why it was not enough
-------------------------------------------------
``exams.exam_type`` has existed since migration 007 with exactly these words in
its CHECK constraint — ``ulangan, uts, uas, tryout, ljk`` — and **no code ever
read it**: not the exam builder, not a report, not a single template. So the
column proves the vocabulary was already agreed, and the gap is that nothing
could *schedule* a period: there was no row to name a date range, no way to say
which one is running, and nothing that made the choice mean the same thing in
every role's page.

The shape chosen here, and the one thing it refuses
---------------------------------------------------
A period is one row: the kind (the vocabulary above, plus ``asesmen`` for School
Assessment), a name, a start and an end date, and whether it is the **running**
one. Exactly one period is running at a time — that is enforced as a database
invariant with a partial unique index, not as a rule the routes remember — so
"which period is this paper in" has exactly one answer for every reader, which is
what makes it the same answer in a teacher's page and a pupil's list.

Writes are the deputy's, reads are both officials'
--------------------------------------------------
The authority split is the one `app/routes/principal.py` already documents for
invigilation: a vice principal *builds*, a principal *reads*. The write routes
therefore live under ``/vice-principal/*`` and none under ``/principal/*``, and
that is checked structurally rather than promised.

These guards follow `tests/unit/test_invigilation.py`: the service is driven
through a fake Supabase and the routes are read as source, because the property
worth pinning is *where the school comes from* and *which decorator a route
carries* — neither of which a request would show more clearly.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRINCIPAL_ROUTES = ROOT / "app" / "routes" / "principal.py"
MIGRATION = ROOT / "supabase" / "migrations" / "050_assessment_periods.sql"
REASON_PARTIAL = (ROOT / "app" / "templates" / "shared"
                  / "_assessment_period_reasons.html")
OFFICIAL_PAGE = ROOT / "app" / "templates" / "principal" / "assessment_periods.html"


# ── the vocabulary ───────────────────────────────────────────────────────────

def test_the_four_kinds_are_exactly_the_ones_a_school_runs_on():
    from app.services import assessment_periods as ap

    assert set(ap.KINDS) == {"mid_semester", "final_semester", "tryout", "asesmen"}


def test_the_kinds_line_up_with_the_exam_type_check_constraint():
    """The period a paper belongs to and the paper's own ``exam_type`` are the
    same words, or the two features would describe one paper differently."""
    from app.services import assessment_periods as ap

    sql = MIGRATION.read_text(encoding="utf-8-sig")
    # This migration is the last word on the column, so it has to (a) drop 007's
    # list and (b) accept every kind. Missing (a) leaves the old constraint in
    # force and the widening does nothing, which is the failure worth catching.
    assert "DROP CONSTRAINT IF EXISTS exams_exam_type_check" in sql, (
        "the old exam_type CHECK is not dropped, so widening it would do nothing")
    bodies = re.findall(r"CHECK \(exam_type IN \(([^)]*)\)\)", sql, re.S)
    assert bodies, "this migration does not declare exam_type's vocabulary"
    accepted = set(re.findall(r"'([a-z_]+)'", bodies[-1]))
    for kind in ap.KINDS:
        assert kind in accepted, (
            f"{kind} is a period a deputy can schedule but exams.exam_type does "
            f"not accept it, so no paper could ever be filed under it")
    # Additive: the words 007 already accepted are still accepted, so a paper
    # filed under the old vocabulary is never refused by this widening.
    for old in ("ulangan", "uts", "uas", "ljk"):
        assert old in accepted, f"the widening dropped {old}, which rows already use"


# ── the service: scope, validation, and one running period ───────────────────

class _Resp:
    def __init__(self, data=None):
        self.data = data


class _Query:
    """A query that really filters, so a missing scope is a real refusal.

    A fake that answered an `update` with the payload it was handed would make
    every scoped write look like it succeeded, and the guard would pass for the
    wrong reason. So this one matches against stored rows the way the database
    does: an update whose filter matches nothing changes nothing and answers
    nothing, which is exactly how `save_period` learns the row is not this
    school's.
    """

    def __init__(self, store, table):
        self.store, self.table = store, table
        self.payload = None
        self.filters = []
        self.mode = "select"

    def select(self, *a, **k):
        self.mode = "select"
        return self

    def insert(self, payload):
        self.mode, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.mode, self.payload = "update", payload
        return self

    def delete(self):
        self.mode = "delete"
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def neq(self, column, value):
        self.filters.append(("!=" + column, value))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def maybe_single(self):
        return self

    def _matches(self, row):
        for key, value in self.filters:
            if key.startswith("!="):
                if str(row.get(key[2:])) == str(value):
                    return False
            elif str(row.get(key)) != str(value):
                return False
        return True

    def execute(self):
        self.store.calls.append((self.table, tuple(self.filters), self.payload,
                                 self.mode))
        if self.table in self.store.fail:
            raise RuntimeError("boom")
        rows = self.store.rows.setdefault(self.table, [])
        if self.mode == "insert":
            row = dict(self.payload)
            row.setdefault("id", f"p{len(rows) + 1}")
            rows.append(row)
            return _Resp([row])
        if self.mode == "update":
            hit = [r for r in rows if self._matches(r)]
            for row in hit:
                row.update(self.payload)
            return _Resp(hit)
        if self.mode == "delete":
            hit = [r for r in rows if self._matches(r)]
            self.store.rows[self.table] = [r for r in rows if r not in hit]
            return _Resp(hit)
        return _Resp([r for r in rows if self._matches(r)])


class FakeSupabase:
    def __init__(self, rows=None, fail=()):
        self.rows = {k: [dict(r) for r in v] for k, v in (rows or {}).items()}
        self.fail = set(fail)
        self.calls = []

    def table(self, name):
        return _Query(self, name)


def _write_call(store, table, mode):
    """The one call that wrote `table` in `mode`, wherever it sits in the run.

    A period write now ends by re-deriving every paper's tag
    (`retag_school_exams`), so the period write is no longer the last call. These
    assertions are about *what* was written and to whose school, not about it being
    last, so they find it by shape.
    """
    hits = [c for c in store.calls if c[0] == table and c[3] == mode]
    assert len(hits) == 1, f"expected one {mode} on {table}, saw {len(hits)}"
    return hits[0]


def _period(**over):
    row = {"id": "p1", "school_id": "s1", "kind": "mid_semester",
           "name": "UTS Ganjil", "start_date": "2026-09-01",
           "end_date": "2026-09-10", "is_active": True}
    row.update(over)
    return row


class TestTheServiceScopesEveryWrite:
    def test_creating_a_period_writes_the_callers_school_and_nothing_from_the_form(self):
        from app.services import assessment_periods as ap

        sb = FakeSupabase()
        out = ap.save_period(sb, "s1", period_id=None, kind="tryout",
                             name="Try Out 1", start_date="2026-11-01",
                             end_date="2026-11-05", is_active=False)

        assert out["ok"] is True
        table, _filters, payload, mode = _write_call(sb, "assessment_periods", "insert")
        assert table == "assessment_periods" and mode == "insert"
        assert payload["school_id"] == "s1", (
            "the school must come from the caller's session, never from the form")
        assert payload["kind"] == "tryout"

    def test_a_kind_outside_the_vocabulary_is_refused_before_any_write(self):
        from app.services import assessment_periods as ap

        sb = FakeSupabase()
        out = ap.save_period(sb, "s1", period_id=None, kind="ulangan_mingguan",
                             name="X", start_date="2026-09-01",
                             end_date="2026-09-02")

        assert out["ok"] is False and out["reason"] == "bad_kind"
        assert not sb.calls, "a refused kind still reached the database"

    def test_an_end_before_the_start_is_refused_before_any_write(self):
        from app.services import assessment_periods as ap

        sb = FakeSupabase()
        out = ap.save_period(sb, "s1", period_id=None, kind="mid_semester",
                             name="UTS", start_date="2026-09-10",
                             end_date="2026-09-01")

        assert out["ok"] is False and out["reason"] == "bad_dates"
        assert not sb.calls

    def test_a_period_without_a_name_is_refused(self):
        from app.services import assessment_periods as ap

        sb = FakeSupabase()
        out = ap.save_period(sb, "s1", period_id=None, kind="mid_semester",
                             name="   ", start_date="2026-09-01",
                             end_date="2026-09-02")

        assert out["ok"] is False and out["reason"] == "name_required"
        assert not sb.calls

    def test_editing_a_period_of_another_school_is_refused(self):
        """The school in the filter is what makes this a refusal rather than a
        cross-NPSN write, and it has to be the *edit*, not only the create."""
        from app.services import assessment_periods as ap

        sb = FakeSupabase(rows={"assessment_periods": []})
        out = ap.save_period(sb, "s1", period_id="p-other-school",
                             kind="mid_semester", name="UTS",
                             start_date="2026-09-01", end_date="2026-09-02")

        assert out["ok"] is False and out["reason"] == "not_found"
        _table, filters, _payload, _mode = sb.calls[-1]
        assert ("school_id", "s1") in filters, (
            "the update was not scoped to the caller's school")

    def test_making_one_period_running_stops_the_other(self):
        from app.services import assessment_periods as ap

        sb = FakeSupabase()
        ap.save_period(sb, "s1", period_id=None, kind="final_semester",
                       name="UAS", start_date="2026-12-01",
                       end_date="2026-12-10", is_active=True)

        clears = [c for c in sb.calls
                  if c[2] is not None and c[2].get("is_active") is False]
        assert ("school_id", "s1") in clears[-1][1], (
            "the clear was not scoped to this school, so it could stop another "
            "school's running period")

    def test_listing_is_scoped_to_the_callers_school(self):
        from app.services import assessment_periods as ap

        sb = FakeSupabase(rows={"assessment_periods": [_period()]})
        rows = ap.list_periods(sb, "s1")

        assert rows and rows[0]["id"] == "p1"
        table, filters, _payload, _mode = sb.calls[-1]
        assert table == "assessment_periods"
        assert ("school_id", "s1") in filters

    def test_deleting_is_scoped_to_the_callers_school(self):
        from app.services import assessment_periods as ap

        sb = FakeSupabase(rows={"assessment_periods": [_period()]})
        out = ap.delete_period(sb, "s1", "p1")

        assert out["ok"] is True
        table, filters, _payload, mode = _write_call(sb, "assessment_periods", "delete")
        assert table == "assessment_periods" and mode == "delete"
        assert ("id", "p1") in filters and ("school_id", "s1") in filters

    def test_the_running_period_is_read_by_school(self):
        from app.services import assessment_periods as ap

        sb = FakeSupabase(rows={"assessment_periods": [_period()]})
        active = ap.active_period(sb, "s1")

        assert active and active["kind"] == "mid_semester"
        _table, filters, _payload, _mode = sb.calls[-1]
        assert ("school_id", "s1") in filters and ("is_active", True) in filters

    def test_a_read_that_fails_is_an_empty_read_not_a_500(self):
        from app.services import assessment_periods as ap

        sb = FakeSupabase(fail=("assessment_periods",))
        assert ap.list_periods(sb, "s1") == []
        assert ap.active_period(sb, "s1") is None


# ── the routes: the deputy writes, the head reads, nobody else ───────────────

class TestTheRoutesSayWhoMayDoWhat:
    def test_both_prefixes_serve_the_page(self):
        source = PRINCIPAL_ROUTES.read_text(encoding="utf-8")
        assert "/principal/assessment-periods" in source
        assert "/vice-principal/assessment-periods" in source

    def test_every_write_is_on_the_deputys_prefix_and_carries_its_guard(self):
        source = PRINCIPAL_ROUTES.read_text(encoding="utf-8")
        starts = [m.start() for m in re.finditer(r"@\w*bp\.route\(", source)]
        starts.append(len(source))
        writes = 0
        for start, end in zip(starts, starts[1:]):
            block = source[start:end]
            if "assessment-periods" not in block:
                continue
            if 'methods=["POST"]' not in block:
                continue
            writes += 1
            assert "/vice-principal/assessment-periods" in block, (
                "a write route was registered outside the deputy's prefix")
            assert "@vice_principal_required" in block, (
                "a writing route has no @vice_principal_required")
        assert writes >= 2, "expected create/edit/delete writes on the deputy's prefix"

    def test_the_head_has_no_write_route_for_a_period(self):
        source = PRINCIPAL_ROUTES.read_text(encoding="utf-8")
        writes = re.findall(
            r'@principal_bp\.route\("(/principal[^"]*)",\s*methods=\["POST"\]\)',
            source)
        assert not [w for w in writes if "assessment-periods" in w], writes

    def test_the_page_route_is_behind_the_officials_guard(self):
        """The head's page carries `@principal_required`; the deputy's carries
        `@vice_principal_required`. Either is a guard, and which one a prefix
        carries is what the role table in docs/RBAC.md says it should be."""
        source = PRINCIPAL_ROUTES.read_text(encoding="utf-8")
        starts = [m.start() for m in re.finditer(r"@\w*bp\.route\(", source)]
        starts.append(len(source))
        seen = 0
        for start, end in zip(starts, starts[1:]):
            block = source[start:end]
            if "assessment-periods" not in block or 'methods=["POST"]' in block:
                continue
            seen += 1
            assert ("@principal_required" in block
                    or "@vice_principal_required" in block
                    or "@school_official_required" in block), block[:90]
        assert seen >= 2, "expected both officials' pages to be registered"

    def test_no_route_takes_the_school_from_the_request(self):
        source = PRINCIPAL_ROUTES.read_text(encoding="utf-8")
        starts = [m.start() for m in re.finditer(r"@\w*bp\.route\(", source)]
        starts.append(len(source))
        for start, end in zip(starts, starts[1:]):
            block = source[start:end]
            if "assessment-periods" not in block:
                continue
            for stolen in ('request.form.get("school_id")',
                           'request.args.get("school_id")',
                           'request.values.get("school_id")'):
                assert stolen not in block, (
                    "a route reads the school from the request instead of the session")
            # The page route reaches the school through the one helper that builds
            # it; the write routes resolve it in their own body.
            assert "_school_id()" in block or "_periods_page(" in block, block[:90]


# ── the schema ───────────────────────────────────────────────────────────────

class TestTheSchemaSaysTheSameThing:
    def test_the_table_carries_its_own_school(self):
        sql = MIGRATION.read_text(encoding="utf-8-sig")
        assert "CREATE TABLE IF NOT EXISTS public.assessment_periods" in sql
        assert re.search(r"school_id UUID NOT NULL REFERENCES public\.schools\(id\)", sql)
        assert "ENABLE ROW LEVEL SECURITY" in sql

    def test_one_running_period_per_school_is_a_database_invariant(self):
        sql = MIGRATION.read_text(encoding="utf-8-sig")
        assert "CREATE UNIQUE INDEX IF NOT EXISTS" in sql
        assert re.search(r"ON public\.assessment_periods\s*\(school_id\)\s*WHERE\s+is_active",
                         sql), (
            "nothing in the database stops two periods from being the running one, "
            "so 'which period is this paper in' has two answers")

    def test_the_kind_is_constrained_to_the_vocabulary(self):
        sql = MIGRATION.read_text(encoding="utf-8-sig")
        assert "CHECK (kind IN (" in sql
        for kind in ("mid_semester", "final_semester", "tryout", "asesmen"):
            assert f"'{kind}'" in sql, kind

    def test_every_policy_names_the_caller_and_the_school(self):
        sql = MIGRATION.read_text(encoding="utf-8-sig")
        policies = re.findall(r'CREATE POLICY "([^"]+)" ON public\.(\w+)\s+'
                              r"FOR (SELECT|INSERT|UPDATE|DELETE|ALL)\s+TO (\w+)", sql)
        assert policies, "no policy was created"
        assert not [p for p in policies if p[3] != "authenticated"], "a policy is open"
        for name, _table, _verb, _role in policies:
            body = sql.split(f'CREATE POLICY "{name}"')[1].split(";")[0]
            assert "public._user_school_id()" in body, (
                f"{name} does not compare the row's school with the caller's")

    def test_the_migration_is_additive_and_idempotent(self):
        sql = MIGRATION.read_text(encoding="utf-8-sig")
        assert not re.search(r"DROP\s+(TABLE|COLUMN)", sql)
        assert not re.search(r"^\s*BEGIN\s*;", sql, re.M)
        assert not re.search(r"^\s*COMMIT\s*;", sql, re.M)
        assert not re.findall(r"CREATE TABLE (?!IF NOT EXISTS)", sql)
        for policy in re.findall(r'CREATE POLICY "([^"]+)"', sql):
            assert f'DROP POLICY IF EXISTS "{policy}"' in sql, (
                f"{policy} is created without being dropped first")


# ── the pages can say why ────────────────────────────────────────────────────

class TestThePagesCanSayWhy:
    def test_every_refusal_has_a_sentence_in_both_languages(self):
        from app.services import assessment_periods as ap

        partial = REASON_PARTIAL.read_text(encoding="utf-8-sig")
        missing = [key for key in ap.REFUSALS
                   if not re.search(rf"'{key}':\s*\[\s*'[^']+',\s*'[^']+',?\s*\]", partial)]
        assert not missing, f"a refusal renders as an empty alert: {missing}"

    def test_the_page_offers_the_four_kinds_and_shows_the_running_one(self):
        page = OFFICIAL_PAGE.read_text(encoding="utf-8-sig")
        for kind in ("mid_semester", "final_semester", "tryout", "asesmen"):
            assert kind in page, f"the form cannot pick {kind}"
        assert "reason_note" in page, "the page refuses a write without saying why"


# ── the structural gates must know about the new routes ──────────────────────

def test_the_period_writes_are_named_in_the_year_lock_gate():
    source = (ROOT / "tests" / "unit" / "test_year_lock.py").read_text(
        encoding="utf-8-sig")
    assert "assessment-periods" in source, (
        "a new write route arrived without the closed-year gate knowing it")


def test_the_period_segment_is_named_in_the_trail():
    source = (ROOT / "app" / "utils" / "breadcrumbs.py").read_text(encoding="utf-8-sig")
    base = (ROOT / "app" / "templates" / "base.html").read_text(encoding="utf-8-sig")
    assert "assessment-periods" in source or "assessment-periods" in base, (
        "the trail would print this segment title-cased")


# ── the school admin can run the calendar without a vice principal ───────────
#
# The window the deputy owns is the *school's*, not the deputy's. A small school
# that never created the role would otherwise have nobody who can name a UTS's
# dates, so the account that administers the school inherits the same controls —
# at its own prefix and behind its own guard, not by loosening the deputy's.

ADMIN_ROUTES = ROOT / "app" / "routes" / "admin_sekolah.py"
ADMIN_PREFIX = "/admin-sekolah/assessment-periods"


def _blocks(source: str):
    """Each `@blueprint.route(…)` and the view beneath it, as one string."""
    starts = [m.start() for m in re.finditer(r"@\w*bp\.route\(", source)]
    starts.append(len(source))
    return [source[a:b] for a, b in zip(starts, starts[1:])]


def _admin_period_blocks():
    """Only the blocks whose *route decorator* is the assessment calendar.

    Keyed on the decorator line, not on the word appearing anywhere: the comment
    above the block names the URL, so a substring scan would fold it into the
    route that happens to sit above it.
    """
    source = ADMIN_ROUTES.read_text(encoding="utf-8")
    return [b for b in _blocks(source)
            if b.lstrip().startswith('@admin_sekolah_bp.route("/assessment-periods')]


class TestTheSchoolAdminCanRunTheCalendar:
    def test_the_admin_prefix_serves_the_calendar(self):
        source = ADMIN_ROUTES.read_text(encoding="utf-8")
        assert re.search(r'@admin_sekolah_bp\.route\("/assessment-periods"',
                         source), (
            "the school admin has no GET route for the assessment calendar")

    def test_the_admin_calendar_is_registered_in_the_url_map(self, app):
        rules = {rule.rule: rule for rule in app.url_map.iter_rules()}
        assert ADMIN_PREFIX in rules, (
            "the school admin has no door to the assessment calendar")
        save = ADMIN_PREFIX + "/save"
        assert save in rules and "POST" in rules[save].methods, save

    def test_every_admin_write_carries_the_admin_guard_on_its_own_prefix(self):
        writes = 0
        for block in _admin_period_blocks():
            if 'methods=["POST"]' not in block:
                continue
            writes += 1
            assert "@admin_sekolah_required" in block, block[:90]
            assert '@admin_sekolah_bp.route("/assessment-periods' in block, (
                "an admin calendar write was registered outside the admin's own "
                "blueprint, so the admin prefix does not describe it: " + block[:90])
        assert writes >= 2, "expected save and delete writes on the admin's prefix"

    def test_the_admin_page_renders_the_same_template_with_write_on(self):
        pages = [b for b in _admin_period_blocks() if 'methods=["POST"]' not in b]
        assert len(pages) == 1, "the admin calendar is one page"
        for block in pages:
            assert 'principal/assessment_periods.html' in block, block[:140]
            assert "can_write=True" in block, block[:140]
            assert "period_save_url" in block and "period_delete_base" in block, block[:140]

    def test_the_admin_route_takes_the_school_from_the_session(self):
        blocks = _admin_period_blocks()
        assert blocks, "no admin assessment-period route was found"
        for block in blocks:
            for stolen in ('request.form.get("school_id")',
                           'request.args.get("school_id")',
                           'request.values.get("school_id")'):
                assert stolen not in block, block[:90]
            assert "_school_id()" in block, block[:90]


class TestOneCalendarTwoDoorsTheTemplateTakesItsUrls:
    """Both writers render one page, so its forms cannot hardcode one door."""

    def test_the_template_does_not_hardcode_the_deputys_prefix(self):
        page = OFFICIAL_PAGE.read_text(encoding="utf-8")
        assert "period_save_url" in page and "period_delete_base" in page
        assert 'action="/vice-principal/assessment-periods/save"' not in page, (
            "the form action is hardcoded, so the admin page would post to the "
            "deputy's door")

    def test_both_callers_pass_their_own_write_urls(self):
        for path, prefix in ((PRINCIPAL_ROUTES, "/vice-principal/assessment-periods"),
                             (ADMIN_ROUTES, ADMIN_PREFIX)):
            source = path.read_text(encoding="utf-8")
            assert "period_save_url" in source, path
            assert f'"{prefix}/save"' in source or f"'{prefix}/save'" in source, path
            assert "period_delete_base" in source, path

    def test_the_admin_menu_links_the_calendar(self):
        base = (ROOT / "app" / "templates" / "base.html").read_text(encoding="utf-8-sig")
        assert ADMIN_PREFIX in base, (
            "the admin calendar is reachable only by typing its URL")


class TestTheAdminSaveUsesTheSessionSchool:
    """The form supplies every field *except* whose calendar it is.

    A `school_id` in the body must be inert: the write is the session's school, or
    an admin could schedule — and delete — inside another school's calendar. The
    guard's own permit/refuse is asserted structurally above; these drive the view
    body directly, because a real sign-in would prove the login, not the scope.
    """

    @staticmethod
    def _view(view):
        """The view under its `login_required`/role wrapper, called directly."""
        return getattr(view, "__wrapped__", view)

    def _fake(self, seen):
        from types import SimpleNamespace
        return SimpleNamespace(
            save_period=lambda sb, school_id, **kw: (
                seen.update({"school": school_id, "kw": kw}),
                {"ok": True, "reason": ""})[1],
            delete_period=lambda sb, school_id, period_id: (
                seen.update({"delete_school": school_id, "period": period_id}),
                {"ok": True, "reason": ""})[1],
            list_periods=lambda *a, **k: [],
            active_period=lambda *a, **k: None,
            KINDS=("mid_semester", "final_semester", "tryout", "asesmen"),
        )

    def test_a_form_school_id_cannot_redirect_the_write(self, app, monkeypatch):
        from flask import g
        from app.routes import admin_sekolah as adm
        seen: dict = {}
        monkeypatch.setattr(adm, "assessment_periods", self._fake(seen))
        monkeypatch.setattr(adm, "get_supabase", lambda: object())
        with app.test_request_context(
                "/admin-sekolah/assessment-periods/save", method="POST",
                data={"kind": "mid_semester", "name": "UTS Ganjil",
                      "start_date": "2026-09-01", "end_date": "2026-09-05",
                      "school_id": "somebody-elses-school"}):
            g.user_id = "admin-1"
            g.user_school_id = "school-9"
            resp = self._view(adm.assessment_period_save)()
        assert seen["school"] == "school-9", (
            "the write took the school from the form instead of the session")
        assert seen["kw"]["name"] == "UTS Ganjil"
        assert resp.status_code == 302

    def test_delete_is_scoped_to_the_session_school_too(self, app, monkeypatch):
        from flask import g
        from app.routes import admin_sekolah as adm
        seen: dict = {}
        monkeypatch.setattr(adm, "assessment_periods", self._fake(seen))
        monkeypatch.setattr(adm, "get_supabase", lambda: object())
        with app.test_request_context("/admin-sekolah/assessment-periods/p-1/delete",
                                      method="POST"):
            g.user_id = "admin-1"
            g.user_school_id = "school-9"
            resp = self._view(adm.assessment_period_delete)("p-1")
        assert seen["delete_school"] == "school-9"
        assert seen["period"] == "p-1"
        assert resp.status_code == 302
