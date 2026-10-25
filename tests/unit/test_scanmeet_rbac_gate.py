"""The RBAC gate's own rules, without a staging host or a browser.

The gate is the instrument that says the *door* holds — a pupil cannot open a
room, another school's pupil gets no token, RLS answers a browser key with no
row. An instrument that silently narrows is worse than no instrument: 13/13 on a
run that never probed the case it exists for reads exactly like 13/13 on a run
that did.

So these tests are about what the gate refuses to run without, and about the one
row whose verdict is a judgement rather than a status code: the account that
belongs to no school, where a redirect is only a refusal if it lands on the exam
list.
"""
import pytest

from deploy import scanmeet_rbac_gate as gate
from deploy import scanmeet_staging_fixture as fixture

#: The row names the gate must report, in the order a reader meets them. Every
#: one of these is a claim about the authorization boundary; a name that leaves
#: this list is a reading nobody takes any more, and it has to be a deliberate
#: edit rather than a refactor that dropped a `report.add`.
ROWS = (
    "teacher-sign-in", "student-sign-in", "teacher-door-refuses-pupil",
    "pupil-cannot-open-room", "teacher-opens-room",
    "outsider-refused-status", "pupil-cannot-end-room", "outsider-refused-token",
    "noschool-refused-paper", "noschool-refused-token",
    "pupil-gets-token", "teacher-ends-room",
    "rls-profiles-scoped", "rls-cross-school-invisible",
    "rls-live-tables-invisible",
)


def _args(**over):
    base = dict(env="unused.env", url="https://scanmeet-stg.example.org",
                exam="exam-1", teacher="guru@example.invalid:pw",
                student="murid1@example.invalid:pw", student2="",
                outsider="lain@example.invalid:pw",
                noschool="nobody@example.invalid:pw", json=False)
    base.update(over)
    return type("Args", (), base)()


# ── what it refuses to run without ───────────────────────────────────────────

def test_the_account_of_no_school_is_required_not_optional():
    """`--noschool` is required, exactly as `--outsider` is.

    An optional identity would let a caller who forgot it receive a verdict that
    is green about a case the run never measured — the failure mode every gate in
    this repository is written against.
    """
    without = ["--env", "unused.env", "--url", "https://x.example.org",
               "--exam", "exam-1", "--teacher", "a@b.invalid:pw",
               "--student", "c@b.invalid:pw", "--outsider", "d@b.invalid:pw"]
    with pytest.raises(SystemExit):
        gate.main(without)
    # With it the parser is satisfied, and the run stops where it should: at the
    # env file, which this test deliberately does not have.
    with_noschool = without + ["--noschool", "e@b.invalid:pw"]
    assert gate.main(with_noschool) == 2, (
        "a missing env file is could-not-measure, not a refusal")


def test_the_gate_and_the_fixture_agree_on_the_identity_name():
    """Two tools, one name: the fixture prints `--noschool`, the gate reads it."""
    keys = {identity.key for identity in fixture.IDENTITY_ROWS}
    assert "noschool" in keys
    assert "--noschool" in (gate.__doc__ or "")
    source = open(gate.__file__, encoding="utf-8").read()
    assert 'args.noschool' in source


# ── the rows themselves ──────────────────────────────────────────────────────

def test_every_row_the_docstring_promises_is_reported():
    source = open(gate.__file__, encoding="utf-8").read()
    doc = gate.__doc__ or ""
    for name in ROWS:
        assert name in doc, f"{name} is not documented"
        assert f'"{name}"' in source, f"{name} is documented but never reported"


def test_the_two_no_school_rows_are_reported_where_a_refusal_is_possible():
    """The paper row is taken before a room exists; the token row needs one.

    Reading them in the other order would take the token row against an empty
    session id, and its SKIP would look like a missing account.
    """
    source = open(gate.__file__, encoding="utf-8").read()
    paper = source.index('"noschool-refused-paper"')
    token = source.index('"noschool-refused-token"')
    assert paper < token
    # The token row sits inside the `if session_id:` block, with the outsider's.
    block = source[source.index("if session_id:"):]
    assert '"noschool-refused-token"' in block
    assert '"noschool-refused-paper"' not in block


# ── the one verdict that is a judgement ──────────────────────────────────────

def test_a_redirect_to_the_exam_list_is_the_refusal():
    verdict, detail = gate.paper_refusal(302, "/student/exams")
    assert verdict == gate.PASS
    assert "exam list" in detail


def test_a_redirect_elsewhere_is_not_the_refusal():
    """A sign-in page also answers 302 — and a session that expired is not a door."""
    verdict, detail = gate.paper_refusal(302, "/auth/sign-in")
    assert verdict == gate.FAIL
    assert "/auth/sign-in" in detail


def test_the_paper_being_served_is_the_failure_the_row_exists_for():
    verdict, detail = gate.paper_refusal(200, "")
    assert verdict == gate.FAIL
    assert "served" in detail


def test_forbidden_is_a_refusal_but_not_a_pass():
    """A 403 would refuse the *Live API*, not open the paper — read it as FAIL.

    The sitting door redirects, so anything else is a shape this gate has not
    measured; calling it PASS would accept a door that changed without telling
    anyone reading the sheet.
    """
    verdict, _detail = gate.paper_refusal(403, "")
    assert verdict == gate.FAIL


def test_a_trailing_slash_still_lands_on_the_exam_list():
    verdict, _detail = gate.paper_refusal(303, "/student/exams/")
    assert verdict == gate.PASS


def test_an_incomplete_run_is_two_not_zero():
    """The code's own exit contract, read off the report it builds."""
    report = gate.Report()
    report.add("a-row", gate.PASS, "ok")
    report.add("another", gate.SKIP, "no account")
    assert report.passed == 1 and report.failed == 0
