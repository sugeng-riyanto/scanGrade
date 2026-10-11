"""One answer to "what is an option", and one to "is this key still a key".

Fase 3 of the exam-authoring work: the builder and the answer-key page drew the
same five option buttons from two hand-written `['A','B','C','D','E']` arrays,
and the answer-key page showed a stored key as if it were readable without asking
whether the question could still read it. Both are how two screens about one
paper drift apart.

Three things are pinned here:

* **The letters are one definition.** `question_types.CHOICE_OPTIONS` is the
  server's answer; `item_analysis.CHOICE_OPTIONS` aliases it and does not list it
  again; `vocabulary()["options"]` serves it; the builder and the answer-key page
  read it rather than spelling it out.
* **A stored key has three states, not two.** `key_state` says whether the app can
  mark the question from it (`set`), whether nothing is stored (`empty`), or
  whether something is stored that this question can no longer read (`stale`) —
  the `"B"` a question kept from when it was multiple choice, on the question that
  has since become a matching one.
* **The page never destroys a mark on its own.** A stale key is seeded empty and
  warned about; only the questions a teacher actually edited are posted, so an
  untouched Save writes nothing and leaves the old value alone.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services import item_analysis as ia
from app.services import question_types as qt

ROOT = Path(__file__).resolve().parents[2]
ROUTES = ROOT / "app" / "routes" / "teacher.py"
ANSWER_KEYS = ROOT / "app" / "templates" / "teacher" / "answer_keys.html"
FORM = ROOT / "app" / "templates" / "teacher" / "exam_form.html"
ITEM_ANALYSIS = ROOT / "app" / "services" / "item_analysis.py"


# ── the one definition of an option ──────────────────────────────────────────

def test_the_canonical_letters_are_the_five_the_sheet_prints():
    assert qt.CHOICE_OPTIONS == ("A", "B", "C", "D", "E")


def test_item_analysis_aliases_the_canonical_letters():
    """Not a second list. `is` holds because an alias and a copy are the whole
    difference between \"one answer\" and \"two answers that can drift.\""""
    assert ia.CHOICE_OPTIONS is qt.CHOICE_OPTIONS


def test_item_analysis_does_not_list_the_letters_again():
    source = ITEM_ANALYSIS.read_text(encoding="utf-8")
    assert 'CHOICE_OPTIONS = qt.CHOICE_OPTIONS' in source
    assert "CHOICE_OPTIONS = (\"A\", \"B\", \"C\", \"D\", \"E\")" not in source


def test_the_vocabulary_serves_the_letters():
    vocab = qt.vocabulary()
    assert vocab["options"] == list(qt.CHOICE_OPTIONS)


# ── what a stored key is ─────────────────────────────────────────────────────

CHOICE = qt.MCQ


@pytest.mark.parametrize("key", ["A", "B", "E", ["A", "B"], "bonus"])
def test_a_key_the_question_can_read_is_set(key):
    assert qt.key_state(CHOICE, key) == qt.KEY_SET


@pytest.mark.parametrize("key", [None, "", []])
def test_nothing_stored_is_empty(key):
    assert qt.key_state(CHOICE, key) == qt.KEY_EMPTY


def test_a_blank_object_on_a_question_that_reads_objects_is_empty():
    assert qt.key_state("match", {}) == qt.KEY_EMPTY


def test_a_letter_on_a_matching_question_is_stale():
    """The shape of a structural edit: a question that was multiple choice becomes
    a matching one, and its `"B"` is left behind. A page that shows it as a key
    lies; one that deletes it destroys a mark somebody set on purpose."""
    assert qt.key_state("match", "B") == qt.KEY_STALE


def test_a_blank_payload_of_the_questions_own_shape_is_empty_not_stale():
    assert qt.key_state("match", {"pairs": []}) == qt.KEY_EMPTY
    assert qt.key_state("order", {"order": []}) == qt.KEY_EMPTY


def test_an_essay_has_no_key_to_be_stale_about():
    assert qt.key_state("essay", "essay") == qt.KEY_EMPTY
    assert qt.key_state("essay", "B") == qt.KEY_EMPTY


def test_the_report_matches_the_grader():
    """`key_state` is built on `key_has_answer`, so a key it calls `set` is one the
    grader marks from — the two cannot disagree about the same paper."""
    for qtype, key in [(CHOICE, "C"), ("truefalse", "true"), ("match", {"pairs": [{"l": "a", "r": "b"}]})]:
        if qt.question_kind(qtype) == qt.KIND_ESSAY:
            continue
        assert (qt.key_state(qtype, key) == qt.KEY_SET) == qt.key_has_answer(qtype, key)


# ── the route ────────────────────────────────────────────────────────────────

def _route_source() -> str:
    return ROUTES.read_text(encoding="utf-8")


def test_the_route_serves_the_option_letters():
    src = _route_source()
    assert "option_letters=list(CHOICE_OPTIONS)" in src


def test_the_route_reports_each_questions_key_state():
    src = _route_source()
    assert "\"key_state\": key_state(qtype, k)" in src
    assert "\"is_stale\": key_state(qtype, k) == KEY_STALE" in src


def test_an_untouched_save_writes_nothing():
    """The write is behind `answer_key != stored`, so opening the page and clicking
    Save is a read — it does not rewrite the key or re-grade every submission."""
    src = _route_source()
    assert "if answer_key != stored:" in src


def test_the_route_still_merges_rather_than_overwrites():
    """The guard that stopped this page writing `essay` over a matching question's
    pairs must survive the write-nothing change."""
    src = _route_source()
    assert "if is_objective(qtype) and question_kind(qtype) != KIND_CHOICE:" in src


# ── the answer-key page ──────────────────────────────────────────────────────

def _answer_keys_template() -> str:
    return ANSWER_KEYS.read_text(encoding="utf-8")


def test_the_page_draws_the_served_letters():
    html = _answer_keys_template()
    assert "{% for opt in option_letters %}" in html
    assert "{% for opt in ['A','B','C','D','E'] %}" not in html


def test_the_page_warns_about_a_stale_key():
    html = _answer_keys_template()
    assert "{% if q.is_stale %}" in html
    assert 'data-key-stale="{{ q.index }}"' in html


def test_the_page_seeds_a_stale_key_empty():
    """A stale key must not appear in the editor as a selected option."""
    html = _answer_keys_template()
    init = html.split("init() {", 1)[1].split("markDirty", 1)[0]
    assert "{% elif q.is_stale %}" in init
    stale_branch = init.split("{% elif q.is_stale %}", 1)[1].split("{% elif", 1)[0]
    assert this_empty(stale_branch), "a stale key must seed this.keys[idx] = []"


def this_empty(branch: str) -> bool:
    return "this.keys[{{ q.index }}] = [];" in branch


def test_the_page_posts_only_the_questions_edited():
    html = _answer_keys_template()
    assert "dirty: {}" in html
    assert "markDirty(idx) { this.dirty[idx] = true; }" in html
    getjson = html.split("getJson() {", 1)[1].split("\n        }", 1)[0]
    assert "if (!this.dirty[k]) return;" in getjson


def test_the_page_marks_both_edits_dirty():
    html = _answer_keys_template()
    toggle = html.split("toggle(idx, opt) {", 1)[1].split("},", 1)[0]
    assert "this.markDirty(idx);" in toggle
    bonus = html.split("toggleBonus(idx) {", 1)[1].split("},", 1)[0]
    assert "this.markDirty(idx);" in bonus


# ── the builder ──────────────────────────────────────────────────────────────

def test_the_builder_reads_the_served_letters():
    html = FORM.read_text(encoding="utf-8")
    assert "optionLetters: SG_QT.options" in html
    assert "<template x-for=\"opt in ['A','B','C','D','E']\">" not in html
    assert "<template x-for=\"opt in optionLetters\">" in html


# ── the builder's structural-change warning ──────────────────────────────────


def test_the_builder_keeps_the_shape_a_question_came_with():
    """`@change` fires after the model moved, so the shape being left has to be
    remembered — on a loaded question and on a new one alike."""
    html = FORM.read_text(encoding="utf-8")
    assert "q._kind = kind;   // the shape a later type change would leave" in html
    blank = html.split("blankQuestion(type) {", 1)[1].split("return q;", 1)[0]
    assert "q._kind = kind;" in blank


def test_the_warning_only_fires_when_a_filled_key_would_be_dropped():
    html = FORM.read_text(encoding="utf-8")
    body = html.split("onTypeChange(i) {", 1)[1].split("onBonusToggle", 1)[0]
    assert "const wasKind = q._kind || kind;" in body
    assert "if (wasKind !== kind && this.keyFilledFor(q, wasKind)) {" in body
    assert "this.keyWarnings[i] = true;" in body
    assert "delete this.keyWarnings[i];" in body


def test_the_warning_knows_what_a_filled_key_is_per_kind():
    html = FORM.read_text(encoding="utf-8")
    body = html.split("keyFilledFor(q, kind) {", 1)[1].split("dismissKeyWarning", 1)[0]
    for kind in ("'choice'", "'truefalse'", "'match'", "'dragdrop'", "'ordering'", "'pgk'"):
        assert kind in body, f"`keyFilledFor` must know {kind}"


def test_the_builder_names_the_question_it_would_affect():
    html = FORM.read_text(encoding="utf-8")
    assert ":data-key-warning=\"i\"" in html
    assert "dismissKeyWarning(i)" in html
    # The count is placed *between* two bilingual pairs, never inside one, so both
    # halves stay countable by the i18n gate.
    banner = html.split("data-key-warning", 1)[1].split("dismissKeyWarning", 1)[0]
    assert "(i + 1)" in banner
    assert "+ ' ' + (i + 1) + ' ' + t(" in banner
