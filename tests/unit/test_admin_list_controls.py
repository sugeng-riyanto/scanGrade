"""Filtering, show-all and asc/desc on the teachers and students rosters.

The two pages were fixed at 50 rows, ordered by one hard-coded column, with a
single search box each and no way to sort. Requested: the same controls on both —
a filter, an asc/descending sort, and a "show all" choice — and a class-ordered
download. These guards pin the shared helpers the two routes use, and the card
order the download depends on.
"""
from __future__ import annotations

from pathlib import Path

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
