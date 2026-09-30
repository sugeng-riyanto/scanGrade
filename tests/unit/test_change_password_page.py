"""The page a login card's one-time password lands on rendered *empty*.

Reported live as "masih kosong dan belum berhasil": `/auth/change-password` served
the chrome and no form, so there was nothing to submit and nothing that explained
why. Measured from outside first — the route exists, answers 200, and redirects an
anonymous reader to the login door — so the fault had to be in what it renders.

It is the branch in `base.html`. `{% if g.user_id %}` renders the authenticated
chrome and only `{% block content %}`; otherwise it renders `{% block content_noauth
%}`. This page is **reached with a session** — it is the one path `login_required`
lets through while `must_change_password` is set, and the redirect sends the reader
there the moment they sign in with the printed password — yet it defined only
`content_noauth`. So the authenticated branch drew its chrome, looked for a
`content` block, found none, and rendered a blank main: the reader with the printed
password, the only reader who can be on this page, saw nothing.

The fix is to render the same body from both blocks, so which branch the chrome
takes cannot decide whether the form exists. These tests render the real template
with a real request context, because the defect was invisible to every test that
read the file instead of rendering it.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "app" / "templates" / "auth" / "change_password.html"
TEMPLATES = ROOT / "app" / "templates"
ROUTE_MODULES = sorted((ROOT / "app" / "routes").glob("*.py"))

FIELDS = ('name="current_password"', 'name="new_password"', 'name="confirm_password"')


def _as(app, user_id):
    """A request context for whoever is on the page, signed in or not."""
    from flask import g

    context = app.test_request_context("/auth/change-password")
    context.push()
    if user_id:
        g.user_id = user_id
        g.user_name = "Murid Uji"
        g.user_email = "murid@example.test"
        g.user_role = "murid"
        g.user_school_id = "sch-1"
    g.tz_offset = 7
    g.show = {}
    return context


def _render(app):
    return app.jinja_env.get_template("auth/change_password.html").render()


# ── it renders, for both readers ─────────────────────────────────────────────

def test_the_form_renders_for_the_reader_who_has_a_session(app):
    """The only reader who can be on this page: the flag is what sent them here."""
    context = _as(app, "stu-1")
    try:
        html = _render(app)
    finally:
        context.pop()
    for field in FIELDS:
        assert field in html, (
            f"the signed-in reader is shown a page with no {field!r} in it — "
            "base.html took its authenticated branch and this template had nothing "
            "for `content`, which is the blank page that was reported")
    assert "fa-key" in html, "not even the card heading rendered"


def test_the_form_still_renders_for_a_reader_without_a_session(app):
    """The other branch. It is unreachable through the route today — a session is
    required — but the template is what decides, so both branches are held."""
    context = _as(app, None)
    try:
        html = _render(app)
    finally:
        context.pop()
    for field in FIELDS:
        assert field in html, f"the anonymous branch dropped {field!r}"


# ── and it says so in the file, so a copy-paste cannot undo it ────────────────

def test_the_template_answers_both_of_base_html_s_branches():
    """Reading the file alone would not have caught the blank page, so this is the
    secondary guard: it names the two blocks the fix needs, and a future edit that
    keeps only one of them fails here rather than in front of a pupil."""
    text = TEMPLATE.read_text(encoding="utf-8")
    assert "{% block content %}" in text, (
        "the page no longer fills `content`, so the authenticated branch — the one "
        "this page is reached on — renders nothing")
    assert "{% block content_noauth %}" in text, (
        "the page no longer fills `content_noauth`, so it breaks for a reader "
        "without a session")
    # One body, two doors: the markup lives in a macro the two blocks call, because
    # two copies of a form is how one of them stops being updated.
    assert text.count("{{ body() }}") == 2, (
        "the two blocks do not share one body — the form has been duplicated")


# ── the class of defect, not just this page ──────────────────────────────────

def _session_gated_pages():
    """Every `(module, view, template)` a view behind a `*_required` decorator serves.

    Any such view runs with a session, so `base.html` takes its authenticated branch
    and the page must fill `content` — the branch `change_password.html` did not.
    Read from the decorators rather than a list of paths on purpose: a new page that
    forgets the block is exactly what this is for, and a hand-kept list is what
    fails to notice.
    """
    found = []
    for path in ROUTE_MODULES:
        # `utf-8-sig`: at least one route module carries a BOM, which `ast.parse`
        # refuses as a non-printable character at line 1.
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            decorators = [ast.unparse(d) for d in node.decorator_list]
            if not any(re.search(r"_required\b", d) for d in decorators):
                continue
            for const in ast.walk(node):
                if (isinstance(const, ast.Constant) and isinstance(const.value, str)
                        and const.value.endswith(".html")):
                    found.append((path.name, node.name, const.value))
    return found


def test_every_page_a_session_gate_serves_fills_the_content_block():
    assert _session_gated_pages(), (
        "no session-gated view renders a template any more — this guard has stopped "
        "reading what it thinks it reads")
    offenders = []
    for module, view, name in _session_gated_pages():
        page = TEMPLATES / name
        if not page.exists():
            continue  # a partial or a template from another root; not this guard's
        text = page.read_text(encoding="utf-8")
        if 'extends "base.html"' not in text:
            continue
        if "{% block content %}" not in text:
            offenders.append(f"{module}:{view}() renders {name}")
    assert not offenders, (
        "a view behind a *_required decorator renders a base.html page that never "
        "fills `content`, so the authenticated branch draws its chrome over an empty "
        "main — the blank page this file exists for:\n  " + "\n  ".join(offenders))
