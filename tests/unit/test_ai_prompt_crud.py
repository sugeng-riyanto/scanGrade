"""The AI grading prompt is a setting a teacher can actually change.

`Edit Prompts →` on /teacher/ai-settings is an anchor to `#prompts`, and no element
on the page carried that id. Ten built-in prompts shipped in the wizard, one save
route existed that posted the whole array as a blob, and no control anywhere edited
one prompt — so the answer to "can I write a different prompt for each subject?"
was *the page says so and the app cannot*. These tests drive the routes and render
the page, because a control that is present but posts to nothing is the defect.

Two behaviours are the point rather than the plumbing:

* the prompts are the caller's own, and the active id is repaired against the list
  every time it is read — a delete can never leave grading with no criteria;
* a prompt that names a subject is the one used for that subject's exam, which is
  what makes the sentence on the page true.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

TEACHER = "teacher-1"


# ── a supabase shaped like the two tables these routes touch ────────────────

class _Query:
    def __init__(self, store, name):
        self.store = store
        self.name = name
        self.rows = list(store.get(name) or [])

    def select(self, *a, **k):
        return self

    def eq(self, column, value):
        self.rows = [r for r in self.rows if str(r.get(column)) == str(value)]
        return self

    def limit(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)

    def upsert(self, payload):
        """The one write these routes do: the teacher's single settings row."""
        self.store.setdefault("_writes", []).append(dict(payload))
        rows = self.store.setdefault("teacher_ai_settings", [])
        merged = dict(payload)
        if rows:
            rows[0].update(merged)
        else:
            rows.append(merged)
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=[merged]))


class _FakeSupabase:
    def __init__(self, prompts=None, active="default", keys=None):
        row = {"teacher_id": TEACHER, "active_prompt_id": active}
        if prompts is not None:
            row["prompts"] = prompts
        self.store = {
            "teacher_ai_settings": [row] if prompts is not None else [],
            "teacher_ai_keys": keys or [],
        }
        self.writes = []

    def table(self, name):
        query = _Query(self.store, name)
        # `writes` is read by the tests as "did anything get written", so the query
        # object appends through the same list the fake owns.
        self.store.setdefault("_writes", self.writes)
        return query


DEFAAULT_TEMPLATE = "Nilai berdasarkan ketepatan konsep.\nSoal: {question}\n"


def _client(monkeypatch, app, fake):
    from app.routes import teacher as tmod
    from app.utils import auth as authmod

    monkeypatch.setattr(authmod, "_session_for", lambda token: {
        "user_id": TEACHER, "email": "t@x", "name": "t", "role": "guru",
        "school_id": "school-1", "status": "active",
    })
    monkeypatch.setattr(tmod, "get_supabase", lambda: fake)
    # The two plan gates are another feature's test; what is asserted here is that
    # the routes sit *behind* them, which the wiring test below reads. Both are
    # patched where they were imported, not where they are used, or the decorator
    # keeps its own reference and reaches for a real database.
    monkeypatch.setattr(authmod, "check_subscription_write", lambda: (True, ""))
    monkeypatch.setattr("app.decorators.subscription.check_feature_limit",
                        lambda *a, **k: (True, ""))
    monkeypatch.setattr(tmod, "log_activity", lambda *a, **k: None, raising=False)
    client = app.test_client()
    client.set_cookie("access_token", "tok")
    with client.session_transaction() as sess:
        sess["_csrf_token"] = "tok-csrf"
    client.environ_base["HTTP_X_CSRF_TOKEN"] = "tok-csrf"
    return client


def _prompts(fake):
    row = (fake.store.get("teacher_ai_settings") or [{}])[0]
    return json.loads(row["prompts"]) if isinstance(row.get("prompts"), str) else row.get("prompts")


# ── reading the column ──────────────────────────────────────────────────────

class TestReadingTheColumn:
    def test_a_json_string_is_read_as_a_list(self):
        from app.routes.teacher import _prompt_rows
        rows = _prompt_rows({"prompts": json.dumps([
            {"id": "ipa", "label": "IPA", "subject": "Fisika", "template": "x"}])})
        assert rows == [{"id": "ipa", "label": "IPA", "subject": "Fisika", "template": "x"}]

    def test_a_row_with_no_subject_reads_as_any_subject(self):
        from app.routes.teacher import _prompt_rows
        rows = _prompt_rows({"prompts": [{"id": "default", "label": "Default",
                                          "template": "x"}]})
        assert rows and rows[0]["subject"] == ""

    def test_a_row_whose_id_is_not_a_slug_is_dropped(self):
        from app.routes.teacher import _prompt_rows
        rows = _prompt_rows({"prompts": [
            {"id": "has space", "label": "x", "template": "y"},
            {"id": "ok", "label": "x", "template": "y"},
            {"label": "no id", "template": "y"},
        ]})
        assert [r["id"] for r in rows] == ["ok"]

    def test_a_missing_column_reads_as_no_prompts(self):
        from app.routes.teacher import _prompt_rows
        assert _prompt_rows({}) == []
        assert _prompt_rows({"prompts": "not json"}) == []


class TestTheActiveIdIsRepaired:
    def test_an_active_id_naming_a_gone_prompt_falls_back_to_the_first(self):
        from app.routes.teacher import _prompts_and_active
        fake = _FakeSupabase(prompts=[{"id": "a", "label": "A", "template": "x"},
                                      {"id": "b", "label": "B", "template": "y"}],
                             active="deleted")
        prompts, active = _prompts_and_active(fake, TEACHER)
        assert [p["id"] for p in prompts] == ["a", "b"]
        assert active == "a"

    def test_no_prompts_at_all_answers_an_empty_active_not_a_crash(self):
        from app.routes.teacher import _prompts_and_active
        prompts, active = _prompts_and_active(_FakeSupabase(), TEACHER)
        assert prompts == [] and active == ""


# ── the routes ──────────────────────────────────────────────────────────────

class TestAdding:
    def test_a_prompt_is_added_with_its_own_id(self, monkeypatch, app):
        fake = _FakeSupabase(prompts=[{"id": "default", "label": "Default", "template": "x"}],
                             active="default")
        client = _client(monkeypatch, app, fake)

        resp = client.post("/teacher/ai-settings/prompt/add", data={
            "label": "Esai Sejarah", "subject": "Sejarah", "template": "Nilai tahun dan tokoh.",
        })

        assert resp.status_code in (302, 303), resp.data[:300]
        rows = _prompts(fake)
        assert [r["id"] for r in rows] == ["default", "esai-sejarah"]
        assert rows[1]["subject"] == "Sejarah"
        assert fake.store["teacher_ai_settings"][0]["active_prompt_id"] == "default"

    def test_the_same_label_twice_gets_a_distinct_id(self, monkeypatch, app):
        fake = _FakeSupabase(prompts=[{"id": "default", "label": "Default", "template": "x"}])
        client = _client(monkeypatch, app, fake)

        client.post("/teacher/ai-settings/prompt/add",
                    data={"label": "Ketat", "template": "a"})
        client.post("/teacher/ai-settings/prompt/add",
                    data={"label": "Ketat", "template": "b"})

        assert [r["id"] for r in _prompts(fake)] == ["default", "ketat", "ketat-2"]

    def test_a_prompt_with_no_text_is_refused(self, monkeypatch, app):
        fake = _FakeSupabase(prompts=[{"id": "default", "label": "Default", "template": "x"}])
        client = _client(monkeypatch, app, fake)

        client.post("/teacher/ai-settings/prompt/add", data={"label": "Kosong", "template": "  "})

        assert [r["id"] for r in _prompts(fake)] == ["default"]
        assert fake.writes == []

    def test_a_prompt_over_the_cap_is_refused(self, monkeypatch, app):
        from app.services.ai_prompt import MAX_TEACHER_PROMPT
        fake = _FakeSupabase(prompts=[{"id": "default", "label": "Default", "template": "x"}])
        client = _client(monkeypatch, app, fake)

        client.post("/teacher/ai-settings/prompt/add", data={
            "label": "Panjang", "template": "a" * (MAX_TEACHER_PROMPT + 1)})

        assert fake.writes == []

    def test_the_first_prompt_becomes_active_when_there_was_none(self, monkeypatch, app):
        fake = _FakeSupabase(prompts=[])
        client = _client(monkeypatch, app, fake)

        client.post("/teacher/ai-settings/prompt/add",
                    data={"label": "Pertama", "template": "nilai"})

        assert fake.store["teacher_ai_settings"][0]["active_prompt_id"] == "pertama"


class TestEditing:
    def test_the_text_and_the_subject_are_saved(self, monkeypatch, app):
        fake = _FakeSupabase(prompts=[{"id": "esai", "label": "Esai",
                                       "subject": "", "template": "lama"}],
                             active="esai")
        client = _client(monkeypatch, app, fake)

        client.post("/teacher/ai-settings/prompt/esai/save", data={
            "label": "Esai Baru", "subject": "Biologi", "template": "baru"})

        rows = _prompts(fake)
        assert rows[0] == {"id": "esai", "label": "Esai Baru",
                           "subject": "Biologi", "template": "baru"}

    def test_editing_a_prompt_that_does_not_exist_writes_nothing(self, monkeypatch, app):
        fake = _FakeSupabase(prompts=[{"id": "esai", "label": "Esai", "template": "lama"}])
        client = _client(monkeypatch, app, fake)

        client.post("/teacher/ai-settings/prompt/ghost/save",
                    data={"label": "x", "template": "y"})

        assert fake.writes == []


class TestActivating:
    def test_the_chosen_prompt_is_the_active_one(self, monkeypatch, app):
        fake = _FakeSupabase(prompts=[{"id": "a", "label": "A", "template": "x"},
                                      {"id": "b", "label": "B", "template": "y"}],
                             active="a")
        client = _client(monkeypatch, app, fake)

        client.post("/teacher/ai-settings/prompt/b/activate")

        assert fake.store["teacher_ai_settings"][0]["active_prompt_id"] == "b"


class TestDeleting:
    def test_deleting_the_active_prompt_moves_active_to_a_sibling(self, monkeypatch, app):
        fake = _FakeSupabase(prompts=[{"id": "a", "label": "A", "template": "x"},
                                      {"id": "b", "label": "B", "template": "y"}],
                             active="a")
        client = _client(monkeypatch, app, fake)

        client.post("/teacher/ai-settings/prompt/a/delete")

        assert [r["id"] for r in _prompts(fake)] == ["b"]
        assert fake.store["teacher_ai_settings"][0]["active_prompt_id"] == "b"

    def test_deleting_the_last_prompt_is_refused(self, monkeypatch, app):
        fake = _FakeSupabase(prompts=[{"id": "only", "label": "Only", "template": "x"}],
                             active="only")
        client = _client(monkeypatch, app, fake)

        client.post("/teacher/ai-settings/prompt/only/delete")

        assert [r["id"] for r in _prompts(fake)] == ["only"]
        assert fake.writes == []

    def test_deleting_an_unknown_id_is_refused(self, monkeypatch, app):
        fake = _FakeSupabase(prompts=[{"id": "a", "label": "A", "template": "x"}])
        client = _client(monkeypatch, app, fake)

        client.post("/teacher/ai-settings/prompt/ghost/delete")

        assert fake.writes == []


# ── the subject is what makes the sentence on the page true ─────────────────

class TestSubjectSelectsTheCriteria:
    SETTINGS = {"active_prompt_id": "default", "prompts": [
        {"id": "default", "label": "Default", "subject": "", "template": "UMUM"},
        {"id": "mat", "label": "Matematika", "subject": "Matematika", "template": "MAT"},
    ]}

    def test_the_subject_prompt_wins_over_the_active_one(self):
        from app.services.ai_service import _get_active_prompt
        assert _get_active_prompt(self.SETTINGS, subject="Matematika") == "MAT"

    def test_the_match_ignores_case_and_surrounding_space(self):
        from app.services.ai_service import _get_active_prompt
        assert _get_active_prompt(self.SETTINGS, subject="  matematika ") == "MAT"

    def test_a_subject_nothing_declares_uses_the_active_prompt(self):
        from app.services.ai_service import _get_active_prompt
        assert _get_active_prompt(self.SETTINGS, subject="Sejarah") == "UMUM"

    def test_no_subject_asked_uses_the_active_prompt(self):
        from app.services.ai_service import _get_active_prompt
        assert _get_active_prompt(self.SETTINGS) == "UMUM"


# ── the page ────────────────────────────────────────────────────────────────

@pytest.fixture
def page(monkeypatch, app):
    fake = _FakeSupabase(prompts=[
        {"id": "default", "label": "Default (Semua Mapel)", "subject": "", "template": "umum"},
        {"id": "ipa", "label": "IPA", "subject": "Fisika", "template": "ipa"},
    ], active="default")
    client = _client(monkeypatch, app, fake)
    return client.get("/teacher/ai-settings")


class TestThePageHasTheControl:
    def test_the_link_lands_on_a_section_that_exists(self, page):
        assert page.status_code == 200
        html = page.get_data(as_text=True)
        assert 'id="prompts"' in html, "the Edit Prompts link still points at nothing"
        assert 'href="#prompts"' in html

    def test_every_prompt_gets_its_own_edit_form(self, page):
        html = page.get_data(as_text=True)
        for pid in ("default", "ipa"):
            assert f'action="/teacher/ai-settings/prompt/{pid}/save"' in html
        assert html.count('name="template"') >= 3   # two prompts 	+ the add form

    def test_the_add_form_posts_to_the_add_route(self, page):
        assert 'action="/teacher/ai-settings/prompt/add"' in page.get_data(as_text=True)

    def test_a_short_list_is_not_replaced_by_the_built_ins(self, monkeypatch, app):
        """Two prompts is a choice a teacher made, not an old row to upgrade.

        The page used to upgrade any row holding fewer than three prompts back to
        the ten built-ins. That was harmless while nothing could delete a prompt
        and is a delete button that undoes itself now.
        """
        fake = _FakeSupabase(prompts=[
            {"id": "a", "label": "A", "template": "x"},
            {"id": "b", "label": "B", "template": "y"},
        ], active="a")
        client = _client(monkeypatch, app, fake)
        client.get("/teacher/ai-settings")
        assert [r["id"] for r in _prompts(fake)] == ["a", "b"]
        assert fake.writes == []

    def test_the_active_prompt_is_named_and_the_other_is_not_marked(self, page):
        html = page.get_data(as_text=True)
        marker = html.split('data-ai-prompt="ipa"', 1)[1].split("</div>", 3)
        assert 'data-ai-prompt="default"' in html
        # The active badge is on the first card only; the literal is bound, so the
        # check is that the "Use" action exists for the inactive one and not for it.
        assert 'action="/teacher/ai-settings/prompt/ipa/activate"' in html
        assert 'action="/teacher/ai-settings/prompt/default/activate"' not in html
        assert marker  # the card is there

    def test_every_post_form_on_the_page_carries_a_csrf_field(self, page):
        html = page.get_data(as_text=True)
        for chunk in html.split('<form')[1:]:
            body = chunk.split('</form>')[0]
            if 'method="POST"' not in body:
                continue          # `<form method="dialog">` is the timezone sheet
            assert 'name="_csrf_token"' in body, "a prompt form has no CSRF field"


class TestTheRoutesAreBehindThePlan:
    def test_every_prompt_write_route_is_gated_on_ai_grading(self):
        import inspect
        from app.routes import teacher as tmod

        for name in ("ai_prompt_add", "ai_prompt_save", "ai_prompt_activate",
                     "ai_prompt_delete", "ai_save_prompt", "ai_reset_prompt"):
            src = inspect.getsource(getattr(tmod, name))
            # The decorators are above the def, so read the enclosing source of the
            # module around it rather than the function body.
        module = inspect.getsource(tmod)
        for route in ('"/ai-settings/prompt/add"', '"/ai-settings/prompt/<prompt_id>/save"',
                      '"/ai-settings/prompt/<prompt_id>/activate"',
                      '"/ai-settings/prompt/<prompt_id>/delete"',
                      '"/ai-settings/save-prompt"', '"/ai-settings/reset-prompt"'):
            start = module.index(route)
            # The decorators stack *below* the route line, so the gate is read after it.
            block = module[start:start + 400]
            assert 'require_subscription("ai_grading")' in block, (
                f"{route} is not gated on the AI-grading entitlement")
