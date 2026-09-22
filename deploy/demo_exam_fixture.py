"""The one exam a release is allowed to sit.

Why a fixture at all
--------------------
The post-deploy smoke test opens a real exam as a student, because the two
anti-cheat panels (the fullscreen blocker and the away blur with its countdown)
exist on one page and nowhere else: a release that drops them is a release whose
supervision is gone, and page lists of paths cannot see it.

A check that opens a paper has to open *a* paper, and which paper a student has
is data, not code — so the demo data has to guarantee one. It has to be a paper

* **assigned to a class the demo student is in**, or the door refuses her;
* **with no window**, or it stops being sittable on a date nobody wrote down
  (`exam_window.window_state` reads a row with neither timestamp as OPEN, which
  is why `WINDOW` below is two NULLs rather than a wide range);
* **with anti-cheat and fullscreen on**, because that is what *arms* the panels:
  the page's `antiCheat.enabled` gates the whole ladder and `fullscreen_required`
  gates the blocker, so an exam with them off is an exam whose panels could never
  appear however healthy the code is;
* **with nothing to lose** — a student who has already submitted it is not
  offered it again, so the seeder voids the old attempts and the fixture is
  sittable again on every release.

This module is stdlib-only on purpose, and it is imported from two places that
must not drift: `deploy/smoke_test.py` (which looks for the marker below on a
student's exam list) and `manage.py` (which writes it). The smoke test is run by
the deploy runner with *no app environment*, so it cannot import the app; the
seeder could, but sharing the spec is cheaper than keeping two copies of a title
in step.

The exam is ordinary in every other way: a teacher can open it, edit it, grade
it, and a demo visitor can sit it. It is a demo paper, not a special case in the
app.
"""

#: The marker. Matched exactly, after stripping, on the exam list and in the
#: database — so keep it plain ASCII and keep it unique.
TITLE = "Latihan Ujian ScanGrade"

#: Every demo school has this subject, which is why the fixture can be created
#: in all three without a per-school lookup table.
SUBJECT = "Matematika"

DURATION_MINUTES = 60
TOTAL_QUESTIONS = 5
PASSING_SCORE = 70

#: Five questions, four of them multiple choice and one true/false: enough for
#: the page to render a paper with more than one kind of control, and all of it
#: auto-gradable so a demo visitor who submits it gets a score rather than a pile
#: of unmarked essays.
QUESTION_TYPES = {"0": "mcq", "1": "mcq", "2": "mcq", "3": "mcq", "4": "true_false"}
ANSWER_KEY = {"0": "B", "1": "A", "2": "D", "3": "C", "4": "true"}
QUESTION_WEIGHTS = {"0": 20, "1": 20, "2": 20, "3": 20, "4": 20}

#: On purpose, and the reason this fixture is part of a *deploy* gate: an exam
#: with `anti_cheat_enabled` off never arms the ladder, and one with
#: `fullscreen_required` off never arms the blocker.
ANTI_CHEAT = {
    "anti_cheat_enabled": True,
    "penalty_per_violation": 5,
    "max_violations": 5,
    "auto_submit_on_max": True,
    "fullscreen_required": True,
    "randomize_questions": False,
    "randomize_options": False,
    "watermark_name": True,
    "block_copy_paste": True,
    "block_right_click": True,
    "block_screenshot": True,
    "allow_calculator": False,
}

#: No window: OPEN on every reading, for ever, which is what "always sittable"
#: means to `exam_window.window_state`.
WINDOW = {"start_at": None, "end_at": None}

#: A demo visitor may sit it, submit it, and sit it again — the seeder voids the
#: old attempts anyway, but an attempt limit of one would make the paper vanish
#: for whoever demoed it between two releases.
MAX_ATTEMPTS = 5
PUBLISH_MODE = "auto"


def is_fixture(title) -> bool:
    """Is this the fixture? The title, stripped and compared exactly.

    Exact rather than a prefix: this decides whether a row is the fixture that may
    be rewritten or a teacher's exam that must be left alone, and a teacher who
    titles their paper \"Latihan Ujian ScanGrade Kelas 8\" means theirs.
    """
    return str(title or "").strip() == TITLE


# ── the write ────────────────────────────────────────────────────────────────
#
# Here rather than in `manage.py`, next to the spec it is written from, for two
# reasons. The first is that the smoke test and the writer have to agree, and one
# module cannot drift from itself. The second is practical: `manage.py` builds the
# whole app at import time, and building the app starts the retention loop, which
# purges the moment it starts — so anything that had to go through `manage.py`
# could not be exercised from a laptop against the real database without risking a
# destructive pass. This function takes a Supabase client and nothing else.

def _find_or_create(supabase, payload: dict, school_id: str, say) -> str | None:
    rows = (supabase.table("exams").select("id,title")
            .eq("school_id", school_id).execute().data or [])
    found = [row["id"] for row in rows if is_fixture(row.get("title"))]
    if found:
        exam_id = found[0]
        supabase.table("exams").update(payload).eq("id", exam_id).execute()
        if len(found) > 1:
            # Left alone on purpose — deleting a row a teacher may have made is not
            # this function's business — but named, because a school with two
            # fixtures is one where the check may open the other one.
            say(f"   note: {len(found)} exams carry the fixture title; only the "
                f"first is repaired")
        return exam_id
    created = supabase.table("exams").insert(payload).execute().data or []
    return created[0]["id"] if created else None


def ensure(supabase, school_id: str, say=print) -> dict | None:
    """Make sure this school has the one exam a release may sit.

    Idempotent and *repairing*: the fixture is found by its own title and written
    over rather than inserted again (a run per release would otherwise leave a
    shelf of identical papers), and every field the check depends on is written on
    every run — no window, the anti-cheat flags back on, the assignment back to
    every class in the school. "The fixture exists" and "the fixture is sittable"
    are different claims, and only the second one keeps a gate honest: a teacher
    who set an end date on the demo paper, or turned its anti-cheat off, must not
    be able to close the gate by accident.

    Returns what it did, or None when the school cannot hold one (no classes, no
    teacher) — a school the check has nothing to open on rather than an error.
    """
    subject = (supabase.table("subjects").select("id")
               .eq("school_id", school_id).eq("name", SUBJECT)
               .limit(1).execute().data or [])
    subject_id = subject[0]["id"] if subject else None

    classes = [c["id"] for c in (supabase.table("classes").select("id")
                                 .eq("school_id", school_id)
                                 .order("name").execute().data or [])]
    if not classes:
        say("   ⚠️  no classes in this school — a fixture assigned to nobody is "
            "refused by the door, skipped")
        return None

    # Who owns it: `exams.teacher_id` is NOT NULL, and a fixture with no owner is
    # one no teacher can open. The subject's own teacher when the school has one,
    # otherwise any guru.
    teacher_id = None
    if subject_id:
        assigned = (supabase.table("teacher_assignments").select("teacher_id")
                    .eq("school_id", school_id).eq("subject_id", subject_id)
                    .limit(1).execute().data or [])
        teacher_id = assigned[0]["teacher_id"] if assigned else None
    if not teacher_id:
        gurus = (supabase.table("profiles").select("id")
                 .eq("school_id", school_id).eq("role", "guru")
                 .limit(1).execute().data or [])
        teacher_id = gurus[0]["id"] if gurus else None
    if not teacher_id:
        say("   ⚠️  no teacher in this school — nothing can own the fixture, skipped")
        return None

    exam_id = _find_or_create(supabase, {
        "teacher_id": teacher_id, "school_id": school_id,
        "title": TITLE, "subject": SUBJECT, "subject_id": subject_id,
        "duration_minutes": DURATION_MINUTES,
        "total_questions": TOTAL_QUESTIONS,
        "passing_score": PASSING_SCORE,
        "status": "active", "is_published": True, "publish_mode": PUBLISH_MODE,
        "question_types": QUESTION_TYPES, "answer_key": ANSWER_KEY,
        "question_weights": QUESTION_WEIGHTS,
        "class_ids": classes, "max_attempts": MAX_ATTEMPTS,
        **WINDOW, **ANTI_CHEAT,
    }, school_id, say)
    if not exam_id:
        say("   ⚠️  the fixture could not be created")
        return None

    # Nothing to lose. A student who has already submitted it is not offered it
    # again, so the fixture would vanish for the very account the check signs in
    # as. The attempt is *voided*, never deleted: `retracted` is the app's own word
    # for "this attempt does not stand and the paper may be sat again", and
    # `open_sitting` reopens it with a fresh clock and no answers.
    voided = 0
    try:
        attempts = (supabase.table("submissions").select("id,status")
                    .eq("exam_id", exam_id).execute().data or [])
        voided = len([row for row in attempts if row.get("status") != "retracted"])
        if voided:
            (supabase.table("submissions").update({"status": "retracted"})
             .eq("exam_id", exam_id).neq("status", "retracted").execute())
    except Exception as error:
        say(f"   ⚠️  a standing attempt could not be voided: {str(error)[:60]}")

    say(f"   📝 {TITLE} → {exam_id[:8]}… ({len(classes)} class(es), "
        f"{voided} attempt(s) voided)")
    return {"exam_id": exam_id, "classes": len(classes), "voided": voided}
