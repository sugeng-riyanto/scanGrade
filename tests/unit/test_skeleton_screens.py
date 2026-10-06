"""A page that waits for data draws the shape of what is coming, not a spinner.

`session_review.html` paints nothing until its one fetch settles, and what it
painted during that wait was a spinner in the middle of an empty column. The
reader sees an empty page, then a full one — two arrivals, and in between no
information about what is loading or how much of it there is.

The replacement is a skeleton: the same four stat cards and the same two content
cards, at the same sizes, drawn immediately. What this file guards is that the
skeleton stays *honest*, because every one of these properties is something a
later edit can quietly drop:

* **the rule exists, and it can be switched off.** The sweep is decoration; a
  reader who asked for less motion must still get the shape and the wait. A
  `@keyframes` with no `prefers-reduced-motion` escape is a rule that ignores
  that request, and the previous such rule in this app shipped for months.
* **the sheen is not white.** A white sweep is invisible against the light card
  and a bright stripe against the dark one. The band has to read as a soft sheen
  on *both*, which is why it is a translucent slate rather than a literal.
* **it is not silent.** A skeleton carries no text, so a screen reader gets an
  empty page unless the region says it is busy and names the wait. A decorative
  skeleton is an empty page with better paint.
* **it takes the theme's surface, not a colour of its own.** The fill is
  Tailwind's `bg-surface-100`, which `theme.css` remaps in dark mode. A
  hard-coded hex here would be a light-mode skeleton on a dark page — and would
  not be caught by any contrast test, because it paints no text.
* **it has the shape of what follows.** The point of the pattern is that the
  layout does not jump. A skeleton that uses its own grid is a placeholder that
  gets replaced by a different layout — which is the jump it was meant to avoid.

Measured against the template before this test existed: the loading block held a
centred `animate-spin` ring and the line "Memuat sesi...", and no `sg-skel`
existed anywhere in the tree.
"""
from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[2]
THEME_CSS = ROOT / "app" / "static" / "css" / "theme.css"
PAGE = ROOT / "app" / "templates" / "teacher" / "session_review.html"


@pytest.fixture(scope="module")
def css() -> str:
    return THEME_CSS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def skeleton_block(page: str) -> str:
    """The skeleton region itself: from its own marker to the error state.

    Read from the marker rather than by line number, so the assertions below are
    about the block and not about wherever it happens to sit in the file.
    """
    start = page.find("data-sg-skeleton")
    assert start != -1, "the loading region must be marked with data-sg-skeleton"
    end = page.find('x-show="!loading && error"', start)
    assert end != -1, "the error region that follows the skeleton must still exist"
    return page[start:end]


# ── the rule ─────────────────────────────────────────────────────────────────

def _skel_rule(css: str) -> str:
    rule = re.search(r"\.sg-skel\s*\{([^}]*)\}", css)
    assert rule, "`.sg-skel` must be defined in theme.css"
    return rule.group(1)


def test_the_skeleton_rule_exists(css: str):
    """`.sg-skel` is the whole pattern: a band painted across the cell itself."""
    body = _skel_rule(css)
    assert "background-image" in body, \
        "the band is the cell's own background, so the box clips it"
    assert "background-size" in body, \
        "without a width larger than the cell the band has nowhere to travel"
    assert re.search(r"animation:\s*sg-skel-sweep", body), \
        "the cell must run the sweep"
    assert re.search(r"@keyframes\s+sg-skel-sweep", css), \
        "the sweep keyframes must exist, or the band never moves"


def test_the_sweep_takes_no_stacking_height(css: str):
    """A background needs no layer; a positioned box would be a panel to order.

    `test_layer_scale.py` requires every element that leaves the flow to name a
    layer, because otherwise what covers what is decided by document order. A
    sweep painted as the cell's own background leaves nothing to decide: the box
    clips it. Drawing it as an `absolute` pseudo-element would turn every
    skeleton cell into a panel the scale has to place, for no gain.
    """
    body = _skel_rule(css)
    assert not re.search(r"position:\s*(absolute|fixed)", body), \
        ("the skeleton cell must not leave the flow — an absolute skeleton is a "
         "panel the layer scale has to account for")


def test_the_sweep_is_surrendered_when_motion_is_not_wanted(css: str):
    """`prefers-reduced-motion` gets the shape and the wait, without the sweep."""
    guard = re.search(
        r"@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{(.*?)\}", css, re.S)
    assert guard, "theme.css must honour `prefers-reduced-motion: reduce`"
    assert re.search(r"\.sg-skel\s*\{[^}]*animation:\s*none", guard.group(1)), \
        ("the reduced-motion guard must switch the skeleton's animation off — a "
         "guard that only slows another element leaves this one moving")


def test_the_sheen_is_not_white(css: str):
    """The band has to be visible on the light card *and* soft on the dark one."""
    body = _skel_rule(css)
    gradient = re.search(r"linear-gradient\(([^;]*)\)", body)
    assert gradient, "the band is a gradient"
    stops = gradient.group(1).lower()
    assert "rgba(148, 163, 184" in stops, \
        ("the sheen must be the translucent slate band, which reads on both "
         "themes")
    assert not re.search(r"#fff|white|255,\s*255,\s*255", stops), \
        ("a white sweep is invisible on the light card and a stripe on the dark "
         "one, so the band must not be white")


# ── the page ─────────────────────────────────────────────────────────────────

def test_the_page_shows_the_skeleton_instead_of_a_spinner(skeleton_block: str):
    """The wait is drawn as content, not as a ring in an empty column."""
    assert "sg-skel" in skeleton_block, \
        "the loading region must draw skeleton cells"
    assert skeleton_block.count("sg-skel") >= 4, \
        "one skeleton cell is not a shape; the panel has several regions"
    assert "animate-spin" not in skeleton_block, \
        ("the centred spinner is what this replaces — a skeleton *and* a spinner "
         "is the same empty page with more paint")


def test_the_skeleton_is_announced(skeleton_block: str):
    """A skeleton carries no text, so the region has to say it is working."""
    assert 'aria-busy="true"' in skeleton_block, \
        "the loading region must report that it is busy"
    assert "sr-only" in skeleton_block, \
        ("a reader who cannot see the shimmer must be told the page is loading "
         "and what it is loading")


def test_the_skeleton_takes_the_theme_surface_not_a_colour_of_its_own(
        skeleton_block: str):
    """`bg-surface-100` is remapped in dark mode; a hex would not be."""
    assert "bg-surface-100" in skeleton_block, \
        "the fill must be the theme's surface utility, so dark mode follows"
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", skeleton_block), \
        ("the skeleton must not paint a colour of its own — a hex here is a "
         "light-mode skeleton on a dark page, and no contrast test looks at a "
         "cell that paints no text")


def test_the_skeleton_has_the_shape_of_what_follows(page: str, skeleton_block: str):
    """Same grid, or the real layout arrives as a jump — the thing it prevents."""
    grid = "grid grid-cols-2 sm:grid-cols-4 gap-3"
    assert page.count(grid) == 2, \
        ("the stats grid must appear twice: once for the real cards and once in "
         "the skeleton, so the two share a shape")
    assert grid in skeleton_block, \
        "the skeleton's stat row must use the grid the real stats use"
    # Four cards, or the row changes height when the data lands.
    cards = re.findall(r"bg-white rounded-xl p-4 shadow-sm border border-surface-200",
                       skeleton_block)
    assert len(cards) == 4, \
        ("the stats row is four cards; a skeleton with a different count is a "
         "different height")


def test_a_failed_load_still_says_so(page: str):
    """The skeleton must be able to stop: a fetch that fails is not a forever-wait."""
    assert 'x-show="!loading && error"' in page, \
        "the error region must survive, or a failed load shows a skeleton forever"
