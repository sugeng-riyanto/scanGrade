"""A Tailwind utility whose *name* is built at render time compiles to nothing.

Tailwind reads the template as plain text. `bg-gradient-to-r from-{{ color }}-600`
therefore matches no utility: the scanner sees `from-` and `-600`, generates
neither, and the element keeps whatever its ancestor set. Nothing errors, no
404, no console warning.

That is exactly how two of the three "Log in" buttons on `/demo` shipped
invisible — white text on the page's own light gradient, on the one page whose
entire job is to get someone signed in. The emerald one worked, which is why it
survived review: two thirds of the page looked right.

Three rules, because the defect has three faces:

1. **No partial utility name in a class attribute.** An interpolation that sits
   flush against an identifier character (`text-{{ accent }}-700`,
   `{{ color }}-500`) is building a name the compiler cannot see. Interpolating a
   *whole* class string from literals in the same file is fine and stays allowed —
   every branch is visible in the source, so the scanner finds it.
2. **Every colour utility a class-bearing attribute or a script names exists in
   the compiled CSS.** This catches the same bug from the other side, and catches
   a stale `tailwind.css` — the build is committed, so a template change without
   `npm run css:build` is exactly how this ships.
3. **No utility name assembled in JavaScript.** `:class="'from-' + color"` and
   `` :class="`bg-${tone}-500`" `` are rule 1's bug in different clothes: the
   fragments are literal, the *name* is not, so Tailwind sees `from-` and
   generates nothing. Rule 1 cannot see inside an Alpine expression and rule 2
   cannot see a name that never appears whole, which is why this needs its own
   rule rather than a wider regex. The note above `JS_ATTR`, at the foot of this
   file, says exactly what it catches and what it deliberately does not.

Rule 2 scopes itself to `class`-like attributes and script regions on purpose:
base.html *writes* CSS rules for `.bg-gray-50` and friends in its dark-mode
remap, and those names are supposed to be absent from Tailwind's output.
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

    def test_every_colour_utility_a_script_names_is_in_the_css(self):
        """A class chosen in JS compiles by the same rule as one written in HTML.

        Rule 2 used to read only `class`-bearing attributes, so the palette in
        `ai_settings.html`'s `x-data` provider list and the whiteboard toggle in
        `schools.html`'s `<script>` were never compared against the build. They
        are compiled today only because Tailwind scans the raw file; a stale
        `tailwind.css` would leave `bg-emerald-50` styling nothing, and the pill
        that should be selected would look exactly like the ones that are not.
        """
        css = COMPILED_CSS.read_text(encoding="utf-8")
        offenders = []
        for rel, regions_ in js_sources():
            named = set()
            for _, text in regions_:
                named.update(COLOR_UTILITY.findall(text))
            for token in sorted(named):
                selector = re.escape("." + escape_class(token))
                if not re.search(selector + r"(?![A-Za-z0-9_-])", css):
                    offenders.append(f"{rel}: {token}")
        assert not offenders, (
            "these colour utilities are named inside JavaScript but are not in the compiled "
            "stylesheet, so the elements render unstyled:\n  "
            + "\n  ".join(offenders[:12])
            + "\n\nRun `npm run css:build`."
        )

    def test_the_script_scan_actually_reads_both_kinds_of_region(self):
        """Prove the extractor reaches `x-data` and `<script>`, not just one of them."""
        named = {
            rel: set().union(*[set(COLOR_UTILITY.findall(t)) for _, t in regions_]) if regions_ else set()
            for rel, regions_ in js_sources()
        }
        assert "bg-emerald-50" in named.get("teacher/ai_settings.html", set()), (
            "ai_settings.html picks a provider pill's colours from an object literal in "
            "`x-data`; if the sweep cannot see those, half the page is unchecked."
        )
        assert "bg-violet-100" in named.get("super_admin/schools.html", set()), (
            "schools.html writes its toggle's classes from a `<script>`; if the sweep "
            "cannot see those, the other half is unchecked."
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


# ── rule 3: a name assembled in Alpine or in a script ─────────────────────
#
# `:class="'from-' + color + '-600'"` is rule 1's bug wearing different clothes.
# Rule 1 cannot see it, because the glue is JavaScript rather than `{{ }}`; rule 2
# cannot see it either, because the attribute names `from-` and `-600` and neither
# is a class the compiler can emit. What ships is a button that keeps its
# ancestor's gradient — the /demo failure, reachable from a second direction.
#
# So: a literal fragment that leaves a Tailwind name unfinished, glued to a
# computed piece, is an offender. A whole class string chosen by a ternary
# (`on ? 'bg-green-500' : 'bg-gray-300'`) stays allowed, exactly as in rule 1:
# every branch is literal and the scanner finds all of them.
#
# The tripwire deliberately does not catch a name whose *palette* is computed at
# a distance — `const stem = 'bg-'; … stem + '-500'`. Nothing in the file names a
# utility there, so no pattern can separate it from `'code-' + n`. It catches the
# shapes people actually write (a name split by `+`, or a `${}` inside one), and
# a false negative is still better than a rule nobody can keep green.

# Alpine bindings, event handlers, and the body of every <script> element.
JS_ATTR = re.compile(r"""(?:^|\s)((?::|@|x-)[\w.:-]+|on\w+)\s*=\s*"([^"]*)\"""", re.I | re.S)
SCRIPT_BODY = re.compile(r"<script\b[^>]*>(.*?)</script>", re.S | re.I)

# A JS string, or a template literal (which may span lines and hold ${}).
JS_STRING = re.compile(r"'((?:\\.|[^'\\\n])*)'|\"((?:\\.|[^\"\\\n])*)\"|`((?:\\.|[^`\\])*)`", re.S)
CONCAT_CALL = re.compile(r"\.concat\s*\(([^()]*)\)")

# One `${...}`, deepest nesting one — nothing sane nests further in a class name.
TEMPLATE_HOLE = re.compile(r"\$\{(?:[^{}]|\{[^{}]*\})*\}")

SENTINEL = "\u2400"  # stands in for a computed piece once a template is flattened
VARIANT = r"(?:[a-z-]+:)*"
STEM = (
    r"(?:bg|text|border|from|to|via|ring|divide|shadow|outline|decoration|fill|stroke|"
    r"placeholder|caret|accent)"
)
# `bg-`, `text-emerald-`, `hover:border-` — a name that stops just short of a shade.
DANGLING_STEM = re.compile(r"^" + VARIANT + STEM + r"(?:-" + COLOR + r")?-$")

# One whole utility name, anchored at both ends: a stem, a palette (which may be
# the computed piece), a shade, and an optional arbitrary value.
#
# The end anchor is what keeps SVG attribute names out of the sweep — `text-`,
# `stroke-`, `fill-` and `caret-` are all Tailwind stems *and* the beginnings of
# `text-anchor=`, `stroke-width=` and friends, which `app/static/js/tools.js`
# writes by the hundred. Requiring the token to end like a name (never on `=`,
# `<`, `>` or `(`) separates the two by shape rather than by guesswork.
PALETTE = r"(?:" + COLOR + r"|" + SENTINEL + r")"
NAME_SHAPE = re.compile(
    r"^" + VARIANT + STEM
    + r"(?:-" + PALETTE + r")?"                      # bg, bg-emerald, bg-${tone}
    + r"(?:-(?:" + SHADE + r"|" + SENTINEL + r"))?"  # -500, or -${shade}
    + r"(?:\[[^\]]*\])?$"                            # text-[…]
)


def strip_js_comments(text):
    """JavaScript with its comments blanked, offsets and line numbers preserved.

    Commented-out code is not a defect and must not fail the build — the same
    courtesy `clean()` extends to `{# #}` and `<!-- -->` for the other rules.
    A `//` is only treated as a comment when no quote precedes it on its line, so
    a URL inside a string survives; `/* */` is blanked outright.
    """
    text = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
    kept = []
    for line in text.split("\n"):
        cut = line.find("//")
        if cut != -1 and not any(q in line[:cut] for q in "'\"`"):
            line = line[:cut]
        kept.append(line)
    return "\n".join(kept)


def js_regions(path):
    """(line, text) for every place a template runs JavaScript."""
    text = clean(path)
    for m in SCRIPT_BODY.finditer(text):
        yield text.count("\n", 0, m.start(1)) + 1, m.group(1)
    for m in JS_ATTR.finditer(text):
        yield text.count("\n", 0, m.start(2)) + 1, m.group(2)


def js_sources():
    """Every file that runs JavaScript here: templates, plus the static bundles."""
    for path in templates():
        yield path.relative_to(TEMPLATES).as_posix(), list(js_regions(path))
    for path in sorted((ROOT / "app" / "static" / "js").glob("*.js")):
        yield "static/js/" + path.name, [(1, path.read_text(encoding="utf-8"))]


def assembled_names(text):
    """(offset, token, reason) for each unfinished utility name built from literals.

    A literal is analysed when it is *glued*: to a `+` on either side, to a
    `${...}` inside it, or to the argument list of a `.concat(` call. Anything
    that is a whole class string on its own is left alone.
    """
    text = strip_js_comments(text)
    concat = [m.span(1) for m in CONCAT_CALL.finditer(text)]
    found = []
    for m in JS_STRING.finditer(text):
        group = m.group(1) if m.group(1) is not None else m.group(2)
        is_template = m.group(3) is not None
        content = m.group(3) if is_template else group
        if content is None:
            continue
        left, right = text[: m.start()].rstrip(), text[m.end():].lstrip()
        glued = (
            (is_template and "${" in content)
            or left.endswith("+")
            or right.startswith("+")
            or any(a <= m.start() < b for a, b in concat)
        )
        # A literal that is *nothing but* the tail of a name — `'bg-'`,
        # `'text-emerald-'` — is a fragment by construction, wherever it sits:
        # there is no complete class it could be. This is what catches a partial
        # name picked by a ternary (`ok ? 'border-' : 'no-'`) inside a longer
        # concatenation, where no `+` touches the fragment itself.
        if not glued and not DANGLING_STEM.match(content):
            continue
        tokens = (TEMPLATE_HOLE.sub(SENTINEL, content) if is_template else content).split()
        for token in tokens:
            if token.endswith("-") and DANGLING_STEM.match(token):  # `'bg-' + tone`
                found.append((m.start(), token, "a name left open for a computed piece to finish"))
            elif SENTINEL in token and NAME_SHAPE.match(token):
                # Read the sentinel back as `${…}` when reporting: a failure
                # message is read in a plain terminal, where the private-use
                # character is a replacement glyph and says nothing.
                shown = token.replace(SENTINEL, "${...}")
                found.append((m.start(), shown, "a computed piece sitting inside the name"))
    return found


def names_in_snippet(snippet):
    """What the sweep sees in a snippet: an Alpine binding's value, else the code.

    A template's `:class="…"` is JavaScript *inside* an HTML attribute, so the
    sweep reads the value and never the surrounding quotes. Feeding a raw
    attribute here would test a different string than the one that ships — the
    outer double quotes would swallow the inner single-quoted fragments.
    """
    values = [m.group(2) for m in JS_ATTR.finditer(snippet)]
    return assembled_names(values[0] if values else snippet)


class TestNoUtilityNameIsAssembledInJavaScript:
    def test_no_glued_literal_leaves_a_utility_name_unfinished(self):
        offenders = []
        for rel, regions_ in js_sources():
            for base, text in regions_:
                for offset, token, reason in assembled_names(text):
                    line = base + text.count("\n", 0, offset)
                    offenders.append(f"{rel}:{line}  {token!r} — {reason}")
        assert not offenders, (
            "this JavaScript builds part of a Tailwind utility name at render time, so the "
            "utility is never generated and the element silently keeps whatever styling it "
            "inherited:\n  " + "\n  ".join(offenders[:12])
            + "\n\nName each utility literally and pick between whole class strings "
            "(`ok ? 'bg-green-500' : 'bg-gray-300'`), which the compiler can see."
        )

    def test_the_sweep_is_reading_the_regions_it_claims_to(self):
        """A sweep over an empty list passes for the wrong reason."""
        sources = list(js_sources())
        assert len(sources) > 30, f"only {len(sources)} JavaScript-bearing files found"
        regions_ = [text for _, rs in sources for _, text in rs]
        assert any("classList" in t or "fetch(" in t for t in regions_), (
            "no <script> body was extracted, so rule 3 cannot see a single function"
        )
        assert any(t.strip() == "goToSlide(i)" for t in regions_), (
            "no Alpine binding *value* was extracted, so rule 3 cannot see a single `:class`"
        )

    def test_the_rule_is_catching_what_it_claims(self):
        """Prove each shape fires, since the detector is regex work."""
        bad = [
            ":class=\"'from-' + color + '-600'\"",
            ":class=\"'border ' + (ok ? 'border-' : 'no-') + tone + '-300'\"",
            ":class=\"`bg-${tone}-500`\"",
            ":class=\"`text-${tone}`\"",
            ":class=\"`hover:border-${tone}-700`\"",
            "btn.className = 'px-3 text-' + tone + '-700'",
            "el.classList.add('bg-' + tone)",
            "el.className = el.className.concat('bg-', tone)",
        ]
        for snippet in bad:
            assert names_in_snippet(snippet), f"the detector let this through: {snippet}"

    def test_whole_class_strings_and_other_strings_stay_allowed(self):
        """The legitimate shapes must not be flagged, or the rule gets switched off."""
        good = [
            # Whole class strings chosen per branch — every one is literal.
            ":class=\"ok ? 'bg-green-500' : 'bg-gray-300'\"",
            ":class=\"'w-1.5 h-1.5 shrink-0 ' + (m.can_annotate ? 'bg-green-500' : 'bg-gray-300')\"",
            ":class=\"selected ? p.border + ' ' + p.bg : 'border-surface-200'\"",
            # Strings that are not class names at all.
            "document.getElementById('ocr-text-' + qIdx)",
            "fetch('/super-admin/api/school/' + id + '/toggle-whiteboard')",
            "btn.textContent = enabled ? 'ON' : 'OFF'",
            # A `+`-joined class list whose fragments are complete.
            ":class=\"'border-2 p-3 ' + extra\"",
        ]
        for snippet in good:
            assert not names_in_snippet(snippet), (
                f"the detector flagged a legitimate line: {snippet}"
            )

    def test_commented_out_code_is_not_an_offender(self):
        """A dev commenting out a bad line must not be blocked by the sweep."""
        assert not names_in_snippet("// el.className = 'text-' + tone + '-700'")
        assert not names_in_snippet("/* el.className = 'text-' + tone + '-700' */")
        assert names_in_snippet("el.className = 'text-' + tone + '-700'"), (
            "the stripper is eating live code, not just comments"
        )
        assert not names_in_snippet("var u = 'https://api.example.com/' + path"), (
            "a URL with `//` and a `+` is not a comment"
        )

    def test_svg_attribute_names_are_not_mistaken_for_class_names(self):
        """`text-anchor=` and `stroke-width=` start with Tailwind stems.

        tools.js builds most of the geometry panel this way. Without the shape
        check the sweep reports about twenty of them and the rule gets deleted.
        """
        for snippet in (
            "'<text x=\"' + x + '\" y=\"' + y + '\" text-anchor=\"middle\">' + v + '</text>'",
            "'<line stroke-width=\"' + w + '\"/>'",
            "'<circle fill=\"' + tone + '\"/>'",
        ):
            assert not names_in_snippet(snippet), (
                f"an SVG attribute name was read as a class: {snippet}"
            )
