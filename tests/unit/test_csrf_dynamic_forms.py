"""A form the page clones has to carry a token, and it only gets one at submit.

`/teacher/ai-settings/<id>/delete` answered `{"error":"CSRF token invalid"}`. The
route was fine and the button was fine; the *token* was missing, because base.html
stamped `form[method="POST"]` once on `DOMContentLoaded` and the key list on that
page is an Alpine `<template x-for>` — its forms did not exist yet. Every control
the page rendered from a list had the same hole, so the fix is one submit-time
listener rather than one token per page.

Three things are asserted, because each is a different way the same bug comes back:

1. the token is stamped at **submit** time (a load-time sweep is the defect);
2. the listener is registered in the **capture** phase, so a page's own `submit`
   handler that calls `preventDefault()` still sends it;
3. the page the bug was reported on carries the field in its markup too, so the
   control works when scripting is off and a reader can see it.
"""
from __future__ import annotations

import re
from pathlib import Path

BASE = Path("app/templates/base.html")
AI_SETTINGS = Path("app/templates/teacher/ai_settings.html")


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestTheTokenArrivesAtSubmit:
    def test_the_stamp_is_a_submit_listener(self):
        src = _text(BASE)
        assert "document.addEventListener('submit'" in src
        # The load-time sweep is the bug, not a belt: it cannot see a clone.
        assert "querySelectorAll('form[method=\"POST\"]')" not in src

    def test_the_listener_captures(self):
        src = _text(BASE)
        match = re.search(r"addEventListener\('submit',\s*function\s*\(e\)\s*\{[^}]*\},\s*(true|false)\s*\)", src)
        assert match, "the submit listener is not registered with a phase argument"
        assert match.group(1) == "true", (
            "a bubbling listener runs after a page's own handler may have cancelled the submit"
        )

    def test_the_stamp_is_idempotent(self):
        src = _text(BASE)
        assert "input[name=\"_csrf_token\"]" in src
        assert "if (field)" in src


class TestThePageThatReportedIt:
    def test_every_cloned_key_form_carries_the_field(self):
        src = _text(AI_SETTINGS)
        forms = re.findall(r"<form[^>]*method=\"POST\"[^>]*>(.*?)</form>", src, re.DOTALL)
        assert forms, "the key list has no forms at all"
        for body in forms:
            assert 'name="_csrf_token"' in body, (
                "a POST form on /teacher/ai-settings has no CSRF field of its own"
            )

    def test_the_delete_form_posts_to_the_delete_route(self):
        src = _text(AI_SETTINGS)
        assert "'/teacher/ai-settings/' + k.id + '/delete'" in src


class TestEveryRouteItCallsExists:
    """A field with no route, or a route with no field, is the same dead button."""

    def test_the_forms_name_routes_that_are_registered(self):
        routes_src = Path("app/routes/teacher.py").read_text(encoding="utf-8")
        template = _text(AI_SETTINGS)
        for suffix in ("toggle", "delete"):
            assert f"'/{suffix}'" in template
            assert re.search(rf'"/ai-settings/<key_id>/{suffix}"', routes_src), (
                f"the {suffix} route is not registered"
            )
