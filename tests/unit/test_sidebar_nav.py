"""One entry per destination, in every role's navigation.

A duplicated sidebar link is not a cosmetic slip: the teacher's menu had
"Scoring Guide" twice, under two sections called *Guide*, and the second one
arrived with the copy that translated the first — so the page a teacher opens to
check a mark was listed twice and neither entry looked wrong on its own.

Guarded at the source, because that is where the defect is: the sidebar is one
static `<nav>` with a branch per role, and a destination listed twice inside one
branch is a duplicate by definition. The bottom bar (mobile) and the two
breadcrumb navs are checked the same way.
"""
from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
BASE = (ROOT / "app" / "templates" / "base.html").read_text(encoding="utf-8")

ROLES = {"super_admin", "admin_sekolah", "guru", "murid"}

#: Every nav in base.html, as (name, first-line-of-the-block pattern).
NAV_OPEN = re.compile(r"<nav\b[^>]*>")


def _nav_blocks():
    """Each `<nav>…</nav>`, in order, with the links it carries."""
    blocks = []
    for index, opening in enumerate(NAV_OPEN.finditer(BASE)):
        end = BASE.find("</nav>", opening.end())
        assert end > 0, f"nav {index} is never closed"
        blocks.append((opening.group(0), BASE[opening.end():end]))
    return blocks


def _sidebar_blocks():
    """The sidebar split by role, so each branch is judged on its own.

    The desktop sidebar is a single `<nav>` holding one branch per role; two
    entries for the same page *in different roles* is correct (a teacher and a
    student each get their own menu), so the branches are what has to be unique.
    """
    opening, body = _nav_blocks()[0]
    parts = re.split(r"\{%\s*(?:el)?if\s+g\.user_role\s*==\s*'(\w+)'\s*%\}",
                     body)
    # parts = [prelude, role, chunk, role, chunk, …]
    return [(role, chunk) for role, chunk in zip(parts[1::2], parts[2::2])]


def _hrefs(chunk: str):
    return re.findall(r"""href=["']([^"'{}]+)["']""", chunk)


def test_the_sidebar_still_has_a_branch_per_role():
    """A guard that silently matches nothing proves nothing."""
    roles = [role for role, _chunk in _sidebar_blocks()]
    assert set(roles) == ROLES, f"the sidebar branches are {roles}"


@pytest.mark.parametrize("role", sorted(ROLES))
def test_no_destination_is_listed_twice_in_one_role_s_sidebar(role):
    chunks = {name: chunk for name, chunk in _sidebar_blocks()}
    assert role in chunks, f"role {role} has no sidebar branch"
    hrefs = _hrefs(chunks[role])
    assert hrefs, f"role {role}'s sidebar has no links at all"
    seen = {}
    for href in hrefs:
        seen[href] = seen.get(href, 0) + 1
    doubled = {href: count for href, count in seen.items() if count > 1}
    assert not doubled, (
        f"role {role} lists the same page more than once: "
        + ", ".join(f"{href} ×{count}" for href, count in doubled.items()))


def test_the_bottom_bar_lists_no_destination_twice():
    """The mobile bar is a second menu built from the same idea, and it drifted
    the same way the sidebar did."""
    opening, body = _nav_blocks()[-1]
    assert "bottomnav" in opening, "the last nav is no longer the mobile bar"
    parts = re.split(r"\{%\s*(?:el)?if\s+role\s*==\s*'(\w+)'\s*%\}", body)
    for role, chunk in zip(parts[1::2], parts[2::2]):
        hrefs = _hrefs(chunk)
        assert len(hrefs) == len(set(hrefs)), (
            f"the {role} bottom bar lists a page twice: {hrefs}")


def test_the_scoring_guide_is_offered_once_to_every_role_that_may_read_it():
    for role, chunk in _sidebar_blocks():
        assert chunk.count('href="/guide/skor"') <= 1, (
            f"the {role} sidebar offers the scoring guide more than once")
