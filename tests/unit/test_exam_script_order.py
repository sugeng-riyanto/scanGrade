"""A deferred helper script is a trap: Alpine starts before it runs.

Alpine is loaded with `defer` (base.html). By the time a deferred script executes
the document is no longer `loading`, so Alpine begins in the next **microtask** —
which is *before* the next deferred script in the document. A page helper that
defines a global read by `x-data` therefore has to be a classic script:

    x-data="examApp(...)"   →   examApp() reads sgExamMedia while it is built

`/static/js/exam-media.js` and `/static/js/tools.js` were loaded with `defer` on
the student exam page. Alpine called `examApp(6000, 12, '')` first, `sgExamMedia`
was undefined, and the whole Alpine scope died on

    ReferenceError: sgExamMedia is not defined

leaving a blank white exam (`stripLifted is not defined`, `isOnline is not
defined`, … — every `x-text`/`:class` on the page). It reproduced in a real
headless Chromium and in no server-side render, because the server half is fine:
the fault is script *order* in the browser.

This guard fails the day a page helper is deferred again. It is a template rule,
not a browser — the render half is already pinned by
`test_student_exam_kind_rendering.py`.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "app" / "templates"
BASE = TEMPLATES / "base.html"
STUDENT_EXAM = TEMPLATES / "student" / "take_exam.html"

#: `<script … defer …>` tags, capturing the whole tag so the `src` can be read.
_SCRIPT = re.compile(r"<script\b[^>]*>", re.I)


def _deferred_scripts(text: str):
    for tag in _SCRIPT.findall(text):
        if re.search(r"\bdefer\b", tag, re.I):
            m = re.search(r'src=["\']([^"\']+)["\']', tag)
            yield (m.group(1) if m else "<inline>")


def test_the_premise_still_holds_alpine_is_deferred():
    """If Alpine ever becomes a classic script this rule changes meaning, so the
    premise is asserted rather than assumed."""
    tags = _SCRIPT.findall(BASE.read_text(encoding="utf-8"))
    alpine = [t for t in tags if "alpine.min.js" in t]
    assert alpine, "base.html no longer loads Alpine"
    assert re.search(r"\bdefer\b", alpine[0], re.I), (
        "Alpine is no longer deferred — revisit this guard's premise")


def test_no_template_defers_a_helper_script():
    """A deferred page helper runs *after* Alpine has already started, so a global
    it defines is missing when `x-data` is evaluated. Only the Alpine vendor file
    itself may be deferred."""
    offenders = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for src in _deferred_scripts(text):
            if "alpine.min.js" in src:
                continue
            offenders.append(f"{path.relative_to(ROOT)}: defer {src}")
    assert not offenders, (
        "these load a helper with `defer`, so Alpine starts before it:\n  "
        + "\n  ".join(offenders))


def test_the_student_exam_page_loads_its_helpers_classically():
    """The exact page the blank-exam report is about, pinned directly."""
    text = STUDENT_EXAM.read_text(encoding="utf-8")
    for helper in ("exam-media.js", "tools.js"):
        tags = [t for t in _SCRIPT.findall(text) if helper in t]
        assert tags, f"the student exam page no longer loads {helper}"
        for tag in tags:
            assert not re.search(r"\bdefer\b", tag, re.I), (
                f"{helper} is deferred again — Alpine will call examApp() before "
                f"it runs, which is the blank white exam")


def test_the_guard_bites_on_the_defect_it_describes():
    """Pointed at the real shape, because a guard that cannot fail is a comment."""
    assert list(_deferred_scripts('<script defer src="/static/js/x.js"></script>')) \
        == ["/static/js/x.js"]
    assert list(_deferred_scripts('<script src="/static/js/x.js"></script>')) == []
