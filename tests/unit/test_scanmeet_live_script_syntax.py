"""A syntax error in a page script is invisible to every other guard here.

`scan-live-exam.js` declared `const body` inside a function that already had a
parameter called `body`. That is a SyntaxError raised while the browser *parses*
the file, so nothing in it ran: `discover()` never fired, the teacher's page sat
on "Memeriksa sesi Live..." and every control stayed hidden — and the page still
rendered 200, the SDK still loaded, and the media gate's first row still passed.
The twelve-row run read it as "the page offers no *Mulai pengawasan* control",
which is true and says nothing about the cause.

No server-side test can see this: the file is static text that only a JavaScript
parser rejects. `node --check` is that parser, and CI installs node for the
jsdom suites already.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "app" / "static" / "js"

#: The floor that keeps "no findings" from meaning "nothing was opened".
MINIMUM_FILES = 1

NODE = shutil.which("node")


def check(path: Path) -> subprocess.CompletedProcess:
    return subprocess.run([NODE, "--check", str(path)], capture_output=True,
                          text=True, timeout=120)


@pytest.mark.skipif(NODE is None, reason="no node on this box; the syntax check "
                                        "needs a JavaScript parser (CI installs one)")
def test_the_scanmeet_page_script_parses():
    target = SCRIPTS / "scan-live-exam.js"
    assert target.is_file(), "the exam page's own script is missing"
    result = check(target)
    assert result.returncode == 0, (
        "scan-live-exam.js does not parse — the browser rejects the whole file at "
        "load, so no control it reveals will ever appear:\n" + result.stderr)


@pytest.mark.skipif(NODE is None, reason="no node on this box")
def test_every_scanmeet_script_parses():
    files = sorted(SCRIPTS.glob("scan-*.js"))
    assert len(files) >= MINIMUM_FILES, f"found {len(files)} ScanMeet scripts"
    broken = []
    for path in files:
        result = check(path)
        if result.returncode != 0:
            broken.append(f"{path.name}: {result.stderr.strip().splitlines()[0]}")
    assert not broken, "scripts the browser cannot parse:\n" + "\n".join(broken)
