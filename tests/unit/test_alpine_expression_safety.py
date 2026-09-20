"""Two ways a template's Alpine expression breaks without anything failing.

Neither defect shows up as a Python exception, a template error or a 500: the page
answers 200, the markup is intact, and only the browser console knows. Both were
found on `/teacher/analysis` in a live preview, which is the point — the suite had
already gone green.

**An apostrophe inside a `t()` pair.** `t('id text','English text')` lives inside an
HTML attribute, so the parser decodes `&#39;` to `'` *before* Alpine evaluates the
expression — and the string ends early:

    t('apa yang dikatakan...','what the class&#39;s answers say about the questions')

raised `missing ) after argument list`, and the span rendered **empty** in both
languages. An entity that decodes to a quote is not an escape here; it is the
closing quote. `&quot;` must *not* be flagged: a double quote inside a single-quoted
JS string is legal, and it is how a template writes a quoted word in an attribute it
cannot put quotes in any other way.

**A component that draws a chart, initialized twice.** Alpine calls a component's
own `init()` method, so `x-data="itemAnalysis(...)" x-init="init()"` runs it twice.
For most components the second run is merely wasteful; for one that creates a
Chart.js chart it is an error on the second chart —

    Canvas is already in use. Chart with ID '0' must be destroyed before ...

— the chart survives only because the *first* call won, and the language watcher is
registered twice, so every toggle redraws everything twice.

What these rules deliberately do **not** flag: a pair built by concatenation
(`t('Hapus ' + n + ' murid?', …)`), a pair whose text comes from Jinja
(`t('{{ 'Edit' if exam else 'Buat' }} Ujian', …)`), and an escaped apostrophe
(`t('it\\'s','it\\'s')`). All three are legal JS by the time Alpine runs.
"""
from __future__ import annotations

import html
import pathlib
import re

TEMPLATES = pathlib.Path(__file__).resolve().parents[2] / "app" / "templates"

#: A call, up to the end of the attribute it lives in.
ANY_CALL = re.compile(r"""(?<![\w.$])t\('""")

#: Entities that decode to a *single* quote — the delimiter these pairs use.
APOSTROPHE_ENTITIES = ("&#39;", "&#x27;", "&apos;")

#: A contraction (`class's`, `it's`, `don't`) sitting inside a call: the raw
#: apostrophe that ends the JS string just as the entity does.
CONTRACTION = re.compile(r"[A-Za-z]'[A-Za-z]")

#: `x-data="component(...)" ... x-init="init()"` on one element.
DOUBLE_INIT = re.compile(
    r"""x-data\s*=\s*"[A-Za-z_$][\w$]*\s*\([^"]*\)"[^>]{0,400}?x-init\s*=\s*"init\(\)\"""",
    re.S,
)


def pages() -> list[pathlib.Path]:
    return sorted(TEMPLATES.rglob("*.html"))


def call_text(decoded: str, start: int) -> str:
    """One call's text: to the end of its attribute, or the end of the line."""
    tail = decoded[start:]
    stop = min([index for index in (tail.find('")'), tail.find("'\")"), tail.find("\n"))
                if index >= 0] or [len(tail)])
    return tail[:stop + 2]


def broken_calls(text: str) -> list[str]:
    """Every `t(` whose arguments cannot survive the HTML parser, with its line."""
    decoded = html.unescape(text)
    broken = []
    for match in ANY_CALL.finditer(decoded):
        call = call_text(decoded, match.start())
        if "{{" in call or "+" in call:
            # built by Jinja or by concatenation: not two literals by construction
            continue
        reason = None
        if any(entity in call for entity in APOSTROPHE_ENTITIES):
            reason = "an entity that decodes to an apostrophe"
        elif CONTRACTION.search(call):
            reason = "a raw apostrophe inside a pair"
        if reason:
            line = decoded[:match.start()].count("\n") + 1
            broken.append(f"line {line} ({reason}): {call.splitlines()[0][:120]}")
    return broken


def test_every_bilingual_pair_survives_html_decoding():
    offenders = []
    for page in pages():
        text = page.read_text(encoding="utf-8")
        if "t('" not in text:
            continue
        for item in broken_calls(text):
            offenders.append(f"{page.relative_to(TEMPLATES)} {item}")
    assert not offenders, (
        "these t() calls do not survive the browser decoding the attribute, so Alpine"
        " raises and the element renders nothing:\n  " + "\n  ".join(offenders)
    )


def test_the_rule_would_catch_the_defect_it_describes():
    """A guard that cannot fail is a comment, so it is pointed at the real text."""
    entity = ("<span x-text=\"t('apa yang dikatakan jawaban kelas tentang soalnya',"
              "'what the class&#39;s answers say about the questions')\"></span>")
    assert broken_calls(entity), "the entity rule stopped biting on the real defect"
    raw = "<span x-text=\"t('Ini benar','it's correct')\"></span>"
    assert broken_calls(raw), "the raw-apostrophe rule stopped biting"
    # …and the shapes that are legal, which is what keeps this test honest:
    assert not broken_calls("<span x-text=\"t('itu \\'s','it\\'s')\"></span>")
    assert not broken_calls('<span x-text="t(\'Ajukan via &quot;Hapus&quot;\','
                            '\'Use the &quot;Delete&quot; form\')"></span>')
    assert not broken_calls("<span x-text=\"t('Hapus ' + n + ' murid?','Delete " 
                            + "' + n + ' students?')\"></span>")


def test_a_chart_component_is_not_initialized_twice():
    offenders = []
    for page in pages():
        text = page.read_text(encoding="utf-8")
        if "new Chart(" not in text:
            continue
        for match in DOUBLE_INIT.finditer(text):
            line = text[:match.start()].count("\n") + 1
            offenders.append(f"{page.relative_to(TEMPLATES)}:{line}")
    assert not offenders, (
        "these chart components pair x-data=\"component(...)\" with x-init=\"init()\","
        " so Alpine runs init() twice and the second Chart.js chart on one canvas"
        " throws:\n  " + "\n  ".join(offenders)
    )
