"""A server value pasted into an Alpine attribute is JavaScript, not text.

Found while verifying the translated school dashboard: the active academic year
rendered as ``2026-06-14 - 2007``. The template read
``x-text="{{ active_year.end_date[:10] }}"``, which the server fills in as
``x-text="2027-06-14"`` — and Alpine evaluates that as an expression, so it
computed ``2027 - 6 - 14 = 2007``. Nothing errors; the wrong number simply
appears, which is why it survived unnoticed.

The same shape produced a second, quieter failure: ``x-text="{{ e.subject }}"``
becomes ``x-text="Matematika"``, a reference to an undefined variable, so the
subject cell renders blank.

A number happens to work (``x-text="5"`` is a valid literal), which is what makes
this easy to get away with most of the time. The rule these tests enforce is
uniform instead: an interpolation into a JS-expression attribute must be quoted,
unless the Jinja expression is itself a literal.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = sorted((ROOT / "app" / "templates").rglob("*.html"))

# Attributes whose value Alpine evaluates as JavaScript.
EXPRESSION_ATTRS = (
    "x-text", "x-html", "x-show", "x-model", "x-bind",
    ":class", ":title", ":placeholder", ":disabled", ":value", ":checked",
    ":href", ":action", ":aria-label", ":max", ":min",
)

ATTR = re.compile(
    r"\b(" + "|".join(re.escape(a) for a in EXPRESSION_ATTRS) + r')="([^"]*)"',
    re.S,
)


def _leading_interpolation(value: str) -> str | None:
    """The Jinja expression a JS attribute starts with, if it starts with one."""
    value = value.strip()
    if not value.startswith("{{"):
        return None
    end = value.find("}}")
    if end == -1:
        return None
    return value[2:end].strip()


def _is_js_literal(expr: str) -> bool:
    """True when the expression can only render something JavaScript can parse.

    Two shapes qualify. A Jinja string literal — ``'true' if f.enabled else
    'false'`` — renders ``true``/``false``, so the feature-flag toggles are
    correct as written, and a quoted string is the right way to place text. An
    expression ending in ``|tojson`` renders a JSON literal (a number, ``null``,
    ``true``/``false``), which is what belongs in a numeric comparison like
    ``x-show="{{ n|tojson }} > 0"`` — and it degrades to ``null`` rather than
    ``None``, which would be an undefined variable.

    An expression yielding raw data (``e.subject``, ``active_year.end_date[:10]``)
    qualifies for neither.
    """
    if expr.startswith("'") or expr.startswith('"'):
        return True
    return bool(re.search(r"\|\s*tojson\b", expr))


def test_no_server_value_is_evaluated_as_javascript():
    offenders = []
    for path in TEMPLATES:
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in ATTR.finditer(text):
            attr, value = m.group(1), m.group(2)
            expr = _leading_interpolation(value)
            if expr is None or _is_js_literal(expr):
                continue
            line = text.count("\n", 0, m.start()) + 1
            offenders.append(
                f"{path.relative_to(ROOT)}:{line}  {attr}=\"{value.strip()[:70]}\"")

    assert not offenders, (
        "these Alpine attributes interpolate a server value unquoted, so Alpine "
        "evaluates it as JavaScript — a date becomes a subtraction and a name "
        "becomes an undefined variable. Wrap the interpolation in quotes "
        "(x-text=\"'{{ value }}'\"):\n  " + "\n  ".join(offenders))


def test_the_academic_year_dates_render_as_dates():
    """The regression this file exists for. A date is the worst case: `2027-06-14`
    is valid JavaScript, so this fails silently with a plausible-looking number
    instead of an error."""
    text = (ROOT / "app" / "templates" / "admin_sekolah" / "dashboard.html") \
        .read_text(encoding="utf-8")
    assert 'x-text="\'{{ active_year.end_date[:10] }}\'"' in text, \
        "the academic-year end date must be quoted, or it renders as 2027-6-14=2007"
    assert '<span x-text="{{ active_year.end_date' not in text


def test_the_results_page_does_not_comment_out_the_card_it_calls():
    """The other half of the same defect, read from the page that lost it.

    The page's component calls `Object.assign(sgVisualSummary(), {...})`, and the
    factory is defined in the card it includes — so the include has to be outside
    every HTML comment. Being inside one is exactly this: the last `<!--` before
    the include comes after the last `-->`.
    """
    raw = (ROOT / "app" / "templates" / "student" / "results.html") \
        .read_text(encoding="utf-8")
    include = "{% include 'student/_visual_summary.html' %}"
    i = raw.index(include)
    assert raw.rfind("<!--", 0, i) < raw.rfind("-->", 0, i), (
        "the shared card is included inside an HTML comment, so its markup and its "
        "`window.sgVisualSummary` factory are comment text: the page's component "
        "throws on that name and the results table renders empty")


def test_the_exam_subject_renders_as_text():
    text = (ROOT / "app" / "templates" / "teacher" / "dashboard.html") \
        .read_text(encoding="utf-8")
    assert '<span x-text="\'{{ e.subject' in text, \
        "a bare subject name is an undefined variable to Alpine and renders blank"
    assert '<span x-text="{{ e.subject' not in text


ATTRIBUTE = re.compile(r"([\w:.\-@]+)\s*=\s*\"([^\"]*)\"")


def test_no_attribute_pretends_html_has_escape_sequences():
    """HTML does not process backslash escapes, so `\"` inside a double-quoted
    attribute does not escape anything — it *closes* the attribute. The rest of
    the expression then parses as a series of bogus boolean attributes, and the
    directive is silently truncated.

    Written the other way round while translating the AI settings page: a
    `t('... seperti \"kunci rumah\" ...')` pair lost everything after the first
    quote and the list item rendered blank. `&quot;` is the HTML-level escape and
    is what an HTML attribute needs — it survives to the parser as a real quote
    without ending the attribute.
    """
    offenders = []
    for path in TEMPLATES:
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in ATTRIBUTE.finditer(text):
            if m.group(2).endswith("\\"):
                line = text.count("\n", 0, m.start()) + 1
                offenders.append(
                    f"{path.relative_to(ROOT)}:{line}  {m.group(1)}=\""
                    f"{m.group(2)[-50:]}\"")

    assert not offenders, (
        "these attribute values end in a backslash, which means the author wrote "
        "`\\\"` expecting it to escape an inner quote — HTML closes the attribute "
        "there instead, so the directive is truncated. Use &quot; :\n  "
        + "\n  ".join(offenders))


def test_the_quoted_phrases_survive_as_real_quotes():
    """The strings that used to carry backslash escapes must still reach Alpine
    with a quote in them, not with the escape stripped and the text mangled."""
    ai = (ROOT / "app" / "templates" / "teacher" / "ai_settings.html") \
        .read_text(encoding="utf-8")
    assert "&quot;kunci rumah&quot;" in ai
    assert "&quot;Create API Key&quot;" in ai

    settings = (ROOT / "app" / "templates" / "student" / "settings.html") \
        .read_text(encoding="utf-8")
    assert "&quot;Ekspor Data Saya&quot;" in settings
    assert "&quot;Hapus Akun&quot;" in settings


def test_the_analytics_visibility_checks_use_tojson():
    """`x-show="{{ n }} > 0"` is a numeric comparison, so quoting is wrong — but
    the value still has to be a JavaScript literal. tojson is what makes that a
    guarantee instead of a coincidence that holds only while n is an int.

    And it has to survive the *attribute*: `tojson` leaves double quotes alone,
    so the moment the value is a string or a mapping the HTML parser ends the
    attribute inside it. `forceescape` is what keeps it inside — and it costs
    nothing while the value is an int.
    """
    text = (ROOT / "app" / "templates" / "teacher" / "analytics.html") \
        .read_text(encoding="utf-8")
    assert "{{ stats.total_submissions|tojson|forceescape }}" in text
    assert 'x-show="{{ stats.total_submissions }}' not in text



def test_no_template_opens_an_html_comment_it_closes_as_a_jinja_one():
    """`<!-- ... #}` is not a comment at all: it is a comment that never ends.

    Jinja only closes a comment with `#}` when it opened it with `{#`. Written the
    other way round, the `#}` is emitted into the HTML verbatim, and the browser
    reads everything up to the next real `-->` as comment text — markup, scripts
    and all. Nothing is parsed and nothing runs, and because the browser is right
    to do that, the page still answers 200. `student/results.html` lost its whole
    chart card this way, including `window.sgVisualSummary`: the page's own
    component calls that factory, threw `sgVisualSummary is not defined`, and
    every Alpine binding on the page — the row list, the score toggle, the filters
    — failed with it, so the student's results table rendered empty while the route
    had handed the page three papers.

    A blind spot worth naming: this reads the file, not the rendered page, so it
    would also flag a legal `<!-- {# note #} -->`. That is deliberate — two comment
    syntaxes nested in each other is the mistake, and one of them never reaches the
    browser.
    """
    closed_by_jinja = []
    never_closed = []
    for path in TEMPLATES:
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"<!--", text):
            end = text.find("-->", m.start())
            segment = text[m.start():end if end != -1 else len(text)]
            if "#}" in segment:
                line = text.count(chr(10), 0, m.start()) + 1
                closed_by_jinja.append(f"{path.relative_to(ROOT)}:{line}")
        # With every real Jinja comment gone, an `<!--` left in the file can only
        # be markup — so it must reach a `-->`. This is the same defect one step
        # further on: a comment that is never closed at all.
        stripped = re.sub(r"\{#.*?#\}", " ", text, flags=re.S)
        for m in re.finditer(r"<!--", stripped):
            if stripped.find("-->", m.start()) == -1:
                line = stripped.count(chr(10), 0, m.start()) + 1
                never_closed.append(f"{path.relative_to(ROOT)}:{line}")

    assert not closed_by_jinja, (
        "an HTML comment is 'closed' by a Jinja `#}`, so it never closes: the browser "
        "reads everything up to the next `-->` as comment text, and whatever it "
        "wrapped is never parsed or run. Use `{# ... #}` or `<!-- ... -->`, never one "
        "opening the other:\n  " + "\n  ".join(closed_by_jinja))
    assert not never_closed, (
        "an HTML comment in these files is never closed, so everything after it — to "
        "the next `-->` anywhere in the document — is comment text and never renders:\n  "
        + "\n  ".join(never_closed))


def test_the_literal_interpolation_exemption_does_not_hide_data():
    """The exemption is narrow on purpose. If it ever stopped being narrow, the
    guard above would go quiet on exactly the values that break."""
    assert _is_js_literal("'true' if f.enabled else 'false'")
    assert _is_js_literal("stats.total_submissions|tojson")
    assert _is_js_literal("stats.total_submissions | tojson")
    assert not _is_js_literal("student_count")
    assert not _is_js_literal("active_year.end_date[:10]")
    assert not _is_js_literal("e.subject or '-'")
    assert _leading_interpolation("'{{ x }}'") is None       # already quoted
    assert _leading_interpolation("{{ x }} ? 'a' : 'b'") == "x"
    assert _leading_interpolation("t('a','b')") is None      # not an interpolation
