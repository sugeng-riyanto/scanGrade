"""Every page has a door, and this is the guard that keeps it that way.

Reported twice, from opposite ends. `/auth/change-password` existed and nothing
linked it — an operator had to know the URL. Then the merge of the live room and
the pattern view left the *survivor* with almost no way in, and the template
marketplace with no inbound link at all. The first was fixed by adding the link;
the second by adding two doors and a guard for *that* page. Neither stopped the
next orphan, because a guard for one page is a guard for one page.

So this reads the app the way a reader walks it: it collects every registered GET
route that renders a template, extracts every page reference from every template
(and from the route bodies themselves, which is where a `redirect()` lives), and
walks from the doors a reader can actually arrive at. Anything left unvisited is a
page whose only entrance is knowing its URL.

Two things make it honest rather than noisy, because a guard that fails on code
gets deleted:

* **A reference is resolved the way the browser resolves it.** Exact paths are
  preferred, so a link to `/teacher/exams/new` does not count as a link to
  `/teacher/exams/<exam_id>`; a variable segment matches a Jinja expression
  (`/teacher/exams/{{ exam.id }}`), which is how these templates write it; and a
  `url_for('teacher.my_exams')` is resolved through the app's own endpoint map.
* **A page that is not navigable by design is exempt — in writing, with a reason.**
  A download, a print view a button opens from a completed form, a token URL, a
  payment callback: these are reached by an action rather than a link, and a guard
  that demanded an `<a href>` for each would be wrong. Every exemption is a rule
  plus the sentence that justifies it, and `test_every_exemption_says_why` fails on
  a blank reason.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = ROOT / "app" / "templates"

#: Pages reached by an *action* rather than a link, with the reason. Each entry is
#: the route rule as Flask registers it. The list is deliberately short: it is the
#: set of pages for which "some page links it" is the wrong question.
EXEMPT: dict[str, str] = {
    # A login door: the link to it arrives by email, never from inside the app.
    "/auth/reset-password":
        "a password-reset door reached from the emailed link, not from a page",
    # The box's own probe, read from the shell and the uptime checks.
    "/monitor":
        "the box's own operations page, read from outside the app rather than by "
        "a reader following a link",
    # A print view a button opens from the page it belongs to.
    "/teacher/analytics/print":
        "a print view the analytics page opens in a new tab from its print button",
    # The payment gateway's return callback the payer is sent to.
    "/admin-sekolah/payment/success":
        "the payment gateway's return callback, which the payer arrives at from "
        "Midtrans rather than from a link in the app",
    # The legacy `/admin` panel: it renders for the old admin role and has no
    # menu by design, so every one of its pages is opened from the super-admin
    # panel's records instead of from a link.
    "/admin/compliance":
        "a page of the legacy /admin panel, which carries no menu; the "
        "super-admin panel is where these records are read from now",
    "/admin/compliance/pdp":
        "the data-protection reference the compliance page above links to; same "
        "legacy panel, with no door of its own",
    # A teacher's bulk student import: the register belongs to the school admin,
    # who imports it at `/admin-sekolah/import`. The guru page is the older door
    # to the same act and had no business in the teacher's menu, so its link was
    # removed and it is left reachable only by its address.
    "/students/import":
        "a legacy bulk-register import that duplicates the school admin's "
        "`/admin-sekolah/import`; it was removed from the teacher menu, so it sits "
        "outside the navigable surface by design",
}


# ── reading the routes ───────────────────────────────────────────────────────

def _view_source(fn) -> str:
    """The view's own source, decorators unwrapped.

    A route's body is where a `redirect("/seller/dashboard")` lives, and a
    blueprint wraps its views in role guards, so the source of the wrapper alone
    would miss it. Walking `__wrapped__` reads the view under the guard.
    """
    src, seen, level = "", set(), fn
    for _ in range(8):
        if level is None or id(level) in seen:
            break
        seen.add(id(level))
        try:
            src += inspect.getsource(level)
        except (OSError, TypeError):
            pass
        level = getattr(level, "__wrapped__", None)
    return src


def _page_routes(app):
    """Registered GET routes that render a template — the pages a reader can open."""
    out = []
    for rule in app.url_map.iter_rules():
        if "GET" not in rule.methods or rule.endpoint == "static":
            continue
        if rule.rule.startswith("/static"):
            continue
        source = _view_source(app.view_functions.get(rule.endpoint))
        if "render_template" in source:
            out.append(rule)
    return out


# ── reading the references ───────────────────────────────────────────────────

def _pattern(rule_text: str) -> re.Pattern:
    """A rule as the regex a template's reference would have to match.

    `<int:plan_id>` and `<exam_id>` both become one non-empty, slash-free segment,
    which is exactly what stands in for it in a Jinja-built URL.
    """
    parts, cursor = [], 0
    for match in re.finditer(r"<[^>]+>", rule_text):
        parts.append(re.escape(rule_text[cursor:match.start()]))
        parts.append(r"[^/]+")
        cursor = match.end()
    parts.append(re.escape(rule_text[cursor:]))
    return re.compile("".join(parts))


class References:
    """How a body of text (a template, or a view) refers to routes."""

    def __init__(self, app):
        self.exact = sorted({r.rule for r in app.url_map.iter_rules()
                             if "<" not in r.rule and r.endpoint != "static"})
        self.variable = [(r.rule, _pattern(r.rule)) for r in app.url_map.iter_rules()
                         if "<" in r.rule and r.endpoint != "static"]
        self.by_endpoint = {r.endpoint: r.rule for r in app.url_map.iter_rules()
                            if r.endpoint != "static"}

    def in_text(self, text: str) -> set[str]:
        found = {rule for rule in self.exact if rule in text}
        for rule, regex in self.variable:
            hit = regex.search(text)
            while hit:
                # Prefer the exact route where one exists: `/teacher/exams/new` is
                # not a reference to `/teacher/exams/<exam_id>`. The matched
                # segment can carry the closing quote or angle bracket of the
                # attribute it sits in (`.../new">`), so it is trimmed to the
                # path before the comparison.
                candidate = hit.group(0).rstrip("\"'>").strip()
                if candidate in self.exact:
                    hit = regex.search(text, hit.end())
                    continue
                found.add(rule)
                break
        for endpoint in re.findall(r"url_for\(\s*['\"]([\w.]+)['\"]", text):
            if endpoint in self.by_endpoint:
                found.add(self.by_endpoint[endpoint])
        return found


# ── templates: which are rendered, and which include which ───────────────────

_TEMPLATE_LITERAL = re.compile(r"render_template\(\s*['\"]([^'\"]+\.html)['\"]")
_INCLUDE = re.compile(r"\{%-?\s*(?:extends|include)\s+['\"]([^'\"]+)['\"]")


def _all_templates() -> dict[str, str]:
    out = {}
    for path in TEMPLATE_DIR.rglob("*.html"):
        out[str(path.relative_to(TEMPLATE_DIR)).replace("\\", "/")] = \
            path.read_text(encoding="utf-8", errors="replace")
    return out


def _includes(text: str) -> set[str]:
    return set(_INCLUDE.findall(text))


def _transitive_includes(start: str, texts: dict[str, str]) -> set[str]:
    """Every template whose copy is really on the page: extends and include are
    both "this file's body is rendered here", which is why the chrome counts."""
    seen, frontier = set(), [start]
    while frontier:
        name = frontier.pop()
        if name in seen:
            continue
        seen.add(name)
        frontier.extend(_includes(texts.get(name, "")) - seen)
    return seen


# ── the walk ─────────────────────────────────────────────────────────────────

#: The doors a reader arrives at without following a link inside the app: the
#: landing page and the authentication doors (plus everything they redirect to
#: after a successful sign-in, which the walk reaches through the view bodies).
SEED_RULES = ("/", "/auth/login", "/auth/login-user", "/demo")


def reachable_pages(app) -> tuple[set[str], set[str]]:
    """(reachable rules, every page rule), by walking from the doors."""
    refs = References(app)
    texts = _all_templates()
    pages = {rule.rule: rule for rule in _page_routes(app)}

    edges: dict[str, set[str]] = {}
    for rule in pages.values():
        source = _view_source(app.view_functions.get(rule.endpoint))
        found = refs.in_text(source)
        for name in _TEMPLATE_LITERAL.findall(source):
            for part in _transitive_includes(name, texts):
                found |= refs.in_text(texts.get(part, ""))
        edges[rule.rule] = found

    reached: set[str] = set()
    frontier = [seed for seed in SEED_RULES if seed in pages or True]
    # A seed that is not itself a page route still contributes its references, so
    # the walk starts from its view body (this is how a dashboard is reached: the
    # sign-in route redirects to it).
    pending = list(SEED_RULES)
    while pending:
        rule_text = pending.pop()
        if rule_text in reached:
            continue
        reached.add(rule_text)
        for nxt in edges.get(rule_text, set()):
            if nxt not in reached:
                pending.append(nxt)
    return reached, set(pages)


def unreachable_pages(app) -> list[str]:
    reached, pages = reachable_pages(app)
    return sorted(rule for rule in pages if rule not in reached and rule not in EXEMPT)


# ── the guard ────────────────────────────────────────────────────────────────

def test_every_page_is_reachable_from_a_navigable_page(app):
    orphans = unreachable_pages(app)
    assert not orphans, (
        "these pages render a template but no page links to them, so the only "
        "entrance is knowing the URL:\n  " + "\n  ".join(orphans))


def test_every_exemption_says_why():
    blanks = [rule for rule, reason in EXEMPT.items() if not (reason or "").strip()]
    assert not blanks, f"an exemption without a reason is how a guard goes quiet: {blanks}"


# ── the guard has teeth ──────────────────────────────────────────────────────

def test_it_finds_a_page_it_knows_is_linked(app):
    """`/teacher/templates` is linked from the exams list, so it must be reached —
    otherwise the walk is broken and the guard would pass by visiting nothing."""
    reached, pages = reachable_pages(app)
    assert "/teacher/templates" in pages
    assert "/teacher/templates" in reached


def test_the_walk_reaches_the_five_role_dashboards(app):
    reached, _ = reachable_pages(app)
    for dashboard in ("/teacher/dashboard", "/student/dashboard",
                      "/admin-sekolah/dashboard", "/super-admin/dashboard",
                      "/vice-principal/dashboard"):
        assert dashboard in reached, (
            f"the walk never reaches {dashboard}, so it is not reading the app "
            f"the way a reader does")


def test_a_link_is_not_credited_to_the_wrong_route(app):
    """The exact-preferred rule: a link to `/teacher/exams/new` must not read as a
    reference to `/teacher/exams/<exam_id>`."""
    refs = References(app)
    found = refs.in_text('<a href="/teacher/exams/new">')
    assert "/teacher/exams/new" in found
    assert "/teacher/exams/<exam_id>" not in found


def test_a_jinja_built_url_is_a_reference(app):
    refs = References(app)
    assert "/teacher/exams/<exam_id>" in refs.in_text(
        '<a href="/teacher/exams/{{ exam.id }}">')


def test_a_url_for_is_resolved_through_the_endpoint_map(app):
    refs = References(app)
    assert "/teacher/exams" in refs.in_text("url_for('teacher.my_exams')")


@pytest.mark.parametrize("reason", list(EXEMPT.values()))
def test_the_exemptions_are_reasons_not_labels(reason):
    assert len(reason) > 20, "an exemption's reason is the point of it"
