"""The shipped pages, measured in a browser on every push, not only on the box.

Three measurements point a real browser at this app — the exam builder's 44px finger
floor (`deploy/touch_gate.py`), the pupil's exam page with the exception question's
row on it (`deploy/exam_render_gate.py`), and the handshake page's own script (the
client half of `deploy/seb_door_gate.py`) — and all three used to run only on the
deploy box, against the live database, after a release had already been fetched.
`deploy/browser_gate.py` runs them against the app served out of *this checkout* by
`deploy/fixture_app.py`, and `.github/workflows/browser-gate.yml` runs that on every
push.

These are the guards, and they are deliberately split three ways, because the three
things that can rot are different:

* the **verdict**, which is pure arithmetic over the parts (`_verdict`) — including
  the two rules that keep it honest: a finding outranks a gap, and a part this
  release does not ship is its own answer rather than a pass;
* the **handshake page's reading**, judged sentence by sentence (`seb_findings`) —
  the one measurement whose defects are not geometry, so nothing else can see them;
* the **fixture**, which is the floor everything above stands on: if it stopped
  serving the two pages (a renamed route, an import that starts needing a real
  database), every part would answer "could not measure" and a green run would mean
  nothing. So the fixture is driven here — a signed-in request for each page, and the
  paper's own question types — rather than trusted.

The live browser run is `deploy/browser_gate.py`'s own job, and the box still runs
the gates it always did: a green fixture is not evidence about the box's database,
accounts or TLS.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
GATE_PATH = ROOT / "deploy" / "browser_gate.py"
FIXTURE_PATH = ROOT / "deploy" / "fixture_app.py"
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "browser-gate.yml"


def _load(path: Path, name: str):
    """Import a `deploy/` module by path — `deploy/` is not a package."""
    if not path.exists():
        pytest.fail(f"{path.relative_to(ROOT)} does not exist")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gate():
    return _load(GATE_PATH, "sg_browser_gate")


@pytest.fixture(scope="module")
def fixture():
    return _load(FIXTURE_PATH, "sg_fixture_app")


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _branches_of_run(source: str) -> dict[str, set[str]]:
    """What each `if "<part>" in only:` branch of `run()` actually calls.

    Read as syntax, so the branch's condition is matched *whole* (`"touch" in only`,
    not `"touch" in only and False`) and the calls are the ones inside it.
    """
    import ast

    parts: dict[str, set[str]] = {}
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        # `ast.unparse` writes a string constant with single quotes, whatever the
        # source used; the comparison is about the condition's *shape*, so the quoting
        # is normalised rather than spelled twice.
        condition = ast.unparse(node.test).replace('"', "'")
        for name in ("touch", "render", "seb"):
            if condition != f"'{name}' in only":
                continue
            calls = {ast.unparse(call.func) for call in ast.walk(node)
                     if isinstance(call, ast.Call)}
            parts.setdefault(name, set()).update(calls)
    return parts


def _shell_blocks(text: str) -> list[str]:
    """Every `run:` script in a workflow, dedented.

    Enough YAML to be checked, and deliberately not a parser: the repository has no
    YAML dependency and adding one to read its own workflow would be a test that
    proves the dependency. What is read here is one block scalar per step, by its own
    indentation, which is all the shape these files use.
    """
    lines = text.splitlines()
    blocks: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.strip() in ("run: |", "run: >"):
            indent = len(line) - len(line.lstrip())
            body = []
            index += 1
            while index < len(lines):
                following = lines[index]
                if following.strip() and (len(following) - len(following.lstrip())) <= indent:
                    break
                body.append(following[indent + 2:] if len(following) > indent + 2 else "")
                index += 1
            blocks.append("\n".join(body))
            continue
        index += 1
    return blocks


def _without_comments(source: str) -> str:
    """A module's code, with its comments and docstrings blanked out.

    A guard that reads prose fires on a *sentence*: this file's own docstrings name
    `deploy/touch_gate.py`, and a rule like "the reading is written before the verdict"
    is satisfied by a paragraph explaining it. The repository has written this trap
    down more than once (the i18n sweep read a JS comment as copy; the launcher's
    forbidden-words check lit up on the block's own header).

    Blanked **in place** rather than rebuilt from tokens: a rebuild loses the spaces
    between them, and a guard looking for `args.json_out` would be reading
    `args . json_out`. The code keeps its own text and its own line numbers; only the
    comments and the docstrings become blanks.
    """
    import io
    import tokenize

    lines = source.splitlines(keepends=True)
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            is_docstring = (token.type == tokenize.STRING
                            and token.string.lstrip().startswith(('"""', "'''")))
            if token.type != tokenize.COMMENT and not is_docstring:
                continue
            (srow, scol), (erow, ecol) = token.start, token.end
            if srow == erow:
                line = lines[srow - 1]
                lines[srow - 1] = line[:scol] + " " * (ecol - scol) + line[ecol:]
                continue
            first = lines[srow - 1]
            lines[srow - 1] = first[:scol] + ("\n" if first.endswith("\n") else "")
            for row in range(srow + 1, erow):
                line = lines[row - 1]
                lines[row - 1] = "\n" if line.endswith("\n") else ""
            last = lines[erow - 1]
            lines[erow - 1] = " " * ecol + last[ecol:]
    except tokenize.TokenError:                                   # pragma: no cover
        return source
    return "".join(lines)


# ── the exit contract ────────────────────────────────────────────────────────


class TestTheExitContract:
    """0 measured and clean, 1 a finding, 2 nothing could be measured."""

    def test_without_a_browser_it_cannot_measure(self):
        """Exit 2 is "we could not look", and a CI job must not read it as a pass.

        The workflow turns this into a failed job on purpose — see the last assertion
        in `TestTheWorkflow`. What is asserted here is the gate's own half: a box with
        no browser answers 2 and says so, rather than 0.
        """
        env = dict(os.environ)
        env["SG_CHROME"] = ""
        result = subprocess.run(
            [sys.executable, str(GATE_PATH), "--base-url", "http://127.0.0.1:9",
             "--teacher", "t@example.test:pw", "--student", "s@example.test:pw",
             "--seb-exam", "some-exam"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env, timeout=120,
        )
        assert result.returncode == 2, (
            f"with no browser the gate answered {result.returncode}, not 2\n"
            f"{result.stdout}\n{result.stderr}")
        assert "could not measure" in (result.stdout + result.stderr).lower()

    def test_a_part_that_is_not_a_part_is_not_measured(self, gate):
        """A typo in `--only` must not run a subset and call it a full run."""
        code, lines, payload = gate.run(only=("wombat",))
        assert code == gate.EXIT_CANNOT_MEASURE
        assert payload["reason"] == "unknown-part"
        assert "wombat" in "\n".join(lines)
        assert gate.run(only=())[0] == gate.EXIT_CANNOT_MEASURE, (
            "an empty subset measured nothing and said nothing")
        # ...and through the CLI, which is how CI would ever pass one.
        result = subprocess.run(
            [sys.executable, str(GATE_PATH), "--only", "wombat"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120,
        )
        assert result.returncode == 2, result.stdout + result.stderr
        assert "wombat" in result.stdout

    def test_a_finding_outranks_a_gap(self, gate):
        """`broken` must never be reported as `could not tell`."""
        assert gate._verdict({
            "touch": {"state": gate.STATE_UNMEASURED},
            "render": {"state": gate.STATE_FINDING},
        }) == gate.EXIT_FINDING

    def test_an_unmeasured_part_is_never_a_pass(self, gate):
        assert gate._verdict({
            "touch": {"state": gate.STATE_OK},
            "render": {"state": gate.STATE_UNMEASURED},
        }) == gate.EXIT_CANNOT_MEASURE

    def test_a_part_this_release_does_not_ship_does_not_fail_the_run(self, gate):
        """But it is its own state, printed by name — never folded into `ok`.

        This is the whole reason `not-shipped` exists: a release that drops the
        handshake page is a release that dropped it, and a run that answered `ok`
        would be a run that could not tell that from a page that works.
        """
        assert gate.STATE_NOT_SHIPPED != gate.STATE_OK
        assert gate._verdict({
            "touch": {"state": gate.STATE_OK},
            "seb": {"state": gate.STATE_NOT_SHIPPED},
        }) == gate.EXIT_OK

    def test_a_part_that_raises_is_not_measured_rather_than_a_finding(self, gate):
        """A traceback out of CI reads as a defect in the release, and is not one."""
        def boom():
            raise RuntimeError("the proxy ate the socket")

        part = gate._guarded("seb half", boom)
        assert part["state"] == gate.STATE_UNMEASURED
        assert "the proxy ate the socket" in "\n".join(part["lines"])

    def test_a_driven_gate_s_own_exit_code_is_its_state(self, gate):
        assert gate._state_for(0) == gate.STATE_OK
        assert gate._state_for(1) == gate.STATE_FINDING
        assert gate._state_for(2) == gate.STATE_UNMEASURED
        assert gate._state_for(3) == gate.STATE_UNMEASURED


# ── the handshake page's script, as a verdict ────────────────────────────────


def _reading(**overrides):
    """A reading of the handshake page, with the values a real headless Chrome
    produced.

    Taken from a run of `deploy/browser_gate.py` against the fixture on 2026-10-12:
    `state='refused'`, `initialized=True`, `refused_shown=True`, `seb_api=False`,
    `reports=1`, `reports_total=2`, `reason='no_client'`, `token=True`, `claims=0`,
    `status=200`, landed on the handshake page — plus the retry phase (C7): the
    control was there and pressable, the press put one report on the refusal route,
    the route took it, it carried `no_key`, and the pupil was still on the handshake
    page afterwards. The clean case below is that reading, not an idealised one.

    Three of these fields are *derived* on the reading by the SEB gate
    (`ClientReading.retry_dead` / `retry_unfiled` / `retry_away`), so this file does
    not re-derive them: they are inputs here, and `test_seb_door_gate.py` drives the
    derivation itself against the shipped class. `test_the_shipped_reading_is_judged_
    clean` below closes the loop by handing this verdict a real `ClientReading`.
    """
    fields = dict(state="refused", initialized=True, refused_shown=True, seb_api=False,
                  reports=1, reports_total=2, reason="no_client", token=True,
                  claims=0, status=200,
                  landed="/student/exams/aaa/seb-claim",
                  retry_control=True, retry_asks=1, retry_requests=1, retry_posts=1,
                  retry_reason="no_key", retry_status=200, retry_navigations=0,
                  retry_landed="/student/exams/aaa/seb-claim",
                  retry_dead=False, retry_unfiled=False, retry_away=False)
    fields.update(overrides)
    reading = SimpleNamespace(**fields)
    reading.relocked = str(fields["landed"]).endswith("/seb-claim")
    return reading


class TestTheHandshakePagesVerdict:
    """The one measurement whose defects are not geometry."""

    def test_the_reading_a_real_browser_produced_is_clean(self, gate):
        assert gate.seb_findings(_reading()) == []

    @pytest.mark.parametrize("override,needle", [
        ({"initialized": False}, "script never ran"),
        ({"seb_api": True}, "not the population"),
        ({"state": "checking"}, "settled on"),
        ({"refused_shown": False}, "refusal panel"),
        ({"reports": 0}, "no refusal report"),
        ({"reason": "some_other_reason"}, "some_other_reason"),
        ({"token": False}, "CSRF"),
        ({"claims": 1}, "claim"),
        ({"status": 403}, "403"),
        ({"landed": "/student/exams/aaa"}, "admitted this browser"),
    ], ids=["script-never-ran", "wrong-population", "never-settled", "panel-missing",
            "no-report", "wrong-reason", "no-csrf", "posted-a-claim",
            "route-refused", "paper-admitted"])
    def test_each_defect_is_its_own_sentence(self, gate, override, needle):
        problems = gate.seb_findings(_reading(**override))
        assert len(problems) == 1, problems
        assert needle.lower() in problems[0].lower(), problems[0]

    def test_the_refusal_reason_is_the_one_the_page_and_the_gate_share(self, gate):
        """A reworded reason must be a red test, not a reading that never matches.

        `no_client` is the page's own contract with `seb_door_log.CLIENT_REASONS`, and
        the SEB gate spells it too. This file's copy is the one that would silently
        stop matching, so it is held to theirs whenever theirs is importable.
        """
        assert gate.NO_CLIENT_REASON == "no_client"
        seb_gate = ROOT / "deploy" / "seb_door_gate.py"
        if not seb_gate.exists():
            pytest.skip("this branch has no SEB door gate to hold the string to")
        module = _load(seb_gate, "sg_seb_door_gate_for_reason")
        assert gate.NO_CLIENT_REASON == module.NO_CLIENT_REASON

    def test_the_state_the_page_settles_on_is_the_one_the_seb_gate_judges(self, gate):
        """`refused` is read off the page; the SEB gate lists the states it accepts."""
        assert gate.REFUSED == "refused"


class TestTheRetryThePanelOffers:
    """The refusal panel's own control, pressed — C7, as CI judges it.

    The box's verdict and this one are about the same reading, and they *say* different
    things on purpose: the box counts the rows the press left in the database, while
    this gate has no database at all — so it judges the press by what the browser put
    on the wire (a report to the refusal route, the status the route answered, the
    reason it carried) and by whether the pupil was still on the handshake page. What
    it must not do is pass a page it could not press: a release whose SEB gate has no
    retry phase is unmeasured, never clean.
    """

    @pytest.mark.parametrize("override,needle", [
        ({"retry_control": False}, "no retry"),
        ({"retry_dead": True}, "dead"),
        ({"retry_away": True, "retry_navigations": 1,
          "retry_landed": "/student/exams/aaa"}, "navigates away"),
        ({"retry_unfiled": True, "retry_posts": 0}, "filed nothing"),
    ], ids=["no-control", "dead", "navigated", "unfiled"])
    def test_each_way_a_press_can_fail_is_its_own_sentence(self, gate, override,
                                                           needle):
        problems = gate.seb_findings(_reading(**override))
        assert len(problems) == 1, problems
        assert needle.lower() in problems[0].lower(), problems[0]

    def test_only_the_first_failure_of_one_press_is_reported(self, gate):
        """One press is one act, so its verdict is one sentence — a page that reported
        all three would send its reader to three files for one broken control."""
        problems = gate.seb_findings(_reading(retry_dead=True, retry_unfiled=True,
                                              retry_away=True, retry_requests=0))
        retry = [p for p in problems if "retry" in p.lower()]
        assert len(retry) == 1, problems

    def test_the_reason_the_press_must_file_is_the_seb_gate_s_own(self, gate):
        """A reworded second answer must be a red test, not a verdict that never fits.

        `no_key` is the page's own contract with `seb_door_log.CLIENT_REASONS`, and the
        SEB gate spells it too. This file's copy is the one that would silently stop
        matching, so it is held to theirs whenever theirs is importable. The page's own
        half of the contract (`report('no_key')` in its script) is asserted where the
        page is: `test_seb_door_gate.py`, which only exists on a branch that ships the
        door — this gate has to stay importable on one that does not.
        """
        assert gate.NO_KEY_REASON == "no_key"
        seb_gate = ROOT / "deploy" / "seb_door_gate.py"
        if not seb_gate.exists():
            pytest.skip("this branch has no SEB door gate to hold the string to")
        module = _load(seb_gate, "sg_seb_door_gate_for_no_key")
        assert gate.NO_KEY_REASON == module.NO_KEY_REASON

    def test_a_driver_without_the_press_is_unmeasured_rather_than_clean(self, gate):
        """The two files ship in one release, so 'this release's SEB gate does not press
        the control' is a gap in the measurement and never a pass.

        Read as syntax rather than as a substring: the branch must exist, must answer
        `unmeasured`, and must be decided by asking the reading whether it carries the
        press at all.
        """
        import ast

        source = _without_comments(_source(GATE_PATH))
        part = None
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.FunctionDef) and node.name == "seb_part":
                part = node
        assert part is not None, "seb_part is gone"
        branch = [node for node in ast.walk(part)
                  if isinstance(node, ast.If)
                  and "hasattr" in ast.unparse(node.test)
                  and "retry_control" in ast.unparse(node.test)]
        assert branch, ("no branch asks whether the reading carries the retry phase")
        body = ast.unparse(branch[0])
        assert "STATE_UNMEASURED" in body, (
            "a release whose gate cannot press the retry is not unmeasured by this one")

    def test_the_shipped_reading_is_judged_clean_by_this_verdict(self, gate):
        """The loop closed against the real class, on a checkout that has both files.

        Everything above hands this verdict a plain namespace, which cannot catch the
        one drift that matters here: the SEB gate renaming a field the CI verdict
        reads. So the real `ClientReading` is built, given a clean press, and judged.
        """
        seb_gate = ROOT / "deploy" / "seb_door_gate.py"
        if not seb_gate.exists():
            pytest.skip("this branch has no SEB door gate to judge a reading from")
        module = _load(seb_gate, "sg_seb_door_gate_for_reading")
        reading = module.ClientReading(
            state="refused", initialized=True, refused_shown=True, seb_api=False,
            reports=1, reports_total=3, reason="no_client", token=True, claims=0,
            status=200, landed=f"/student/exams/aaa{module.CLAIM_SUFFIX}",
            retry_control=True, retry_asks=1, retry_requests=1, retry_posts=1,
            retry_reason=module.NO_KEY_REASON, retry_status=200,
            retry_navigations=0,
            retry_landed=f"/student/exams/aaa{module.CLAIM_SUFFIX}")
        assert gate.seb_findings(
            reading, no_client=module.NO_CLIENT_REASON,
            no_key=module.NO_KEY_REASON) == [], (
            "the shipped reading is judged a finding, so one of the two verdicts is "
            "reading a name the other no longer produces")


# ── the fixture: the pages really are served, with no database ───────────────


def _signed_in(app, fixture, role):
    """A test client for one fixture account, through the app's own sign-in.

    Not a session cookie written by hand: `sign_in_with_password` mints the token the
    fake GoTrue will later resolve, which is what makes "signed in" here mean what it
    means on the box — the same login, the same profile read, the same session.
    """
    db = fixture.FixtureDb(fixture.seed("http://127.0.0.1:9"))
    auth = fixture.FixtureAuth(fixture.accounts())
    app.extensions["supabase"] = db
    app.extensions["supabase_auth"] = auth
    email = fixture.TEACHER_EMAIL if role == "guru" else fixture.PUPIL_EMAIL
    session = auth.sign_in_with_password({"email": email, "password": fixture.PASSWORD})
    client = app.test_client()
    client.set_cookie("access_token", session.session.access_token)
    return client, db


class TestTheFixtureServesThePagesTheGateMeasures:
    """The floor under every part: if these pages stop rendering, nothing is measured.

    Driven through the app's own test client rather than a browser, so this is the
    guard that runs everywhere — including in a job with no Chrome.
    """

    def test_the_exam_builder_serves_the_root_the_touch_gate_measures(self, app, fixture):
        client, _ = _signed_in(app, fixture, "guru")
        page = client.get("/teacher/exams/new")
        assert page.status_code == 200, page.status_code
        assert "sg-exam-builder" in page.get_data(as_text=True), (
            "the builder page is served without the scope the 44px floor is scoped to, "
            "so the touch gate would measure nothing and say OK")

    def test_the_pupil_s_exam_page_renders_a_question(self, app, fixture):
        client, _ = _signed_in(app, fixture, "pupil")
        page = client.get(f"/student/exams/{fixture.EXAM_ID}")
        assert page.status_code == 200, page.status_code
        assert "q-btn" in page.get_data(as_text=True), (
            "the pupil's page carries no question control, which is the blank page the "
            "render gate exists to catch")

    def test_the_paper_is_the_one_the_render_gate_looks_for(self, fixture):
        """`deploy/demo_exam_fixture.py`, seeded rather than restated.

        The render gate reads that spec to decide which question it is measuring, so a
        fixture seeded from anything else would hand it a paper it cannot judge: it
        would answer "this paper carries no exception question" and refuse to measure.
        """
        spec = fixture.demo_fixture()
        exams = fixture.seed("http://127.0.0.1:9")["exams"]
        assert [row["title"] for row in exams[:1]] == [spec.TITLE]
        assert exams[0]["question_types"] == spec.QUESTION_TYPES
        # The exception question is held only where this release *measures* one: the
        # gate's `MULTI_TYPE` is its own statement of which question it is looking for
        # (and `test_exam_render_gate.py` pins it there), so a checkout whose render
        # gate has no exception half is not a release with a missing question — the
        # release it describes never had one to measure.
        render_gate = _load(ROOT / "deploy" / "exam_render_gate.py", "sg_render_gate")
        if hasattr(render_gate, "MULTI_TYPE"):
            assert render_gate.MULTI_TYPE in set(spec.QUESTION_TYPES.values()), (
                "the demo fixture declares no exception question, so the row the "
                "render gate measures is not on the paper it opens")
        assert exams[0]["require_seb"] in (False, None)

    def test_the_second_paper_is_the_gated_one_the_handshake_half_needs(self, fixture):
        """A gated paper is the only one that sends a browser to the handshake page."""
        exams = fixture.seed("http://127.0.0.1:9")["exams"]
        gated = [row for row in exams if row["id"] == fixture.GATED_EXAM_ID]
        assert len(gated) == 1, exams
        assert gated[0]["require_seb"] is True
        assert gated[0]["title"] != exams[0]["title"], (
            "the render gate picks the fixture by its exact title; the gated copy must "
            "not be mistakable for it")

    def test_both_clients_are_replaced_and_nothing_else_is(self, fixture, app):
        """What is faked is exactly two things — the pages are the release's own.

        Asserted by *calling* the wiring and comparing the objects, not by reading the
        source for the two names: a line that became `["supabase_auth"] = None` is an
        app that signs nobody in, and it reads exactly like the real one in a grep. The
        mutation harness found that gap, which is what the harness is for.
        """
        db, auth = fixture.FixtureDb({}), fixture.FixtureAuth(fixture.accounts())
        fixture.install(app, db, auth)
        assert app.extensions["supabase"] is db
        assert app.extensions["supabase_auth"] is auth, (
            "the fixture did not hand the app its own auth client, so the login it signs"
            " gates in with is not the fixture's")
        assert "create_app(" in _without_comments(_source(FIXTURE_PATH)), (
            "the fixture no longer builds the real app")

    def test_the_accounts_cannot_sign_into_anything_real(self, fixture):
        """The `.test` domain is reserved, and the fake refuses every other address."""
        for role in ("guru", "murid"):
            credentials = fixture.credentials(role)
            email, _, password = credentials.partition(":")
            assert email.endswith(".test"), email
            assert password == fixture.PASSWORD
        auth = fixture.FixtureAuth(fixture.accounts())
        with pytest.raises(Exception):
            auth.sign_in_with_password({"email": "head@sekolah.example.com",
                                        "password": fixture.PASSWORD})
        with pytest.raises(Exception):
            auth.get_user("not-a-token-this-fixture-minted")


class TestTheFakeAnswersFilters:
    """A fake that returned everything would render nonsense and pass every gate."""

    def _db(self, fixture):
        return fixture.FixtureDb({"profiles": [
            {"id": "1", "role": "guru", "school_id": "s1"},
            {"id": "2", "role": "murid", "school_id": "s1"},
            {"id": "3", "role": "murid", "school_id": "s2"},
        ]})

    def test_a_filter_filters(self, fixture):
        assert len(self._db(fixture).table("profiles").select("*").execute().data) == 3
        two = (self._db(fixture).table("profiles").select("*")
               .eq("school_id", "s1").execute().data)
        assert [row["id"] for row in two] == ["1", "2"]

    def test_in_and_maybe_single_answer_the_shapes_the_app_reads(self, fixture):
        db = self._db(fixture)
        rows = db.table("profiles").select("*").in_("role", ["murid"]).execute().data
        assert [row["id"] for row in rows] == ["2", "3"]
        missing = db.table("profiles").select("*").eq("id", "9").maybe_single().execute()
        assert missing.data is None, "a miss must be row-shaped None, never []"

    def test_writes_land_and_stay(self, fixture):
        db = self._db(fixture)
        db.table("submissions").insert({"exam_id": "e1", "student_id": "2"}).execute()
        assert [row["exam_id"] for row in db.rows("submissions")] == ["e1"]
        assert db.rows("submissions")[0].get("id"), "an inserted row must carry an id"
        db.table("profiles").update({"status": "active"}).eq("role", "murid").execute()
        assert [row["status"] for row in db.rows("profiles")
                if row["role"] == "murid"] == ["active", "active"]

    def test_an_unknown_table_is_empty_rather_than_an_error(self, fixture):
        """The app asks for tables this fixture does not seed; that is not a 500."""
        db = fixture.FixtureDb({})
        assert db.table("announcements").select("*").execute().data == []
        assert db.rpc("some_report").execute().data == []

    def test_a_number_compares_against_a_string_the_way_postgrest_does(self, fixture):
        """A route asks `.eq("id", "1")` for an integer column; a row that is there
        must answer, or the page silently renders as if it were not."""
        db = fixture.FixtureDb({"school_settings": [{"id": 1, "name": "X"}]})
        row = db.table("school_settings").select("*").eq("id", "1").maybe_single().execute()
        assert row.data == {"id": 1, "name": "X"}


# ── one copy of the browser plumbing ────────────────────────────────────────


class TestOneCopyOfTheBrowserPlumbing:
    """The CDP client, the launcher and the locator live in `deploy/touch_gate.py`.

    Three gates already import them rather than copy them, and the reason is written
    in that file: the copies had drifted into one class that kept the browser's events
    and one that dropped them, which is how one gate ends up blind to the event the
    other one watches for.
    """

    # `_free_port` is deliberately not on this list: the fixture needs a free *HTTP*
    # port and the gates need a free *debug* port, and the four lines of `socket` that
    # answer both are not the thing that drifted. The CDP client and the launcher's
    # two box lessons are.
    @pytest.mark.parametrize("name", ["_CDP", "browser_launch", "locate_browser",
                                      "sign_in"])
    def test_neither_new_file_defines_its_own(self, name):
        for path in (GATE_PATH, FIXTURE_PATH):
            body = _without_comments(_source(path))
            assert f"def {name}(" not in body, f"{path.name} defines its own {name}"

    def test_the_gate_imports_the_plumbing_it_uses(self):
        body = _without_comments(_source(GATE_PATH))
        assert "import touch_gate" in body
        assert "touch_gate.locate_browser()" in body
        assert "touch_gate.sign_in(" in body

    def test_every_part_of_run_really_drives_its_measurement(self, gate):
        """Each part is measured **by a branch of `run()`**, read as syntax.

        A substring search for `touch_gate.run(` cannot tell a call that runs from one
        behind `if "touch" in only and False:` — a part that never measures anything
        while the file still names it. That mutation survived the first version of this
        guard, so the branch's own test is matched exactly and its body is read.
        """
        measured = _branches_of_run(_without_comments(_source(GATE_PATH)))
        assert "touch_gate.run" in measured.get("touch", set()), measured
        assert "exam_render_gate.run" in measured.get("render", set()), measured
        assert "seb_part" in measured.get("seb", set()), measured

    def test_the_gate_reuses_the_two_dom_gates_rather_than_re_measuring(self):
        """A second copy of a measurement is a second thing to keep in step."""
        body = _without_comments(_source(GATE_PATH))
        assert "import exam_render_gate" in body
        assert "touch_gate.run(" in body
        assert "exam_render_gate.run(" in body

    def test_the_gate_reuses_the_seb_gate_s_browser_half(self):
        """Loaded by name, because a branch without the door must still import this."""
        body = _without_comments(_source(GATE_PATH))
        assert 'import seb_door_gate' in body
        assert "module.client_half(" in body
        assert "module.PAPER_PATH" in body and "module.CLAIM_SUFFIX" in body

    def test_no_browser_path_is_restated_anywhere_new(self):
        for path in (GATE_PATH, FIXTURE_PATH):
            body = _without_comments(_source(path))
            assert "chrome.exe" not in body
            assert "google-chrome" not in body, (
                f"{path.name} restates the browser search that locate_browser owns")
            # The key is *named* in the gate's operator-facing message ("set SG_CHROME
            # to a Chrome/Chromium binary") and that is the point of the message; what
            # no new file may do is read it, because `locate_browser` owns that lookup
            # and the empty-string-means-do-not-measure rule that goes with it.
            assert 'os.environ.get("SG_CHROME"' not in body, (
                f"{path.name} reads the browser conf key itself instead of leaving it "
                f"to locate_browser")


# ── the workflow ─────────────────────────────────────────────────────────────


class TestTheWorkflowThatRunsIt:
    """`on every push` is the workflow's half of the request, so it is guarded too."""

    def _workflow(self) -> str:
        if not WORKFLOW_PATH.exists():
            pytest.fail(".github/workflows/browser-gate.yml does not exist — the pages "
                        "are measured on the box and nowhere else")
        return _source(WORKFLOW_PATH)

    def test_it_runs_the_gate_on_every_push(self):
        text = self._workflow()
        assert "deploy/browser_gate.py" in text
        assert "push:" in text and "pull_request:" in text

    def test_it_installs_the_app_s_own_dependencies(self):
        """The gate imports the app, so CI builds the app the box builds."""
        assert "pip install -r requirements.txt" in self._workflow()

    def test_it_points_the_gates_at_a_browser_it_actually_found(self):
        text = self._workflow()
        assert "SG_CHROME:" in text
        assert "command -v" in text and "google-chrome" in text
        # ...and a runner with no browser fails the step rather than answering 2 and
        # going green, which is the whole difference between this and the box.
        assert "no Chrome/Chromium on this runner" in text

    def test_exit_two_fails_the_job(self):
        """The box keeps a release it could not look at; CI must not keep a green run.

        The gate's own exit contract is unchanged — 2 means "could not measure" — and
        this is the caller that decides what that costs. A CI job whose gate silently
        skipped would be worse than no gate, because it would read as coverage. The
        annotation is part of the assertion: a message that says so without the
        `::error::` prefix is a log line the job still passes on (a mutation proved
        exactly that).
        """
        text = self._workflow()
        assert "::error::the browser gate could not measure the pages (exit 2)" in text
        assert "::error::the browser gate found a regression" in text
        assert "exit 1" in text

    def test_it_runs_the_gate_s_own_guards_first(self):
        text = self._workflow()
        assert "tests/unit/test_browser_gate.py" in text

    def test_the_reading_is_kept_for_a_run_that_failed(self):
        """The run that failed is the run whose measurement someone needs."""
        text = self._workflow()
        assert "--json-out browser-gate.json" in text
        assert "actions/upload-artifact" in text
        assert "if: always()" in text

    def test_every_shell_block_the_workflow_runs_parses(self):
        """A workflow is only as good as the shell in it, and this one has a heredoc.

        Nobody can run GitHub Actions from a checkout, so the half that *is* checkable
        is checked: every `run:` script is fed to `bash -n`. It is not ceremony — the
        summary in this workflow feeds Python through a heredoc from inside a `{ }`
        group, and an indentation slip there is a job that dies before the gate runs
        at all, which is the one failure mode this whole file exists to avoid.
        """
        bash = shutil.which("bash")
        if not bash:
            pytest.skip("no bash on this machine to parse the workflow with")
        blocks = _shell_blocks(self._workflow())
        assert blocks, "no `run:` block was found — the workflow's shape changed"
        for block in blocks:
            done = subprocess.run([bash, "-n", "-c", block], capture_output=True,
                                  text=True, encoding="utf-8", errors="replace")
            assert done.returncode == 0, (
                f"a run: block is not valid shell:\n{block}\n---\n{done.stderr}")


class TestTheGatesOwnContract:
    def test_the_reading_is_written_whatever_the_verdict_is(self, gate):
        """A reading written only on a clean run is missing exactly when it is needed.

        Structural rather than behavioural on purpose: the alternative is a browser run
        inside the suite. The write must come before `return code` and must not be
        inside the `if args.json:` branch.
        """
        body = _without_comments(_source(GATE_PATH))
        write = body.index("args.json_out")
        verdict = body.index("return code", write)
        print_branch = body.index("if args.json:", write)
        assert write < print_branch < verdict, (
            "the reading is written after the branch that prints it, so a run that "
            "printed nothing left nothing behind")

    def test_it_names_the_three_parts_in_one_place(self, gate):
        assert gate.PARTS == ("touch", "render", "seb")

    def test_the_json_payload_is_json(self, gate):
        """`--json` is what CI parses for its summary, so it must serialise."""
        payload = {"parts": {"seb": {"state": gate.STATE_NOT_SHIPPED,
                                     "payload": {"reason": "not-shipped"}}}}
        assert json.loads(json.dumps(payload))["parts"]["seb"]["state"] == "not-shipped"
