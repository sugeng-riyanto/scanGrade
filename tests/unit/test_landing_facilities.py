"""Every facility the landing page advertises must exist in this repository.

The performance block is held to its measurements — `test_landing_claims.py` and
`deploy/claims_gate.py` re-read the published table against the raw files in
`docs/measurements/`. The feature grid had no such rule, and it drifted the way an
unguarded claim does: it advertised **"3 Tipe Soal — MCQ, Esai Teks (paragraph),
Esai Canvas"** while `question_types.PICKER_TYPES` had grown to six standalone
types, so the page undersold the product by half *and* named `essay_text`, the one
type the builder had deliberately stopped offering.

A card now names the artifact that proves it (`data-facility="…"`), and these
tests hold the three directions that make that mean something:

1. a card with no artifact is a claim nobody can check;
2. an artifact with no card is dead weight dressed up as evidence;
3. the numbers the page prints — how many question types, and which — are the
   registry's numbers, so the page cannot outrun the code again.

The last one is the point. A page that restates the application is a second copy
of it, and a second copy is what drifts.
"""
import html as html_mod
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LANDING = ROOT / "app" / "templates" / "landing.html"

# ── what proves what ─────────────────────────────────────────────────────────
#
# facility -> the artifacts that prove it, each as (repository path, a pattern
# that must appear in it). A claim no file supports cannot be listed here, which
# is what stops a card being added without one.
#
# Several facilities point at the same file on purpose: the student exam page is
# where the anti-cheat rules, the drawing tools and the calculator actually are,
# and naming the file once per capability is more honest than inventing a module
# name per capability.

# Every pattern must match on its own, and every one is a plain linear search.
#
# The first version expressed "all of these" as lookaheads in one pattern
# (`(?s)(?=.*protractor)(?=.*compass)`). It passed while the capabilities were
# there and *hung* once one was gone: an unanchored `.*` inside a lookahead that
# cannot be satisfied tries every start position and rescans the file at each one,
# which is quadratic on a 2,000-line template. A gate that hangs instead of failing
# is worse than a gate that fails — a deploy would sit there rather than refuse the
# release — so the shape is gone, and
# `test_the_patterns_cannot_only_fail_slowly` keeps it out.
EVIDENCE: dict[str, list[tuple[str, list[str]]]] = {
    "question-types": [
        ("app/services/question_types.py", [r"\bPICKER_TYPES\b"]),
    ],
    "ai-grading": [
        # The card names four providers, so the code must name all four.
        ("app/services/ai_service.py",
         [r"(?i)\bgemini\b", r"(?i)\bopenai\b", r"(?i)\bdeepseek\b", r"(?i)\bgroq\b"]),
    ],
    "anti-cheat": [
        ("app/templates/student/take_exam.html", [r"\bmax_violations\b", r"\bwatermark\b"]),
        ("app/templates/student/take_exam.html",
         [r"\baddEventListener\('copy'", r"\baddEventListener\('paste'"]),
        # "submitted automatically at the violation limit" is a behaviour, not a
        # string: the limit and the auto-submit path both have to be there.
        ("app/templates/student/take_exam.html",
         [r"\bauto_submit_on_max\b", r"\bsubmitExam\b"]),
    ],
    "digital-tools": [
        ("app/templates/student/take_exam.html", [r"(?i)\bprotractor\b", r"(?i)\bcompass\b"]),
    ],
    "analytics-export": [
        ("app/services/export_service.py",
         [r"\bdef export_to_xlsx\b", r"\bdef export_to_pdf\b"]),
    ],
    "multi-school": [
        ("app/utils/auth.py", [r"\buser_school_id\b"]),
        ("supabase/migrations/002_school_classes_nisn.sql", [r"(?i)\bnpsn\b"]),
    ],
    "online-payment": [
        ("app/services/midtrans_service.py", [r"\bdef create_snap_transaction\b"]),
        ("app/routes/admin_sekolah.py", [r"\btrial_days = 14\b"]),
    ],
    "scientific-calculator": [
        ("app/templates/student/take_exam.html", [r"\bcalcSciFunc\b", r"\{l:'sin'"]),
    ],
    # The answer sheet is both *made* and *read* by this app, so the card has to
    # name both halves: a generator with no reader is a PDF, and a reader with no
    # generator is a camera pointed at somebody else's form.
    "omr-scan": [
        ("app/routes/tools.py", [r"generate-answer-sheet"]),
        ("app/services/answer_sheet_generator.py", [r"\bdef generate_answer_sheet\b"]),
        ("app/services/omr_service.py", [r"\bdef process_scan\b"]),
    ],
    # "a document, not a screenshot" is the whole claim, and it is proved by the
    # routes that render a sheet rather than by the results page that prints itself.
    "print-reports": [
        ("app/routes/teacher.py", [r'"/results/print"',
                                   r'"/submissions/<submission_id>/print"']),
        # The signature block is the half of "a document, not a screenshot" that
        # makes it a document a school files, so it is the anchor rather than the
        # template merely existing.
        ("app/templates/print/report_card.html", [r'class="sign"', r"Kepala Sekolah",
                                                 r"Guru Mata Pelajaran"]),
    ],
    "mark-scheme": [
        ("app/services/mark_scheme.py", [r"\bdef build_weights\b",
                                        r"\bdef normalise_to_100\b"]),
        # Part-marks are the second half of the card, and they live in the grader.
        ("app/services/question_types.py", [r"\bdef part_factor\b"]),
    ],
    # "a report you can hand over, and a link you can take back" is two claims:
    # the documents are built here, and the link that publishes a redacted copy of
    # one is a service with its own lifecycle (minted, resolved, revoked).
    "report-share": [
        ("app/services/learner_report.py",
         [r"\bdef learner_pdf\b", r"\bdef learner_xlsx\b", r"\bdef learners_zip\b"]),
        ("app/services/analysis_share.py",
         [r"\bdef create\b", r"\bdef revoke\b", r"\bdef resolve\b"]),
        # The redaction is the half that makes a public link safe to hand out, so
        # the card is anchored on the branches that withhold the students section
        # and refuse the per-learner appendix on a shared copy — not on the word
        # "public" appearing somewhere in the file.
        ("app/services/analysis_report.py",
         [r"\bif public:", r"\bappendix and public\b"]),
    ],
    "bilingual-dark": [
        ("app/templates/base.html", [r"\bsetLang\b", r"\btoggleDark\b"]),
        # "a release is refused if any text fails a contrast check" is a deploy
        # behaviour, so the artifact is the gate that refuses it.
        ("deploy/theme_gate.sh", [r"test_dark_theme_contrast"]),
    ],
}


def page() -> str:
    return LANDING.read_text(encoding="utf-8")


def cards() -> list[str]:
    """The facility keys the page advertises, in source order."""
    return re.findall(r'data-facility="([^"]+)"', page())


@pytest.fixture(scope="module")
def rendered(app) -> str:
    """The landing page as a visitor is served it, entities decoded.

    Rendered rather than grepped, because the type list is written by Jinja from
    the registry: reading the template source would check the *instructions* for
    the claim instead of the claim a reader gets.
    """
    import sys

    sys.path.insert(0, str(ROOT))
    from flask import g

    with app.test_request_context("/"):
        g.user = None
        body = app.jinja_env.get_template("landing.html").render()
    return html_mod.unescape(body)


# ── 1. and 2. the two directions of a claim ─────────────────────────────────

class TestCardsAndArtifactsAreOneToOne:
    def test_every_card_names_an_artifact(self):
        advertised = set(cards())
        assert advertised, "the feature grid has no data-facility cards at all"
        unsupported = advertised - set(EVIDENCE)
        assert not unsupported, (
            f"the landing page advertises {sorted(unsupported)} with no entry in "
            "EVIDENCE, so the claim cannot be checked. Add the artifact that proves "
            "it, or take the card out."
        )

    def test_every_artifact_has_a_card(self):
        dead = set(EVIDENCE) - set(cards())
        assert not dead, (
            f"EVIDENCE proves {sorted(dead)}, but no card advertises them — the grid "
            "was edited and the proof left behind."
        )

    def test_no_card_is_repeated(self):
        seen = cards()
        assert len(seen) == len(set(seen)), f"a facility card appears twice: {seen}"


# ── 3. the artifact really does prove the claim ─────────────────────────────

class TestTheArtifactProvesTheClaim:
    @pytest.mark.parametrize("facility", sorted(EVIDENCE))
    def test_the_named_files_exist_and_match(self, facility):
        for rel, needles in EVIDENCE[facility]:
            path = ROOT / rel
            assert path.exists(), (
                f"the '{facility}' card names {rel} as its evidence, and there is no "
                "such file."
            )
            text = path.read_text(encoding="utf-8", errors="replace")
            for needle in needles:
                assert re.search(needle, text), (
                    f"the '{facility}' card is proved by {rel}, but that file no longer "
                    f"contains {needle!r}. Either the capability was removed — then "
                    "the card is a claim about nothing — or it moved, and the card "
                    "should name where it went."
                )

    def test_the_patterns_cannot_only_fail_slowly(self):
        """A check that hangs when it fails is not a check.

        This is not hypothetical: the first version of this table combined its
        needles into one pattern with `.*` lookaheads, which passes instantly while
        the capability exists and goes quadratic the moment it does not — the two
        mutations that removed `max_violations` and `protractor` both ran past a
        150-second timeout instead of reporting a failure. Naming the needles
        separately keeps every search linear.
        """
        for facility, artifacts in EVIDENCE.items():
            for rel, needles in artifacts:
                assert needles, f"the '{facility}' card lists {rel} with nothing to check"
                for needle in needles:
                    assert ".*" not in needle and ".+" not in needle, (
                        f"{facility} -> {rel} uses {needle!r}. A wildcard inside a "
                        "pattern that can fail rescans the file at every start "
                        "position: the gate would hang rather than go red. Split it "
                        "into separate needles instead."
                    )
                    re.compile(needle)          # a pattern that cannot compile is not one


# ── 4. the numbers on the page are the registry's ───────────────────────────

class TestTheTypeListIsTheRegistrys:
    """The claim that drifted, held in both directions and both languages."""

    def test_the_old_count_is_gone(self, rendered):
        # The rendered page, not the template source: the source carries a comment
        # explaining this history, and a reader never sees it. What must not come
        # back is the *claim*.
        for stale in ("3 Tipe Soal", "3 Question Types"):
            assert stale not in rendered, (
                f"'{stale}' is back. The builder offers more types than that: the "
                "list is `question_types.PICKER_TYPES`, and the page must count it "
                "rather than restate it."
            )

    def test_the_page_counts_the_registry(self, rendered):
        from app.services import question_types as qt

        n = len(qt.PICKER_TYPES)
        assert f"{n} Tipe Soal" in rendered, (
            f"the page does not say '{n} Tipe Soal', which is how many types "
            "PICKER_TYPES holds."
        )
        assert f"{n} Question Types" in rendered

    def test_the_page_lists_exactly_the_registrys_types(self, rendered):
        """Not "contains these names" — *these and no others*.

        A label the builder cannot produce is a type a teacher will look for and
        not find, which is worse than advertising too few.
        """
        from app.services import question_types as qt

        picker = qt.vocabulary()["picker"]
        assert ", ".join(t["id"] for t in picker) in rendered, (
            "the Indonesian type list on the page is not the registry's list"
        )
        assert ", ".join(t["en"] for t in picker) in rendered, (
            "the English type list on the page is not the registry's list"
        )

    def test_a_type_the_builder_does_not_offer_is_not_advertised(self, rendered):
        """`essay_text` is a legacy form the app still grades; the builder stopped
        offering it beside the canvas essay, so the landing page must not offer it
        either."""
        from app.services import question_types as qt

        assert qt.ESSAY_TEXT not in qt.PICKER_TYPES, (
            "the builder now offers the typed essay again — this test's premise has "
            "changed, and the page copy should be reviewed with it."
        )
        assert "Esai Teks" not in rendered, (
            "the landing page offers a typed essay ('Esai Teks'), which the exam "
            "builder deliberately does not: a pupil would be promised a question "
            "their teacher cannot set."
        )


# ── 5. a named tool must be a tool this app uses ────────────────────────────

class TestNamedToolsAreReal:
    """A provider name is a claim too, and the easiest one to get wrong."""

    PROVIDERS = ("Gemini", "OpenAI", "DeepSeek", "Groq")

    @pytest.mark.parametrize("provider", PROVIDERS)
    def test_a_provider_named_on_the_page_is_named_in_the_code(self, provider):
        if provider not in page():
            pytest.skip(f"the page does not name {provider}")
        source = (ROOT / "app" / "services" / "ai_service.py").read_text(
            encoding="utf-8", errors="replace").lower()
        assert provider.lower() in source, (
            f"the landing page advertises {provider} grading, but ai_service.py never "
            "mentions it — the page is promising a provider the app cannot call."
        )

    def test_midtrans_is_named_only_if_it_is_wired(self):
        if "Midtrans" not in page():
            pytest.skip("the page does not name Midtrans")
        service = ROOT / "app" / "services" / "midtrans_service.py"
        assert service.exists(), (
            "the landing page advertises Midtrans, and there is no midtrans_service."
        )
        assert "create_snap_transaction" in service.read_text(encoding="utf-8"), (
            "midtrans_service.py exists but cannot create a transaction, so the card "
            "advertises a payment flow that does not run."
        )


# ── 6. the facilities can be tried, and the page says where ────────────────

class TestTheGridPointsAtWhereToCheck:
    def test_the_grid_links_to_the_demo(self, rendered):
        """A facility a reader cannot try is a claim again; the demo is where the
        list stops being a list.

        Scoped to the grid on purpose: the hero's tutorial row also links to
        ``/demo``, so asking the whole page would keep passing after this link was
        removed.
        """
        block = rendered.split("<!-- Features Grid -->")[1].split("<!-- Evidence")[0]
        assert 'href="/demo"' in block, (
            "the facilities grid no longer points at the demo, so nothing on the page "
            "tells a reader where to try what it just claimed."
        )

    def test_the_facilities_block_sends_readers_to_a_page_not_a_path(self, rendered):
        """The repository is private, so a facility has to be checkable by *using*
        the app. (The performance block may name its artifacts — it says where the
        raw files live and links to /capacity, which is a page a reader can open.)

        Read off the rendered page: the template carries a comment naming its own
        test file, and a reader never sees that. The rule is about the copy they
        get.
        """
        block = rendered.split("<!-- Features Grid -->")[1].split("<!-- Evidence")[0]
        leaked = re.findall(r"[\w/]*\.(?:py|html|json|sql|md)\b", block)
        assert not leaked, (
            f"the facilities block points readers at files they cannot open: "
            f"{sorted(set(leaked))}. Link the demo instead."
        )
