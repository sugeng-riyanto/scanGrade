"""The way in for an issued account, and the way back in for every role.

A school prints a login card: the teacher, pupil, principal or vice principal signs in
once with the password on it and is sent to `/auth/change-password` before any other
page opens. Two things on that path are the user's, not the server's, and both were
wrong in ways nothing else catches:

* **the browser was told not to save the new password.** `change_password.html`
  carried `autocomplete="off"` on its own form — which is the signal that suppresses
  the "save this password?" offer — so the one password the account will ever be
  looked after by was the one a browser could not remember. The fields were already
  marked `current-password` / `new-password`, so the form was contradicting itself.
* **the door four of the six roles use had no way back in.** `/auth/login-user` signs
  in teachers, pupils, principals and vice principals, and it carried no link to
  `/auth/forgot-password` — only the admin door did. A pupil who lost a card could
  reset only by typing the URL, and the reset path itself was checked separately
  (`test_password_reset_flow.py`).

These are read from the templates because that is where the defect lives: a route
serving a form that tells the browser not to save a password passes every test that
only checks the route answers `200`.
"""
from __future__ import annotations

import pathlib
import re

AUTH = pathlib.Path(__file__).resolve().parents[2] / "app" / "templates" / "auth"


def _html(name: str) -> str:
    return (AUTH / name).read_text(encoding="utf-8")


# ── every door has a way back in ─────────────────────────────────────────────

def test_the_user_door_offers_a_way_back_in():
    """Teachers, pupils and both officials sign in here, so the link has to be here."""
    assert "auth.forgot_password" in _html("login_user.html"), (
        "the teacher/student door offers no forgot-password link, so the four roles "
        "it signs in can reset only by guessing the URL")


def test_the_admin_door_still_offers_one():
    assert "auth.forgot_password" in _html("login.html")


def test_the_new_link_is_bilingual():
    """Every other label on that page is a `t()` pair; a bare one would be the only
    string that ignores the toggle, and the i18n gate counts it as an offender."""
    html = _html("login_user.html")
    assert re.search(r"t\('[^']+','[^']+'\)[^>]*></a>\s*\n", html) or \
        "t('Lupa password?','Forgot password?')" in html, (
        "the forgot-password link is not a bilingual pair")


# ── the browser is allowed to save the password it just set ──────────────────

def test_the_change_page_lets_the_browser_save_the_new_password():
    # Read the *tags*, not the file: the page's own comment names the attribute it is
    # explaining, and a guard that fires on a sentence gets relaxed instead of fixed.
    html = _html("change_password.html")
    forms = re.findall(r"<form[^>]*>", html)
    assert forms, "the page has no form"
    for tag in forms:
        assert 'autocomplete="off"' not in tag, (
            "the form a login card lands on tells the browser not to offer saving the "
            "new password — the one change the page exists to make")
    marked = [t for t in re.findall(r'<input[^>]*type="password"[^>]*>', html)
              if 'autocomplete="new-password"' in t]
    assert len(marked) == 2, (
        "the two new-password fields must both be marked, or the browser cannot tell "
        "which of the three fields is the one to store")


def test_every_page_that_sets_a_password_marks_its_fields():
    """A reset and a first sign-in are the same act from the browser's side."""
    missing = []
    for name in ("set_new_password.html", "reset_password.html", "register.html",
                 "change_password.html"):
        for tag in re.findall(r'<input[^>]*type="password"[^>]*>', _html(name)):
            if 'autocomplete="new-password"' not in tag and \
               'autocomplete="current-password"' not in tag:
                missing.append((name, tag))
    assert not missing, (
        "a password field carries no autocomplete hint, so the browser cannot offer "
        "to save or fill it: " + "; ".join(f"{n}: {t}" for n, t in missing))
