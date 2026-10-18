"""Every guard in this repository has to run where nobody is watching.

A test file is a paragraph until something executes it. That is not a hypothetical
here: the Supabase project ref was recorded one letter short and the database's
region was wrong on a UU PDP disclosure, and nothing failed for weeks — because
nothing read either of them. The fix was a guard, and a guard whose only runner is
a person remembering to run it is the same silence with extra steps. So the whole
of `tests/unit` runs on every push and every pull request, and this file is what
keeps that job from becoming decoration:

* **it parses.** A workflow with a YAML error does not run and GitHub says nothing
  about it — a job that never starts cannot fail. `PyYAML` is in `requirements.txt`
  for exactly this assertion.
* **it cannot be filtered into silence.** No path filter on the trigger, no
  `--ignore`/`-k`/`-m`/`--deselect` on the command, no `continue-on-error`, no
  `|| true`, no `set +e` around the suite. Each of those is a way to keep the tick
  green about tests that did not run.
* **it names the directory, not the files.** A list is a thing that goes stale: the
  next guard added to `tests/unit` would not be in it, and nothing would say so.
  That is the defect this job exists to end, so the job must not reintroduce it.
* **the conditions the suite needs are installed, not skipped.** Full git history
  (`test_merge_runbook.py` reads four historical commits), a browser (the two
  browser-gate suites skip by name without one), and the node toolchain (jsdom for
  the essay editor, tailwind for the stylesheet rebuild). A suite that skips in CI
  because the job was too thin is a suite that never ran.
"""
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
SUITE = WORKFLOWS / "unit-tests.yml"
UNIT = ROOT / "tests" / "unit"
PYPROJECT = ROOT / "pyproject.toml"
PY_VERSION_FILE = ROOT / ".python-version"
LOCKFILE = ROOT / "package-lock.json"

#: What the job must actually execute. Written once so a change of target is one
#: edit here rather than a sentence in five tests.
TARGET = "python -m pytest tests/unit"

#: Ways a green tick can be a lie. Each is refused in the full-suite job, with the
#: reason stated where it is refused; `-x` is not in the list on purpose — stopping
#: at the first finding still fails the job.
#:
#: Read from the workflow's **parsed** `run` bodies and step keys, never from the
#: file's text: this workflow's own header explains that these are refused, and a
#: guard that fires on the sentence describing the rule is a guard somebody deletes.
PYTEST_SILENCERS = ("--ignore", "--deselect", "-k", "-m", "--maxfail",
                    "--lf", "--last-failed")

#: An exit code thrown away. Only judged on the suite's own command: `|| true`
#: inside `$(command -v …)` is the project's own browser-discovery idiom and does
#: not swallow the step (that step exits 1 when nothing is found).
SWALLOWED = ("|| true", "|| exit 0", "; true")


def _load(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    doc = yaml.safe_load(text)
    assert isinstance(doc, dict), f"{path.name} is not a mapping"
    return doc


def _triggers(doc: dict) -> dict:
    """`on:` is the boolean `True` to YAML 1.1, which is what PyYAML implements.

    A reader that only looked up the string would report every workflow in this
    repository as having no trigger at all.
    """
    triggers = doc.get("on", doc.get(True))
    return triggers if isinstance(triggers, dict) else {}


def _steps(doc: dict):
    for job_name, job in (doc.get("jobs") or {}).items():
        for step in (job or {}).get("steps") or []:
            yield job_name, job, step


def _run_text(doc: dict) -> str:
    return "\n".join(str(step.get("run") or "") for _, _, step in _steps(doc))


def _workflows() -> list[Path]:
    return sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))


def _pytest_arguments(line: str) -> list[str]:
    """What pytest is told, with the `-m` that summons pytest itself dropped.

    A substring search for `-m ` matched `python -m pytest` — the invocation, not a
    marker filter — which is how a guard ends up refusing the thing it protects.
    """
    tokens = line.split()
    return tokens[tokens.index("pytest") + 1:] if "pytest" in tokens else tokens


def _python_floor() -> str:
    text = PYPROJECT.read_text(encoding="utf-8")
    found = re.search(r'^requires-python\s*=\s*"[>=~^]*\s*(\d+\.\d+)', text, re.MULTILINE)
    assert found, "pyproject.toml declares no requires-python floor"
    return found.group(1)


def _node_versions_jsdom_accepts() -> list[tuple[str, int, int]]:
    """`(operator, major, minor)` per alternative in jsdom's own `engines.node`."""
    lock = yaml.safe_load(LOCKFILE.read_text(encoding="utf-8"))
    entry = (lock.get("packages") or {}).get("node_modules/jsdom") or {}
    engines = (entry.get("engines") or {}).get("node")
    assert engines, "package-lock.json records no engines for jsdom"
    out = []
    for alternative in engines.split("||"):
        found = re.match(r"\s*([\^~]|>=|==)?v?(\d+)(?:\.(\d+))?", alternative.strip())
        assert found, f"unreadable jsdom engine clause: {alternative!r}"
        operator = found.group(1) or "=="
        out.append((operator, int(found.group(2)), int(found.group(3) or 0)))
    return out


def _satisfies(pinned_major: int, operator: str, major: int, minor: int) -> bool:
    """Does `pinned_major` (meaning its latest `.x`) fall inside one clause?"""
    if operator in ("^", "~", "=="):
        return pinned_major == major
    if operator == ">=":
        if pinned_major != major:
            return pinned_major > major
        return True          # '24' means the latest 24.x, which is >= 24.<minor>
    return False


class TestAWorkflowThatDoesNotParseNeverRuns:
    """The failure GitHub cannot report: the job never starts, so nothing is red."""

    def test_the_directory_has_workflows(self):
        assert _workflows(), "no workflow in .github/workflows — CI would do nothing"

    @pytest.mark.parametrize("path", _workflows(), ids=lambda p: p.name)
    def test_every_workflow_parses(self, path):
        doc = _load(path)
        assert doc.get("jobs"), f"{path.name} declares no jobs"

    @pytest.mark.parametrize("path", _workflows(), ids=lambda p: p.name)
    def test_every_workflow_has_a_trigger(self, path):
        """A workflow with no `on:` is never scheduled, and nothing says so."""
        assert _triggers(_load(path)), f"{path.name} has no trigger"

    @pytest.mark.parametrize("path", _workflows(), ids=lambda p: p.name)
    def test_every_workflow_names_a_job_it_can_run(self, path):
        doc = _load(path)
        for job_name, job, _step in _steps(doc):
            assert job.get("runs-on"), f"{path.name}:{job_name} names no runner"


class TestTheSuiteRunsOnEveryPush:

    def test_there_is_a_job_for_the_whole_suite(self):
        assert SUITE.is_file(), (
            f"{SUITE.name} is gone — the unit suite then runs only where a person "
            "runs it, which is the silence this file exists to end")

    def test_it_runs_on_push_and_pull_request(self):
        triggers = _triggers(_load(SUITE))
        for event in ("push", "pull_request"):
            assert event in triggers, f"{SUITE.name} does not run on {event}"

    def test_no_trigger_is_narrowed_by_paths_or_branches(self):
        """A `paths:` filter is a job that silently does not run for most commits."""
        for event, config in _triggers(_load(SUITE)).items():
            if not isinstance(config, dict):
                continue
            for narrowed in ("paths", "paths-ignore", "branches", "branches-ignore"):
                assert narrowed not in config, (
                    f"{SUITE.name}: `{event}` filters on `{narrowed}`, so a commit "
                    "outside it is never tested")

    def test_it_executes_the_directory_and_not_a_file_list(self):
        """A list goes stale; the directory cannot."""
        runs = _run_text(_load(SUITE))
        assert TARGET in runs, f"{SUITE.name} does not run `{TARGET}`"
        target_line = next(
            line for line in runs.splitlines() if TARGET in line and not line.strip().startswith("#"))
        assert "tests/unit/test_" not in target_line, (
            "the suite is named file-by-file; a guard added tomorrow would not run")
        assert UNIT.is_dir() and any(UNIT.glob("test_*.py"))

    def test_the_directory_is_where_the_guards_live(self):
        """The motivating case, stated as a fact about this tree."""
        assert (UNIT / "test_supabase_region.py").is_file()
        assert TARGET in _run_text(_load(SUITE))

    def test_nothing_may_silence_the_suite(self):
        doc = _load(SUITE)
        runs = _run_text(doc)
        for silencer in PYTEST_SILENCERS[:2]:
            assert silencer not in runs, f"{SUITE.name} runs `{silencer}`"
        for job_name, _job, step in _steps(doc):
            assert step.get("continue-on-error") is not True, (
                f"{SUITE.name}:{job_name} marks a step continue-on-error")
        target_line = next(line for line in runs.splitlines() if TARGET in line)
        arguments = _pytest_arguments(target_line)
        for flag in PYTEST_SILENCERS[2:]:
            assert not any(arg == flag or arg.startswith(flag + "=")
                           for arg in arguments), (
                f"the suite runs with `{flag}`: {target_line.strip()}")
        for swallowed in SWALLOWED:
            assert swallowed not in target_line, (
                f"the suite's own command ends in `{swallowed}`")

    def test_it_has_a_timeout(self):
        """A hung suite must release the runner rather than hold it for six hours."""
        assert SUITE.read_text(encoding="utf-8").count("timeout-minutes:") >= 1


class TestItInstallsWhatTheSuiteNeeds:
    """A suite that skips in CI because the job was too thin never ran either."""

    def test_it_pins_the_python_floor_the_project_declares(self):
        doc = _load(SUITE)
        pinned = [step.get("with", {}).get("python-version")
                  for _j, _job, step in _steps(doc)
                  if str(step.get("uses", "")).startswith("actions/setup-python")]
        assert pinned, "no setup-python step"
        assert pinned[0].strip("'\"") == _python_floor()
        assert PY_VERSION_FILE.read_text(encoding="utf-8").strip() == _python_floor()

    def test_it_installs_the_apps_own_dependency_list(self):
        runs = _run_text(_load(SUITE))
        assert "pip install -r requirements.txt" in runs
        assert (ROOT / "requirements.txt").is_file()
        assert "pytest" in (ROOT / "requirements.txt").read_text(encoding="utf-8")

    def test_it_checks_out_the_history_a_suite_reads(self):
        """`test_merge_runbook.py` re-reads four historical commits."""
        doc = _load(SUITE)
        checkouts = [step for _j, _job, step in _steps(doc)
                     if str(step.get("uses", "")).startswith("actions/checkout")]
        assert checkouts, "no checkout step"
        assert str(checkouts[0].get("with", {}).get("fetch-depth")) == "0", (
            "a shallow clone makes the merge-runbook suite a tree it cannot read")

    def test_it_gives_the_browser_gates_a_browser(self):
        doc = _load(SUITE)
        runs = _run_text(doc)
        assert "google-chrome" in runs and "command -v" in runs
        assert "::error::" in runs and "exit 1" in runs, (
            "a runner with no browser must fail the job, not skip the gates")
        suite_step = next(
            step for _j, _job, step in _steps(doc)
            if TARGET in str(step.get("run") or ""))
        env = suite_step.get("env") or {}
        assert "SG_CHROME" in env, (
            "the browser-gate suites skip without SG_CHROME; handing it over is what "
            "makes them run here")
        assert "steps.browser.outputs.path" in str(env["SG_CHROME"])

    def test_it_gives_the_jsdom_suites_a_node_toolchain(self):
        doc = _load(SUITE)
        runs = _run_text(doc)
        assert "npm ci" in runs, "jsdom and tailwind are node dependencies, not pip ones"
        assert LOCKFILE.is_file(), "`npm ci` needs a committed lockfile"

    def test_the_node_version_is_one_jsdom_accepts(self):
        """A bump of jsdom's engines must not leave CI on an unsupported Node."""
        doc = _load(SUITE)
        pinned = [str(step.get("with", {}).get("node-version", "")).strip("'\"")
                  for _j, _job, step in _steps(doc)
                  if str(step.get("uses", "")).startswith("actions/setup-node")]
        assert pinned, "no setup-node step"
        major = int(re.match(r"(\d+)", pinned[0]).group(1))
        clauses = _node_versions_jsdom_accepts()
        assert any(_satisfies(major, *clause) for clause in clauses), (
            f"the job pins node {pinned[0]}; jsdom accepts {clauses}")

    def test_it_refuses_a_committed_env(self):
        """Judged on the step that would run, not on the prose around it."""
        doc = _load(SUITE)
        mentions = [step for _j, _job, step in _steps(doc)
                    if ".env" in str(step.get("run") or "")]
        assert mentions, (
            "no step looks for a `.env`: the suite takes a different shape with one "
            "in the tree, and a committed `.env` is a leaked service-role key")
        refusal = "\n".join(str(step.get("run") or "") for step in mentions)
        assert "-e .env" in refusal and "exit 1" in refusal, (
            "the job mentions a `.env` but does not refuse one")


class TestTheCommitThatEditsItIsJudged:

    def test_the_pre_commit_hook_opens_on_a_workflow_edit(self):
        """A workflow that stops running the suite is invisible — it never starts."""
        hook = (ROOT / "deploy" / "git-hooks" / "pre-commit").read_text(encoding="utf-8")
        line = next(l for l in hook.splitlines() if "grep -qE" in l)
        pattern = line.split("grep -qE '", 1)[1].rsplit("'", 1)[0]
        compiled = re.compile(pattern)
        for rel in (".github/workflows/unit-tests.yml",
                    ".github/workflows/region-guard.yml"):
            assert compiled.search(rel), f"{rel} does not trigger the gate"

    def test_the_deploy_gate_runs_this_guard(self):
        """The gate the hook runs, so the refusal arrives before the commit does."""
        gate = (ROOT / "deploy" / "theme_gate.sh").read_text(encoding="utf-8")
        assert "tests/unit/test_ci_workflows.py" in gate


class TestNothingElseIsDoingAPushOrADeploy:

    @pytest.mark.parametrize("path", _workflows(), ids=lambda p: p.name)
    def test_no_workflow_pushes_or_deploys(self, path):
        runs = _run_text(_load(path))
        for forbidden in ("git push", "deploy/scangrade-deploy.sh", "sudo ", "gh pr "):
            assert forbidden not in runs, f"{path.name} runs `{forbidden}`"

    @pytest.mark.parametrize("path", _workflows(), ids=lambda p: p.name)
    def test_every_test_file_a_workflow_names_exists(self, path):
        """A renamed suite must not leave a job asserting a file that is not there."""
        for name in re.findall(r"tests/unit/test_[A-Za-z0-9_]+\.py", path.read_text(encoding="utf-8")):
            assert (ROOT / name).is_file(), f"{path.name} names {name}, which is gone"
