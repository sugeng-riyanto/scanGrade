"""Does every page say what language it is in, and mark the parts that differ?

`<html lang>` is not decoration: it is what a screen reader uses to pick a voice
and a dictionary. Two halves of this app disagree about language on purpose —

* a page whose own copy was never translated pins itself with
  `{% set content_lang = 'id' %}` and declares Indonesian whatever the reader
  chose, because a toggle cannot translate copy that does not exist;
* the chrome (sidebar, header, footer, bottom nav) *does* follow the toggle on
  every page.

So on a pinned page the document is Indonesian while the navigation is English.
Without a `lang` on the region that switches, a screen reader reads "Dashboard"
in an Indonesian voice and "Nilai" in an English one — WCAG 3.1.2 (Language of
Parts). These guards hold both ends: the declaration comes from one place, and
every element whose copy follows the toggle says so.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "app" / "templates"
BASE = TEMPLATES / "base.html"

TAG = re.compile(r"<(/?)([a-zA-Z][-\w]*)((?:\"[^\"]*\"|'[^']*'|[^>\"'])*)>")
VOID = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "source", "track", "wbr",
}
# Copy in two languages. `t()` re-renders with the toggle; `sgT()` is frozen and
# is only ever used to build a message shown once.
PAIR = re.compile(r"\b(?:t|sgT)\(\s*'")
# An element that says "my copy is the reader's language, whatever the document
# says". Region-level marking is deliberate: in this chrome every string is a
# pair, so the region *is* the smallest element that contains the change.
MARK = re.compile(r":lang=\"\s*lang\s*\"")
PIN = re.compile(r"\{%\s*set\s+content_lang\s*=\s*'(id|en)'\s*%\}")
# The accessible name of every language switch in the app. A template that names
# it is offering a toggle, and a toggle has to go through setLang().
SWITCH = re.compile(r"'Switch to English'|'Ganti ke Bahasa Indonesia'")
# Not the element's own copy. An `@handler` builds a message for a dialog or a
# toast: it is assembled once, at the moment of the action, in the reader's
# language by construction — and what it says can also be data from the server,
# so no element can promise which language it is in. The rest render nothing in
# the document's flow.
NOT_COPY = {
    "x-init", "x-data", "x-effect", "x-for", "x-show", "x-model",
    ":class", ":style", ":disabled", ":value",
}


def stripped_of_content(src):
    """base.html without its two content blocks — i.e. just the chrome."""
    for block in ("content", "content_noauth"):
        src = re.sub(
            r"\{%\s*block " + block + r"\s*%\}.*?\{%\s*endblock\s*%\}",
            "",
            src,
            flags=re.S,
        )
    return src


def attribute_holding(tag_start, tag_text, offset):
    """The name of the attribute the pair at `offset` sits inside, if any."""
    rel = offset - tag_start
    best = None
    for m in re.finditer(r"([:@a-zA-Z][-:\w.]*)\s*=\s*[\"']", tag_text):
        if m.start() < rel:
            best = m.group(1)
    return best


def rendered_pairs(src):
    """Every pair that becomes an element's own copy: (line, attr, owner, marked)."""
    pairs = []
    for m in PAIR.finditer(src):
        offset = m.start()
        line = src.count("\n", 0, offset) + 1
        stack = []
        owner = None
        attribute = None
        in_script = False
        for tag in TAG.finditer(src):
            if tag.start() > offset:
                break
            name = tag.group(2).lower()
            if name == "script":
                in_script = not tag.group(1)
            if tag.group(1):
                for i in range(len(stack) - 1, -1, -1):
                    if stack[i][0] == name:
                        del stack[i:]
                        break
                continue
            if name in VOID or tag.group(3).rstrip().endswith("/"):
                continue
            stack.append((name, tag.group(3), tag.start()))
            if tag.start() <= offset < tag.end():
                attribute = attribute_holding(tag.start(), tag.group(3), offset)
                owner = stack[-1]
                break
        if in_script or (attribute and (attribute in NOT_COPY or attribute.startswith("@"))):
            continue
        marked = [f for f in stack if MARK.search(f[1])]
        pairs.append(
            (
                line,
                attribute,
                f"{marked[-1][0]}@{src.count(chr(10), 0, marked[-1][2]) + 1}"
                if marked
                else (f"{owner[0]}@{src.count(chr(10), 0, owner[2]) + 1}" if owner else None),
                bool(marked),
            )
        )
    return pairs


def templates():
    return [p for p in sorted(TEMPLATES.rglob("*.html")) if p.suffix == ".html"]


class TestTheDeclarationComesFromOnePlace:
    def test_the_html_element_resolves_the_pin_and_the_default(self):
        tag = re.search(r"<html\b[^>]*>", BASE.read_text(encoding="utf-8")).group(0)
        assert "_content_lang or default_lang" in tag, (
            "the <html> tag no longer resolves a pinned page against the app "
            "default — the declaration now has two sources"
        )

    def test_the_pin_rides_on_the_element_both_scripts_read(self):
        """The scripts and both Alpine helpers read `data-content-lang`.

        Stop emitting it and the pin survives only in the server-rendered
        attribute: the first toggle overwrites the declaration of a page whose
        copy never changed language.
        """
        src = BASE.read_text(encoding="utf-8")
        assert re.search(r"\{%\s*if _content_lang\s*%\}data-content-lang=", src)
        assert "document.documentElement.dataset.contentLang" in src

    def test_a_stored_choice_never_overrides_a_pin(self):
        src = BASE.read_text(encoding="utf-8")
        early = re.search(r"<script>(if\(localStorage.*?)</script>", src).group(1)
        assert "!document.documentElement.dataset.contentLang" in early, (
            "the early script applies the stored choice unconditionally, so a "
            "page of Indonesian copy would announce itself as English"
        )

    def test_one_helper_decides_what_the_document_declares(self):
        src = BASE.read_text(encoding="utf-8")
        setter = re.search(r"setLang\(next\)\s*\{(.*?)\n        \},", src, re.S)
        assert setter, "setLang() is gone — find whoever took over the toggle"
        assert (
            "document.documentElement.dataset.contentLang || next"
            in setter.group(1)
        ), "setLang() stopped consulting the pin, so the two now disagree"

    def test_no_page_computes_the_document_language_itself(self):
        offenders = []
        for path in templates():
            if path == BASE:
                continue
            src = path.read_text(encoding="utf-8", errors="ignore")
            if re.search(r"documentElement\.lang\s*=(?!=)", src):
                offenders.append(path.relative_to(TEMPLATES).as_posix())
        assert not offenders, (
            "a page writes <html lang> on its own instead of going through "
            f"setLang(): {offenders}"
        )

    def test_every_language_control_goes_through_the_shared_helper(self):
        """A *call* on a control, not a mention in a comment.

        Every toggle in this app flips the same two things — the stored choice
        and the document's declaration — so every toggle has to go through the
        one helper that does both. Landing still names `setLang()` in a comment,
        which is why this reads the click handler rather than the file.
        """
        offenders = []
        call = re.compile(r'(?:@click|x-on:click)="[^"]*\bsetLang\(')
        for path in templates():
            src = path.read_text(encoding="utf-8", errors="ignore")
            if SWITCH.search(src) and not call.search(src):
                offenders.append(path.relative_to(TEMPLATES).as_posix())
        assert not offenders, (
            "a page offers a language switch whose control does not call "
            f"setLang(), so <html lang> never follows it: {offenders}"
        )


class TestThePartsThatSwitchSaySo:
    """Every element whose copy follows the toggle carries `:lang=\"lang\"`.

    Redundant on a page that follows the toggle itself; load-bearing on a pinned
    one, where the document is Indonesian and the chrome is not.
    """

    def test_base_html_chrome_is_marked_region_by_region(self):
        """One bottom nav, not two.

        There used to be a second, role-specific bar next to this one — both
        `fixed bottom-0`, z-40 and z-50, so a phone got 12 links in one bar with
        half of them behind the other. The role-specific one was removed and its
        destinations are all in this one, whose labels are `t()` pairs already;
        the region is still marked, and `test_base_html_has_one_bottom_nav`
        keeps it from growing a twin again.
        """
        src = BASE.read_text(encoding="utf-8")
        regions = {
            "the sidebar": r"<aside :lang=\"lang\"",
            "the top bar": r"<header :lang=\"lang\"",
            "the bottom nav": r"<nav :lang=\"lang\"[^>]*fixed bottom-0",
            "the footer": r"<footer :lang=\"lang\"",
        }
        missing = [name for name, pat in regions.items() if not re.search(pat, src)]
        assert not missing, (
            "the chrome follows the toggle but no longer declares it, so on a "
            f"pinned page it is announced in the wrong voice: {missing}"
        )

    def test_base_html_has_one_bottom_nav(self):
        """Two stacked phone bars put half the destinations behind the other.

        The role check is read *inside* the bar, not from the file: the sidebar
        branches on the same four role names, so `role == 'murid' in src` stayed
        true after the bar's own branch was renamed — a substring guard satisfied
        by another occurrence of the same expression guards nothing.
        """
        src = BASE.read_text(encoding="utf-8")
        bars = list(re.finditer(r"<nav[^>]*fixed bottom-0[^>]*>", src))
        assert len(bars) == 1, (
            f"{len(bars)} bottom navs in base.html. There is one phone bar: a second "
            "one renders on top of it (`fixed bottom-0` twice), and a role gets a bar "
            "whose links it cannot all reach."
        )
        start = bars[0].end()
        end = src.find("</nav>", start)
        assert end > start, "the phone bar is never closed"
        bar = src[start:end]
        for role in ("super_admin", "admin_sekolah", "guru", "murid"):
            assert f"role == '{role}'" in bar, (
                f"the phone bar has no branch for {role}, so that role gets an empty bar."
            )

    def test_no_unmarked_copy_that_follows_the_toggle(self):
        offenders = []
        for path in templates():
            src = path.read_text(encoding="utf-8", errors="ignore")
            if path == BASE:
                src = stripped_of_content(src)
            elif not PIN.search(src):
                continue  # the page itself follows the toggle; no marking needed
            for line, attribute, owner, marked in rendered_pairs(src):
                if marked:
                    continue
                offenders.append(
                    f"{path.relative_to(TEMPLATES).as_posix()}:{line} "
                    f"({attribute or 'text'}, inside {owner or 'nothing'})"
                )
        assert not offenders, (
            "this copy changes language with the toggle, inside a page that "
            "declares one fixed language, and nothing marks it:\n  "
            + "\n  ".join(offenders)
        )

    def test_the_marking_is_dynamic(self):
        """`:lang=\"lang\"`, never a fixed `lang=\"en\"`.

        A fixed value would be right in one toggle position and wrong in the
        other — the same defect, shipped with more confidence. The one legitimate
        fixed value is a run that is *only shown* in that language: the alert
        macro renders both halves of a message, each declared and each bound to
        the language that shows it.
        """
        offenders = []
        for path in templates():
            src = path.read_text(encoding="utf-8", errors="ignore")
            for m in re.finditer(r'(?<![:\w-])lang="(en|id)"', src):
                if path.name in (
                    "monitor.html",
                    "report_card.html",
                    "result_detail_pdf.html",
                    "print_exam_report.html",
                ):
                    continue  # standalone documents, Indonesian by construction
                tag = TAG.match(src, src.rfind("<", 0, m.start()))
                text = tag.group(0) if tag else ""
                mine, other = m.group(1), ("en" if m.group(1) == "id" else "id")
                shown = re.search(
                    r'x-show="lang\s*===\s*\'%s\'' % mine, text
                ) or re.search(
                    r'x-show="lang\s*!==\s*\'%s\'' % other, text
                )
                if shown:
                    continue
                line = src.count("\n", 0, m.start()) + 1
                offenders.append(f"{path.relative_to(TEMPLATES).as_posix()}:{line}")
        assert not offenders, (
            "a fixed lang= on an element is right in one toggle position and "
            f"wrong in the other: {offenders}"
        )


# ── the title is read out, not rendered ─────────────────────────────────────

TITLE = re.compile(r"\{%\s*block\s+title\s*%\}(.*?)\{%\s*endblock\s*%\}",
                   re.S)


def title_blocks(src):
    return [(src.count("\n", 0, m.start()) + 1, m.group(1).strip())
            for m in TITLE.finditer(src)]


class TestTheTitleIsText:
    """`{% block title %}` lands inside `<title>`, which renders no HTML at all.

    A `<span x-text=\"…\">` there is not translated, it is *spelled out*: the
    browser tab showed the markup — `<span x-text="t('Analisis Butir Soal'…` —
    to every reader, in both languages. Two pages had one. The visible heading
    keeps its span; the tab gets text, and a page that wants its tab translated
    sets `document.title` from an Alpine effect, after the toggle exists.
    """

    def test_no_title_block_carries_markup(self):
        offenders = []
        for path in templates():
            src = path.read_text(encoding="utf-8", errors="ignore")
            for line, body in title_blocks(src):
                if "<" in body or "x-text" in body:
                    offenders.append(
                        f"{path.relative_to(TEMPLATES).as_posix()}:{line}"
                    )
        assert not offenders, (
            "a <title> renders no HTML, so this markup is read out verbatim as "
            "the tab's text:\n  " + "\n  ".join(offenders)
        )

    def test_a_page_that_wants_a_translated_tab_sets_it_from_the_toggle(self):
        """The offline half, done the way that works: an Alpine effect that reads
        `t(...)`. Both pages that used to spell their title out now do this, so the
        pattern is recorded rather than re-invented."""
        for name in ("teacher/analysis.html", "teacher/analytics.html"):
            src = (TEMPLATES / name).read_text(encoding="utf-8", errors="ignore")
            assert "document.title = t(" in src, (
                f"{name} declares a fixed tab title and never follows the toggle")
            assert not TITLE.search(src).group(1).strip().count("<"), (
                f"{name} is back to putting markup in `<title>`")
