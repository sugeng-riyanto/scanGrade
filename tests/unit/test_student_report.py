"""One learner's page: the arithmetic, the reasons, and what a stranger may read.

Three properties decide whether this page is safe to hand to a family, and each
one is asserted here rather than trusted:

* **the learner is addressed by id.** Two learners in one class can share a full
  name, and a page keyed on a name would confidently describe the wrong child.
  Every test that needs a learner passes an id, and one test proves that a shared
  name resolves to two different pages.
* **the credits are `item_analysis`'s own.** The page adds reasons and a level
  breakdown; it must not grade the sheet a second time, so a question's earned
  marks here are the same share the item statistics were built from.
* **the answer key is teacher-only.** `with_key=False` is what a share link
  renders, and it has to be empty *in the payload*, not merely hidden in markup —
  a key that reaches the browser is a key a student can read.

The page is rendered the way the route renders it (a crafted context through the
real Jinja environment), because the failure mode these tests guard against is a
template that stops carrying a sentence, which a payload test cannot see.
"""
from __future__ import annotations

import contextlib
import pathlib
import re

import pytest

from app.services import exam_report as er
from app.services import item_analysis
from app.services import learner_report as lr
from app.services import question_types as qt

TEMPLATE = (pathlib.Path(__file__).resolve().parents[2]
            / "app" / "templates" / "teacher" / "analysis_student.html")

EXAM = {
    "id": "exam-1",
    "title": "Mid Semester 1",
    "subject": "IPA",
    "class_name": "VIII-A",
    "school_name": "SMP Negeri 1 ScanGrade",
    "teacher_name": "Budi",
    "passing_score": 60,
    "total_questions": 5,
    "question_weights": {"0": 20.0, "1": 20.0, "2": 20.0, "3": 20.0, "4": 20.0},
    # A question with no key at all: the app's own grader scores it wrong, and this
    # page must say "no key" instead — which is a different sentence to a family.
    "answer_key": {"0": "A", "2": "true", "3": "essay",
                   "4": {"pairs": [{"left": "air", "right": "cair"}],
                             "extra": ["padat"]}},
    "question_types": {"0": "mcq", "1": "mcq", "2": "true_false",
                       "3": "essay", "4": "match"},
}

#: Two of these share a name on purpose. That is the fact this page is built around.
LEARNERS = [
    # id, name, answers, teacher marks, final score
    ("stu-1", "Bella Safira",
     {"0": "A", "2": "true", "4": {"pairs": [{"left": "air", "right": "cair"}]}},
     {"3": 100}, 100.0),
    ("stu-2", "Ahmad Pratama", {"0": "B", "2": "false"}, {}, 20.0),
    ("stu-3", "Ahmad Pratama", {"2": "true"}, {"3": 50}, 60.0),
]


def submissions(rows=None):
    out = []
    for sid, name, answers, scores, final in (rows or LEARNERS):
        out.append({
            "student_id": sid, "student_name": name, "answers": answers,
            "teacher_feedback": {"scores": scores}, "final_score": final,
        })
    return out


def analysis_of(rows=None, exam=None):
    return item_analysis.analyse(exam or EXAM, submissions(rows))


def learner_of(sid, rows=None, exam=None, **kw):
    payload = exam if exam is not None else EXAM
    return er.learner(analysis_of(rows, payload), payload, sid, **kw)


# ── identity ─────────────────────────────────────────────────────────────────

class TestWhoThePageIsAbout:
    def test_a_learner_is_addressed_by_id(self):
        who = learner_of("stu-2")
        assert who["who"]["name"] == "Ahmad Pratama"
        assert who["who"]["student_id"] == "stu-2"

    def test_two_learners_who_share_a_name_are_two_pages(self):
        """The reason this is keyed by id, in one assertion.

        Keyed by name, the second Ahmad would have been described with the first
        one's paper — the wrong child, in a document a school files.
        """
        first, third = learner_of("stu-2"), learner_of("stu-3")
        assert first["who"]["name"] == third["who"]["name"]
        assert first["who"]["pct"] != third["who"]["pct"]
        assert first["who"]["student_id"] != third["who"]["student_id"]

    def test_an_unknown_id_is_not_a_learner(self):
        assert learner_of("stu-9") is None
        assert learner_of("") is None

    def test_a_paper_with_no_id_gets_no_page(self):
        """A submission whose row could not name a profile is a paper this app
        cannot address — and saying so is better than matching on a name."""
        rows = [(None, "Tanpa Id", {"0": "A"}, {}, 20.0)]
        analysis = analysis_of(rows)
        assert analysis.people, "the fixture has no paper to address"
        assert er.learner(analysis, EXAM, "stu-1") is None
        assert er.learner(analysis, EXAM, "Tanpa Id") is None


# ── the credits are the analyser's own ───────────────────────────────────────

#: Two pairs, and a paper that got one of them. The key and the answer are
#: deliberately different strings, so "the learner's answer was echoed back" and
#: "the key was printed" are distinguishable on the page.
TWO_PAIR_EXAM = dict(EXAM, answer_key={
    "0": "A", "2": "true", "3": "essay",
    "4": {"pairs": [{"left": "air", "right": "cair"},
                     {"left": "es", "right": "padat"}],
          "extra": ["gas"]}})

#: The scheme flag. `question_types.part_factor` can always compute a share, but
#: `item_analysis` only *asks* it for an exam carrying a scheme: an exam marked
#: before schemes existed keeps the all-or-nothing marks already returned to
#: students, and saving it in the builder must not silently re-score them.
PARTIAL_SCHEME = dict(TWO_PAIR_EXAM, question_weights={
    **{str(i): 20.0 for i in range(5)}, "_scheme": {"partial": True}})

HALF_RIGHT = ("stu-x", "Uji",
              {"4": {"pairs": [{"left": "air", "right": "cair"},
                                {"left": "es", "right": "gas"}]}},
              {}, 0.0)

#: Marks that do not divide evenly — what a paper scaled to 100 across six questions
#: looks like on the wire, and what the live exam this page was built for carries
#: (16.666… each). Summing the *rounded* parts and the exact parts gives two answers
#: that differ by a hundredth, and this is the only fixture that can tell them apart.
UNEVEN_EXAM = dict(EXAM, question_weights={str(i): 100 / 6 for i in range(5)})


class TestTheCredits:
    def test_a_full_credit_question_pays_the_whole_question(self):
        who = learner_of("stu-1")
        first = who["questions"][0]
        assert first["state"] == "full"
        assert first["earned"] == 20.0
        assert who["totals"]["credited"] == 80.0
        # Four of this paper's five questions are markable — Q2 has no key — and
        # the learner was awarded all four. Credited and scoreable are one total
        # read two ways, so a learner with everything right must top both out.
        assert who["totals"]["credited"] == who["totals"]["possible"]

    def test_a_wrong_objective_answer_pays_nothing(self):
        second = learner_of("stu-2")["questions"][0]
        assert second["state"] == "none"
        assert second["earned"] == 0.0

    def test_a_partly_correct_match_pays_part_of_the_question(self):
        """The class's item statistics use the app's own part credit — the share of
        the key's pairs the learner got — so this page has to as well. A page that
        rounded it to right/wrong would disagree with the table it was opened from.

        Two pairs, one right: half the question, and the *marks* are halved rather
        than the state being named without a number.
        """
        who = learner_of("stu-x", [HALF_RIGHT], exam=PARTIAL_SCHEME)
        question = who["questions"][4]
        assert question["state"] == "part"
        assert question["earned"] == 10.0
        assert 0 < question["earned"] < question["marks"]

    def test_the_page_never_uses_the_grader_the_statistics_did_not_use(self):
        """The same answer, on the same question, on an exam carrying no scheme:
        all-or-nothing, so nothing is paid and the page says "Incorrect". Two
        graders is how a page comes to disagree with the table it was opened from,
        and this is the assertion that keeps them one."""
        who = learner_of("stu-x", [HALF_RIGHT], exam=TWO_PAIR_EXAM)
        assert who["questions"][4]["state"] == "none"
        assert who["questions"][4]["earned"] == 0.0

    def test_the_shares_are_the_ones_the_statistics_used(self):
        analysis = analysis_of()
        person = next(p for p in analysis.people if p.student_id == "stu-1")
        who = er.learner(analysis, EXAM, "stu-1")
        for index, item in enumerate(analysis.items):
            share = person.shares[index]
            earned = who["questions"][index]["earned"]
            if share is not None:
                assert earned == pytest.approx(round(share * item.marks, 2))
            # A share the analyser did not give is never a mark: only a question
            # the learner could have earned on carries a number at all.
            elif who["questions"][index]["state"] not in er.COUNTED_STATES:
                assert earned is None

    def test_the_level_table_totals_the_same_marks_as_the_card(self):
        """Three readings of one number — the marks column, the totals card, and the
        level table — and the first version disagreed with itself: the level table
        summed the exact per-question marks while the card summed the rounded ones,
        so a paper of 16.666…-mark questions showed **50.0** in the level row and
        **50.01** in the card above it, both claiming to be the same marks."""
        for sid in ("stu-1", "stu-2", "stu-3"):
            who = learner_of(sid, exam=UNEVEN_EXAM)
            assert round(sum(l["possible"] for l in who["levels"]), 2) == \
                who["totals"]["possible"], sid
            assert round(sum(l["marks"] for l in who["levels"]), 2) == \
                who["totals"]["credited"], sid
            scored = [q for q in who["questions"] if q["earned"] is not None]
            assert round(sum(q["marks"] for q in scored), 2) == \
                who["totals"]["possible"], sid

    def test_the_marks_column_adds_up_to_its_own_total(self):
        """The one verification this page invites: a reader can add the marks
        column and compare it with the card above. A blank printed as `—` while the
        total counted it as zero is how the two stopped agreeing, so the column is
        asserted against the totals rather than one row at a time."""
        for sid in ("stu-1", "stu-2", "stu-3"):
            who = learner_of(sid)
            scored = [q for q in who["questions"] if q["earned"] is not None]
            assert sum(q["earned"] for q in scored) == pytest.approx(
                who["totals"]["credited"]), sid
            assert sum(q["marks"] for q in scored) == pytest.approx(
                who["totals"]["possible"]), sid

    def test_an_essay_the_teacher_has_not_marked_is_not_a_zero(self):
        who = learner_of("stu-2")
        essay = who["questions"][3]
        assert essay["state"] == "unmarked"
        assert essay["earned"] is None
        assert who["counts"]["unmarked"] == 1
        # ...and it is not in the denominator either: a level share that counted an
        # unmarked essay would report a weakness the learner has no way to fix.
        assert 20.0 not in [level["possible"] for level in who["levels"]]

    def test_a_question_with_no_key_is_named_as_such(self):
        who = learner_of("stu-2")
        unkeyed = who["questions"][1]
        assert unkeyed["state"] == "unkeyed"
        assert unkeyed["earned"] is None
        assert who["counts"]["unkeyed"] == 1

    def test_a_blank_is_a_blank_and_counts_against_the_learner(self):
        """The opposite of the class table on purpose: the class share is a mean
        over the papers that answered, and a family reading their own child's page
        is owed the denominator their child actually faced.

        Scoreable here is three of five questions — Q2 has no key and Q4 is an
        essay this learner's teacher has not marked — and the blanks are inside
        that three rather than excused from it.
        """
        who = learner_of("stu-2")
        blank = who["questions"][4]
        assert blank["state"] == "blank"
        assert blank["earned"] == 0.0, "a blank paid nothing and must print nothing"
        assert who["totals"]["possible"] == 60.0
        assert who["counts"]["blank"] >= 1

    def test_every_state_the_page_can_name_is_reachable(self):
        """Six states, and a mutation cannot quietly make one of them dead: each is
        produced here by the data that causes it."""
        seen = set()
        for sid in ("stu-1", "stu-2", "stu-3"):
            seen |= {q["state"] for q in learner_of(sid)["questions"]}
        assert {"full", "part", "none", "blank", "unmarked", "unkeyed"} <= seen


# ── position ─────────────────────────────────────────────────────────────────

class TestPosition:
    def test_the_rank_comes_from_the_class_ranking(self):
        who = learner_of("stu-1")
        ranks = {row["name"]: row["rank"] for row in
                 er.ranking(analysis_of().people)}
        assert who["who"]["rank"] == ranks["Bella Safira"]
        assert who["class"]["learners"] == 3

    def test_the_gap_is_signed_against_the_class_mean(self):
        top = learner_of("stu-1")["gap"]
        bottom = learner_of("stu-2")["gap"]
        assert top["points"] > 0 and "di atas" in top["text"][0]
        assert bottom["points"] < 0 and "di bawah" in bottom["text"][0]

    def test_a_learner_on_the_class_mean_is_not_called_above_it(self):
        rows = [("a", "A", {"0": "A"}, {}, 60.0), ("b", "B", {"0": "B"}, {}, 60.0)]
        who = learner_of("a", rows)
        assert who["gap"]["points"] == 0.0
        assert who["gap"]["text"][0] == "tepat pada rata-rata kelas"

    def test_the_statement_says_what_the_gap_says(self):
        """The paragraph a school files carries its own copy of the comparison, so
        the two have to agree: a child told "tepat pada rata-rata kelas" on one line
        and "di atas rata-rata" in the filed sentence is the same claim made twice,
        one of them wrong."""
        rows = [("a", "A", {"0": "A"}, {}, 60.0), ("b", "B", {"0": "B"}, {}, 60.0)]
        who = learner_of("a", rows)
        assert "tepat pada rata-rata kelas" in who["statement"]["body"][0]
        assert "di atas rata-rata" not in who["statement"]["body"][0]

    def test_the_school_standard_is_reported_separately(self):
        who = learner_of("stu-2")
        assert who["class"]["configured"] is True
        assert who["class"]["kkm"] == 60
        assert who["statement"]["head"][0].startswith("Mencapai 20.0")

    def test_no_kkm_means_no_pass_claim(self):
        exam = dict(EXAM, passing_score=None)
        who = learner_of("stu-1", exam=exam)
        assert who["class"]["configured"] is False
        assert "KKM" not in who["statement"]["body"][1]


# ── the doors into this page ─────────────────────────────────────────────────

class TestTheDoorsIntoThisPage:
    def test_the_statement_carries_the_id_its_link_needs(self):
        """The class report links a learner's statement to this page, and a link
        needs an id. `None` is a real answer for a paper that could not name a
        profile — the template then prints the paragraph without a link — but an id
        that is always `None` is a page with a door that is never painted."""
        report = er.report(analysis_of(), EXAM)
        assert report["statements"], "the fixture writes no statements"
        for row in report["statements"]:
            assert row["id"], f"a statement came out unaddressable: {row['name']}"
        assert {row["id"] for row in report["statements"]} == \
            {row["id"] for row in report["ranking"]}

    def test_a_paper_with_no_id_gets_a_statement_with_no_link(self):
        rows = [(None, "Tanpa Id", {"0": "A"}, {}, 20.0)]
        report = er.report(analysis_of(rows), EXAM)
        assert report["statements"][0]["id"] is None


# ── the level table, and what is blamed on whom ──────────────────────────────

class TestLevelsAndBlame:
    def test_a_learner_level_row_carries_the_class_share_beside_it(self):
        who = learner_of("stu-1")
        for level in who["levels"]:
            assert "class_share" in level and "share" in level
            assert level["possible"] >= level["marks"]

    def test_the_unlabelled_row_is_never_called_a_strength(self):
        """"Level Tanpa level adalah yang terkuat" is not a finding, it is the
        absence of one — a paper nobody labelled gets no level sentence."""
        who = learner_of("stu-1")
        assert all("Tanpa level" not in line[1] for line in who["strengths"])

    def test_a_question_the_whole_class_failed_is_not_the_learners_fault(self):
        """Not every bad mark is the child's. In this fixture nobody in the class
        answered Q1 correctly, so the one question this learner missed is one the
        class missed too — and calling that a personal gap would be false.

        The two sentences are different findings and the page keeps them apart, so
        this asserts the class-level one *by name* and the personal one is absent.
        """
        rows = [("s1", "Ada", {"0": "B"}, {}, 0.0),
                ("s2", "Budi", {"0": "B"}, {}, 0.0),
                ("s3", "Citra", {"0": "B"}, {}, 0.0)]
        who = learner_of("s1", rows)
        joined = " ".join(line[1] for line in who["weaknesses"])
        assert "not this learner's" in joined, joined
        assert "most of the class" not in joined, \
            "a question nobody in the class answered was charged to the learner"

    def test_a_question_the_class_managed_is_charged_to_the_learner(self):
        """The other side of the same rule, so the two branches cannot collapse
        into one: a question the class answered and this learner missed is theirs."""
        who = learner_of("stu-2")  # missed Q3, which the class scored 67% on
        joined = " ".join(line[1] for line in who["weaknesses"])
        assert "most of the class" in joined, joined
        assert "Q3" in joined

    def test_a_weakness_is_named_with_its_number(self):
        who = learner_of("stu-1")
        lines = who["strengths"] + who["weaknesses"] + who["remediation"]
        assert lines, "a page with no sentences is a page nobody can act on"
        assert any(re.search(r"\d", line[1]) for line in lines), \
            "every claim on this page carries the number that made it a claim"

    def test_every_sentence_is_a_pair(self):
        who = learner_of("stu-1")
        sentences = who["strengths"] + who["weaknesses"] + who["remediation"]
        for line in sentences:
            assert isinstance(line, tuple) and len(line) == 2
            assert line[0] and line[1], f"a half-translated sentence: {line}"

    def test_the_states_come_from_the_service(self):
        """The page prints the state names from `learner.states`, so the six words
        exist once. A template with its own copy is how a page ends up saying
        "Salah" while the service says "Tidak dijawab"."""
        who = learner_of("stu-1")
        assert set(who["states"]) == set(er.ANSWER_STATES)
        for key, pair in who["states"].items():
            assert pair == er.ANSWER_STATES[key]

    def test_a_question_level_is_named_by_the_one_level_vocabulary(self):
        names = er.level_names()
        who = learner_of("stu-1")
        for question, item in zip(who["questions"], analysis_of().items):
            assert question["level_name"] == names[item.level or "unlabelled"]


# ── the key ──────────────────────────────────────────────────────────────────

class TestTheAnswerKey:
    def test_the_teachers_copy_carries_the_key(self):
        who = learner_of("stu-1")
        assert who["with_key"] is True
        assert who["questions"][0]["key"] == "A"
        assert who["questions"][4]["key"] == "air → cair"

    def test_the_shared_copy_does_not_carry_it_at_all(self):
        """Not hidden in markup: absent from the payload. The key is what one
        learner's page teaches the next learner who opens it, and the exam can
        still be open for the rest of the class."""
        who = learner_of("stu-1", with_key=False)
        assert who["with_key"] is False
        assert all(question["key"] == "" for question in who["questions"])

    def test_the_shared_copy_still_says_whether_the_answer_was_right(self):
        who = learner_of("stu-1", with_key=False)
        assert who["questions"][0]["state"] == "full"
        assert who["questions"][0]["answered"] == "A"


# ── the page ─────────────────────────────────────────────────────────────────

#: The answer-key column header, as the template writes it. Asserted on the
#: attribute rather than on `>Key<`: the heading is an `t('id','en')` pair, so its
#: English half is *inside* the attribute — and a test that looked for `Key` as
#: body text would pass on a page that rendered no key column at all.
KEY_HEADER = "x-text=\"t('Kunci','Key')\""

#: The matching key this fixture uses. Fixed on a question **stu-2 left blank**, so
#: finding it on a page proves the key was printed rather than the learner's own
#: answer being echoed back — with a learner who answered correctly the two strings
#: are the same, and the assertion would prove nothing.
MATCH_KEY = "air \u2192 cair"


def macro_pair(html: str, pair: tuple[str, str]) -> bool:
    """Whether a `(id, en)` pair reached the page through the `pair()` macro.

    The macro hands the pair to Alpine as a JSON array, and `forceescape` writes
    its quotes as `&#34;` — so the raw words are on the page while the surrounding
    shape is escaped. A test reading the raw words would pass either way; this
    reads the shape.
    """
    return f"&#34;{pair[0]}&#34;, &#34;{pair[1]}&#34;" in html


@pytest.fixture(scope="module")
def page(app):
    """Four copies of the page: both audiences, for two different learners.

    stu-1 answered the matching question, so their key is also their answer. stu-2
    left it blank, which is the copy that can tell a printed key from an echoed
    answer — the distinction the redaction tests are actually about.
    """
    @contextlib.contextmanager
    def signed_in(path):
        from flask import g
        with app.test_request_context(path):
            g.user_id = "tea-1"
            g.user_name = "Guru Uji"
            g.user_email = "guru@example.test"
            g.user_role = "guru"
            g.tz_offset = 7
            g.show = {}
            yield

    #: A link that is already live, so the revoke control is rendered too — the
    #: shape the page has *after* the first click.
    live_share = {"url": f"http://localhost/r/{'a' * 20}", "state": "active",
                  "views": 3, "created_at": None, "expires_at": None,
                  "student_id": "stu-1"}

    def render(sid="stu-1", public=False, share=None, **kw):
        payload = er.learner(analysis_of(), EXAM, sid, with_key=not public, **kw)
        with signed_in("/teacher/analysis/exam-1/report/student/stu-1"):
            return app.jinja_env.get_template("teacher/analysis_student.html").render(
                exam=EXAM, analysis=analysis_of(), learner=payload,
                public_view=public, share=share,
                back_url="/teacher/analysis/exam-1/report", lang="id")

    def noauth(sid="stu-1"):
        """The body as `base.html`'s anonymous chrome renders it: the macro alone.

        Which chrome wraps the page is the *session's* question and which copy it is
        is the route's, so the block a stranger gets cannot be inspected by rendering
        this template as if signed in — the first version of the page defined only
        `content`, and a stranger got the app's shell with **nothing in it**.
        """
        with signed_in("/r/token"):
            module = app.jinja_env.get_template(
                "teacher/analysis_student.html").make_module({
                    "exam": EXAM, "analysis": analysis_of(),
                    "learner": er.learner(analysis_of(), EXAM, sid, with_key=False),
                    "share": None, "back_url": "/", "lang": "id"})
            return module.body(True)

    return {
        "teacher": render("stu-1"),
        "public": render("stu-1", public=True),
        "teacher_blank": render("stu-2"),
        "public_blank": render("stu-2", public=True),
        "teacher_shared": render("stu-1", share=live_share),
        "noauth": noauth(),
        # stu-1 *answered* the matching question correctly, so their own answer and
        # the key are the same two words — only the learner who left it blank can
        # tell a published key from an echoed answer.
        "noauth_blank": noauth("stu-2"),
    }


class TestThePage:
    def test_it_extends_the_shell_so_the_toggle_applies(self):
        assert '{% extends "base.html" %}' in TEMPLATE.read_text(encoding="utf-8")

    def test_no_placeholder_survives(self, page):
        for name, html in page.items():
            assert "{{" not in html and "{%" not in html, name
            assert "Undefined" not in html, name

    def test_every_section_the_page_promises_is_on_it(self):
        """The template's own text, because a heading is a `(id, en)` pair: its
        English half lives inside the attribute that renders it, and what the page
        owes a reader is the pair — in one language at a time, selected by the
        button."""
        source = TEMPLATE.read_text(encoding="utf-8")
        for heading in ("Individual Learner Report", "Position and Totals",
                        "Achievement per Kisi-kisi Level",
                        "Question-by-Question Analysis", "Strengths",
                        "Weaknesses", "What to do next",
                        "Statement of Achievement", "Method and limits"):
            assert heading in source, f"the page no longer carries: {heading}"

    def test_the_teachers_copy_shows_the_key_column(self, page):
        assert "Learner\u2019s answer" in page["teacher"]
        assert KEY_HEADER in page["teacher"]
        assert MATCH_KEY in page["teacher_blank"], \
            "the key column is on the page but carries no key"

    def test_a_shared_copy_has_no_key_column_and_no_key(self, page):
        """Not hidden in markup: the column is gone and the key with it. The value
        is asserted on a learner who left that question *blank*, so it can only be
        the key."""
        assert KEY_HEADER not in page["public"]
        assert KEY_HEADER not in page["public_blank"]
        assert MATCH_KEY not in page["public_blank"]
        assert "A shared copy carries no answer key" in page["public"]

    def test_a_shared_copy_names_nobody_else(self, page):
        html = page["public"]
        assert "Bella Safira" in html
        assert "Ahmad Pratama" not in html

    def test_a_shared_copy_offers_no_way_back_that_is_not_there(self, page):
        """A stranger holds one link and nothing else, so a "back to the results
        report" that lands on the public landing page describes a page they cannot
        reach — the label is dropped rather than pointed somewhere else."""
        assert "Kembali ke laporan hasil" not in page["public"]
        assert "Kembali ke laporan hasil" in page["teacher"]

    def test_the_page_lists_the_states_in_the_services_own_words(self, page):
        """One vocabulary, rendered from `learner.states` — a page with its own copy
        of these words is how it ends up saying "Salah" where the service says
        "Tidak dijawab". Both halves travel, so the language button can rewrite the
        chips without a round trip."""
        assert macro_pair(page["teacher"], er.ANSWER_STATES["unkeyed"])
        assert macro_pair(page["teacher_blank"], er.ANSWER_STATES["blank"])
        assert macro_pair(page["public_blank"], er.ANSWER_STATES["blank"])

    def test_the_block_a_stranger_gets_is_the_redacted_copy(self, page):
        """`base.html` picks the chrome from the session and this template picks the
        copy from `public_view`, and the two blocks have to agree: a page that
        defines only `content` renders the app's shell with nothing in it for a
        stranger, which is what a share link served for its first live minute."""
        source = TEMPLATE.read_text(encoding="utf-8")
        assert "{% block content_noauth %}{{ body(true) }}{% endblock %}" in source
        assert ("{% block content %}{{ body(public_view|default(false, true)) }}"
                "{% endblock %}") in source
        anon = page["noauth"]
        assert "Individual Learner Report" in anon, "the stranger's copy is empty"
        assert KEY_HEADER not in anon
        assert MATCH_KEY not in page["noauth_blank"]
        assert "Share this learner" not in anon
        assert "Bella Safira" in anon

    def test_the_share_controls_are_scoped_to_this_learner(self, page):
        """The button sits under one child's name, so the link it mints must be
        that child's — and stopping it must not be the class report's own revoke.
        The forms post the learner-scoped routes, written out in the markup."""
        exam_id, sid = EXAM["id"], "stu-1"
        assert f"/report/student/{sid}/share\"" in page["teacher"]
        assert (f"/report/student/{sid}/share/revoke\""
                in page["teacher_shared"]), \
            "the stop-sharing form is not scoped to the learner"
        assert f"/teacher/analysis/{exam_id}/share/revoke" not in page["teacher_shared"], \
            "the learner's page offers the class report's own revoke"
        assert page["teacher_shared"].count("http://localhost/r/") == 1
        # A shared copy carries no share control at all: a reader of a link has
        # nothing to hand out and nothing to revoke.
        assert "Share this learner" not in page["public"]

    def test_the_wide_tables_can_scroll_on_a_phone(self):
        source = TEMPLATE.read_text(encoding="utf-8")
        for table in re.findall(r"<table[^>]*>", source):
            assert "min-w-[" in table, f"a table without a minimum width: {table}"

    def test_both_doors_into_a_learners_page_are_written_out(self):
        """Two ways in, from the class report: the ranking table and the statement
        written about that learner. A learner's page that nothing links to is a page
        nobody opens."""
        report = (pathlib.Path(__file__).resolve().parents[2] / "app" / "templates"
                  / "teacher" / "analysis_report.html").read_text(encoding="utf-8")
        doors = re.findall(r"href=\"/teacher/analysis/\{\{ exam\.id \}\}/report/student/\{\{ row\.id \}\}\"",
                           report)
        assert len(doors) >= 2, \
            "the class report no longer opens a learner's own report from both the ranking and the statement"


# ── a drawing is shown, never stringified ────────────────────────────────────

#: The shape the exam page writes, and the one this page was printing as text:
#: `pages` keyed by page **index**, each carrying a `data:` URL. Two pages so the
#: ordering is asserted rather than assumed.
DRAWN_ANSWER = {
    "pages": {
        "5": {"canvas": "data:image/png;base64,AAAA", "textBoxes": ["gaya"]},
        "11": {"canvas": "data:image/png;base64,BBBB"},
    },
}

#: One learner, whose essay answer is a drawing. `stu-1` keeps the name the rest of
#: this file uses so a fixture that drifts shows up as a rename, not as a passing
#: test on an empty page.
DRAWN_ROWS = [
    ("stu-1", "Bella Safira", {"0": "A", "3": DRAWN_ANSWER}, {"3": 60}, 60.0),
]


class TestADrawingIsShownNotStringified:
    """A cell of this page carried the whole stored answer — the dict, the base64
    and all — because `describe_answer` is written for *keys* and fell through to
    `str(value)` on a submission. It was reported from the live page by copying the
    cell, and the same string reached the CSV, XLSX and PDF built from that row."""

    def test_an_answer_is_described_by_its_pages(self):
        assert qt.describe_attempt("essay", DRAWN_ANSWER) == "6, 12"
        assert qt.describe_attempt("mcq", DRAWN_ANSWER) == "6, 12", \
            "a drawing stored under an objective question is still not a repr"

    def test_the_page_numbers_are_the_readers_own(self):
        """The map is index-keyed — the exam page writes `page - 1` — so `0` is the
        paper's page 1, and a label printing the raw key sends a family to a page
        the paper has not got."""
        assert qt.page_label("0") == "1"
        assert qt.describe_attempt("essay", {"pages": {"0": {"canvas": "data:x"}}}) == "1"

    def test_a_plain_answer_reads_exactly_as_it_did(self):
        assert qt.describe_attempt("mcq", "A") == "A"
        assert qt.describe_attempt("mcq", ["A", "B"]) == "A, B"
        assert qt.describe_attempt("true_false", "true") == "True"
        assert qt.describe_attempt("match",
                                   {"pairs": [{"left": "air", "right": "cair"}]}) == "air → cair"

    def test_a_structure_this_version_cannot_read_is_empty_not_a_repr(self):
        """The safety property, stated as one: a shape nothing here recognises comes
        back as an empty cell. Its repr is what put a megabyte of base64 on a report
        a family reads."""
        for value in ({"unknown": 1}, {"canvas": "data:image/png;base64,AAAA"}, {}):
            assert qt.describe_attempt("mcq", value) == ""

    def test_the_drawings_travel_as_data_urls_in_page_order(self):
        assert qt.answer_drawings(DRAWN_ANSWER) == [
            ("6", "data:image/png;base64,AAAA"),
            ("12", "data:image/png;base64,BBBB"),
        ]
        assert qt.answer_drawings("A") == []
        assert qt.answer_drawings(None) == []

    def test_the_row_carries_the_text_and_the_drawing_separately(self):
        """Two fields on purpose: the surfaces that cannot show an image read the
        text, and the page reads the images — and neither can print the other's."""
        row = next(q for q in learner_of("stu-1", DRAWN_ROWS)["questions"]
                   if q["no"] == 4)
        assert row["answered"] == "6, 12"
        assert row["drawings"] == [("6", "data:image/png;base64,AAAA"),
                                   ("12", "data:image/png;base64,BBBB")]

    def test_the_rendered_page_shows_the_drawing_and_no_repr(self, app):
        payload = learner_of("stu-1", DRAWN_ROWS)
        with app.test_request_context(
                "/teacher/analysis/exam-1/report/student/stu-1"):
            html = app.jinja_env.get_template(
                "teacher/analysis_student.html").render(
                    exam=EXAM, analysis=analysis_of(DRAWN_ROWS), learner=payload,
                    public_view=False, share=None,
                    back_url="/teacher/analysis/exam-1/report", lang="id")

        assert 'src="data:image/png;base64,AAAA"' in html
        assert 'src="data:image/png;base64,BBBB"' in html
        assert "&#39;pages&#39;" not in html and "'pages'" not in html, \
            "the stored answer is being printed as its own repr again"

    def test_the_documents_never_carry_the_base64(self):
        """A spreadsheet cell holding a megabyte of base64 is a workbook nothing can
        open, and the same row feeds all three documents."""
        who = learner_of("stu-1", DRAWN_ROWS)

        assert "data:image" not in lr.learner_csv(who, EXAM, lang="id")
        assert b"data:image" not in lr.learner_xlsx(who, EXAM, lang="id")
        assert b"data:image" not in lr.learner_pdf(who, EXAM, lang="id")
        # …and the page numbers are what they carry instead, so the fact that there
        # is work on those pages is not lost on the way out.
        assert "6, 12" in lr.learner_csv(who, EXAM, lang="id")
