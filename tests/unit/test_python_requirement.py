"""The interpreter floor, why declaring it is not the same as refusing it, and
why the refusal must be asked about *a run* rather than about this box.

`pyproject.toml` says `requires-python` and `.python-version` says which
interpreter tooling should pick, but neither can stop a `.venv` that already
exists: a checkout built with 3.11 ignores both. The run then dies at
*collection* with a bare `SyntaxError` in a test file nobody touched — which is
the failure this file exists to keep from coming back.

The first shape of the guard refused that from an import in `tests/conftest.py`,
so **every** pytest run refused an older interpreter. That is right for the
suite and wrong for the deploy gate: `deploy/theme_gate.sh` runs seven theme
tests, not one of which uses 3.12 syntax, and `deploy/scangrade-deploy.sh` runs
that gate on the box. The result was a release that quarantined itself — the gate
refused it with exit 1, the runner rolled the checkout back to the previous
release and recorded the refusal — and no page showed a symptom, because the site
kept serving the release it already had.

So the question is asked about the run: below the floor, the *target files* are
compiled first, and only a run that would die at collection is refused. A run
whose targets all parse here (the gate's own set) proceeds.

The three declarations (`pyproject.toml`, `.python-version`, `MINIMUM`) are
asserted to agree, because a drift between them is how the confusing failure
returns.
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

import python_requires

ROOT = Path(python_requires.__file__).resolve().parent
FLOOR = f"{python_requires.MINIMUM[0]}.{python_requires.MINIMUM[1]}"
GATE = ROOT / "deploy" / "theme_gate.sh"

#: The two files the suite genuinely cannot run on an older interpreter: they use
#: PEP 701 f-strings, which only 3.12 can parse. Measured, not remembered — see
#: ``test_a_real_old_interpreter_still_refuses_the_whole_suite``.
PEP701_FILES = ("tests/unit/test_breadcrumbs.py", "tests/unit/test_reports_hub.py")


# ── the rule on its own ──────────────────────────────────────────────────────

def test_the_interpreter_running_the_suite_is_supported():
    # No argument: the real `sys.version_info`. If this raises, the suite cannot
    # run here at all, which is the point.
    python_requires.check()


@pytest.mark.parametrize("major,minor", [(2, 7), (3, 9), (3, 10), (3, 11)])
def test_below_the_floor_is_refused(major, minor):
    with pytest.raises(RuntimeError):
        python_requires.check(SimpleNamespace(major=major, minor=minor))


@pytest.mark.parametrize("major,minor", [(3, 12), (3, 13), (3, 14)])
def test_the_floor_and_above_are_allowed(major, minor):
    python_requires.check(SimpleNamespace(major=major, minor=minor))


def test_the_refusal_names_the_reason_and_the_fix():
    # A refusal that does not tell you what to do is barely better than the
    # SyntaxError it replaces, so the message must carry all three: the floor,
    # the cause, and the command.
    with pytest.raises(RuntimeError) as exc:
        python_requires.check(SimpleNamespace(major=3, minor=11))
    message = str(exc.value)
    assert FLOOR in message
    assert "3.11" in message
    assert "PEP 701" in message
    assert "python3.12 -m venv" in message


def test_the_declarations_agree_with_the_floor():
    # `requires-python` is what pip/uv/editors read; `.python-version` is what
    # pyenv/uv pick from. Either drifting below the module's floor would send
    # tooling after an interpreter the suite refuses.
    declares = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert declares["project"]["requires-python"] == f">={FLOOR}"

    pinned = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
    assert pinned == FLOOR


# ── the rule about a run ─────────────────────────────────────────────────────

def _posix(text: str) -> str:
    """Compare paths the way the repo writes them, not the way this box joins them."""
    return text.replace("\\", "/")


def _cannot_parse(*names):
    """A parser that behaves like an interpreter unable to parse *names*.

    The real probe compiles with the running interpreter, which on a 3.12 box
    parses everything — so the refusal path needs a parser that fails the way a
    3.11 box fails, without this test having to run one.
    """
    def parser(source: str, filename: str) -> None:
        normalized = filename.replace("\\", "/")
        for name in names:
            if normalized.endswith(name):
                raise SyntaxError(
                    "f-string expression part cannot include a backslash",
                    (filename, 1, 1, ""),
                )
    return parser


def test_a_run_whose_targets_all_parse_is_allowed_below_the_floor():
    """The deploy gate's case: this is the deadlock, in one assertion.

    `theme_gate.sh` runs seven theme tests that parse on any interpreter the box
    can have. Refusing them refuses every release the box would otherwise serve,
    over a property of the box rather than of the release.
    """
    refusal = python_requires.refusal_for_run(
        ["tests/unit/test_language_toggle.py"],
        version_info=SimpleNamespace(major=3, minor=11),
        parser=_cannot_parse(*PEP701_FILES),
    )
    assert refusal is None


def test_a_run_that_would_die_at_collection_is_refused_and_names_the_file():
    message = python_requires.refusal_for_run(
        ["tests"],
        version_info=SimpleNamespace(major=3, minor=11),
        parser=_cannot_parse(*PEP701_FILES),
    )
    assert message is not None
    # Which file, not just "the suite is broken": the whole point of the guard is
    # that the reader is not sent to a file they did not touch.
    for name in PEP701_FILES:
        assert name in _posix(message)
    assert FLOOR in message
    assert "python3.12 -m venv" in message


def test_at_or_above_the_floor_no_run_is_refused():
    assert python_requires.refusal_for_run(
        ["tests"], version_info=SimpleNamespace(major=3, minor=12)
    ) is None


def test_a_directory_target_is_walked_and_a_file_target_is_read():
    found = python_requires.unparseable(
        ["tests"], parser=_cannot_parse("test_reports_hub.py")
    )
    assert [entry.path.replace("\\", "/") for entry in found] == ["tests/unit/test_reports_hub.py"]


# ── the shape that keeps the deadlock from returning ─────────────────────────

def _top_level_calls(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = []
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            func = node.value.func
            names.append(getattr(func, "id", None) or getattr(func, "attr", "?"))
    return names


def test_importing_the_module_does_not_run_the_check():
    """`check()` at import is exactly how the deploy gate was deadlocked.

    Every pytest run — the pre-commit hook's, the suite's, and
    `deploy/theme_gate.sh`'s seven files on the box — loads `tests/conftest.py`,
    so a refusal at import refuses all of them, including the gate that decides
    whether a release may be served.
    """
    assert "check" not in _top_level_calls(ROOT / "python_requires.py"), (
        "python_requires.check() runs at import; that refuses every pytest run, "
        "so deploy/theme_gate.sh refuses on a box below the floor and quarantines "
        "the release. Ask about the run instead (refusal_for_run)."
    )


def test_the_suite_asks_the_question_at_configure_time():
    """And it asks about the run's own targets, not about nothing."""
    tree = ast.parse((ROOT / "tests" / "conftest.py").read_text(encoding="utf-8"))
    hooks = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "pytest_configure"]
    assert hooks, "tests/conftest.py no longer has a pytest_configure hook"

    called = {getattr(n.func, "attr", None) for n in ast.walk(hooks[0]) if isinstance(n, ast.Call)}
    assert "refusal_for_run" in called, (
        "the configure hook does not ask python_requires whether this run can be parsed"
    )
    assert "run_targets" in called, (
        "the hook refuses without saying what the run targets, so the guard would "
        "be judged on nothing"
    )


class _Config:
    """The two attributes of pytest's config this rule reads."""

    def __init__(self, args, testpaths):
        self.args = args
        self._testpaths = testpaths

    def getini(self, name):
        assert name == "testpaths"
        return self._testpaths


def test_the_run_targets_are_the_paths_pytest_was_handed():
    assert python_requires.run_targets(_Config(["tests/unit/test_a.py", "-q"], ["tests"])) == [
        "tests/unit/test_a.py"
    ]


def test_a_bare_run_is_judged_on_testpaths_rather_than_on_nothing():
    # A guard whose target list comes out empty refuses nothing and is
    # indistinguishable from one that passed — which is how a gate stops running
    # without anybody noticing.
    assert python_requires.run_targets(_Config([], ["tests"])) == ["tests"]
    assert python_requires.run_targets(_Config([], [])) == ["."]


# ── against a real interpreter below the floor ───────────────────────────────
#
# The rule is prose until an old interpreter actually runs it, and the defect this
# file exists for only appeared on one. So these tests look for a real Python
# under the floor and drive the module with it; a box that has none skips them
# rather than pretending the question was answered.

def _older_interpreter() -> list[str] | None:
    candidates = [["python3.11"], ["python3.10"], ["python3.9"], ["py", "-3.11"], ["py", "-3.10"]]
    # `uv` keeps its interpreters somewhere PATH does not show but can name.
    for version in ("3.11", "3.10", "3.9"):
        try:
            found = subprocess.run(["uv", "python", "find", version],
                                   capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            break
        if found.returncode == 0 and found.stdout.strip():
            candidates.append([found.stdout.strip()])
    for candidate in candidates:
        try:
            probe = subprocess.run(
                candidate + ["-c", "import sys;print('%d.%d' % sys.version_info[:2])"],
                capture_output=True, text=True, timeout=60,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if probe.returncode != 0 or not probe.stdout.strip():
            continue
        try:
            major, minor = (int(part) for part in probe.stdout.strip().split("."))
        except ValueError:
            continue
        if (major, minor) < python_requires.MINIMUM:
            return candidate
    return None


def _run_with(interpreter: list[str], code: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        interpreter + ["-c", code],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )


@pytest.fixture(scope="module")
def older() -> list[str]:
    found = _older_interpreter()
    if found is None:
        pytest.skip("no interpreter below the floor is installed here")
    return found


def test_a_real_old_interpreter_can_import_the_module(older):
    # The deadlock itself: this import raised on the box, and the deploy gate
    # loads it before it collects a single test.
    result = _run_with(older, "import python_requires")
    assert result.returncode == 0, (
        f"importing python_requires under {older} failed:\n{result.stderr}"
    )


def test_a_real_old_interpreter_may_run_the_deploy_gates_own_targets(older):
    """The gate's real list, from the gate's own file, on a real old interpreter."""
    targets = [t for t in _gate_targets() if t.endswith(".py")]
    code = (
        "import sys; sys.path.insert(0, '.')\n"
        "import python_requires\n"
        f"refusal = python_requires.refusal_for_run({targets!r})\n"
        "print(refusal or 'allowed')\n"
    )
    result = _run_with(older, code)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "allowed", (
        "the deploy gate would be refused on this interpreter, which is how a "
        f"release quarantines itself:\n{result.stdout}"
    )


def test_a_real_old_interpreter_still_refuses_the_whole_suite(older):
    code = (
        "import sys; sys.path.insert(0, '.')\n"
        "import python_requires\n"
        "print(python_requires.refusal_for_run(['tests']) or 'allowed')\n"
    )
    result = _run_with(older, code)
    assert result.returncode == 0, result.stderr
    refusal = _posix(result.stdout)
    assert "allowed" != refusal.strip(), "the suite must still be refused on an old interpreter"
    for name in PEP701_FILES:
        assert name in refusal, f"{name} is not named in the refusal:\n{refusal}"


def _gate_targets() -> list[str]:
    """The files `deploy/theme_gate.sh` runs, read out of the gate itself.

    Read rather than restated: a test that names its own copy of the list stops
    testing the gate the moment somebody edits the real one.
    """
    match = re.search(r'^TESTS="([^"]+)"', GATE.read_text(encoding="utf-8"), re.M)
    assert match, "deploy/theme_gate.sh no longer declares its TESTS list"
    return match.group(1).split()


def test_the_gates_targets_all_exist():
    missing = [target for target in _gate_targets() if not (ROOT / target).is_file()]
    assert not missing, f"deploy/theme_gate.sh names files that are not in the release: {missing}"
