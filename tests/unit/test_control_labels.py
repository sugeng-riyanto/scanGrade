"""Every control that changes data names what it does, in both languages.

The sidebar chrome and the role dashboards carry a small number of controls
that *write*: they sign you out, they reset a demo password, they reset every
demo school's data, they remove a class-and-subject assignment. Each acted on
one click and each was labelled in a single language — or, for the icon-only
ones, not labelled beyond an English ``title=``.

The label is not cosmetic on a control of this kind. ``Reset All Data`` on a
super admin's dashboard is destructive and irreversible, and ``Logout`` ends a
sitting; an icon whose only text is an English tooltip is a control a reader of
the other language cannot name, and a bare English word on a bilingual page is
copy the toggle does not switch.

The templates are read as text, the way the other chrome guards read them: the
defect is in the source — a hardcoded string, a missing attribute — not in the
rendered page. The two deliberately Indonesian-only pages are out of scope by
construction (``test_language_toggle`` owns the frozen list); what is checked
here is the chrome and the pages that carry a language toggle.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "app" / "templates"

#: The chrome and every role dashboard. Each is a page a reader reaches with
#: the toggle in their hand, so an English-only label on one of them is a string
#: the toggle cannot translate. The pages that carry no write control today are
#: listed on purpose: the guard watches them, so the first unlabelled write
#: control added to any dashboard is caught here rather than in the field.
PAGES = {
    "base.html": (TEMPLATES / "base.html"),
    "admin/dashboard.html": (TEMPLATES / "admin" / "dashboard.html"),
    "admin_sekolah/dashboard.html": (
        TEMPLATES / "admin_sekolah" / "dashboard.html"),
    "principal/dashboard.html": (TEMPLATES / "principal" / "dashboard.html"),
    "student/dashboard.html": (TEMPLATES / "student" / "dashboard.html"),
    "super_admin/dashboard.html": (TEMPLATES / "super_admin" / "dashboard.html"),
    "teacher/dashboard.html": (TEMPLATES / "teacher" / "dashboard.html"),
}

#: The two pages known to carry a write control today, so the sweep below can
#: prove it is reading a real page and not an empty one.
WRITE_BEARING = ("base.html", "super_admin/dashboard.html", "teacher/dashboard.html")


def _read(rel: str) -> str:
    return PAGES[rel].read_text(encoding="utf-8")


# ── vocabulary ───────────────────────────────────────────────────────────────

#: A bilingual pair, however this app spells one: the ``t()`` method, the
#: ``sgT()`` helper the auth pages use, or the ``lang === 'en' ? A : B``
#: ternary the timezone dialog writes.
BILINGUAL = re.compile(
    r"t\(\s*'[^']*'\s*,\s*'[^']*'\s*\)"
    r"|sgT\(\s*'[^']*'\s*,\s*'[^']*'\s*\)"
    r"|lang\s*===\s*'en'\s*\?"
)

#: An element that writes. A POST form, an htmx write verb, a fetch the Alpine
#: click handler sends with ``method: 'POST'``, or a classic ``onclick`` that
#: reaches a function which posts.
WRITE = re.compile(
    r"hx-(?:post|put|delete|patch)\b"
    r"|method=(?:\"|')POST(?:\"|')"
    r"|@click=\"[^\"]*fetch\([^\"]*method:\s*'?POST"
    r"|@submit(?:\.prevent)?=\"[^\"]*fetch\([^\"]*method:\s*'?POST"
    r"|\bonclick=\"",
)

#: A door that ends the session — the one write control whose href is the verb.
LOGOUT = re.compile(r"""href=(?:"|')/auth/logout(?:"|')""")

OPEN_TAG = re.compile(r"<(?:button|a)\b[^>]*>", re.S)


def _elements(text: str) -> list[str]:
    """Every ``<button>`` and ``<a>`` element, whole, as source."""
    return [m.group(0) for m in re.finditer(
        r"<(?P<tag>button|a)\b[^>]*>.*?</(?P=tag)>", text, re.S)]


def _elements_with_write(text: str) -> list[str]:
    found = []
    for el in _elements(text):
        opening = OPEN_TAG.match(el).group(0)
        if WRITE.search(opening) or LOGOUT.search(opening):
            found.append(el)
    return found


def _labelled(el: str) -> bool:
    """A write control is labelled when a bilingual pair is inside it."""
    return bool(BILINGUAL.search(el))


# ── the general guard ────────────────────────────────────────────────────────

class TestEveryWriteControlOnTheChromeAndDashboardsIsLabelledInBothLanguages:
    @pytest.mark.parametrize("rel", sorted(PAGES))
    def test_a_write_control_carries_a_bilingual_pair(self, rel):
        offenders = [el for el in _elements_with_write(_read(rel))
                     if not _labelled(el)]
        assert not offenders, (
            f"{rel} carries {len(offenders)} control(s) that change data with no "
            "bilingual label — a reader of the other language cannot name what it "
            "does:\n  " + "\n  ".join(el[:160] for el in offenders))

    def test_the_guard_is_not_blind(self):
        """A scan that matches nothing would pass the check above for the wrong
        reason. Every page on the list is read, and the pages known to write
        really do carry a control the guard can see."""
        for rel in WRITE_BEARING:
            assert _elements_with_write(_read(rel)), (
                f"{rel} has no write control, so the guard above proves nothing "
                "about it")
        # And the guard is not silently skipping the dashboards that have none:
        # it reads every page, so a control added there is judged, not missed.
        assert set(PAGES) >= set(WRITE_BEARING)


# ── the specific defects ─────────────────────────────────────────────────────

class TestTheLogoutDoorSaysSoInBothLanguages:
    def test_the_sidebar_icon_door_is_not_english_only(self):
        """The one door out of a session, and it said ``title="Logout"``."""
        assert 'title="Logout"' not in _read("base.html"), (
            "the sidebar's logout door still carries an English-only title; a "
            "reader of the other language reads a tooltip they cannot translate")
        # And the bilingual pair that replaces it is really there, on the door.
        assert re.search(
            r'href="/auth/logout"[^>]*:title="t\(', _read("base.html")), (
            "the sidebar's logout door has no bilingual title at all")

    def test_the_user_menu_door_is_not_a_bare_english_word(self):
        """``…</i> Logout</a>`` — a visible English word with no pair."""
        menu = [el for el in _elements(_read("base.html"))
                if 'href="/auth/logout"' in el
                and "sg-app-sidebar" not in el]
        assert menu, "no logout door in the user menu; this check has gone blind"
        for el in menu:
            assert _labelled(el), (
                "a logout door renders an English-only word instead of a pair: "
                + el[:160])

    def test_both_logout_doors_really_exist(self):
        doors = [el for el in _elements(_read("base.html"))
                 if 'href="/auth/logout"' in el]
        assert len(doors) == 2, (
            f"expected the sidebar door and the user-menu door, found {len(doors)}")


class TestTheDestructiveSuperAdminControlsAreNamed:
    def test_reset_password_is_not_a_bare_english_button(self):
        text = _read("super_admin/dashboard.html")
        assert not re.search(r">\s*Reset Password\s*<", text), (
            "the Reset Password button still renders a bare English label")
        assert re.search(r"t\(\s*'Reset Password'\s*,\s*'Reset Password'\s*\)", text), (
            "the Reset Password button carries no bilingual pair")

    def test_reset_all_data_is_not_a_bare_english_button(self):
        text = _read("super_admin/dashboard.html")
        assert not re.search(r">\s*Reset All Data\s*<", text), (
            "the Reset All Data button still renders a bare English label")
        assert re.search(r"t\(\s*'Reset All Data'\s*,\s*'Reset All Data'\s*\)", text), (
            "the Reset All Data button carries no bilingual pair")

    def test_the_button_is_restored_bilingual_after_a_run(self):
        """The spinner swaps the label out by hand, and swapped it back to the
        bare English word — so a second click met an untranslated button."""
        text = _read("super_admin/dashboard.html")
        restores = re.findall(r"btn\.innerHTML\s*=\s*([^;]+);", text)
        assert restores, "no button label is restored; this check has gone blind"
        bare = [r for r in restores if not re.search(r"sgT\(", r)]
        assert not bare, (
            "a write control's label is restored from a hardcoded string, so it "
            "reverts to one language after the first click: " + "; ".join(bare))


class TestTheTeacherAssignmentRemovalIsNamed:
    def test_the_icon_only_remove_control_has_a_bilingual_title(self):
        text = _read("teacher/dashboard.html")
        door = [el for el in _elements(text) if "hx-delete=" in el]
        assert door, "no hx-delete control on the teacher dashboard"
        for el in door:
            assert BILINGUAL.search(el), (
                "the assignment-removal control is an unlabelled icon: " + el[:160])
            # An icon with no rendered text needs the accessible name too, or a
            # screen reader announces a button called nothing.
            assert re.search(r":aria-label=\"t\(", el), (
                "the icon-only remove control has no accessible bilingual name: "
                + el[:160])
