"""The interpreter floor, and why declaring it is not the same as refusing it.

`pyproject.toml` says `requires-python` and `.python-version` says which
interpreter tooling should pick, but neither can stop a `.venv` that already
exists: a checkout built with 3.11 ignores both. The run then dies at
*collection* with a bare `SyntaxError` in a test file nobody touched — which is
the failure this test exists to keep from coming back.

So the rule is checked in one place (`python_requires.check`) and the three
declarations are asserted to agree with it, because a drift between them is
exactly how the confusing failure returns.
"""
from __future__ import annotations

import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

import python_requires

ROOT = Path(python_requires.__file__).resolve().parent
FLOOR = f"{python_requires.MINIMUM[0]}.{python_requires.MINIMUM[1]}"


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
