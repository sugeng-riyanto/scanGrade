"""Filtering, show-all and asc/desc on the teachers and students rosters.

The two pages were fixed at 50 rows, ordered by one hard-coded column, with a
single search box each and no way to sort. Requested: the same controls on both —
a filter, an asc/descending sort, and a "show all" choice — and a class-ordered
download. These guards pin the shared helpers the two routes use, and the card
order the download depends on.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.routes import admin_sekolah as adm
from app.services import login_cards

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "app" / "templates" / "admin_sekolah"


class TestPageSize:
    def test_all_means_everything(self):
        assert adm._per_page_arg("all") == 0
        assert adm._per_page_arg("Semua") == 0

    def test_a_known_size_is_kept(self):
        assert adm._per_page_arg("100") == 100

    def test_a_bad_value_falls_back_to_fifty(self):
        assert adm._per_page_arg("9999") == 50
        assert adm._per_page_arg("garbage") == 50


class TestSortAndSlice:
    ROWS = [
        {"name": "Citra", "class_name": "VII-B", "nisn": "3"},
        {"name": "Ahmad", "class_name": "VII-A", "nisn": "1"},
        {"name": "Budi", "class_name": "VII-A", "nisn": "2"},
    ]
    KEYS = {
        "class": lambda r: (r["class_name"].lower(), r["name"].lower()),
        "name": lambda r: r["name"].lower(),
        "nisn": lambda r: r["nisn"],
    }

    def test_ascending_by_class_groups_then_names(self):
        rows, total, pages, page = adm._apply_sort_page(
            self.ROWS, sort="class", direction="asc", page=1, per_page=50, keys=self.KEYS)
        assert [r["name"] for r in rows] == ["Ahmad", "Budi", "Citra"]
        assert total == 3 and pages == 1 and page == 1

    def test_descending_reverses(self):
        rows, *_ = adm._apply_sort_page(
            self.ROWS, sort="name", direction="desc", page=1, per_page=50, keys=self.KEYS)
        assert [r["name"] for r in rows] == ["Citra", "Budi", "Ahmad"]

    def test_show_all_is_a_single_page(self):
        rows, total, pages, page = adm._apply_sort_page(
            self.ROWS, sort="name", direction="asc", page=1, per_page=0, keys=self.KEYS)
        assert len(rows) == 3 and total == 3 and pages == 1

    def test_a_page_past_the_end_clamps_to_the_last(self):
        rows, total, pages, page = adm._apply_sort_page(
            self.ROWS, sort="name", direction="asc", page=99, per_page=2, keys=self.KEYS)
        assert page == pages == 2 and len(rows) == 1

    def test_an_unknown_sort_uses_the_first_key_not_an_error(self):
        rows, *_ = adm._apply_sort_page(
            self.ROWS, sort="nonsense", direction="asc", page=1, per_page=50, keys=self.KEYS)
        assert [r["name"] for r in rows] == ["Ahmad", "Budi", "Citra"]


class TestCardsAreOrderedByClass:
    def test_cards_sort_by_group_then_name(self):
        cards = [
            {"group": "VII-B", "name": "Zahra"},
            {"group": "VII-A", "name": "Budi"},
            {"group": "VII-A", "name": "Ahmad"},
        ]
        login_cards._sort_cards(cards)
        assert [(c["group"], c["name"]) for c in cards] == [
            ("VII-A", "Ahmad"), ("VII-A", "Budi"), ("VII-B", "Zahra")]


class TestTheTemplatesCarryTheControls:
    def test_both_pages_offer_show_all_and_a_direction(self):
        for name in ("teachers.html", "students.html"):
            html = (TEMPLATES / name).read_text(encoding="utf-8")
            assert 'name="per_page"' in html, f"{name} has no page-size control"
            assert 'value="all"' in html, f"{name} has no 'show all' option"
            assert 'name="dir"' in html, f"{name} has no asc/desc control"

    def test_students_can_filter_by_class(self):
        html = (TEMPLATES / "students.html").read_text(encoding="utf-8")
        assert 'name="class_id"' in html

    def test_teachers_can_filter_by_subject(self):
        html = (TEMPLATES / "teachers.html").read_text(encoding="utf-8")
        assert 'name="subject_id"' in html


# ── who has activated their account, and who is still on the issued password ──
#
# "Belum aktivasi" is `profiles.must_change_password` — one column, one meaning on
# all four rosters. The same three questions are asked of every one of them: which
# of the two states a reader wants, whether a name finds a person whatever their
# list is keyed on, and what the page does with a filter it cannot parse.

class TestTheActivationFilter:
    def test_an_unknown_word_is_both_states_not_a_refusal(self):
        assert adm._activation_arg("") == ""
        assert adm._activation_arg(None) == ""
        assert adm._activation_arg("nonsense") == ""

    def test_the_two_words_are_kept_and_normalised(self):
        assert adm._activation_arg("activated") == "activated"
        assert adm._activation_arg("  PENDING  ") == "pending"

    def test_the_flag_is_the_inverse_of_must_change_password(self):
        assert adm._is_activated({"must_change_password": False}) is True
        assert adm._is_activated({"must_change_password": True}) is False

    def test_the_split_counts_the_roster_it_is_given(self):
        rows = [{"activated": True}, {"activated": False}, {"activated": True}]
        assert adm._split_by_activation(rows) == {"activated": 2, "pending": 1}

    def test_a_row_with_no_flag_is_not_accused_of_pending(self):
        """"Belum aktivasi" is a claim about a person; no evidence is not that claim."""
        assert adm._is_activated({}) is True


class TestTheNameSearch:
    def test_it_matches_a_name_a_number_or_an_address(self):
        row = {"name": "Ahmad Pratama", "nisn": "1234567890",
               "employee_number": "P-77", "email": "ahmad@school.test"}
        for needle in ("ahmad", "AHMAD", "1234", "p-77", "school.test"):
            assert adm._matches_search(row, needle), needle

    def test_an_empty_needle_matches_everyone(self):
        assert adm._matches_search({"name": "X"}, "") is True

    def test_it_does_not_match_a_field_the_row_does_not_carry(self):
        assert adm._matches_search({"name": "Ahmad"}, "1234") is False


class TestTheThreeRostersCarryTheControls:
    def test_each_list_page_names_the_filter_and_the_chart(self):
        for name in ("teachers.html", "students.html", "officials.html"):
            html = (TEMPLATES / name).read_text(encoding="utf-8")
            assert 'name="activation"' in html, f"{name} has no activation filter"
            assert "sgActivation" in html, f"{name} has no activation chart"
            assert "activation_chart" in html, (
                f"{name} does not read the payload the route counted")

    def test_each_row_says_which_state_it_is_in(self):
        """The count and the pie answer "how many"; the row's own badge answers "which",
        and that is the question the filter above it is used to ask. Both states carry
        one, because a badge that appears on the pending half alone reads as decoration
        on the other."""
        for name, flag in (("teachers.html", "t"), ("students.html", "s"),
                           ("officials.html", "o")):
            html = (TEMPLATES / name).read_text(encoding="utf-8")
            assert f"{{% if {flag}.activated %}}" in html, (
                f"{name} carries no per-row activation badge")
            assert 'data-activated="1"' in html, f"{name} has no activated badge"
            assert 'data-must-change="1"' in html, f"{name} has no pending badge"

    def test_every_list_route_reads_the_filter_and_counts_the_roster(self):
        src = (ROOT / "app" / "routes" / "admin_sekolah.py").read_text(encoding="utf-8")
        for fn in ("def students(", "def teachers(", "def officials("):
            body = src.split(fn, 1)[1].split("\ndef ", 1)[0]
            assert "_activation_arg(" in body, fn
            assert "_split_by_activation(" in body, fn
            assert "activation_chart=activation_chart" in body, fn
            assert "_matches_search(" in body, fn

    def test_the_activation_choice_survives_the_pagination_links(self):
        """A filter dropped from `base_qs` is a filter that resets on page 2."""
        src = (ROOT / "app" / "routes" / "admin_sekolah.py").read_text(encoding="utf-8")
        assert "activation=activation," in src

    def test_the_chart_colours_are_not_a_two_literal_array(self):
        """The i18n sweep reads `['#a','#b']` as a bilingual pair of copy: name them."""
        for name in ("teachers.html", "students.html", "officials.html"):
            html = (TEMPLATES / name).read_text(encoding="utf-8")
            assert "backgroundColor: [activatedColour, pendingColour]" in html, name


class TestTheMembershipPageFiltersByName:
    def test_it_has_a_name_box_and_a_direction(self):
        html = (TEMPLATES / "membership.html").read_text(encoding="utf-8")
        assert 'name="q"' in html, "the page has no name filter"
        assert 'name="dir"' in html, "the page has no A–Z / Z–A choice"

    def test_the_route_filters_both_lists_and_orders_the_members(self):
        src = (ROOT / "app" / "routes" / "membership_routes.py").read_text(encoding="utf-8")
        body = src.split("def membership_home(", 1)[1].split("\n@", 1)[0]
        assert "requests_shown" in body and "members_shown" in body
        assert "members_shown.sort(" in body

    def test_an_empty_table_says_which_emptiness_it_is(self):
        """"No membership yet" and "nothing matched that search" are different facts."""
        html = (TEMPLATES / "membership.html").read_text(encoding="utf-8")
        assert "No member matches that search." in html
        assert "No membership is recorded at this school yet." in html
        assert "No request matches that search." in html


# ── the other direction: a control nobody reads, and a cap that came back ─────
#
# Every rule above asserts a control is *on the page*, and not one of them would notice
# the failure this file was written for. The two rosters were fixed at 50 rows, ordered by
# one hard-coded column, with a search box each — so a filter that was added to a page and
# never read by its route looked exactly like a working one, and a route that went back to
# a literal page size would break the control the page still offers.
#
# `FILTERS` is the vocabulary both halves are judged against, and it is deliberately
# shorter than `name="…"`: `page` is pagination, driven by links on both sides; the
# teachers page's `assignment_present` is a hidden field of its *POST* form, read where it
# is posted; and the membership route's `status` is the flash outcome a redirect sets, not
# a control. Those three are read, or set, by something other than a filter bar — so
# judging them here would be a guard that fires on correct code.

FILTERS = ("activation", "class_id", "dir", "per_page", "q", "sort", "subject_id")

ROOT = TEMPLATES.parents[2]
ROUTES = ROOT / "app" / "routes"

#: (the page, the module its reader lives in, the route that renders it)
PAGES = (
    ("teachers.html", ROUTES / "admin_sekolah.py", "def teachers("),
    ("students.html", ROUTES / "admin_sekolah.py", "def students("),
    ("officials.html", ROUTES / "admin_sekolah.py", "def officials("),
    ("membership.html", ROUTES / "membership_routes.py", "def membership_home("),
)


def _route_body(module, fn):
    src = module.read_text(encoding="utf-8")
    assert fn in src, f"{module.name} no longer defines {fn.strip('def (')}"
    return src.split(fn, 1)[1].split("\ndef ", 1)[0].split("\n@", 1)[0]


def _offers(page):
    html = (TEMPLATES / page).read_text(encoding="utf-8")
    return set(re.findall(r'name="([a-z_]+)"', html)) & set(FILTERS)


def _reads(body):
    return set(re.findall(
        r"request\.(?:args|values)\.get\(\s*['\"]([a-z_]+)['\"]", body)) & set(FILTERS)


class TestNoControlIsADeadFilter:
    @pytest.mark.parametrize("page,module,fn", PAGES, ids=[p for p, _, _ in PAGES])
    def test_every_filter_the_page_offers_is_read_by_its_route(self, page, module, fn):
        unread = _offers(page) - _reads(_route_body(module, fn))
        assert not unread, (
            f"{page} offers {sorted(unread)} and {fn.strip('def (')} never reads it, so the "
            "control sits in the filter bar changing nothing")

    @pytest.mark.parametrize("page,module,fn", PAGES, ids=[p for p, _, _ in PAGES])
    def test_every_filter_the_route_reads_is_offered_by_the_page(self, page, module, fn):
        unsettable = _reads(_route_body(module, fn)) - _offers(page)
        assert not unsettable, (
            f"{fn.strip('def (')} filters on {sorted(unsettable)} and {page} cannot set it, "
            "so the filter only exists in a URL somebody types by hand")

    def test_the_filter_vocabulary_still_names_the_controls(self):
        """An empty vocabulary passes both readings above without looking at anything."""
        seen = set()
        for page, _, _ in PAGES:
            seen |= _offers(page)
        assert {"q", "dir", "per_page", "sort"} <= seen, (
            f"the filter vocabulary no longer matches the pages: {sorted(seen)}")


class TestNoRosterCapsItselfAtALiteralAgain:
    def test_no_roster_route_slices_at_a_literal_page_size(self):
        """The fault this file was written for: 50 rows, hard-coded, on both rosters."""
        for page, module, fn in PAGES:
            if page == "membership.html":
                continue  # a two-list page, not a paginated one
            body = _route_body(module, fn)
            found = re.search(r"\[\s*:\s*\d+\s*\]|\.limit\(\s*\d+\s*\)", body)
            assert not found, (
                f"{fn.strip('def (')} slices its rows at a literal ({found.group(0)}) again — "
                "the page size is `_per_page_arg`, and a literal ignores what the reader "
                "chose in the control the page offers")
