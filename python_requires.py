"""The interpreter this project needs, checked the moment it is imported.

Why a check and not just a declaration
--------------------------------------
``pyproject.toml`` declares ``requires-python`` and ``.python-version`` tells
tooling which interpreter to pick. Neither can stop a venv that *already exists*:
a checkout whose ``.venv`` was made with 3.11 pays no attention to either file,
and the failure that follows is misleading. Two test files use PEP 701 f-strings
— an f-string expression containing the quote character and a backslash — which
Python 3.11 cannot even parse. The run then dies at *collection* with a bare
``SyntaxError`` in ``tests/unit/test_reports_hub.py``, a file the reader did not
touch, which looks like a broken test rather than the wrong interpreter.

``tests/conftest.py`` imports this module before anything else, so the suite
refuses with the reason and the fix instead — before pytest has parsed a single
test, and before any dependency is imported. Import it from anything else that
must not run on an older interpreter.
"""
from __future__ import annotations

import sys

#: The floor. 3.12 is where PEP 701 landed: f-strings may contain the same quote
#: they are delimited by, and backslashes inside the expression.
MINIMUM = (3, 12)

#: The fix, quoted in the message so the refusal is actionable on its own.
_VENV_RECIPE = "python3.12 -m venv .venv"


def check(version_info=None) -> None:
    """Refuse an interpreter older than :data:`MINIMUM`.

    ``version_info`` is injectable so the rule can be tested without running
    under the version it refuses; it defaults to the running interpreter.
    """
    info = sys.version_info if version_info is None else version_info
    if (info.major, info.minor) < MINIMUM:
        need = ".".join(str(part) for part in MINIMUM)
        raise RuntimeError(
            f"ScanGrade needs Python {need} or newer, but this interpreter is "
            f"{info.major}.{info.minor}.\n"
            "Python 3.11 and older cannot parse PEP 701 f-strings, which two "
            "test files use, so the suite would fail at collection with a "
            "SyntaxError instead of this message.\n"
            f"Recreate the venv with a {need} interpreter:\n"
            f"    {_VENV_RECIPE}\n"
            "See `.freebuff/run.md`, step 1."
        )


check()
