"""The interpreter this project needs, and which runs must be refused over it.

Why a check and not just a declaration
--------------------------------------
``pyproject.toml`` declares ``requires-python`` and ``.python-version`` tells
tooling which interpreter to pick. Neither can stop a venv that *already exists*:
a checkout whose ``.venv`` was made with an older interpreter pays no attention to
either file, and the failure that follows is misleading. Two test files use PEP
701 f-strings — an f-string expression containing the quote character and a
backslash — which 3.11 cannot even parse. The run then dies at *collection* with
a bare ``SyntaxError`` in ``tests/unit/test_breadcrumbs.py``, a file the reader did
not touch, which looks like a broken test rather than the wrong interpreter.

Why the question is asked about *a run*, not about this box
-----------------------------------------------------------
The first version refused at import, because ``tests/conftest.py`` called
``check()`` as it was imported. Every pytest run loads that file, so *every* run
refused an older interpreter — including the seven theme tests
``deploy/theme_gate.sh`` runs on the box, not one of which uses 3.12 syntax. The
gate therefore refused the release that carried the guard, ``scangrade-deploy.sh``
rolled the checkout back to the previous release and quarantined the commit, and
nothing on any page showed a symptom: the site kept serving the release it already
had. A test-time guard must not be able to deadlock the pipeline that checks it.

So the question is now asked about the run (`refusal_for_run`): below the floor
the *target files* are compiled with the interpreter that will import them, and
only a run that would die at collection is refused — naming the files it would die
on. A run whose targets all parse here (the gate's own set) proceeds. Above the
floor nothing is refused and nothing is scanned.

`check` remains the rule on its own, so a second caller refuses the same
interpreter with the same message instead of writing its own comparison.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

#: The floor. 3.12 is where PEP 701 landed: f-strings may contain the same quote
#: they are delimited by, and backslashes inside the expression.
MINIMUM = (3, 12)

#: The fix, quoted in the message so the refusal is actionable on its own.
_VENV_RECIPE = "python3.12 -m venv .venv"


@dataclass(frozen=True)
class Unparseable:
    """A file this interpreter cannot even parse, and the parser's own reason."""

    path: str
    reason: str


def _floor() -> str:
    return ".".join(str(part) for part in MINIMUM)


def _below(info) -> bool:
    """Whether *info* is older than the floor. The comparison, in one place."""
    return (info.major, info.minor) < MINIMUM


def _message(info, offenders: "list[Unparseable]" = ()) -> str:
    need = _floor()
    lines = [
        f"ScanGrade needs Python {need} or newer, but this interpreter is "
        f"{info.major}.{info.minor}."
    ]
    if offenders:
        lines += [
            "",
            "This run would die at collection with a bare SyntaxError in a file",
            "the reader did not touch, in these files:",
            *(f"    {offender.path}: {offender.reason}" for offender in offenders),
        ]
    lines += [
        "",
        "Python 3.11 and older cannot parse PEP 701 f-strings, which two test",
        "files use, so the suite would fail at collection with a SyntaxError",
        "instead of this message.",
        f"Recreate the venv with a {need} interpreter:",
        f"    {_VENV_RECIPE}",
        "See `.freebuff/run.md`, step 1.",
    ]
    return "\n".join(lines)


def check(version_info=None) -> None:
    """Refuse an interpreter older than :data:`MINIMUM`, for any caller.

    ``version_info`` is injectable so the rule can be tested without running
    under the version it refuses; it defaults to the running interpreter.
    """
    info = sys.version_info if version_info is None else version_info
    if _below(info):
        raise RuntimeError(_message(info))


def _target_files(targets):
    """Every ``.py`` a run over *targets* would import, in a stable order."""
    for target in targets:
        path = Path(target)
        if path.is_dir():
            yield from sorted(path.rglob("*.py"))
        elif path.suffix == ".py":
            yield path


def _parse_here(source: str, filename: str) -> None:
    """Parse *source* the way importing it would: with this interpreter."""
    compile(source, filename, "exec")


def unparseable(targets, parser=_parse_here) -> list[Unparseable]:
    """The files among *targets* this interpreter cannot even parse.

    ``parser`` is injectable for the same reason ``version_info`` is: a 3.12 box
    parses everything, so the refusal path is only reachable on an older one.
    """
    found: list[Unparseable] = []
    for path in _target_files(targets):
        try:
            source = path.read_text(encoding="utf-8")
        except OSError:
            # Unreadable is not unparseable; pytest reports that itself, and a
            # guard that guessed would refuse a release over a permission bit.
            continue
        try:
            parser(source, str(path))
        except SyntaxError as exc:
            found.append(Unparseable(str(path), exc.msg))
    return found


def run_targets(config) -> list[str]:
    """The paths a pytest run was asked to collect, from pytest's own config.

    Lives here rather than in the conftest hook for one reason: a guard whose
    target list comes out empty refuses nothing and looks identical to a guard
    that passed, so the derivation is the part that must be testable.
    """
    args = [arg for arg in config.args if not arg.startswith("-")]
    if not args:
        # No paths on the command line: pytest falls back to `testpaths`, and so
        # does this — otherwise a bare `pytest` would be judged on nothing and
        # would answer "allowed" about a run nobody looked at.
        args = list(config.getini("testpaths")) or ["."]
    return args


def refusal_for_run(targets, *, version_info=None, parser=_parse_here) -> str | None:
    """The message a run over *targets* must stop with here, or ``None`` to go on.

    Below the floor this refuses only a run that would actually die at collection
    — see the module docstring for why that distinction is the difference between
    a guard and a deadlock.
    """
    info = sys.version_info if version_info is None else version_info
    if not _below(info):
        return None
    offenders = unparseable(targets, parser=parser)
    if not offenders:
        return None
    return _message(info, offenders)
