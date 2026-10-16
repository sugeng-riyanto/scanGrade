"""A tag that renders leaves a receipt on the page: the reader sees `%}`.

`print/report_card.html` carried a line whose entire content was ` %}` — the closer of a tag
whose opener an edit had already replaced (`902abc5` swapped `{% set school = school or {} %}`
for the renamed `{% set school = card.school or {} %}` and left the closer behind). Jinja had
no opener to consume it, so it was *text*: every report card printed since 30 September
carried a stray `%}` under its "Cetak / Tutup" buttons, on the sheet a school hands a family.

Nothing caught it, and the reason is worth stating: `test_print_reports.py` renders this
document and asserts what it *carries* — the marks, the name, the signature line — and a
sheet with one extra line of punctuation satisfies every one of those assertions. Presence is
checked in a dozen places; **absence needs its own test**, which is this file.

Two readings, because they fail differently:

* the **templates**, lexed by Jinja itself. A `%}` that lands in a *text* token has no opener
  to consume it, so it renders — 148 files, no fixture needed, nothing to keep in step;
The *document* half lives in `test_print_reports.py`, beside the fixtures that sign in and
fetch both routes that serve it: this file reads templates, and the suite owns the page.

The honest limit is the marker list. `%}` is swept because it is not a sequence any language
this app writes inline produces — not HTML, not CSS, not JavaScript. `}}` is deliberately
**not** swept: it is ordinary JavaScript here (`{detail: {error: e}}`, `headers: {'X-CSRF-Token': …}`,
`{i: {p: [{x_pct, y_pct, text}]}}`) in a dozen pages, and a guard that fires on correct code
gets deleted — the note `deploy/i18n_coverage.py` already carries about `label` and `para`.
A closer is a finding in a *text* token and nowhere else.
"""
from pathlib import Path

import pytest
from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "app" / "templates"

#: Closers a text node may never carry, and the reason the list is this short: every one of
#: them has to be impossible to write on purpose in HTML, CSS or JS.
STRAY_CLOSERS = ("%}",)

#: Templates that may print one anyway, each with the reason it is deliberate. An exemption
#: is a sentence rather than a name in a list — the same rule `deploy/render_fixture_gate.py`
#: follows for the documents it cannot render — and the test below refuses an entry whose
#: file is gone, so an exemption cannot outlive what it excused.
CLOSER_IN_TEXT_OK: dict[str, str] = {}


def _templates() -> list[Path]:
    return sorted(TEMPLATES.rglob("*.html"))


def _stray_closers(source: str) -> list[str]:
    """`["line 216: %}"]` for every closer this template would print as text.

    Read through Jinja's own lexer: an expression, a comment or a `{% raw %}` block is not
    text, and a closer inside one is not a defect. A text token's first line is the line it
    starts on, so the number it reports is the number in the file.
    """
    found = []
    for lineno, token, value in Environment().lex(source):
        if token != "data":
            continue
        for offset, line in enumerate(value.splitlines()):
            if any(marker in line for marker in STRAY_CLOSERS):
                found.append(f"line {lineno + offset}: {line.strip()[:70]}")
    return found


class TestTheSweepReadsEveryTemplate:
    def test_it_found_the_templates(self):
        """A glob that matches nothing is not a clean tree — it is a check that ran."""
        files = _templates()
        assert len(files) > 100, f"only {len(files)} template(s) found under {TEMPLATES}"
        assert TEMPLATES / "print" / "report_card.html" in files

    def test_the_reader_looks_at_text_and_not_at_code(self):
        """The three shapes that decide whether this guard is worth having."""
        assert _stray_closers("%}\n") == ["line 1: %}"], (
            "an orphan closer is exactly what this guard is for")
        assert _stray_closers("{{ '%}' }}\n") == [], (
            "a closer inside an expression is code — sweeping it would fire on any page "
            "that prints a percentage")
        assert _stray_closers("{# %! #}\n") == [], (
            "a closer inside a comment renders nothing")


class TestNoTemplatePrintsACloser:
    @pytest.mark.parametrize("path", _templates(), ids=lambda p: p.name)
    def test_no_template_leaves_a_closer_in_its_text(self, path):
        relative = path.relative_to(TEMPLATES).as_posix()
        if relative in CLOSER_IN_TEXT_OK:
            pytest.skip(CLOSER_IN_TEXT_OK[relative])
        found = _stray_closers(path.read_text(encoding="utf-8"))
        assert not found, (
            f"{relative} carries a Jinja closer as *text*, so every reader of that page sees "
            f"it: " + "; ".join(found) + " — a closer with no opener is what a mangled edit "
            "leaves behind")

    def test_the_exemptions_name_files_that_exist(self):
        for relative, reason in CLOSER_IN_TEXT_OK.items():
            assert (TEMPLATES / relative).exists(), (
                f"{relative} is exempt from the sweep and does not exist")
            assert reason.strip(), f"{relative} is exempt without a reason"

class TestTheGateRunsTheSweep:
    """A guard only the suite runs is one a release can outrun.

    The pre-commit hook and the VPS auto-deploy run `deploy/theme_gate.sh`, so a finding
    this file makes has to be a finding *there* or it never refuses anything. Scoped to the
    `TESTS=` assignment on purpose: the gate's header names this suite too, and a check that
    reads the whole file is satisfied by the sentence describing the run after the run itself
    is deleted — the trap `tests/unit/test_i18n_coverage.py` records.
    """

    def test_the_theme_gate_runs_this_file(self):
        gate = (ROOT / "deploy" / "theme_gate.sh").read_text(encoding="utf-8")
        line = next((line for line in gate.splitlines()
                     if line.startswith("TESTS=")), "")
        assert line, "the gate no longer lists any suite"
        # Unquoted first: the assignment ends with `...test_template_artifacts.py"`.
        listed = line.split("=", 1)[1].strip().strip('"').split()
        assert "tests/unit/test_template_artifacts.py" in listed, (
            "the gate does not run this sweep, so a release can ship a stray closer")


