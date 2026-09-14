"""A Tailwind utility whose *name* is built at render time compiles to nothing.

Tailwind reads the template as plain text. `bg-gradient-to-r from-{{ color }}-600`
therefore matches no utility: the scanner sees `from-` and `-600`, generates
neither, and the element keeps whatever its ancestor set. Nothing errors, no
404, no console warning.

That is exactly how two of the three "Log in" buttons on `/demo` shipped
invisible — white text on the page's own light gradient, on the one page whose
entire job is to get someone signed in. The emerald one worked, which is why it
survived review: two thirds of the page looked right.

Two rules, because the defect has two faces:

1. **No partial utility name.** An interpolation that sits flush against an
   identifier character (`text-{{ accent }}-700`, `{{ color }}-500`) is building a
   name the compiler cannot see. Interpolating a *whole* class string from
   literals in the same file is fine and stays allowed — every branch is visible
   in the source, so the scanner finds it.
2. **Every colour utility a class attribute names exists in the compiled CSS.**
   This catches the same bug from the other side, and catches a stale
   `tailwind.css` — the build is committed, so a template change without
   `npm run css:build` is exactly how this ships.

Rule 2 scopes itself to `class`-like attributes on purpose: base.html *writes*
CSS rules for `.bg-gray-50` and friends in its dark-mode remap, and those names
are supposed to be absent from Tailwind's output.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "app" / "templates"
COMPILED_CSS = ROOT / "app" / "static" / "css" / "tailwind.css"

# Comments paint nothing and compile nothing, so neither rule reads them.
JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.S)
HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)

# A class-bearing attribute: `class`, `:class`, `x-bind:class`, `::class`.
CLASS_ATTR = re.compile(r"""\b(?:x-bind:|::?)?class\s*=\s*"([^"]*)\"""", re.I | re.S)

INTERPOLATION = re.compile(r"\{\{.*?\}\}", re.S)

IDENT = re.compile(r"[A-Za-z0-9_-]")

# Namespaces this project actually paints with, plus its two aliases. `brand` is
# aliased to the `primary` palette and `surface` is custom — both live in
# tailwind.config.js.
COLOR = (
    r"(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|"
    r"sky|blue|indigo|violet|purple|fuchsia|pink|rose|surface|primary|brand)"
)
SHADE = r"(?:50|100|200|300|400|500|600|700|800|900|950)"
COLOR_UTILITY = re.compile(
    r"\b(?:[a-z-]+:)*(?:bg|text|border|from|to|via|ring|divide|shadow|outline|decoration|"
    r"fill|stroke|placeholder|caret|accent)-" + COLOR + r"-" + SHADE + r"\b"
)

# Characters Tailwind escapes when it writes a selector.
ESCAPED = ":/[].%#()"


def clean(path):
    """The template with comments blanked, offsets and line numbers preserved."""
    text = path.read_text(encoding="utf-8")
    for pattern in (JINJA_COMMENT, HTML_COMMENT):
        text = pattern.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    return text


def templates():
    return sorted(TEMPLATES.rglob("*.html"))


def class_attributes(path):
    """(line, value) for every class-bearing attribute in a template."""
    text = clean(path)
    for m in CLASS_ATTR.finditer(text):
        yield text.count("\n", 0, m.start()) + 1, m.group(1)


def escape_class(token):
    return "".join(("\\" + ch) if ch in ESCAPED else ch for ch in token)


class TestNoUtilityNameIsBuiltAtRenderTime:
    def test_no_interpolation_is_flush_against_an_identifier(self):
        offenders = []
        for path in templates():
            rel = path.relative_to(TEMPLATES).as_posix()
            for line, value in class_attributes(path):
                for m in INTERPOLATION.finditer(value):
                    before = value[m.start() - 1] if m.start() else ""
                    after = value[m.end()] if m.end() < len(value) else ""
                    if (before and IDENT.match(before)) or (after and IDENT.match(after)):
                        offenders.append(f"{rel}:{line} …{value[max(0, m.start() - 18):m.end() + 10]}…")
        assert not offenders, (
            "these class attributes build part of a Tailwind utility name at render time, so "
            "the utility is never generated and the element silently keeps its ancestor's "
            "styling:\n  " + "\n  ".join(offenders[:12])
            + "\n\nName each utility literally (a `{% set %}` map is the pattern used in "
            "demo.html), or move the whole class string into the template source."
        )

    def test_the_rule_is_catching_what_it_claims(self):
        """The detector is regex work; prove it fires on the shape it describes."""
        text = 'class="bg-gradient-to-r from-{{ color }}-600 to-{{ color }}-500"'
        value = CLASS_ATTR.search(text).group(1)
        hits = [
            m for m in INTERPOLATION.finditer(value)
            if IDENT.match(value[m.start() - 1])
            or (m.end() < len(value) and IDENT.match(value[m.end()]))
        ]
        assert len(hits) == 2

    def test_whole_class_strings_from_literals_stay_allowed(self):
        """The legitimate shape must not be flagged, or the rule gets switched off."""
        text = (
            '<div class="badge {{ \'bg-green-100 text-green-700\' if active '
            "else 'bg-gray-100 text-gray-500' }}\">"
        )
        value = CLASS_ATTR.search(text).group(1)
        for m in INTERPOLATION.finditer(value):
            before = value[m.start() - 1] if m.start() else ""
            after = value[m.end()] if m.end() < len(value) else ""
            assert not ((before and IDENT.match(before)) or (after and IDENT.match(after)))


class TestTheCompiledCssHasWhatTheTemplatesAskFor:
    def test_the_build_is_committed(self):
        assert COMPILED_CSS.exists(), (
            "app/static/css/tailwind.css is missing. It is committed rather than built on the "
            "server, so every template change needs `npm run css:build` before it ships."
        )

    def test_every_colour_utility_a_class_attribute_names_is_in_the_css(self):
        css = COMPILED_CSS.read_text(encoding="utf-8")
        offenders = []
        for path in templates():
            rel = path.relative_to(TEMPLATES).as_posix()
            named = set()
            for _, value in class_attributes(path):
                named.update(COLOR_UTILITY.findall(value))
            for token in sorted(named):
                selector = re.escape("." + escape_class(token))
                if not re.search(selector + r"(?![A-Za-z0-9_-])", css):
                    offenders.append(f"{rel}: {token}")
        assert not offenders, (
            "these colour utilities are used in a class attribute but are not in the compiled "
            "stylesheet, so the elements render unstyled — which for a `from-`/`to-` pair means "
            "they inherit a gradient and their text becomes invisible:\n  "
            + "\n  ".join(offenders[:12])
            + "\n\nRun `npm run css:build`; if the class is built by interpolation, name it "
            "literally instead."
        )

    def test_the_detector_would_notice_a_missing_utility(self):
        """A check that cannot fail is decoration. `from-blue-600` exists; this does not."""
        css = COMPILED_CSS.read_text(encoding="utf-8")
        assert re.search(re.escape("." + "from-blue-600") + r"(?![A-Za-z0-9_-])", css), (
            "the demo page's admin login button asks for `from-blue-600`; if the build does not "
            "contain it, that button renders as an empty white box."
        )
        assert not re.search(
            re.escape("." + escape_class("from-cerulean-600")) + r"(?![A-Za-z0-9_-])", css
        ), "the detector matches a token that cannot exist, so it is not actually reading the CSS"
