"""The tenant boundary, checked by the build instead of by a reader.

`deploy/school_authority_gate.py` answers one question: does every teacher-facing
write route file its rows under the school of *this* request? A teacher can be an
active member of more than one NPSN since migration 044, so a route has two schools
it could name — the one this request acts in (`g.user_school_id`) and the one their
profile names — and `school_id` is the column that scopes a paper's weighting, its
pupils, its marks and its reports. Four releases shipped a route that resolved it
from a request body, from the profile's school, or from the session, and every one
was found by hand.

The gate is run here, not restated: each defect shape is written into a scratch
tree and the gate's own exit code and JSON are read back. The fixtures are modules
the gate actually parses, so a shape that stops being caught fails this file rather
than passing a reading of the source. `TestTheShippedTreeIsClean` points the gate at
this repository, which is the claim the release makes.

Mutation-checked, 17/17 injected defects caught (`.freebuff/mutate_school_authority.py`):
a shape that stops being reported, the kinds collapsing into one, the clean shape
becoming a finding, the request body no longer traced to the request, the profile's
school allowed through, the fallback-to-active branch unread, an unparseable module
read as clean, the door selector going blind, a run that read no route counting as
clean, the count leaving the success line, the exit-2 arms turned into a pass, the
wiring in `deploy/theme_gate.sh` removed, and the failure text naming a tool that
does not exist.

The first run of that harness missed six, and each was a real hole in the guards:
three shapes it was not exercising (a body-named school carried through a local, a
body's claim with a fallback to the active school, and the home school in its
attribute spelling), a renamed remedy the real tool still named elsewhere in the
same file, and two assertions — the missing-modules message and the unparseable
module — that the newer zero-routes rule answered for instead. One further mutation
was **removed as equivalent** rather than chased: `audit`'s early refusal for an
empty `app/routes/` became a pass with no change in behaviour, because the caller
refuses on `examined == 0` and printed the same sentence. The early return is gone,
so there is one rule instead of two spellings of one refusal.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
GATE = DEPLOY / "school_authority_gate.py"
THEME_GATE = DEPLOY / "theme_gate.sh"
HOOK = DEPLOY / "git-hooks" / "pre-commit"

EXIT_FINDING, EXIT_CANNOT_RUN = 1, 2

#: A module with one teacher-facing create route, written the way this repository
#: writes it: the body's claim goes to the authority and is refused if it cannot be
#: verified, and the school the row is filed under is the authority's answer. Every
#: defect below is this module with one line changed.
CLEAN = '''\
from flask import Blueprint, g, request, jsonify
from app.utils.auth import teacher_required, filing_school_for_request

exam_bp = Blueprint("exam", __name__)
WRITABLE = {"title", "duration_minutes"}


@exam_bp.route("/", methods=["POST"])
@teacher_required
def create_exam():
    data = request.get_json()
    school_id, error = filing_school_for_request(
        requested_school_id=data.get("school_id"))
    if error:
        return jsonify({"error": error}), 400
    filtered = {k: v for k, v in data.items() if k in WRITABLE}
    filtered["school_id"] = school_id
    supabase.table("exams").insert(filtered).execute()
    return jsonify({}), 201
'''

#: shape -> (the line in CLEAN it replaces, what it becomes, the kind the gate
#: must report). The pairs are the four defects this repository actually shipped,
#: plus the three ways a school can arrive that are not a body at all.
SHAPES = {
    "the body names the school": (
        '    filtered["school_id"] = school_id\n',
        '    filtered["school_id"] = data.get("school_id")\n',
        "request school"),
    "the claim is used without the authority": (
        '    filtered["school_id"] = school_id\n',
        '    filtered["school_id"] = request.form.get("school_id")\n',
        "request school"),
    "the profile's school is written": (
        '    filtered["school_id"] = school_id\n',
        '    filtered["school_id"] = g.get("user_home_school_id")\n',
        "home school"),
    "the session names it": (
        '    filtered["school_id"] = school_id\n',
        '    filtered["school_id"] = session.get("school_id")\n',
        "session school"),
    "a row's school without @require_school_access": (
        '    filtered["school_id"] = school_id\n',
        '    filtered["school_id"] = existing.get("school_id")\n',
        "row school"),
    "a value nothing can trace": (
        '    filtered["school_id"] = school_id\n',
        '    filtered["school_id"] = pick_school(data)\n',
        "untraceable"),
    "a write scoped by a school the caller named": (
        '    filtered["school_id"] = school_id\n',
        '    supabase.table("exams").update(data).eq("school_id",'
        ' data.get("school_id")).execute()\n',
        "request school"),
    "a body-named school carried through a local": (
        '    filtered["school_id"] = school_id\n',
        '    sid = data.get("school_id")\n    filtered["school_id"] = sid\n',
        "request school"),
    "a body's claim with a fallback to the active school": (
        '    filtered["school_id"] = school_id\n',
        '    filtered["school_id"] = (data.get("school_id")\n'
        '                            or g.get("user_school_id"))\n',
        "request school"),
}

#: A route that takes the school as a parameter: the caller names it, so there is
#: nothing left for the server to resolve.
PARAMETER = CLEAN.replace(
    "def create_exam():",
    "def create_exam(school_id):").replace(
    '    school_id, error = filing_school_for_request(\n'
    '        requested_school_id=data.get("school_id"))\n',
    "").replace(
    "    if error:\n        return jsonify({\"error\": error}), 400\n",
    "")


def run_gate(tree: Path, *args) -> subprocess.CompletedProcess:
    """The real gate, against the tree named — never this file's reading of it."""
    return subprocess.run(
        [sys.executable, str(GATE), "--tree", str(tree), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace")


def tree_with(tmp_path: Path, source: str, name: str = "exam.py") -> Path:
    routes = tmp_path / "app" / "routes"
    routes.mkdir(parents=True, exist_ok=True)
    (routes / name).write_text(source, encoding="utf-8")
    return tmp_path


def kinds(proc: subprocess.CompletedProcess) -> list[str]:
    return [f["kind"] for f in json.loads(proc.stdout)["findings"]]


# ── the shapes this gate exists for ──────────────────────────────

class TestTheDefectShapesAreCaught:
    @pytest.mark.parametrize("shape", sorted(SHAPES))
    def test_each_shape_is_a_finding_with_the_right_kind(self, tmp_path, shape):
        before, after, kind = SHAPES[shape]
        source = CLEAN.replace(before, after)
        assert source != CLEAN, f"the fixture no longer contains {before!r}"
        proc = run_gate(tree_with(tmp_path, source), "--json")

        assert proc.returncode == EXIT_FINDING, (
            f"{shape} was not refused:\n{proc.stdout}{proc.stderr}")
        assert kind in kinds(proc), (
            f"{shape} was reported as {kinds(proc)}, not {kind!r} — the sentence an "
            f"operator fixes from is the kind")

    def test_the_shapes_are_not_all_one_kind(self):
        """A gate that reports every defect as the same kind cannot be acted on:

        a body's claim, the profile's school and the session are fixed in three
        different places, so the kinds have to be distinguishable — and this pins
        that the table above is not vacuous.
        """
        assert len(set(k for _, _, k in SHAPES.values())) >= 4

    def test_a_school_arriving_as_a_parameter_is_refused(self, tmp_path):
        proc = run_gate(tree_with(tmp_path, PARAMETER), "--json")
        assert proc.returncode == EXIT_FINDING
        assert "parameter" in kinds(proc)

    def test_the_clean_shape_is_not_a_finding(self, tmp_path):
        """The other half of every guard here: the right way must pass.

        `filtered["school_id"] = school_id` after `filing_school_for_request(...)`
        is the shape this repository writes, and a gate that flags it would be
        deleted within a week.
        """
        proc = run_gate(tree_with(tmp_path, CLEAN), "--json")
        assert proc.returncode == 0, f"the correct shape was refused: {proc.stdout}"
        assert kinds(proc) == []

    @pytest.mark.parametrize("expression", ['g.user_home_school_id',
                                            'g.get("user_home_school_id")'])
    def test_the_home_school_is_refused_anywhere_in_the_module(self, tmp_path,
                                                               expression):
        """Not only where a school is *supplied*: there is no legitimate use for the

        profile's school on this side of a write, and "used it somewhere I did not
        think to trace" is the failure this gate exists for.

        Both spellings, because the module-wide scan and the value tracer recognize
        the attribute form and the call form separately — a guard that only
        exercised one of them left the other unreachable.
        """
        source = CLEAN + f"\n\ndef dashboard():\n    return {expression}\n"
        proc = run_gate(tree_with(tmp_path, source), "--json")
        assert proc.returncode == EXIT_FINDING, f"{expression} was not refused"
        assert "home school" in kinds(proc)

    #: The one shape the home school is legitimately named in: a cache key. A switcher
    #: has to drop the *destination* school's cached page, and sending a teacher back to
    #: their own school makes the profile's the destination — which is why main's own
    #: `teacher_membership_switch` writes `_forget_cached_dashboard(g.user_home_school_id)`.
    HOME_KEY_CALLS = ('_forget_cached_dashboard(g.get("user_home_school_id"))',
                      'req_cache.invalidate(g.get("user_home_school_id"))',
                      'cache.drop_page_for(g.get("user_home_school_id"))')

    @pytest.mark.parametrize("call", HOME_KEY_CALLS)
    def test_a_cached_page_named_by_the_profile_is_not_a_write(self, tmp_path, call):
        """A value that reaches a cache key cannot file a row under that school.

        Refusing it would refuse the route that has to exist: main's school switcher
        clears the active choice, the teacher lands back on their home school, and the
        stale page waiting there is exactly what must go.
        """
        source = CLEAN + (f"\n\ndef switch_page():\n    return {call}\n")
        proc = run_gate(tree_with(tmp_path, source), "--json")
        assert proc.returncode == 0, (
            f"a cache key was read as a tenant boundary: {call}\n{proc.stdout}")
        assert kinds(proc) == []

    def test_the_exemption_is_not_a_hole(self, tmp_path):
        """The same value where it *supplies* a school is still refused, one line from
        a cache clear — the rule reads where a value goes, not where it is mentioned."""
        before = SHAPES["the profile's school is written"][0]
        written = CLEAN.replace(before, SHAPES["the profile's school is written"][1])
        source = written + (
            '\n\ndef switch_page():\n    return _forget_cached_dashboard('
            'g.get("user_home_school_id"))\n')
        proc = run_gate(tree_with(tmp_path, source), "--json")
        assert proc.returncode == EXIT_FINDING, (
            "a row filed under the profile's school passed because a cache clear sat "
            "beside it:\n" + proc.stdout)
        assert "home school" in kinds(proc)

    def test_the_exemption_needs_a_clearer_word(self, tmp_path):
        """Matched as a word of the call's own name, so an unrelated helper handed the
        profile's school is still a finding — the exemption is not a prefix."""
        source = CLEAN + (
            '\n\ndef estimate():\n    return page_estimate('
            'g.get("user_home_school_id"))\n')
        proc = run_gate(tree_with(tmp_path, source), "--json")
        assert proc.returncode == EXIT_FINDING, (
            "the profile's school passed through a name that is not a cache clearer:\n"
            + proc.stdout)
        assert "home school" in kinds(proc)


# ── what is out of scope, and why ────────────────────────────────

class TestTheSelectorIsTheDoor:
    def test_a_read_route_with_a_body_school_is_not_a_write(self, tmp_path):
        """A GET cannot file a row.

        The tree holds a route with a body-named school and *no* write route, so the
        gate says it could not measure rather than that the release is clean — which
        is the distinction this pins: a gate that treated the GET as a write would
        exit 1 here, and one that passed vacuously would exit 0.
        """
        source = CLEAN.replace('methods=["POST"]', 'methods=["GET"]')
        proc = run_gate(tree_with(tmp_path, source))
        assert proc.returncode == EXIT_CANNOT_RUN, proc.stdout + proc.stderr
        assert "teacher-facing write route" in proc.stderr

    def test_a_route_with_no_teacher_door_is_a_different_audit(self, tmp_path):
        """An admin's writes are the one role that is *not* cross-school.

        The selector is the door, not the blueprint name: a route is teacher-facing
        because a teacher can reach it, which is what makes `role_required("guru")`
        in scope and an admin-only handler out of it.
        """
        source = CLEAN.replace("@teacher_required", "@admin_sekolah_required")
        proc = run_gate(tree_with(tmp_path, source))
        assert proc.returncode == EXIT_CANNOT_RUN, proc.stdout + proc.stderr
        assert "teacher-facing write route" in proc.stderr

    def test_role_required_naming_a_teacher_is_in_scope(self, tmp_path):
        source = CLEAN.replace("@teacher_required", '@role_required("guru")').replace(
            '    filtered["school_id"] = school_id\n',
            '    filtered["school_id"] = data.get("school_id")\n')
        assert run_gate(tree_with(tmp_path, source), "--json").returncode == EXIT_FINDING

    def test_a_row_guard_makes_a_row_school_this_caller_own(self, tmp_path):
        """`@require_school_access` is not a door — it is the evidence.

        The same expression is a finding without it and correct with it, which is
        the one distinction that makes the row rule honest rather than a blanket
        ban.
        """
        source = CLEAN.replace(
            "@teacher_required",
            "@teacher_required\n@require_school_access").replace(
            '    filtered["school_id"] = school_id\n',
            '    filtered["school_id"] = existing.get("school_id")\n')
        assert run_gate(tree_with(tmp_path, source), "--json").returncode == 0

    def test_the_fallback_to_the_active_school_is_allowed(self, tmp_path):
        source = CLEAN.replace(
            '    filtered["school_id"] = school_id\n',
            '    filtered["school_id"] = (existing.get("school_id")\n'
            '                            or g.get("user_school_id"))\n')
        assert run_gate(tree_with(tmp_path, source), "--json").returncode == 0


# ── the exit contract ────────────────────────────────────────────

class TestTheExitContract:
    def test_no_route_modules_is_two_not_zero(self, tmp_path):
        """\"We looked at nothing\" must never read as \"we found nothing\".

        Exit 0 with no modules is the shape that makes a rename of `app/routes/`
        silently disarm this gate for every release after it.
        """
        (tmp_path / "app" / "routes").mkdir(parents=True)
        proc = run_gate(tmp_path)
        assert proc.returncode == EXIT_CANNOT_RUN
        # The *message* is what tells this case from the blind selector below:
        # both are exit 2, and one names the missing directory while the other
        # names the selector. Asserted, because with only the exit code a mutation
        # that let this path fall through to the zero-routes rule stayed green.
        assert "no route modules under" in proc.stderr, proc.stderr

    def test_no_teacher_write_route_at_all_is_two_not_zero(self, tmp_path):
        """A renamed door decorator must not turn this gate into a green light.

        The modules are all there and one of them holds the old defect; the only thing
        wrong is that no route matches the selector. Reporting 0 findings over that
        tree is exactly how the check would stop covering anything.
        """
        source = CLEAN.replace("@teacher_required", "@login_required").replace(
            '    filtered["school_id"] = school_id\n',
            '    filtered["school_id"] = data.get("school_id")\n')
        proc = run_gate(tree_with(tmp_path, source))
        assert proc.returncode == EXIT_CANNOT_RUN, proc.stdout + proc.stderr
        assert "matches nothing" in proc.stderr or "not one teacher-facing" in proc.stderr

    def test_a_module_that_will_not_parse_is_two(self, tmp_path):
        """Beside a module that *does* parse and hold a route, so the refusal can

        only come from this one: on a tree whose only module is broken, the
        zero-routes rule would refuse it anyway and the parse arm would be
        unexercised.
        """
        tree = tree_with(tmp_path, CLEAN)
        (tree / "app" / "routes" / "broken.py").write_text("def broken(:\n",
                                                            encoding="utf-8")
        proc = run_gate(tree)
        assert proc.returncode == EXIT_CANNOT_RUN, (
            "an unparseable module is the box's property, not a verdict on the "
            "release — the app would not start at all\n" + proc.stdout)
        assert "could not measure" in proc.stdout + proc.stderr

    def test_the_human_output_names_the_count_and_the_offending_route(self, tmp_path):
        """A refusal a reader cannot act on is a journal line with no fix in it.

        --json is for other tools; the deploy relays the plain output, so that is
        the form asserted to carry the route and the line.
        """
        bad = run_gate(tree_with(tmp_path, CLEAN.replace(
            '    filtered["school_id"] = school_id\n',
            '    filtered["school_id"] = data.get("school_id")\n')))
        assert bad.returncode == EXIT_FINDING
        assert "1 place(s)" in bad.stdout, bad.stdout
        assert "exam.py" in bad.stdout and "create_exam" in bad.stdout, bad.stdout
        assert "request school" in bad.stdout, bad.stdout


# ── the shipped tree, which is the claim the release makes ───────

class TestTheShippedTreeIsClean:
    def test_the_gate_passes_this_repository(self):
        proc = run_gate(ROOT)
        assert proc.returncode == 0, (
            "a teacher-facing write route in this release resolves its school from "
            "somewhere other than the request's active school:\n"
            + proc.stdout + proc.stderr)

    def test_the_verdict_says_how_many_routes_it_read(self):
        """"0 findings" over 0 routes is the silence this check refuses.

        The number is on the success line so a reader can tell "every route is
        right" from "no route was looked at" — the same reason the coverage tool
        prints its percentage.
        """
        proc = run_gate(ROOT, "--json")
        examined = json.loads(proc.stdout)["examined"]
        assert examined > 0, (
            "the gate reports a clean tree but read no teacher-facing write route")

        human = run_gate(ROOT).stdout
        assert f"{examined} teacher-facing write route(s)" in human, (
            "the count is not on the success line, so the caller cannot tell the two "
            "silences apart")

    def test_it_reads_the_route_modules_and_not_a_stale_list(self):
        """The gate must be pointed at the tree, so a new route module is covered.

        Asserted as behaviour: a copy of this repository whose only route module
        carries the old defect is a finding, which a hard-coded file list could not
        produce.
        """
        routes = ROOT / "app" / "routes"
        assert routes.is_dir()
        assert any(p.suffix == ".py" for p in routes.iterdir())

    def test_the_app_is_clean_before_the_gate_is_armed(self):
        """The order matters for one release only, and it is this one: the gate is

        wired into the build and the tree it judges is the tree it ships with.
        """
        assert run_gate(ROOT).returncode == 0


# ── the wiring: this is a build failure, not a report ────────────

class TestTheBuildRunsIt:
    def gate_source(self) -> str:
        return THEME_GATE.read_text(encoding="utf-8")

    def test_the_tool_is_run_and_guarded_up_front(self):
        src = self.gate_source()
        armament = [ln for ln in src.splitlines() if ln.startswith("ARMAMENT=")]
        assert armament, "the gate no longer checks its own files"
        assert "deploy/school_authority_gate.py" in armament[0], (
            "the school authority check is not in the armament list, so a release "
            "could delete the tool and keep deploying")

    def test_a_finding_is_exit_one_so_the_deploy_rolls_back(self):
        src = self.gate_source()
        arm = src.split('if [ "$SCHOOL_RC" -eq 1 ]', 1)
        assert len(arm) == 2, "the gate does not read the check's finding exit code"
        body = arm[1].split("\nfi", 1)[0]
        assert "exit 1" in body, (
            "a release that files rows into the wrong tenant must be refused like "
            "any other finding\n" + body)
        assert "exit 0" not in body
        assert src.index('if [ "$SCHOOL_RC" -eq 1 ]') < src.index("theme gate: OK —"), (
            "the gate can print OK without having read this check's verdict")

    def test_could_not_measure_is_two_and_not_a_finding(self):
        src = self.gate_source()
        arm = src.split('if [ "$SCHOOL_RC" -eq 2 ]', 1)
        assert len(arm) == 2, (
            "the check's exit 2 is not distinguished from its finding, so a box that "
            "cannot answer takes the release down")
        assert "exit 2" in arm[1].split("\nfi", 1)[0]

    def test_the_tool_prints_its_verdict_and_the_ok_line_claims_it(self):
        """The success line is the release's claim: it must name this question.

        A gate that runs but is not mentioned in the summary is a gate whose
        failure a reader attributes to something else.
        """
        src = self.gate_source()
        assert '"$SCHOOL_OUT"' in src, "the check's output is not relayed"
        ok = [ln for ln in src.splitlines() if "theme gate: OK" in ln]
        assert ok and "teacher-facing write route files its rows under the school" in ok[0]

    def test_the_failure_text_names_the_check_to_run(self):
        """The remedy has to be the tool that answers the question."""
        src = self.gate_source()
        remedy = src.split("cat >&2 <<'EOF'", 1)[1]
        assert "school_authority_gate.py" in remedy, (
            "the failure text does not tell the reader how to see the findings")

    def test_every_tool_the_gate_names_exists(self):
        """The repo's rule for every gate's text: no phantom scripts.

        `deploy/theme_gate.sh` once told the reader to run `audit_page_lang.py`,
        which is not in the repository — a dead end printed exactly when somebody
        needed it. Read over the whole file, so a *renamed* remedy is a finding
        even while the real tool is still named somewhere else in it.
        """
        src = self.gate_source()
        named = set(re.findall(r"\bdeploy/[\w./-]+\.py\b", src))
        named |= {m for m in re.findall(r"\bpython3?\s+([\w./-]+\.py)\b", src)}
        assert named, "the gate names no tool at all; this check has gone blind"
        missing = sorted(n for n in named if not (ROOT / n).is_file())
        assert not missing, (
            f"the gate's own text tells the reader to run a file that does not "
            f"exist: {missing}")

    def test_the_pre_commit_hook_runs_this_gate(self):
        hook = HOOK.read_text(encoding="utf-8")
        assert "theme_gate.sh" in hook, (
            "the hook stops running the gate that now carries this check")
