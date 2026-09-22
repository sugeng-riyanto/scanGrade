"""The tutorials describe the app; they must not describe an older one.

Why this exists
---------------
`test_landing_facilities.py` fixed the landing page's **"3 Tipe Soal — MCQ, Esai Teks,
Esai Canvas"** with a rule: the count and the names are the registry's
(`question_types.PICKER_TYPES`), so the page cannot outrun the code. The tutorials
carried the same sentence and nothing looked at them, so they kept saying three —
and one of the three they named, the typed essay, is the type the exam builder
deliberately stopped offering. A pupil reading it would be promised a question their
teacher cannot set.

So the same rule is applied to all three: the count and the names come from the
registry, and these tests hold it in both languages, in both directions.

The last one is the one that keeps working after this file is forgotten. Both
tutorials pick an icon out of a map keyed by question type, and the map is written by
hand, so a seventh type would render with a fallback. The guard is that the fallback
must never appear on the rendered page: whatever the registry grows to, the pages
have to have been told about it.
"""
import html as html_mod
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "app" / "templates"

#: The two tutorials that list the question types. The admin tutorial has none.
TYPE_LISTING = ("tutorial_guru.html", "tutorial_murid.html")
ALL_TUTORIALS = ("tutorial_guru.html", "tutorial_murid.html", "tutorial_admin_sekolah.html")

#: The icon both tutorials fall back to when the map has no entry for a type.
FALLBACK_ICON = "fa-circle-question"


def render(app, name: str) -> str:
    """One tutorial as a visitor is served it, entities decoded.

    Rendered rather than grepped: the claim a reader gets is the page, and half of
    this copy is written by Jinja from the registry. Reading the source would check
    the instructions for the claim.
    """
    from flask import g

    with app.test_request_context("/"):
        g.user = None
        return html_mod.unescape(app.jinja_env.get_template(name).render())


def source(name: str) -> str:
    return (TEMPLATES / name).read_text(encoding="utf-8")


def _set_block(text: str, name: str) -> str:
    """The literal `{% set <name> = { … } %}` block, for the hand-written maps."""
    m = re.search(r"\{%\s*set\s+" + name + r"\s*=\s*\{(.*?)\}\s*%\}", text, re.S)
    assert m, f"{name} is not written as a `{{% set … = {{…}} %}}` block any more"
    return m.group(1)


# ── the count, and the names, are the registry's ─────────────────────────────

class TestTheTypeListIsTheRegistrys:

    @pytest.mark.parametrize("name", TYPE_LISTING)
    def test_no_tutorial_still_says_three(self, app, name):
        body = render(app, name)
        for stale in ("3 Tipe Soal", "3 Question Types", "3 tipe soal", "3 question types"):
            assert stale not in body, (
                f"{name} still says '{stale}'. The exam builder offers "
                "`question_types.PICKER_TYPES`, so the count has to be read off it "
                "rather than restated — restating it is how the landing page spent "
                "months advertising half the product."
            )

    @pytest.mark.parametrize("name", TYPE_LISTING)
    def test_the_tutorial_counts_the_registry(self, app, name):
        """Case-insensitive on purpose: what has to match is the *count*.

        The header form is "6 Tipe Soal" and the student page's sentence is "Ada 6
        tipe soal:" — both say six, and pinning the capital letter would be testing
        the sentence rather than the number.
        """
        from app.services import question_types as qt

        n = len(qt.PICKER_TYPES)
        body = render(app, name)
        assert re.search(rf"{n}\s+tipe soal", body, re.I), (
            f"{name} does not say '{n} Tipe Soal' anywhere")
        assert re.search(rf"{n}\s+question types", body, re.I), (
            f"{name} does not say '{n} Question Types' anywhere")

    @pytest.mark.parametrize("name", TYPE_LISTING)
    def test_every_type_is_named_in_both_languages(self, app, name):
        from app.services import question_types as qt

        body = render(app, name)
        for t in qt.vocabulary()["picker"]:
            assert t["id"] in body, (
                f"{name} never names '{t['id']}' ({t['v']}) — a question type the "
                "builder offers that neither tutorial explains")
            assert t["en"] in body, f"{name} has no English name for '{t['v']}'"

    @pytest.mark.parametrize("name", ALL_TUTORIALS)
    def test_no_tutorial_offers_a_typed_essay(self, app, name):
        """`essay_text` is still graded; the builder stopped offering it.

        Advertising it tells a pupil to expect a paragraph box their teacher has no
        way to create, which is worse than saying nothing.
        """
        from app.services import question_types as qt

        assert qt.ESSAY_TEXT not in qt.PICKER_TYPES, (
            "the builder offers the typed essay again — this test's premise changed")
        body = render(app, name)
        for gone in ("Esai Teks", "Text Essay"):
            assert gone not in body, f"{name} offers '{gone}', which no builder can set"


# ── the hand-written maps the registry is rendered through ───────────────────

class TestTheMapsCoverTheRegistry:

    @pytest.mark.parametrize("name", TYPE_LISTING)
    def test_every_type_has_an_icon(self, name):
        from app.services import question_types as qt

        block = _set_block(source(name), "_type_icons")
        missing = [t for t in qt.PICKER_TYPES if f"'{t}'" not in block]
        assert not missing, (
            f"{name} has no icon for {missing}. A type added to PICKER_TYPES without "
            "one renders as a question mark, which reads as a broken page rather "
            "than as an unfinished mapping.")

    def test_the_student_tutorial_explains_every_type(self):
        from app.services import question_types as qt

        block = _set_block(source("tutorial_murid.html"), "_type_how")
        missing = [t for t in qt.PICKER_TYPES if f"'{t}'" not in block]
        assert not missing, (
            f"tests/unit/test_tutorial_content.py: {missing} have no answer "
            "instructions in the student tutorial, so a pupil meets a question "
            "kind nothing on the page described.")

    @pytest.mark.parametrize("name", TYPE_LISTING)
    def test_the_fallback_icon_never_reaches_a_reader(self, app, name):
        """The behavioural half of the two tests above.

        Those check the map; this checks the *page*. A type the map does not know
        about still renders — with the fallback — so the map can be `missing` and
        the page still look fine to every test that only reads the map.
        """
        body = render(app, name)
        assert FALLBACK_ICON not in body, (
            f"{name} rendered the fallback icon, so at least one question type in "
            "`PICKER_TYPES` has no entry in its `_type_icons` map — the page is "
            "describing a question kind it cannot draw.")


# ── every public page carries its own way to read it in either language ─────
#
# The landing page, `/capacity`, `/demo` and the three role tutorials all render
# base.html's `content_noauth` branch, which carries no navbar — so the EN/ID
# control that lives in the *authenticated* chrome is simply absent from them.
# They are the only pages a stranger meets before signing up, so each has to
# bring its own control: a page that opens in English with no way to ask for
# Indonesian is copy for one reader. That was reported for the tutorials
# ("page tutorial guru, tutorial siswa, tutorial admin, dan demo belum ada versi
# eng") and is exactly what a fourth public page would reproduce.

#: Every one of these can be rendered with no context beyond `g.user = None`.
#: `/capacity` is the sixth public page and carries its control too, but its
#: template needs the route's `cap` object to render, so it is guarded from the
#: source below rather than from the page.
PUBLIC_NOAUTH = (
    "landing.html",
    "tutorial_guru.html",
    "tutorial_murid.html",
    "tutorial_admin_sekolah.html",
    "demo.html",
)

#: The gesture every one of those controls makes.
CONTROL = re.compile(r"setLang\(lang === 'id'")


class TestEveryPublicPageCarriesItsOwnLanguageControl:

    @pytest.mark.parametrize("name", PUBLIC_NOAUTH)
    def test_the_page_has_a_control(self, app, name):
        body = render(app, name)
        assert CONTROL.search(body), (
            f"{name} opens in English and has no language control of its own. "
            "The toggle in base.html is in the authenticated chrome, which this "
            "page never renders, so the reader has no way to switch.")

    @pytest.mark.parametrize("name", PUBLIC_NOAUTH)
    def test_the_control_says_what_it_does(self, app, name):
        """The visible label is a two-letter code, which announces nothing."""
        body = render(app, name)
        for match in CONTROL.finditer(body):
            window = body[match.start():match.start() + 600]
            end = window.find("</button>")
            if end != -1:
                window = window[:end]
            assert "aria-label" in window, (
                f"{name}: the language control has no accessible name beyond "
                "'ID'/'EN', so a screen reader cannot say what it does")

    def test_the_capacity_page_carries_one_too(self):
        """Read from the source: its template needs the route's `cap` to render."""
        text = source("public/capacity.html")
        match = CONTROL.search(text)
        assert match, (
            "public/capacity.html opens in English with no language control of "
            "its own — the toggle in base.html is in the authenticated chrome, "
            "which this page never renders")
        window = text[match.start():match.start() + 600]
        end = window.find("</button>")
        assert "aria-label" in (window[:end] if end != -1 else window), (
            "public/capacity.html's language control has no accessible name")


# ── the tutorial header survives a 320px phone ───────────────────────────────
#
# Measured, not guessed. The three tutorials wrote three copies of the same
# header, and each copy carried a ~180px "Log in as Teacher" button plus a
# labelled Home link: at 320px the row asked for roughly 400px and pushed the
# login button past the right edge. The rule that fixes it lives in one place
# (`tutorial/_chrome.html`) so a fourth tutorial cannot reintroduce it.

HEADER = re.compile(r"<header.*?</header>", re.S)


class TestTheTutorialHeaderFitsAPhone:

    @pytest.mark.parametrize("name", ALL_TUTORIALS)
    def test_the_header_starts_at_the_small_phone_padding(self, app, name):
        header = HEADER.search(render(app, name))
        assert header, f"{name} has no <header>"
        assert "px-3 sm:px-6" in header.group(0), (
            f"{name}: the header must start at `px-3` — a 24px gutter on each "
            "side is 48px of a 320px screen spent before anything is drawn")

    @pytest.mark.parametrize("name", ALL_TUTORIALS)
    def test_the_role_word_and_the_labels_wait_for_a_wide_screen(self, app, name):
        """Three things are deferred, and each is checked by name.

        The role word (a coloured span) and the two button labels are what make
        the row 400px wide. Counting `hidden sm:inline` occurrences would let the
        role word come back as long as the labels stayed hidden, so the role word
        is matched on its own shape.
        """
        header = HEADER.search(render(app, name)).group(0)
        assert re.search(r'class="hidden sm:inline text-\w+-\d+"', header), (
            f"{name}: the role word is not hidden below `sm`")
        assert header.count("hidden sm:inline ml-1") == 2, (
            f"{name}: both button labels have to be hidden below `sm`, found "
            f"{header.count('hidden sm:inline ml-1')}")

    @pytest.mark.parametrize("name", ALL_TUTORIALS)
    def test_the_two_links_are_forty_pixel_icon_targets_on_a_phone(self, app, name):
        """A tap target does not shrink just because the label is gone."""
        header = HEADER.search(render(app, name)).group(0)
        n = header.count("h-10 w-10 sm:w-auto")
        assert n == 2, (
            f"{name}: expected both header links to be 40px icon targets before "
            f"`sm`, found {n}. An icon-only link that keeps its old padding is a "
            "target a thumb misses.")


# ── the admin tutorial names work the app can do ─────────────────────────────

class TestTheAdminTutorialNamesRealWork:

    #: label -> a route that proves the app does it. The tutorial is the page a
    #: school reads before signing up, so a section with no route behind it is a
    #: promise rather than a description.
    SECTIONS = {
        "Kelas": [("/classes", "admin_sekolah.py")],
        "Mapel": [("/subjects", "admin_sekolah.py")],
        "Import Excel": [("/import", "admin_sekolah.py")],
        "Kode Aktivasi": [("/subscription", "admin_sekolah.py")],
        "Tugaskan": [("teacher_assignments", "admin_sekolah.py")],
        "Pengumuman": [("/comms", "admin_sekolah.py")],
    }

    @pytest.mark.parametrize("label,proofs", sorted(SECTIONS.items()),
                             ids=lambda v: v if isinstance(v, str) else "")
    def test_the_section_is_backed_by_a_route(self, label, proofs):
        for route, module in proofs:
            text = (ROOT / "app" / "routes" / module).read_text(encoding="utf-8")
            assert route in text, (
                f"the admin tutorial teaches '{label}' and {module} has no route "
                f"for it ({route!r}) — the page describes work the app cannot do.")
