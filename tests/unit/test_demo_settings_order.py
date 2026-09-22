"""The demo surface is arranged on one page and drawn on two others.

``/super-admin/demo-settings`` grew two new answers — *in what order*, beside *which*
— and the three pages that share ``school_settings.demo_settings`` have to agree on
both, or a reorder silently reverts and a checkbox does nothing.

The failure this file exists to prevent is subtler than a typo. Ordering and
visibility look like the same question ("which ones, in what order") but they are
not, and answering both with one helper gets one of them wrong:

* the **settings page** must list a switched-off item, because that page is where
  you switch it back on — filtering it would hide the row you came to click;
* the **landing page** and ``/demo`` must *not* draw a switched-off item.

So the guard here is relational rather than a snapshot: for a matrix of blobs,
whatever ``demo_items`` offers must be exactly what the page renders, and in that
order. A page that hard-codes its own list of buttons passes a text assertion and
fails this one.
"""
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import demo_settings as ds


# ── the pages, and the marker that proves a row was drawn ────────
#
# One unique marker per renderable item: the demo account each card prints, and
# the tutorial each button links to. A marker is what *shows* on the page, so a
# grep for it is a real answer to "did the visitor get this?".

TUTORIAL_HREF = {
    "demo_tutorial_guru": "/tutorial/guru",
    "demo_tutorial_siswa": "/tutorial/murid",
    "demo_tutorial_admin": "/tutorial/admin-sekolah",
}

CARD_EMAIL = {
    "demo_super_admin": "superadmin@scan-grade.app",
    "demo_admin_sekolah": "admin_smp@scan-grade.app",
    "demo_guru": "guru_mtk_smp@scan-grade.app",
    "demo_murid": "siswa1_smp@scan-grade.app",
}

SETTINGS_LABEL = {
    "demo_super_admin": "Super Admin",
    "demo_admin_sekolah": "Admin Sekolah",
    "demo_guru": "Guru",
    "demo_murid": "Murid",
    "demo_tutorial_guru": "Tutorial Guru",
    "demo_tutorial_siswa": "Tutorial Siswa",
    "demo_tutorial_admin": "Tutorial Admin",
}


def _render(app, template, blob, signed_in=False, **ctx):
    """Render a template as the route would, with ``demo_settings`` pinned.

    ``base.html`` picks one of two layouts on ``g.user_id``: the app chrome with
    ``{% block content %}``, or the bare layout with ``{% block content_noauth %}
    ``. A page that renders into the wrong one throws its whole body away *without
    an error* — an anonymous render of the settings page yields a page with no form
    in it, which is exactly the kind of silence these tests exist to catch.
    """
    from flask import g

    marker = object()
    previous = app.jinja_env.globals.get("get_demo_settings", marker)
    app.jinja_env.globals["get_demo_settings"] = lambda: blob
    try:
        with app.test_request_context("/"):
            g.user = None
            if signed_in:
                # The chrome reads all of these; a missing one is an UndefinedError.
                g.user_id, g.user_role = "sa-1", "super_admin"
                g.user_name, g.user_email = "Super Admin", "sa@scan-grade.app"
                g.tz_offset = 7
            return app.jinja_env.get_template(template).render(**ctx)
    finally:
        if previous is marker:
            app.jinja_env.globals.pop("get_demo_settings", None)
        else:
            app.jinja_env.globals["get_demo_settings"] = previous


def _order_in(html, markers):
    """The keys of ``markers``, in the order their marker appears in ``html``."""
    found = [(html.index(m), key) for key, m in markers.items() if m in html]
    return [key for _, key in sorted(found)]


def _present(html, markers):
    return {key for key, m in markers.items() if m in html}


# ── a matrix of blobs: everything the sparse column can mean ─────
#
# `demo_settings` is jsonb and the page posts nine booleans, but a blob is allowed
# to be *sparse*: a key it does not carry is "unset", not "off", and the role and
# tutorial groups answer "unset" differently. Every case below is one the column
# can actually hold, including the empty blob a school that never opened the page
# has, and a blob naming an item that no longer exists.

BLOBS = {
    "empty": {},
    "everything_on": {k: True for k in
                      list(CARD_EMAIL) + list(TUTORIAL_HREF) + ["demo_tutorial", "demo_enabled"]},
    "everything_off": {k: False for k in
                       list(CARD_EMAIL) + list(TUTORIAL_HREF) + ["demo_tutorial", "demo_enabled"]},
    "production_shape": {"demo_enabled": True, "demo_tutorial": True, "demo_super_admin": False,
                         "demo_admin_sekolah": True, "demo_guru": True, "demo_murid": True,
                         "demo_tutorial_guru": True, "demo_tutorial_siswa": True,
                         "demo_tutorial_admin": False},
    "one_role_off": {"demo_guru": False},
    "tutorial_master_off": {"demo_tutorial": False},
    "stale_key": {"demo_tutorial": True, "demo_guru_from_2019": True},
    "jsonb_as_string": json.dumps({"demo_tutorial_siswa": False, "demo_tutorial": True}),
}


# ── the service: order is stored, then completed ─────────────────

class TestTheStoredOrder:
    def test_the_stored_order_is_used(self):
        blob = {"tutorial_order": ["demo_tutorial_admin", "demo_tutorial_siswa", "demo_tutorial_guru"]}
        assert ds.demo_order(blob, "tutorials") == [
            "demo_tutorial_admin", "demo_tutorial_siswa", "demo_tutorial_guru"]

    def test_a_missing_order_falls_back_to_the_source_order(self):
        assert ds.demo_order({}, "roles") == list(ds.ROLE_ITEMS)

    def test_an_item_the_order_left_out_is_still_listed(self):
        """A blob that predates ordering keeps working, and a new item still appears.

        Neither half may be dropped: appending in canonical order is what makes
        this safe for a school whose blob was saved by an older release.
        """
        blob = {"tutorial_order": ["demo_tutorial_siswa"]}
        assert ds.demo_order(blob, "tutorials") == [
            "demo_tutorial_siswa", "demo_tutorial_guru", "demo_tutorial_admin"]

    def test_an_unknown_or_repeated_key_does_not_become_a_row(self):
        """A row for a key no page can draw renders an empty card; a repeat renders twice."""
        blob = {"role_order": ["demo_guru", "demo_guru", "demo_murid", "demo_ghost"]}
        assert ds.demo_order(blob, "roles") == [
            "demo_guru", "demo_murid", "demo_super_admin", "demo_admin_sekolah"]

    def test_a_comma_string_order_is_read(self):
        """A form field is ``"a,b,c"``; stored as a list by the route, read as either."""
        assert ds.demo_order({"tutorial_order": "demo_tutorial_admin,demo_tutorial_guru"}, "tutorials") \
            == ["demo_tutorial_admin", "demo_tutorial_guru", "demo_tutorial_siswa"]

    def test_a_json_string_blob_is_not_a_string_of_keys(self):
        """`jsonb` written with `json.dumps` arrives as a JSON *string*.

        Iterating it without parsing would walk its characters and find no key —
        the same trap `class_ids` and `question_types` have each fallen into.
        """
        blob = json.dumps({"tutorial_order": ["demo_tutorial_siswa"]})
        assert ds.demo_order(blob, "tutorials")[0] == "demo_tutorial_siswa"

    def test_a_corrupt_blob_shows_everything(self):
        """Unparseable means "nothing stored", which shows the demo, not nothing."""
        assert ds.demo_order("{not json", "roles") == list(ds.ROLE_ITEMS)
        assert ds.demo_items("{not json", "roles") == list(ds.ROLE_ITEMS)


class TestTheVisibilityRules:
    def test_an_unset_blob_shows_every_item(self):
        """A school that never opened the page gets the full demo — both groups."""
        for group, items in (("roles", ds.ROLE_ITEMS), ("tutorials", ds.TUTORIAL_ITEMS)):
            assert ds.demo_items({}, group) == list(items)

    def test_an_off_item_is_absent(self):
        assert "demo_guru" not in ds.demo_items({"demo_guru": False, "demo_murid": True}, "roles")

    def test_the_tutorial_master_gates_a_button_that_has_no_flag_of_its_own(self):
        """The master switch is a default, not an override.

        A tutorial is drawn when its own flag says so, and follows ``demo_tutorial``
        only when the blob never mentions it — the rule the landing page has always
        read. Getting the precedence backwards would make the master checkbox
        unable to turn a single button off.
        """
        assert ds.demo_items({"demo_tutorial": False}, "tutorials") == []
        assert ds.demo_items({"demo_tutorial": True}, "tutorials") == list(ds.TUTORIAL_ITEMS)

        own_flag_wins = {"demo_tutorial": False, "demo_tutorial_guru": True}
        assert ds.demo_items(own_flag_wins, "tutorials") == ["demo_tutorial_guru"]

    def test_order_and_visibility_are_different_questions(self):
        """The settings page lists what the landing page hides. That is the point."""
        blob = {"demo_guru": False, "demo_murid": True}
        assert ds.demo_order(blob, "roles") == list(ds.ROLE_ITEMS)        # all four rows
        assert ds.demo_items(blob, "roles") == ["demo_murid"]             # one drawn


class TestTheEffectiveFlags:
    def test_the_page_paints_what_the_visitor_sees(self):
        """Ticked == shown, for the blob the settings page used to open blank on."""
        blob = {}
        flags = ds.effective_flags(blob)
        assert flags["demo_tutorial_guru"] is True
        assert "demo_tutorial_guru" in ds.demo_items(blob, "tutorials")
        assert flags["demo_super_admin"] is True
        assert "demo_super_admin" in ds.demo_items(blob, "roles")

    @pytest.mark.parametrize("name", sorted(BLOBS))
    def test_every_flag_agrees_with_the_filter(self, name):
        """Nothing may be ticked-but-hidden or unticked-but-drawn."""
        blob = ds.as_mapping(BLOBS[name])
        flags = ds.effective_flags(blob)
        for group, items in (("roles", ds.ROLE_ITEMS), ("tutorials", ds.TUTORIAL_ITEMS)):
            visible = set(ds.demo_items(blob, group))
            for key in items:
                assert flags[key] is (key in visible), \
                    f"{key} disagrees between the checkbox and {group} on {name}"


# ── the pages: rendered set == offered set ──────────────────────

class TestTheLandingPageDrawsWhatIsOffered:
    @pytest.mark.parametrize("name", sorted(BLOBS))
    def test_the_button_row_matches_the_filter(self, app, name):
        """Not "these buttons exist" — "these and only these, in this order"."""
        blob = BLOBS[name]
        html = _render(app, "landing.html", blob)

        assert _present(html, TUTORIAL_HREF) == set(ds.demo_items(blob, "tutorials")), \
            f"landing page and demo_items disagree on {name}"
        assert _order_in(html, TUTORIAL_HREF) == \
            [k for k in ds.demo_items(blob, "tutorials") if k in TUTORIAL_HREF]

    def test_moving_a_row_moves_the_button(self, app):
        """The whole feature: what the operator arranged is the order on the page."""
        blob = {"demo_tutorial": True,
                "tutorial_order": ["demo_tutorial_admin", "demo_tutorial_guru", "demo_tutorial_siswa"]}
        html = _render(app, "landing.html", blob)
        assert _order_in(html, TUTORIAL_HREF) == [
            "demo_tutorial_admin", "demo_tutorial_guru", "demo_tutorial_siswa"]

    #: The hero's button row. Its container is unique on the page, which is what
    #: lets this test look at the row and not at every link on the landing page.
    ROW = "mt-6 flex items-center justify-center gap-3 flex-wrap"

    def _row(self, html):
        """Just the hero button row.

        Scoped deliberately. The page has a *second* ``/demo`` link now, in the
        note under the facilities grid pointing at where to try what it just
        claimed — and a page-wide count both fails for that good edit and, worse,
        would pass for a bad one (the first link's position is not the row's).
        """
        start = html.index(self.ROW)
        return html[start:html.index("</div>", start)]

    def test_the_demo_link_is_last_in_the_row_and_follows_the_master_toggle(self, app):
        row = self._row(_render(app, "landing.html", {"demo_enabled": True, "demo_tutorial": True}))
        assert row.count('href="/demo"') == 1
        assert row.index('href="/demo"') > max(row.index(h) for h in TUTORIAL_HREF.values())

        off = self._row(_render(app, "landing.html", {"demo_enabled": False}))
        assert 'href="/demo"' not in off


class TestTheDemoPageDrawsWhatIsOffered:
    @pytest.mark.parametrize("name", sorted(BLOBS))
    def test_the_card_stack_matches_the_filter(self, app, name):
        blob = BLOBS[name]
        html = _render(app, "demo.html", blob)

        assert _present(html, CARD_EMAIL) == set(ds.demo_items(blob, "roles")), \
            f"/demo and demo_items disagree on {name}"
        assert _order_in(html, CARD_EMAIL) == \
            [k for k in ds.demo_items(blob, "roles") if k in CARD_EMAIL]

    def test_moving_a_row_moves_the_card(self, app):
        blob = {"demo_super_admin": True, "demo_admin_sekolah": True,
                "demo_guru": True, "demo_murid": True,
                "role_order": ["demo_murid", "demo_guru", "demo_admin_sekolah", "demo_super_admin"]}
        html = _render(app, "demo.html", blob)
        assert _order_in(html, CARD_EMAIL) == [
            "demo_murid", "demo_guru", "demo_admin_sekolah", "demo_super_admin"]

    def test_a_card_cannot_be_drawn_twice(self, app):
        """`elif`, not four `if`s — a hand-edited blob may repeat a key."""
        blob = {"demo_guru": True, "demo_murid": True, "demo_admin_sekolah": True,
                "demo_super_admin": True,
                "role_order": ["demo_guru", "demo_guru", "demo_murid"]}
        html = _render(app, "demo.html", blob)
        cards = re.findall(r'data-demo-card="([^"]+)"', html)
        assert cards.count("demo_guru") == 1, "a repeated key drew its card twice"
        assert cards == ds.demo_order(blob, "roles")


# ── the settings page: every row, in the stored order ───────────

class TestTheSettingsPage:
    def test_it_renders_and_lists_every_row(self, app):
        """It called ``demo_order`` before that helper existed: UndefinedError."""
        blob = {"demo_guru": False}
        html = _render(app, "super_admin/demo_settings.html", blob, signed_in=True,
                       settings=blob, flags=ds.effective_flags(blob))
        for key, label in SETTINGS_LABEL.items():
            assert label in html, f"{key} has no row on the page that switches it"

    def test_a_switched_off_row_is_still_listed(self, app):
        """The one page that must *not* filter — this is where you switch it on."""
        blob = {"demo_tutorial": True, "demo_tutorial_admin": False}
        html = _render(app, "super_admin/demo_settings.html", blob, signed_in=True,
                       settings=blob, flags=ds.effective_flags(blob))
        assert "Tutorial Admin" in html
        assert 'data-demo-key="demo_tutorial_admin"' in html

    def test_the_rows_follow_the_stored_order(self, app):
        """A page drawn in source order would put the old order back on save."""
        blob = {"tutorial_order": ["demo_tutorial_admin", "demo_tutorial_guru", "demo_tutorial_siswa"],
                "role_order": list(reversed(ds.ROLE_ITEMS))}
        html = _render(app, "super_admin/demo_settings.html", blob, signed_in=True,
                       settings=blob, flags=ds.effective_flags(blob))
        rows = re.findall(r'data-demo-key="([^"]+)"', html)
        assert rows == [k for k in ds.demo_order(blob, "roles")] + \
                       [k for k in ds.demo_order(blob, "tutorials")]

    def test_the_checkboxes_start_from_the_effective_flags(self, app):
        """A blank form next to a landing page showing every button is the old bug."""
        blob = {"demo_enabled": True, "demo_tutorial": True, "demo_tutorial_guru": True,
                "demo_tutorial_siswa": False, "demo_tutorial_admin": False}
        html = _render(app, "super_admin/demo_settings.html", blob, signed_in=True,
                       settings=blob, flags=ds.effective_flags(blob))
        seed = re.search(r"demoForm\((\{.*?\})\)", html, re.S)
        assert seed, "the form is not seeded from the server at all"
        assert json.loads(seed.group(1)) == ds.effective_flags(blob)

    def test_the_move_controls_do_not_submit_the_form(self):
        """A bare <button> inside a form submits it: an arrow would save as it moves."""
        src = (Path(__file__).resolve().parents[2]
               / "app" / "templates" / "super_admin" / "demo_settings.html").read_text(encoding="utf-8")
        for button in re.findall(r"<button[^>]*data-move[^>]*>", src):
            assert 'type="button"' in button, f"an arrow button submits the form: {button}"

    def test_the_route_hands_the_page_the_effective_flags(self, app, monkeypatch):
        """The seeding above is only as good as the context the route passes."""
        from flask import g
        from app.routes import super_admin as supermod

        blob = {"demo_tutorial": True, "demo_tutorial_siswa": False}
        captured = {}

        class FakeSettings:
            def select(self, *a, **k): return self
            def eq(self, *a, **k): return self
            def update(self, d): return self
            def insert(self, d): return self
            def maybe_single(self): return self
            def execute(self): return SimpleNamespace(data={"demo_settings": blob})

        monkeypatch.setattr(supermod, "render_template",
                            lambda name, **kw: captured.update(kw) or "page")
        app.extensions["supabase"] = SimpleNamespace(
            table=lambda name: FakeSettings())

        with app.test_request_context("/super-admin/demo-settings"):
            g.user_id, g.user_role = "sa-1", "super_admin"
            supermod.demo_settings.__wrapped__()

        assert captured["flags"] == ds.effective_flags(blob)
        assert captured["flags"]["demo_tutorial_siswa"] is False


# ── the save path: the posted order is what lands in the blob ───

class TestTheOrderSurvivesASave:
    def test_the_posted_row_order_is_stored(self, app, monkeypatch):
        """The page posts the DOM order; the route must not re-derive it."""
        from flask import g
        from app.routes import super_admin as supermod

        written = {}

        class FakeSettings:
            def select(self, *a, **k): return self
            def eq(self, *a, **k): return self
            def maybe_single(self): return self
            def update(self, d): written.update(d); return self
            def insert(self, d): written.update(d); return self
            def execute(self):
                if "demo_settings" in written:
                    return SimpleNamespace(data=[{"id": 1}])
                return SimpleNamespace(data=[{"id": 1}])

        monkeypatch.setattr(supermod, "log_activity", lambda *a, **k: None)
        app.extensions["supabase"] = SimpleNamespace(table=lambda name: FakeSettings())

        form = {
            "demo_tutorial": "true",
            "demo_tutorial_guru": "true", "demo_tutorial_siswa": "true",
            "demo_tutorial_admin": "true",
            "tutorial_order": "demo_tutorial_admin,demo_tutorial_guru,demo_tutorial_siswa",
            "role_order": "demo_murid,demo_guru",
        }
        with app.test_request_context("/super-admin/demo-settings", method="POST", data=form):
            g.user_id, g.user_role = "sa-1", "super_admin"
            supermod.demo_settings.__wrapped__()

        blob = written["demo_settings"]
        assert blob["tutorial_order"] == [
            "demo_tutorial_admin", "demo_tutorial_guru", "demo_tutorial_siswa"]
        # Only real keys, and completion is left to the read path.
        assert blob["role_order"] == ["demo_murid", "demo_guru"]

    def test_a_tampered_order_field_cannot_invent_a_row(self, app, monkeypatch):
        """The field is built by the page, so it is whatever a browser sent."""
        from app.services.demo_settings import order_from_form

        class Form(dict):
            def get(self, k, d=None): return dict.get(self, k, d)

        assert order_from_form(Form({"role_order": "demo_guru,<script>,demo_ghost"}), "roles") \
            == ["demo_guru"]
