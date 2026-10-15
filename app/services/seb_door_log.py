"""Every refusal at the SEB door, kept as a record and never as a charge.

The question this answers, and why nothing answered it
------------------------------------------------------
A teacher turns SEB on for a paper, hands out the ``.seb`` file, and then the room
half-opens. Today the only trace of that is one line in the server's log —
``"SEB refused: exam %s without a matching Config Key or claim"`` — stamped with the
exam and nothing else, so "how many of my pupils could not get in, and when" is a
question you cannot ask the log twice. The panel shows the switch, the keys and the
file; it says nothing about whether the file worked.

So this module records each refusal, per exam, with the pupil and the moment, and
offers one reader each to the two surfaces a teacher already has open: the SEB panel
and the results header.

Where a refusal is *decided* — and why not at the exam door
----------------------------------------------------------
The obvious place to write a row is the exam door, where
``app/routes/student.py`` redirects a client it cannot prove. It is the wrong place,
and measurably so: SEB for macOS/iOS runs on WKWebView, which cannot attach the
Config Key to an HTTP request at all, so on an iPad **every honest visit** is
redirected to the handshake page and then admitted. A row written there would count
that pupil as turned away, on every paper, for ever — a number that is not merely
noisy but wrong in the direction that matters (it blames the platform the feature
exists to support).

The door is a router. The *handshake* is where a client either proves itself or does
not, and it has exactly three endings, two of which are refusals:

* the page's own API answered and the value matched -> the paper opens, no row;
* the page's own API answered and the value did not match -> ``config_key_mismatch``,
  recorded by the claim route;
* the page found nothing to answer with — no SEB at all, or its key not ready ->
  ``no_client`` / ``no_key``, recorded by the report route the page calls itself.

One locked-out visit therefore leaves exactly one row, whichever door the client came
through. What the record cannot say is anything about a client that never loaded the
handshake page at all; the module does not guess, and the panel says only what was
decided.

Not a penalty — and the code says so three ways
-----------------------------------------------
A pupil refused at the door has sat nothing: most often they opened the link in
Chrome, or their ``.seb`` file predates an edit the teacher made. Charging them is
charging a child for their device or for the teacher's own file. So:

* every row goes to ``seb_door_refusal`` (migration 064) and **nothing here writes
  ``violation_logs``** — a source-level test asserts that;
* no reason name is a member of
  ``anti_cheat_service.PENALIZED_VIOLATION_TYPES``, asserted the same way, so a
  reason cannot become a charge by being added to the wrong list;
* neither reader returns anything the score, the ladder or the lockout path reads.

Failure is a missing line, never a lost pupil
---------------------------------------------
Both directions are best-effort, the contract ``seb_access.record`` and
``anti_cheat_service.events_for_exam`` already have: a write that fails is logged and
reported as ``False`` and never raised, because a recording failure must not become a
pupil who cannot open their paper or a teacher who cannot open their panel. The
reader returns an empty list rather than raising, and logs through ``get_logger``
rather than ``current_app.logger`` — the results page assembles its rows from
``teacher._exam_results`` with no request context in the exports, where
``current_app`` raises.
"""
from __future__ import annotations

from app.utils.logger import get_logger

logger = get_logger("seb_door_log")

#: The table, named once. A second spelling would be a write the database refuses at
#: the moment a pupil is being turned away.
TABLE = "seb_door_refusal"

#: The reasons the *page* can report about itself, because only the page can see it:
#: there is no SEB JavaScript API in this browser at all, or the API is there and its
#: configuration key is not ready yet. Sent by ``student/seb_claim.html``.
REASON_NO_CLIENT = "no_client"
REASON_NO_KEY = "no_key"
CLIENT_REASONS = (REASON_NO_CLIENT, REASON_NO_KEY)

#: The reason the *server* decides, at the handshake route: a client that answered,
#: with a value that does not match the key stored on the exam. In practice this is
#: the stale ``.seb`` file — the teacher edited a switch the file mirrors after the
#: file was distributed, and the panel already warns about exactly that state.
REASON_KEY_MISMATCH = "config_key_mismatch"

#: Every reason this release writes. A reason outside it is still *shown* — the
#: unknown-vocabulary rule ``anti_cheat_service._as_event`` follows — because
#: recording an event the report silently drops is how a log stops being evidence.
REASONS = (REASON_NO_CLIENT, REASON_NO_KEY, REASON_KEY_MISMATCH)

#: How a recorded reason reads to a teacher, as one (Indonesian, English) pair table.
#: One table, so the SEB panel and any future surface cannot describe the same refusal
#: two ways — and so a reason with no sentence is a failing test rather than a blank
#: cell. The pair is the *label*; the raw reason travels beside it, the same shape
#: ``KIND_LABELS`` uses.
REASON_LABELS = {
    REASON_NO_CLIENT: ("Dibuka dari browser biasa, bukan SEB",
                       "Opened in an ordinary browser, not SEB"),
    REASON_NO_KEY: ("SEB terbuka, tetapi kunci konfigurasinya belum siap",
                    "SEB was open but its configuration key was not ready"),
    #: No apostrophe in either half, and that is a rule rather than a taste: the
    #: panel binds a pair as `t('{{ id }}','{{ en }}')`, so a `'` in the text closes
    #: the Alpine string early and breaks the page for every reader. A test refuses
    #: one, which is how this string lost its own apostrophe.
    REASON_KEY_MISMATCH: ("Kunci konfigurasi tidak cocok dengan pengaturan ujian ini "
                          "(berkas .seb mungkin sudah lama)",
                          "The configuration key no longer matches the settings for "
                          "this exam (the .seb file may be stale)"),
}

#: What the reader selects, in one place. Every name here has to exist in migration
#: 064 — a column left out of a select arrives *absent* and never as an error, and a
#: column named that does not exist makes PostgREST refuse the whole read. Both are
#: asserted against the migration text.
ROW_COLUMNS = "id, school_id, exam_id, student_id, reason, user_agent, created_at"

#: The embedded profile, which works only because ``student_id`` declares a foreign
#: key to ``profiles``. Named here so the query and the migration can be checked
#: against each other rather than against a memory.
PROFILE_EMBED = "profiles!left(full_name)"

#: Rows a single exam's panel asks for. The panel lists the most recent ones; past
#: this the counts are still exact (they are taken from the same rows) and the list
#: says so, which is the rule ``attempt_summary.MAX_EVENTS`` follows.
MAX_ROWS = 200


def now_iso() -> str:
    """The writer's clock, in the shape PostgREST stores and reads back."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _rows(query) -> list[dict]:
    """A read, or an empty list. The caller never has to guard one."""
    try:
        return query.execute().data or []
    except Exception as exc:                                     # noqa: BLE001
        logger.warning("seb_door_log: read failed: %s", exc)
        return []


def record(supabase, exam: dict, *, student_id, reason: str,
           user_agent: str | None = None) -> bool:
    """Write one ``seb_door_refusal`` row, and report whether it landed.

    Best-effort and never fatal, and the reason is concrete: this is called on the
    path where a pupil is being turned away. A logging failure there must not become
    a second failure — the pupil still gets the handshake page that tells them what
    to open, and the teacher still gets their panel. What is lost is one line of
    evidence, and the loss is logged rather than swallowed.

    A row with no ``exam_id`` or no ``school_id`` cannot be filtered by the readers
    and is not written at all: a refusal nobody can find is not a record.
    """
    exam = exam or {}
    if not exam.get("id") or not exam.get("school_id"):
        logger.warning("seb_door_log: refusing to record a refusal with no exam/school")
        return False
    entry = {
        "school_id": exam.get("school_id"),
        "exam_id": exam.get("id"),
        "student_id": student_id,
        "reason": reason or REASON_NO_CLIENT,
        "user_agent": (user_agent or "")[:500] or None,
        "created_at": now_iso(),
    }
    try:
        written = _rows(supabase.table(TABLE).insert(entry))
    except Exception as exc:                                     # noqa: BLE001
        logger.warning("seb_door_log: could not record %s for exam %s by %s: %s",
                       reason, exam.get("id"), student_id, exc)
        return False
    if not written:
        logger.warning("seb_door_log: insert for %s on exam %s returned nothing",
                       student_id, exam.get("id"))
        return False
    return True


def _as_row(row: dict) -> dict:
    """One stored refusal as the panel needs it.

    ``at`` is left as the server's ISO stamp — the template-localising ``tz`` filter
    turns it into the school's clock, and a timestamp recomputed in the browser would
    be the one field a pupil could move. ``label`` is the pair from
    :data:`REASON_LABELS`, and an unrecognised reason is echoed rather than dropped.
    """
    reason = str(row.get("reason") or "unknown")
    profile = row.get("profiles") or {}
    if not isinstance(profile, dict):
        profile = {}
    return {
        "student_id": row.get("student_id"),
        "name": profile.get("full_name") or None,
        "reason": reason,
        "label": REASON_LABELS.get(reason, (reason, reason)),
        "user_agent": row.get("user_agent"),
        "at": row.get("created_at"),
    }


def refusals_for_exam(supabase, exam_id: str, limit: int = MAX_ROWS) -> list[dict]:
    """One exam's refusals, newest first.

    One query for the whole paper, like ``anti_cheat_service.events_for_exam``: the
    panel asks once, and a log that cannot be read is an empty list and a logged
    warning rather than an exception thrown at the page.
    """
    try:
        return [_as_row(row) for row in
                _rows(supabase.table(TABLE)
                      .select(f"{ROW_COLUMNS}, {PROFILE_EMBED}")
                      .eq("exam_id", exam_id)
                      .order("created_at", desc=True).limit(limit))]
    except Exception as exc:                                     # noqa: BLE001
        logger.warning("seb_door_log: read failed for exam %s: %s", exam_id, exc)
        return []


def refusal_summary(refusals: list[dict]) -> dict:
    """How many pupils, how many attempts, and the moment of the last one.

    Two numbers rather than one, because the question has two halves: five attempts
    by one pupil is a pupil who mistyped a link, while one attempt each by five pupils
    is a file that reached nobody — and a single total cannot tell them apart. The
    pupil count uses distinct ``student_id``; a row whose profile is gone still counts
    as an attempt, and saying so here is better than inventing a pupil for it.

    ``refused_last_at`` is the newest row's moment, which the reader's own ordering
    (newest first) makes it the first row. An empty list makes **no claim**: zero
    pupils, zero attempts, and ``None`` rather than a timestamp, so a page that says
    nothing about *when* cannot be read as "at the epoch".
    """
    pupils = {str(row["student_id"]) for row in refusals if row.get("student_id")}
    return {
        "refused_pupils": len(pupils),
        "refused_attempts": len(refusals),
        "refused_last_at": refusals[0]["at"] if refusals else None,
    }


# ── the school-wide question ─────────────────────────────────────────────────
#
# A teacher's panel answers "how many of my pupils could not get in" for one paper.
# An admin meets the other half of it the morning of a sitting: *which classes and
# which papers keep turning people away*, asked before the room fills rather than
# after. Migration 064 left the index for exactly this read
# (`idx_seb_door_refusal_school_created`) and said so; what was missing was the
# page, and this is the arithmetic behind it.
#
# Two answers, one shape. A class and a paper are ranked by the **same two
# numbers**, because the same two questions are asked of each: how many *pupils*
# (distinct `student_id`) and how many *attempts*. One pupil who tried five times is
# a pupil who mistyped a link; five pupils who tried once each is a file that
# reached nobody, and a single total cannot tell them apart. Ordering is pupils
# first, then attempts, then the most recent refusal — so a tie is decided by
# evidence rather than by the order a dict happened to be built in.

#: The windows the page offers, in days. A whitelist and not a number off the query
#: string: the reader turns it into a timestamp, and "last 999999 days" is a scan
#: nobody asked for.
WINDOWS = (7, 30, 90)
DEFAULT_WINDOW = 30

#: Rows one school's page reads. Past this the *newest* rows are what is read, the
#: page says the window was truncated, and no count can overstate anything: every
#: number here is taken from the rows in hand.
MAX_SCHOOL_ROWS = 2000

#: How many individual refusals the page lists under the rankings.
RECENT_ROWS = 25

#: The pair a refusal with no usable class renders as. A pupil whose profile was
#: deleted keeps its `student_id` (the migration set that column `ON DELETE SET
#: NULL` on purpose) and therefore has no class — named rather than dropped, because
#: "we cannot tell which class" is a different fact from "no refusals".
NO_CLASS_LABEL = ("Kelas tidak diketahui", "Class unknown")

#: The embedded profile, with the class this page groups by. `class_id` is a plain
#: column here rather than a second embed: grouping happens in this process over rows
#: the page already holds, and a join through `classes` would make the read fail as a
#: whole the day that relationship is renamed.
PROFILE_EMBED_CLASS = "profiles!left(full_name, class_id)"


def window_days(raw) -> int:
    """A window the page offers, from whatever arrived in the query string."""
    try:
        days = int(str(raw))
    except (TypeError, ValueError):
        return DEFAULT_WINDOW
    return days if days in WINDOWS else DEFAULT_WINDOW


def refusals_for_school(supabase, school_id: str, days: int = DEFAULT_WINDOW,
                        limit: int = MAX_SCHOOL_ROWS) -> list[dict]:
    """One school's refusals over one window, newest first.

    One query, filtered by the column migration 064 indexes — and by the school,
    which is the same narrowing its RLS policy makes, so this reader and a future
    one behind the anon key cannot disagree about what a school may see. A read that
    fails is an empty list and a logged warning, never an exception thrown at a page:
    an admin whose log is unreadable must still get their dashboard.
    """
    from datetime import datetime, timedelta, timezone
    if not school_id:
        return []
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        rows = _rows(supabase.table(TABLE)
                     .select(f"{ROW_COLUMNS}, {PROFILE_EMBED_CLASS}")
                     .eq("school_id", school_id)
                     .gte("created_at", since)
                     .order("created_at", desc=True).limit(limit))
    except Exception as exc:                                     # noqa: BLE001
        logger.warning("seb_door_log: read failed for school %s: %s", school_id, exc)
        return []
    return [_as_row(row) | {"exam_id": row.get("exam_id"),
                            "class_id": _row_class(row)}
            for row in rows]


def _row_class(row: dict) -> str | None:
    """The class the refused pupil is in, from the embedded profile."""
    profile = row.get("profiles") or {}
    if not isinstance(profile, dict):
        return None
    class_id = profile.get("class_id")
    return str(class_id) if class_id else None


def _rank(groups: dict, key_name: str) -> list[dict]:
    """Order grouping buckets the one way this page orders anything.

    Pupils first (the headcount a room is run by), then attempts (the file that
    reached nobody), then recency. ``-attempts`` and the string key are the tie
    breakers that make the order total, so two runs over the same rows agree.
    """
    out = []
    for key, bucket in groups.items():
        paid = sorted(bucket["reasons"].items(), key=lambda kv: (-kv[1], kv[0]))
        out.append({
            key_name: key,
            "label": bucket["label"],
            "pupils": len(bucket["pupils"]),
            "attempts": bucket["attempts"],
            "last_at": bucket["last_at"],
            "reason": paid[0][0] if paid else None,
            "reason_label": REASON_LABELS.get(paid[0][0], (paid[0][0], paid[0][0]))
                            if paid else None,
        })
    out.sort(key=lambda row: (-row["pupils"], -row["attempts"],
                              str(row[key_name] or "")))
    return out


def rank_refusals(refusals: list[dict], classes: list[dict] | None = None,
                  exams: dict | None = None) -> dict:
    """Who could not get in, ranked by class and by paper — the whole page's data.

    Pure arithmetic over rows a caller already holds, so the ranking can be tested
    without a database and cannot cost a round trip per row. ``classes`` is
    :func:`app.utils.req_cache.school_classes`' own list (id, name) and ``exams`` is
    an id->title map; a class or a paper that cannot be named is still listed, under
    the ``NO_CLASS_LABEL`` pair or under its id, because a bucket the page cannot
    label is not a bucket to hide.

    Nothing here reads the score, the ladder or the lockout path, and the caller
    writes nothing: this is the same non-punitive contract the writers keep.
    """
    class_names = {str(row.get("id")): (row.get("name") or "")
                   for row in (classes or []) if isinstance(row, dict)}
    exam_titles = {str(k): v for k, v in (exams or {}).items()}

    by_class: dict = {}
    by_exam: dict = {}
    reasons: dict = {}
    pupils: set = set()
    papers: set = set()
    moments: list = []

    for row in refusals or []:
        if not isinstance(row, dict):
            continue
        reason = str(row.get("reason") or "unknown")
        reasons[reason] = reasons.get(reason, 0) + 1
        at = row.get("at")
        if at:
            moments.append(at)
        if row.get("student_id"):
            pupils.add(str(row["student_id"]))
        exam_id = row.get("exam_id")
        if exam_id:
            papers.add(str(exam_id))

        class_id = row.get("class_id")
        # The *name*, or None — never a sentence. A label baked here would be one
        # language's words on a page that follows the reader's toggle, and the
        # template is the only place that knows which half to render.
        by_class.setdefault(class_id, {
            "label": (class_names.get(str(class_id)) if class_id else None) or None,
            "pupils": set(), "attempts": 0, "last_at": None, "reasons": {},
        })
        by_exam.setdefault(exam_id, {
            "label": exam_titles.get(str(exam_id)) or (str(exam_id) if exam_id else ""),
            "pupils": set(), "attempts": 0, "last_at": None, "reasons": {},
        })
        for bucket in (by_class[class_id], by_exam[exam_id]):
            bucket["attempts"] += 1
            bucket["reasons"][reason] = bucket["reasons"].get(reason, 0) + 1
            if row.get("student_id"):
                bucket["pupils"].add(str(row["student_id"]))
            if at and (bucket["last_at"] is None or str(at) > str(bucket["last_at"])):
                bucket["last_at"] = at

    recent = []
    for row in (refusals or [])[:RECENT_ROWS]:
        if not isinstance(row, dict):
            continue
        class_id = row.get("class_id")
        recent.append({
            "student_id": row.get("student_id"),
            "name": row.get("name"),
            "class_label": class_names.get(str(class_id)) if class_id else None,
            "class_missing": not (class_id and class_names.get(str(class_id))),
            "exam_id": row.get("exam_id"),
            "exam_label": exam_titles.get(str(row.get("exam_id")))
                          or (str(row.get("exam_id")) if row.get("exam_id") else ""),
            "label": row.get("label") or REASON_LABELS.get(
                str(row.get("reason") or "unknown"), ("", "")),
            "user_agent": row.get("user_agent"),
            "at": row.get("at"),
        })

    return {
        "classes": _rank(by_class, "class_id"),
        "papers": _rank(by_exam, "exam_id"),
        "reasons": [{"reason": reason, "count": count,
                     "label": REASON_LABELS.get(reason, (reason, reason))}
                    for reason, count in sorted(reasons.items(),
                                                key=lambda kv: (-kv[1], kv[0]))],
        "recent": recent,
        "summary": {
            "refused_pupils": len(pupils),
            "refused_attempts": len(refusals or []),
            "papers": len(papers),
            "classes": len([k for k in by_class if k]),
            "refused_last_at": max(moments) if moments else None,
            "truncated": len(refusals or []) >= MAX_SCHOOL_ROWS,
        },
    }


def exam_titles(supabase, exam_ids) -> dict:
    """The titles of the papers a window mentions, in one read.

    One query for the whole page rather than one per row, and an id that has no row
    is simply absent — the ranking falls back to the id, so a paper deleted between
    the two reads is listed rather than dropped.
    """
    ids = [str(i) for i in (exam_ids or []) if i]
    if not ids:
        return {}
    try:
        rows = _rows(supabase.table("exams").select("id, title").in_("id", ids))
    except Exception as exc:                                     # noqa: BLE001
        logger.warning("seb_door_log: could not read exam titles: %s", exc)
        return {}
    return {str(row.get("id")): row.get("title") for row in rows
            if isinstance(row, dict) and row.get("id")}


def attach_refusals(supabase, exam_id: str, stats: dict) -> None:
    """Put the refusal tally on ``stats`` for the results header.

    The header chip is where a teacher meets it — beside the participant count, the
    late chip and the leaving chip — and it is a *headcount of pupils*, the same shape
    ``anti_cheat_service`` gives ``stats['away']``, because "2 pupils could not get in"
    is the sentence a room is run by. ``refused_attempts`` travels with it so the page
    can distinguish two pupils from two attempts if it ever needs to.

    Called from ``teacher._exam_results`` with no request context in the printed sheet
    and the exports, which is why every log line in this module goes through
    ``get_logger``.
    """
    stats.update(refusal_summary(refusals_for_exam(supabase, exam_id)))


__all__ = [
    "CLIENT_REASONS", "DEFAULT_WINDOW", "MAX_ROWS", "MAX_SCHOOL_ROWS",
    "NO_CLASS_LABEL", "PROFILE_EMBED", "PROFILE_EMBED_CLASS", "REASONS",
    "REASON_KEY_MISMATCH", "REASON_LABELS", "REASON_NO_CLIENT", "REASON_NO_KEY",
    "RECENT_ROWS", "ROW_COLUMNS", "TABLE", "WINDOWS",
    "attach_refusals", "exam_titles", "now_iso", "rank_refusals", "record",
    "refusal_summary", "refusals_for_exam", "refusals_for_school", "window_days",
]
