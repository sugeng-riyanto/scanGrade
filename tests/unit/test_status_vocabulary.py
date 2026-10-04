"""One place derives "what is this sitting", and everything else asks it.

`attempt_status` says so itself in its own docstring: *"the status vocabulary — active,
locked, finished, expired — was re-derived in each place, which is one edit away from a
teacher's dashboard calling a paper 'finished' while its pupil is still writing."*

That was true when it was written and was not true yet. Two surfaces still derived:

* **the deadline sweep** (`deadline_service`) decided what may be closed from its own
  `OPEN_STATUSES`, and decided "is this over" from `exam_window.deadline` plus the grace
  constant itself (`has_expired`);
* **the lock gate** (`resume_code`) decided "is this over" from `exam_window.deadline`
  itself (`at_or_past_deadline`), and named the two statuses it moves between with its
  own `DRAFT`/`LOCKED` literals;
* both were one edit from disagreeing with `attempt_status` — the sweep closing a sitting
  the lock gate still offers a resume for, or the reverse.

This file holds the single-source rule as a source scan, because the rule is about
*where the decision lives*, not only about what it returns: a helper that happened to
agree today but recomputed the arithmetic would still be the second answer. The scan is
scoped to those two files on purpose — a reader that merely filters on a *result* status
(`submitted`/`graded`) is not a second vocabulary, and flagging it would make the rule
noisy enough to be ignored.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVICES = ROOT / "app" / "services"

STATUS_MODULE = SERVICES / "attempt_status.py"
SWEEP = SERVICES / "deadline_service.py"
LOCK_GATE = SERVICES / "resume_code.py"

#: The modules allowed to *define* the vocabulary rather than ask for it:
#: `attempt_status` is the one place; `submission_service` owns the `submissions` row
#: and its `LIVE_STATUSES`/`LOCKED_STATUS` are the schema's own names for the states;
#: `exam_window` owns the deadline arithmetic itself.
DEFINERS = {"attempt_status.py", "submission_service.py", "exam_window.py"}

#: The two statuses a *sitting* can hold — the vocabulary this rule is about. A result
#: status is a filter value many readers legitimately spell; a sitting status is the
#: one the sweep and the lock gate move a paper between.
SITTING_LITERALS = ("draft", "locked_pending_resume")

_LITERAL = re.compile(r"""["'](""" + "|".join(SITTING_LITERALS) + r""")["']""")

#: The surfaces the rule is about, named rather than discovered.
IN_SCOPE = {"deadline_service.py", "resume_code.py"}


def _scanned():
    """The two files this rule governs, with the ones that may define it removed."""
    return [p for p in sorted(SERVICES.glob("*.py"))
            if p.name in IN_SCOPE and p.name not in DEFINERS]


class TestOnlyOneModuleDerivesTheVocabulary:
    def test_the_sweep_and_the_lock_gate_ask_for_the_deadline_arithmetic(self):
        offenders = []
        for path in _scanned():
            src = path.read_text(encoding="utf-8")
            for match in re.finditer(r"exam_window\.deadline\b", src):
                line = src[:match.start()].count("\n") + 1
                offenders.append(f"{path.name}:{line}")
        assert not offenders, (
            "these modules call exam_window.deadline instead of asking "
            "attempt_status for the deadline, so they hold a second answer to "
            "\"when does this sitting end\":\n  " + "\n  ".join(offenders))

    def test_the_sweep_and_the_lock_gate_do_not_read_the_grace_constant(self):
        offenders = []
        for path in _scanned():
            src = path.read_text(encoding="utf-8")
            for match in re.finditer(r"LATE_GRACE_SECONDS", src):
                line = src[:match.start()].count("\n") + 1
                offenders.append(f"{path.name}:{line}")
        assert not offenders, (
            "these modules read the late-grace constant themselves instead of asking "
            "attempt_status whether the sitting is over:\n  " + "\n  ".join(offenders))

    def test_the_sweep_and_the_lock_gate_do_not_spell_a_sitting_status(self):
        offenders = []
        for path in _scanned():
            src = path.read_text(encoding="utf-8")
            for match in _LITERAL.finditer(src):
                line = src[:match.start()].count("\n") + 1
                offenders.append(f"{path.name}:{line}  {match.group(0)}")
        assert not offenders, (
            "these modules spell a *sitting* status as a literal instead of naming "
            "the one definition in attempt_status:\n  " + "\n  ".join(offenders))


class TestAttemptStatusOwnsTheVocabulary:
    def test_it_names_the_statuses_a_sitting_can_be(self):
        from app.services import attempt_status

        for name in ("DRAFT", "LOCKED_STATUS", "LIVE_STATUSES", "OPEN_STATUSES"):
            assert hasattr(attempt_status, name), (
                f"attempt_status does not expose {name}, so a surface that needs it "
                f"has to derive it")

    def test_the_open_set_is_the_two_sittings(self):
        from app.services import attempt_status

        assert set(attempt_status.OPEN_STATUSES) == {
            attempt_status.DRAFT, attempt_status.LOCKED_STATUS}, (
            "the sweep's candidate set is not the statuses a sitting can hold")

    def test_it_answers_whether_a_sitting_is_over(self):
        from app.services import attempt_status

        assert callable(getattr(attempt_status, "expired", None)), (
            "attempt_status does not answer \"is this sitting over\", so the sweep "
            "must derive it from the arithmetic")
        assert callable(getattr(attempt_status, "at_or_past_deadline", None)), (
            "attempt_status does not answer \"is this sitting at its deadline\", so "
            "the lock gate must derive it from the arithmetic")

    def test_it_answers_the_deadline_itself(self):
        from app.services import attempt_status

        assert callable(getattr(attempt_status, "deadline_of", None)), (
            "attempt_status does not answer \"when does this sitting end\"")

    def test_it_re_exports_the_grace_it_uses(self):
        from app.services import attempt_status
        from app.utils import exam_window

        assert attempt_status.GRACE_SECONDS == exam_window.LATE_GRACE_SECONDS, (
            "the grace attempt_status applies is not the app's one grace constant")


class TestTheSweepAsksRatherThanDerives:
    def test_the_sweep_takes_its_open_set_from_attempt_status(self):
        src = SWEEP.read_text(encoding="utf-8")
        assert "attempt_status.OPEN_STATUSES" in src, (
            "the sweep still declares its own open-status set")

    def test_the_sweep_asks_attempt_status_whether_a_sitting_is_over(self):
        src = SWEEP.read_text(encoding="utf-8")
        assert "attempt_status.expired" in src, (
            "the sweep still derives \"is this over\" from the arithmetic")

    def test_the_sweep_asks_for_the_deadline_rather_than_computing_it(self):
        src = SWEEP.read_text(encoding="utf-8")
        assert "attempt_status.deadline_of" in src, (
            "the sweep still computes the sitting's end itself")

    def test_the_answers_are_the_same_as_the_one_source(self):
        """The behaviour, not just the wiring: a sitting at its grace is not over."""
        from datetime import datetime, timedelta, timezone

        from app.services import attempt_status, deadline_service

        now = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
        exam = {"duration_minutes": 60, "start_at": None, "end_at": None}
        started = (now - timedelta(minutes=60)).isoformat()
        row = {"status": "draft", "started_at": started}

        # At the deadline: still open for the grace, so not closable yet.
        assert attempt_status.at_or_past_deadline(exam, row, now) is True
        assert attempt_status.expired(exam, row, now) is False
        assert deadline_service.has_expired(exam, started, now) is False

        # Past the grace: the one source and the sweep agree it is over.
        late = now + timedelta(seconds=attempt_status.GRACE_SECONDS + 1)
        assert attempt_status.expired(exam, row, late) is True
        assert deadline_service.has_expired(exam, started, late) is True


class TestTheLockGateAsksRatherThanDerives:
    def test_the_lock_gate_asks_attempt_status_for_the_deadline(self):
        src = LOCK_GATE.read_text(encoding="utf-8")
        assert "attempt_status.at_or_past_deadline" in src, (
            "the lock gate still derives the deadline from the arithmetic")

    def test_the_lock_gate_names_the_statuses_from_the_one_definition(self):
        from app.services import attempt_status, resume_code

        assert resume_code.DRAFT == attempt_status.DRAFT, (
            "the lock gate's draft literal is not the one definition")
        assert resume_code.LOCKED == attempt_status.LOCKED_STATUS, (
            "the lock gate's locked literal is not the one definition")

    def test_the_lock_gate_and_the_status_module_agree_at_the_boundary(self):
        from datetime import datetime, timedelta, timezone

        from app.services import attempt_status, resume_code

        now = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
        exam = {"duration_minutes": 60, "start_at": None, "end_at": None}
        row = {"status": "locked_pending_resume",
               "started_at": (now - timedelta(minutes=60)).isoformat()}

        assert resume_code.at_or_past_deadline(exam, row, now) is True
        assert attempt_status.at_or_past_deadline(exam, row, now) is True
        assert attempt_status.get_attempt_status(
            None, "e1", "s1", exam=exam, row=row, now=now)["status"] == \
            attempt_status.EXPIRED, (
            "the lock gate refuses the resume while the status module still calls "
            "the sitting locked — the two answers a pupil would see at once")
