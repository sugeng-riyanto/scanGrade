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

TEMPLATES = Path(__file__).resolve().parents[2] / "app" / "templates" / "admin_sekolah"


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
