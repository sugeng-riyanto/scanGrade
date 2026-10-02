"""A missing id rendered as the word "None" — and the 500 a browser gets for it.

`/admin-sekolah/classes/<id>/edit` answered **Terjadi Kesalahan**. The traceback
from the live box ends:

    postgrest.exceptions.APIError: {'code': '22P02',
        'message': 'invalid input syntax for type uuid: "None"'}

The path is short and entirely reproducible. A class row whose `school_year_id`
is NULL has that column rendered by `{{ c.school_year_id }}` into a hidden input,
and Jinja writes Python's `None` as the **string** `"None"`. The form posts it
back; the route helper asks Postgres for a uuid; Postgres refuses; the request
500s with nothing about the form in the message.

Two things are wrong and this suite pins both:

* **the template** — a nullable id must never be interpolated bare, because
  "there is no value" and the word `None` are different things to a database;
* **the boundary** — a helper that turns form input into a query must fold a
  null-ish word to `None` and refuse a value that is not a uuid, rather than
  handing either to Postgres. That is the part that makes the next form safe:
  the template fix repairs this field, the coercion repairs the class.

The sweep over every template is deliberate — the same bare interpolation in any
`*_id` field produces the same silent 500, and it is cheaper to catch the class
than the next instance.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "app" / "templates"
ROUTES = ROOT / "app" / "routes" / "admin_sekolah.py"

#: `value="{{ name }}"` where `name` is a bare dotted expression and the last
#: attribute ends in `_id` — the nullable foreign keys a row may not have.
BARE_ID_VALUE = re.compile(r"""value\s*=\s*"\{\{\s*([A-Za-z_][\w.]*)\s*\}\}\"""")


def _bare_id_fields():
    found = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        text = path.read_text(encoding="utf-8")
        for match in BARE_ID_VALUE.finditer(text):
            expr = match.group(1)
            last = expr.rsplit(".", 1)[-1]
            if last.endswith("_id"):
                line = text[: match.start()].count("\n") + 1
                found.append(f"{path.relative_to(ROOT)}:{line} {expr}")
    return found


class TestTemplatesNeverRenderNoneAsAnId:
    def test_no_nullable_id_is_interpolated_bare(self):
        offenders = _bare_id_fields()
        assert not offenders, (
            "these form fields interpolate a nullable id bare, so a row without "
            "one posts the word 'None' and Postgres refuses it as a uuid:\n  "
            + "\n  ".join(offenders))

    def test_the_classes_form_guards_its_school_year(self):
        page = (TEMPLATES / "admin_sekolah" / "classes.html").read_text(encoding="utf-8")
        # The exact field the live traceback named.
        assert 'name="school_year_id"' in page
        assert re.search(r'name="school_year_id"[^>]*value="\{\{\s*c\.school_year_id\s*or', page) \
            or re.search(r'value="\{\{\s*c\.school_year_id\s*or\s*[\'"]{2}\s*\}\}"[^>]*name="school_year_id"', page), \
            "school_year_id must fall back to an empty string, not 'None'"


class TestTheBoundaryRefusesJunk:
    def _mod(self):
        import importlib
        return importlib.import_module("app.routes.admin_sekolah")

    def test_a_nullish_word_folds_to_none(self):
        clean = self._mod()._clean_uuid
        for word in ("", "  ", "None", "none", "null", "undefined", "NIL"):
            assert clean(word) is None, word
        assert clean(None) is None

    def test_a_real_uuid_survives(self):
        clean = self._mod()._clean_uuid
        value = "ebfd494d-5672-4c92-bdae-15d22b409162"
        assert clean(value) == value
        assert clean("  " + value + "  ") == value

    def test_a_non_uuid_is_not_mistaken_for_an_id(self):
        assert self._mod()._is_uuid("None") is False
        assert self._mod()._is_uuid("abc") is False
        assert self._mod()._is_uuid("") is False
        assert self._mod()._is_uuid("ebfd494d-5672-4c92-bdae-15d22b409162") is True

    def test_a_non_uuid_is_refused_without_a_query(self):
        called = []

        class _Boom:
            def table(self, name):
                called.append(name)
                raise AssertionError("a non-uuid must never reach Postgres")

        assert self._mod()._year_in_school(_Boom(), "not-an-id", "sid") is False
        assert self._mod()._teacher_in_school(_Boom(), "not-an-id", "sid") is False
        assert not called, "the helper must refuse before it builds a query"

    def test_the_word_none_means_no_choice_not_a_bad_choice(self):
        # The string "None" is how a bare `{{ c.school_year_id }}` spells a NULL
        # column, and it means the same thing as an empty field: nothing chosen.
        # Folding it to None (allowed, empty) is right; refusing it would turn a
        # normal save into an error the admin cannot explain.
        class _Boom:
            def table(self, name):
                raise AssertionError("no query for a missing id")

        assert self._mod()._year_in_school(_Boom(), "None", "sid") is True
        assert self._mod()._teacher_in_school(_Boom(), "None", "sid") is True
        assert self._mod()._year_in_school(_Boom(), "NULL", "sid") is True

    def test_a_missing_id_is_still_allowed(self):
        # `None` means 'no year chosen', which is legitimate and must stay so —
        # the guard must not turn an empty choice into a refusal.
        class _Boom:
            def table(self, name):
                raise AssertionError("no query for a missing id")

        assert self._mod()._year_in_school(_Boom(), None, "sid") is True
        assert self._mod()._teacher_in_school(_Boom(), None, "sid") is True
        assert self._mod()._year_in_school(_Boom(), "", "sid") is True


class TestTheRoutesCoerceBeforeTheyQuery:
    def _src(self):
        return ROUTES.read_text(encoding="utf-8")

    def test_the_create_route_cleans_its_ids(self):
        src = self._src()
        window = src.split("def create_class", 1)[1][:2500]
        assert "_clean_uuid" in window

    def test_the_edit_route_cleans_its_ids(self):
        src = self._src()
        window = src.split("def edit_class", 1)[1][:2500]
        assert "_clean_uuid" in window
