"""Every Alpine directive is JavaScript living inside an HTML attribute.

Only one character is needed to end one early and hand Alpine a truncated
expression: the delimiter quote itself. On `x-data` the blast radius is the whole
page's scope -- the browser prints the rest of the object as text, Alpine never
starts, and every collapsed control stays hidden. The exam builder shipped exactly
that, from a `// "otomatis"` comment. The teacher dashboard carries the same defect
in a quieter place: a raw double quote inside a `t('... "..." ...')` pair.

Neither raises, neither 500s, and the markup around them is intact, so nothing in
the suite noticed. This file reads every template the way a browser reads it --
Jinja tags skipped whole, comments blanked, each value read up to its delimiter --
and refuses a directive whose delimiter is not real, whose brackets do not close,
or whose value ends in a backslash (HTML has no backslash escape; it closes the
attribute instead).

What it deliberately does not flag: a value built with Jinja (`x-init="init({{
x }})"`), a pair with an entity that is not the delimiter quote (`&quot;` inside a
single-quoted JS string), a JS comment containing an apostrophe, and a directive
whose value spans several lines. All are legal by the time Alpine runs.
"""
from __future__ import annotations

import pathlib
import re

TEMPLATES = pathlib.Path(__file__).resolve().parents[2] / "app" / "templates"

#: Pieces of a template that are not HTML attribute text, blanked (not removed) so
#: the line numbers stay exact. A quote in comment prose or in a `<script>` string
#: is not a quote as far as the HTML parser is concerned.
_BLANKED = (
    re.compile(r"<!--.*?-->", re.S),
    re.compile(r"\{#.*?#\}", re.S),
    re.compile(r"<script\b[^>]*>.*?</script>", re.S | re.I),
    re.compile(r"<style\b[^>]*>.*?</style>", re.S | re.I),
)

#: Jinja delimiters and their closers, skipped whole while reading a value.
_JINJA_TAG = {"{{": "}}", "{%": "%}", "{#": "#}"}

#: An Alpine directive name (and the `:` / `.` in `x-on:`, `x-transition:enter`).
_DIRECTIVE = re.compile(r"(?<![\w-])x-[\w.:@-]+")

#: One attribute name character right after a closing quote means that quote did
#: not close an attribute at all -- the value was broken in the middle of a word.
_SEPARATORS = set(" \t\r\n>")
_ALSO_OK = set("/>=")


def blank_non_attributes(text: str) -> str:
    """Replace comments and script/style bodies with whitespace, lines preserved."""
    def blank(match: re.Match) -> str:
        return re.sub(r"[^\n]", " ", match.group(0))
    for pattern in _BLANKED:
        text = pattern.sub(blank, text)
    return text


def read_value(text: str, start: int, quote: str) -> tuple[str, int]:
    """Read one attribute value, treating Jinja tags as opaque.

    Returns (value, index just past the closing quote).
    """
    i, n = start, len(text)
    while i < n:
        two = text[i:i + 2]
        if two in _JINJA_TAG:
            close = text.find(_JINJA_TAG[two], i + 2)
            i = n if close == -1 else close + 2
            continue
        if text[i] == quote:
            return text[start:i], i + 1
        i += 1
    return text[start:], n


def balanced(expr: str) -> str | None:
    """None when every bracket closes, else what is left open.

    Comments are skipped before quotes are read, so an apostrophe in `// it's` does
    not open a string literal that swallows the rest of the expression.
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


def html_in_code(expr: str) -> bool:
    """True when a `</` appears in the expression itself (not inside a string).

    A value that swallowed the rest of a tag contains `</span>`, and that is how a
    missing delimiter is caught even when the next quote happens to be followed by
    a separator. A legitimate expression may print markup (`html:'<p><br></p>'`),
    so the check reads string literals and comments and skips them.
    """
    i, n = 0, len(expr)
    while i < n:
        char = expr[i]
        if char in "'\"`":
            i += 1
            while i < n and expr[i] != char:
                i += 2 if expr[i] == "\\" else 1
            i += 1
            continue
        if char == "/" and i + 1 < n and expr[i + 1] == "/":
            while i < n and expr[i] != "\n":
                i += 1
            continue
        if expr[i:i + 2] == "</":
            return True
        i += 1
    return False


def directive_hazards(text: str) -> list[str]:
    """Every malformed Alpine directive in one template source, with its line."""
    text = blank_non_attributes(text)
    out: list[str] = []
    for match in _DIRECTIVE.finditer(text):
        cursor = match.end()
        while cursor < len(text) and text[cursor] in " \t\r\n":
            cursor += 1
        if cursor >= len(text) or text[cursor] != "=":
            continue
        cursor += 1
        while cursor < len(text) and text[cursor] in " \t\r\n":
            cursor += 1
        if cursor >= len(text) or text[cursor] not in "\"'":
            continue
        quote = text[cursor]
        value, after = read_value(text, cursor + 1, quote)
        line = text.count("\n", 0, match.start()) + 1

        reasons = []
        following = text[after:after + 1]
        if following and following not in _SEPARATORS and following not in _ALSO_OK:
            reasons.append(
                "the delimiter quote is not a real one -- the attribute is ended "
                "early and the rest of the value becomes tag junk")
        elif value.rstrip().endswith("\\"):
            reasons.append(
                "the value ends in a backslash, which HTML does not process: it "
                "closes the attribute instead of escaping a quote")
        bracket_trouble = balanced(value)
        if bracket_trouble:
            reasons.append(f"the expression does not close ({bracket_trouble})")
        if html_in_code(value):
            reasons.append("the value runs past a tag boundary")

        if reasons:
            out.append(f"line {line}  {match.group(0)}=...\n"
                       f"      {'; '.join(reasons)}\n"
                       f"      value: {value.strip()[:110]}")
    return out


def pages() -> list[pathlib.Path]:
    return sorted(TEMPLATES.rglob("*.html"))


def test_no_alpine_directive_is_ended_early():
    offenders = []
    for page in pages():
        text = page.read_text(encoding="utf-8", errors="replace")
        if "x-" not in text:
            continue
        for item in directive_hazards(text):
            offenders.append(f"{page.relative_to(TEMPLATES)} {item}")
    assert not offenders, (
        "these Alpine directives are not well-formed HTML attributes, so the "
        "browser ends the attribute before Alpine sees it and the element's (or "
        "the page's) scope dies silently. Use `&quot;` for a quote inside a "
        "double-quoted directive, or single-quote the attribute:\n  "
        + "\n  ".join(offenders))


def test_the_directive_rule_bites_on_the_defects_it_describes():
    """Pointed at the real text, because a guard that cannot fail is a comment."""
    # 1. the exam builder: a raw double quote inside a double-quoted x-data.
    early_quote = '<div x-data="{\n  // "otomatis" badges: true\n  a: 1\n}">'
    assert directive_hazards(early_quote), \
        "the early-quote signal stopped firing on the real exam-builder defect"

    # 2. the deploy status page: the closing quote is simply missing.
    missing_quote = ('<span x-text="t(\'Diff\',\'Diff\')></span> '
                     '<code class="font-mono">')
    assert directive_hazards(missing_quote), \
        "the missing-delimiter signal stopped firing on the real defect"

    # 3. a backslash where HTML has no escape.
    backslash = '<div x-text="foo\\"></div>'
    assert directive_hazards(backslash), \
        "the backslash signal stopped firing"
    assert not directive_hazards('<div x-text="foo"></div>')


def test_the_legal_shapes_stay_legal():
    """The shapes that must not be flagged, which is what keeps the rule honest."""
    assert not directive_hazards('<div x-data="{ a: 1, b: \'x\' }"></div>')
    assert not directive_hazards(
        '<span x-text="t(\'Klik &quot;Tambah&quot;\',\'Click &quot;Add&quot;\')">'
        '</span>')
    assert not directive_hazards('<span x-show="!chromeHidden"></span>')
    assert not directive_hazards("<div x-data='it is fine'></div>")
    assert not directive_hazards('<span x-text="a < b"></span>')
    assert not directive_hazards(
        '<div x-data="{ // report\'s card, an apostrophe in a comment\n a: 1 }">')
    assert not directive_hazards('<div x-init="init({{ tiers|tojson }})"></div>')
    assert not directive_hazards('<div x-show="page === {{ pn }}+1"></div>')
    # a quote in comment prose is not an attribute
    assert not directive_hazards('<!-- <span x-text="t(\'Analisis Butir Soal\'…"> -->')
    assert not directive_hazards('<script>const s = \'x-data="oops"\';</script>')
