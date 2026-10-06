"""A distribution can be previewed before it is saved.

The weight matrix is a policy, and the admin typing 40/60 into it is guessing at
the effect: what mark does a pupil with 80 on one component and 90 on the other
actually carry out? The answer only exists once a distribution is saved —
unless the page computes it from a sample set of scores while the admin is still
looking at the fields.

Two things are guarded here:

* ``sgPreviewFinal`` — the page's own arithmetic, run through node. It must be
  the **same rule** ``grade_weighting.compute`` keeps: a component with no sample
  counts as 0, a component with no weight is ignored, and a distribution with no
  weights at all falls back to the simple mean. A preview that disagrees with the
  server is worse than no preview.
* the wiring — the card exists, it follows whichever distribution is selected
  (the school default or a subject's own row) **live**, reads the fields before
  they are saved, and never posts on its own.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WEIGHTS_HTML = ROOT / "app" / "templates" / "admin_sekolah" / "grade_weights.html"

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="needs node to run the page's own rule")

#: The standalone function the template defines, so it can be run on its own.
PREVIEW = re.compile(r"function sgPreviewFinal\([^)]*\) \{(.*?)\r?\n\}", re.S)


def _finals(pairs: list) -> list:
    """Run the page's own preview rule over ``[(weights, scores), …]``."""
    html = WEIGHTS_HTML.read_text(encoding="utf-8")
    match = PREVIEW.search(html)
    assert match, "the page no longer defines the preview's arithmetic"
    script = (
        "const src = " + json.dumps(match.group(0)) + ";\n"
        "eval(src);\n"
        "const cases = " + json.dumps([[w, s] for w, s in pairs]) + ";\n"
        "console.log(JSON.stringify(cases.map(function (c) {"
        " return sgPreviewFinal(c[0], c[1]); })));\n"
    )
    done = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip())


def _preview_methods() -> str:
    """The preview-related methods of the Alpine component, as source."""
    html = WEIGHTS_HTML.read_text(encoding="utf-8")
    names = ("previewWeights", "previewMark", "setSample")
    chunks = []
    for name in names:
        marker = f"{name}("
        assert marker in html, f"the page has no {name} method"
        chunks.append(html.split(marker, 1)[1].split("\n        },", 1)[0])
    return "\n".join(chunks)


def _preview_card() -> str:
    """The preview card's markup, delimited by its own hook and the matrix card."""
    html = WEIGHTS_HTML.read_text(encoding="utf-8")
    assert "data-weight-preview" in html, "there is no preview card"
    return html.split("data-weight-preview", 1)[1].split("Matriks Bobot per Mapel", 1)[0]


class TestThePreviewArithmetic:
    @needs_node
    def test_a_missing_component_counts_as_zero(self):
        """The server's policy: an unsat component is 0, not a renormalised weight."""
        out = _finals([({"a": 40, "b": 60}, {"a": 80})])[0]
        assert out["mode"] == "weighted"
        assert out["final"] == 32.0, (
            "a component with no sample must contribute 0, the way compute() reads it")

    @needs_node
    def test_a_full_sample_is_the_weighted_sum(self):
        out = _finals([({"a": 40, "b": 60}, {"a": 80, "b": 90})])[0]
        assert out["final"] == 86.0
        assert out["total"] == 100

    @needs_node
    def test_no_weights_falls_back_to_the_simple_mean(self):
        out = _finals([({}, {"a": 80, "b": 90})])[0]
        assert out["mode"] == "simple"
        assert out["final"] == 85.0, (
            "a school with no weights counts every score — the preview must too")

    @needs_node
    def test_no_weights_and_no_scores_is_no_mark(self):
        out = _finals([({}, {})])[0]
        assert out["final"] is None, "an empty sample must read as no mark, not 0"

    @needs_node
    def test_a_zero_weight_is_not_a_component(self):
        """A 0% component is outside the policy, exactly as compute() filters it."""
        out = _finals([({"a": 0, "b": 100}, {"a": 100, "b": 50})])[0]
        assert out["final"] == 50.0
        assert [d["component_id"] for d in out["detail"]] == ["b"]

    @needs_node
    def test_a_score_outside_the_policy_is_ignored(self):
        """A sample for a component the distribution does not weight counts for nothing."""
        out = _finals([({"a": 100}, {"a": 70, "z": 100})])[0]
        assert out["final"] == 70.0

    @needs_node
    def test_the_detail_names_each_weighted_component(self):
        out = _finals([({"a": 40, "b": 60}, {"a": 80, "b": 90})])[0]
        assert [(d["component_id"], d["weight"], d["average"]) for d in out["detail"]] == [
            ("a", 40, 80), ("b", 60, 90)]
        empty = _finals([({"a": 100, "b": 0}, {"a": 60})])[0]
        assert empty["detail"][0]["average"] == 60


class TestThePreviewIsWiredBeforeSaving:
    def test_the_page_has_a_preview_card(self):
        html = WEIGHTS_HTML.read_text(encoding="utf-8")
        assert "data-weight-preview" in html, (
            "there is no preview card, so a distribution still has to be saved to be seen")
        assert "previewMark()" in html, "the card does not render the computed mark"

    def test_it_follows_the_selected_distribution_live(self):
        body = _preview_methods()
        assert "this.defaults" in body and "this.row(" in body, (
            "the preview must read the school default or the subject's own row")
        assert "previewSubject" in WEIGHTS_HTML.read_text(encoding="utf-8"), (
            "there is no way to choose which distribution to preview")

    def test_it_never_posts_on_its_own(self):
        assert "post(" not in _preview_methods(), (
            "the preview writes to the server — it is supposed to show a mark "
            "before anything is committed")

    def test_it_reads_the_unsaved_fields(self):
        """A preview of the *saved* policy would be useless while typing."""
        card = _preview_card()
        assert "setSample(" in card, "the sample inputs are not bound to the preview"
        assert "previewScores[" in card, (
            "the sample inputs are not seeded from the preview's own scores")

    def test_the_copy_is_bilingual(self):
        card = _preview_card()
        assert re.search(r"t\('[^']+','[^']+'\)", card), (
            "the preview card carries no bilingual pair, so its copy cannot be translated")
        assert "Nilai Akhir" in card and "Final Mark" in card
