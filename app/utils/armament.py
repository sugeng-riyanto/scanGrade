"""Whether this box is armed to check a release — asked *by* the deploy, using the
one definition that already exists.

Why the app has to be the one that asks
---------------------------------------
The deploy runner refuses a *drifted copy* of itself (Gate 0), and the launcher
refuses a checkout that has lost Gate 0. Neither reaches the worst of the three
states: an installed copy so old that it predates Gate 0 altogether. That copy
deploys happily, running gates from the day it was installed, and the only trace
is a super-admin page nobody has a reason to open. Nothing inside such a file can
judge it — a copy cannot apply the checkout's logic to itself.

What *is* fresh on that box is the app: the copy pulls the new commit and reloads
it, so the code about to be deployed is the code from the repository. So the
deploy's own construct probe builds the app from the new commit, and the app
refuses when the runner that is deploying it is not armed. That fails the release
— the copy's construct gate is one of the oldest gates and every copy carries it,
along with the marker that identifies a probe — and the previous commit keeps
serving. The site is never touched: gunicorn constructs the same app without the
probe marker, and only the probe asks this question.

Why it shells out instead of deciding
-------------------------------------
`deploy/arm-auto-deploy.sh --check` already answers "is this box armed" for a
human: the installed runner (a copy is unarmed), the gate config, the roster. A
second implementation in Python would be a second opinion, and the first thing two
opinions do is disagree. `--check` changes nothing and is safe as any user, so it
is run and its own report is what gets printed. The marker below is what the
deploy greps for, so the journal names the real cause instead of "app did not
construct", which would send the next reader hunting for a Python fault.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

#: The deploy greps for this to tell the app's refusal apart from a construct
#: error. Not a log level, not a return code: the probe is a `python -c` whose
#: output is the only channel back.
MARKER = "SCANGRADE-UNARMED"

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKER = REPO_ROOT / "deploy" / "arm-auto-deploy.sh"

#: The checker reads the filesystem and a roster; it does no network work. Anything
#: approaching this is a broken box rather than a slow one.
TIMEOUT_SECONDS = 30


def unarmed_reason(timeout: int = TIMEOUT_SECONDS) -> str | None:
    """The checker's own report when this box is unarmed, or None when it is armed.

    A checker that cannot be run is reported as unarmed rather than waved through:
    "we could not tell" is not "it is fine", and the whole point of this module is
    that the silent version of that confusion ships code no gate has looked at.
    """
    if not CHECKER.exists():
        return (f"the armament checker is missing from this checkout: {CHECKER}\n"
                f"it arrives with the installer, and until it is there this box "
                f"cannot say what is armed")

    try:
        proc = subprocess.run(
            ["bash", str(CHECKER), "--check"],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return (f"the armament checker did not finish within {timeout}s — treating "
                f"this box as not armed, because an answer nobody can get is not an "
                f"answer")
    except OSError as exc:
        return f"the armament checker could not be run ({type(exc).__name__}: {exc})"

    if proc.returncode == 0:
        return None
    report = (proc.stdout + proc.stderr).strip()
    return report or f"{CHECKER} --check exited {proc.returncode} with no output"
