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
from html.parser import HTMLParser

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


#: A `tojson` filter, with or without the force-escape that makes it legal in an
#: attribute. Jinja's own documentation prescribes `|tojson|forceescape` for exactly
#: this reason.
TOJSON = re.compile(r"\|\s*tojson(\s*\|\s*forceescape)?")


#: A double-quoted HTML attribute and its value.
DOUBLE_QUOTED = re.compile(r'="([^"]*)"', re.S)


def raw_tojson(text: str) -> list[str]:
    """Every un-escaped `tojson` sitting inside a double-quoted attribute.

    `tojson` writes `"guru"` **with its quotes**, and the HTML parser ends a
    double-quoted attribute at the first of them. The attribute value stops there,
    so Alpine is handed a truncated expression:

        <div x-data="{ showPw: false, role: {{ … | tojson }} }">
        →  x-data="{ showPw: false, role: "   and  role: " }" as junk attributes

    `Unexpected token '}'`, the page's whole Alpine scope dies with it, and every
    binding in that scope goes undefined — the login page's Teacher/Student buttons
    and its password reveal did nothing, and the console was the only place that
    said so. Force-escaping turns the quotes into entities, which the parser decodes
    *after* it has found the end of the attribute.

    A single-quoted attribute is safe (JSON's `"` does not end `'`), and a `tojson`
    in element text (`<script type="application/json">{{ x|tojson }}</script>`) is
    not in an attribute at all.
    """
    offenders = []
    for attribute in DOUBLE_QUOTED.finditer(text):
        for hit in TOJSON.finditer(attribute.group(1)):
            if hit.group(1):
                continue
            line = text[:attribute.start()].count("\n") + 1
            offenders.append(f"line {line}: {attribute.group(1).strip()[:120]}")
    return offenders


def test_a_json_value_in_an_attribute_is_force_escaped():
    offenders = []
    for page in pages():
        text = page.read_text(encoding="utf-8")
        if "tojson" not in text:
            continue
        for item in raw_tojson(text):
            offenders.append(f"{page.relative_to(TEMPLATES)} {item}")
    assert not offenders, (
        "these `tojson` values sit raw inside a double-quoted attribute, so the HTML"
        " parser ends the attribute at the JSON's own quote and Alpine gets a broken"
        " expression — add `| forceescape`, or single-quote the attribute:\n  "
        + "\n  ".join(offenders)
    )


def test_the_tojson_rule_bites_on_the_defect_it_describes():
    broken = ('<div x-data="{ showPw: false, role: {{ (role_hint if role_hint in '
              "('guru','murid') else '') | tojson }} }\">")
    assert raw_tojson(broken), "the tojson rule stopped biting on the real defect"
    assert raw_tojson('<div x-data="subApp()" x-init="init({{ tiers | tojson }})">')
    assert not raw_tojson('x-data="{ role: {{ hint | tojson | forceescape }} }"')
    assert not raw_tojson("<input value='{{ ids | tojson }}'>")
    assert not raw_tojson('<script id="d" type="application/json">{{ x | tojson }}</script>')


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


# ── a raw double quote inside the attribute that holds the whole app's state ──
#
# The third way, and the one with the largest blast radius. `x-data` is delimited
# by double quotes, so a `"` *inside* it ends the attribute there: the rest of the
# scope becomes junk attributes on the tag, and every directive that depended on
# it stops working. The page still renders, nothing raises, and most of it is
# server-rendered — so the only symptom is that toggles, toasts and buttons do
# nothing.
#
# It was written into a JavaScript *comment* in `base.html`, which is why it is
# read from the rendered page and not from the source: `report's` in a comment is
# legal (a single quote inside a double-quoted attribute), and the character that
# broke it was a pair of quotes around two ordinary words.

#: Members the body's own scope has to expose, each with the punctuation that makes
#: it a *declaration* rather than a word that merely appears (`shareNote:` is not
#: satisfied by `shareNoteX:`, which is the difference between a rename and a
#: truncation). Pages call the rest.
BODY_MEMBERS = ("sidebarOpen:", "lang:", "t(id, en) {", "setLang(", "docLang(",
                "toggleDark(", "darkMode:", "notify(", "toastId:",
                "loading:", "init() {", "shareNote:", "shareTimer:",
                "async copyShareLink()")

#: A body tag carries a class, the scope and its event bindings — a handful. A
#: truncated scope turns its own JavaScript into attributes and the count explodes
#: (measured: 6 attributes intact, 84 truncated).
BODY_ATTRIBUTE_LIMIT = 12


class _BodyTag(HTMLParser):
    """The `<body>` start tag's attributes, as a browser splits them."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.attributes: dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        if tag == "body" and not self.attributes:
            self.attributes = dict(attrs)


def balanced(expr: str) -> str | None:
    """None when every bracket closes, else what is left open.

    Comments are skipped *before* quotes are read — the lesson every reader in this
    repository has had to learn: an apostrophe in `// report's card` otherwise opens
    a string literal that swallows the rest of the expression.
    """
    pairs = {")": "(", "]": "[", "}": "{"}
    stack: list[str] = []
    index, length = 0, len(expr)
    while index < length:
        char = expr[index]
        if char in "'\"`":
            index += 1
            while index < length and expr[index] != char:
                index += 2 if expr[index] == "\\" else 1
        elif char == "/" and index + 1 < length and expr[index + 1] == "/":
            while index < length and expr[index] != "\n":
                index += 1
        elif char in "([{":
            stack.append(char)
        elif char in ")]}":
            if not stack or stack.pop() != pairs[char]:
                return f"a stray {char!r}"
        index += 1
    return f"{len(stack)} unclosed {''.join(stack)}" if stack else None


def _rendered_base(app) -> str:
    from flask import g
    with app.test_request_context("/"):
        g.user = None
        return app.jinja_env.get_template("base.html").render()


def test_the_body_scope_survives_being_an_attribute(app):
    """Read the rendered tag the way a browser does, then check the scope is whole.

    Asserted three ways, because the failure is silent in all of them: the tag has
    one attribute per attribute, the scope still contains the members the tag and
    the pages call, and its brackets close.
    """
    parser = _BodyTag()
    parser.feed(_rendered_base(app))
    scope = parser.attributes.get("x-data", "")

    assert parser.attributes, "base.html no longer has a body tag with a scope"
    assert len(parser.attributes) <= BODY_ATTRIBUTE_LIMIT, (
        "the body tag has "
        f"{len(parser.attributes)} attributes ({sorted(parser.attributes)[:8]}…) — a "
        "double quote inside x-data ends the attribute there and the rest of the "
        "scope is parsed as tag junk"
    )
    missing = [name for name in BODY_MEMBERS if name not in scope]
    assert not missing, (
        f"the body's Alpine scope is missing {missing}: either it was truncated by a "
        "double quote inside the attribute, or a member was dropped and every page "
        "that calls it now fails silently"
    )
    assert balanced(scope) is None, (
        f"the body's Alpine scope does not close: {balanced(scope)}"
    )


def test_the_scope_rule_bites_on_the_defect_it_describes():
    """Pointed at the real text, because a guard that cannot fail is a comment."""
    truncated = ('<body x-data="{\n'
                 '        // It lives here because of a defect that had no symptom: the\n'
                 '        // learner report\'s card called a method only the analysis page\n'
                 '        // defined, so its "Copy link" button did nothing at all — Alpine\n'
                 '        // cannot resolve a handler that is not in scope, and a click that\n'
                 '        // does nothing looks exactly like a click that worked.\n'
                 '        shareNote: \'\',\n'
                 '        shareTimer: null,\n'
                 '      }" @sg\\:unread.window="fetchUnread()">')
    parser = _BodyTag()
    parser.feed(truncated)
    scope = parser.attributes.get("x-data", "")

    assert len(parser.attributes) > BODY_ATTRIBUTE_LIMIT, \
        "the attribute-count signal stopped firing on the real defect"
    assert "async copyShareLink()" not in scope
    assert balanced(scope) is not None, \
        "the balance signal stopped firing on the real defect"

    # …and the shapes that are legal, which is what keeps this honest.
    assert balanced("x-data=\"{ a: () => { b(); } }\"") is None
    assert balanced("{ 'dialog': 1 }") is None, "a single-quoted key is legal"
    assert balanced("{ // report's card, an apostrophe in a comment\n a: 1 }") is None
    assert balanced("{ a: 1 ") is not None
