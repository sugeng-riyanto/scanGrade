"""What happened in one sitting, described so a teacher decides and the page does not.

Three surfaces used to describe the same exam, and two of them decided things. The
live room marked any student with a violation with a red ring and counted them; the
pattern page was titled *Deteksi Curang*, described a repeated wrong answer as
*semakin mencurigakan* and stamped "Aman" on the classes that had none. Neither
could say *when*, and neither could say *this is not proof* — because a verdict has
no room for a rebuttal.

This module is the reading half of migration 035, which until now was **written and
never read**: `attempt_summary`, `attempt_session_events` and `exam_class_baseline`
were filled on every submit and no page asked for them. So the facts here are the
stored ones, measured by `attempt_summary.summarize` at submit time, compared
against the class's own distribution by `attempt_summary.baseline_rows` — not
recomputed from raw answers on a GET.

Two rules the code enforces rather than documents:

**An observation is four things, and the fourth is its own rebuttal.** Every entry
carries the evidence, the class it is compared against, the ordinary explanation
that fits the same numbers (a phone call, the school's own network, a device whose
clock has drifted), a confidence, and a human next step. A number a reader cannot
argue with is a verdict wearing a chart.

**A shared disturbance excuses the individuals inside it.** When half a class is
unreachable inside one minute, the per-student readings *inside that window* are the
disturbance, not the student: those observations drop to the lowest confidence and
the reason is stated. This is the case a per-student ranking gets wrong, and it is
the reason the class comparison exists at all. A class smaller than
`attempt_summary.MIN_BASELINE_N` gets no per-student observation whatsoever — five
students is not a distribution, and the app already refuses to compare one.
"""
from __future__ import annotations

import json
from collections import defaultdict

from app.services import attempt_summary
from app.utils.logger import get_logger

logger = get_logger("session_review")

#: The three words a confidence may be. Deliberately not a number: "0.73 suspicious"
#: reads as a measurement, and there is nothing here that measured that.
CONFIDENCE = ("low", "medium", "high")

#: Keys of the two observations that are about the class rather than a student.
SHARED_KEY = "shared_disturbance"
SMALL_KEY = "small_sample"

#: How much of a class has to be inside one minute before it counts as shared.
SHARED_SHARE = 0.5

#: A metric is "far from the class" at this multiple of the class's own p90, with a
#: floor so a class whose p90 is zero cannot turn a single extra event into a finding.
FAR_MULTIPLE = 2.0
FAR_FLOOR = 2.0

#: The metrics worth a reading, and how to say them. Ordered: the reader meets the
#: plainest one first.
FACTS = (
    ("away_count", "Jumlah meninggalkan halaman", ""),
    ("away_total_ms", "Total waktu di luar halaman", "ms"),
    ("sync_gap_count", "Jeda sinkronisasi", ""),
    ("offline_ms", "Waktu tanpa koneksi", "ms"),
    ("answer_change_count", "Perubahan jawaban", ""),
    ("effective_active_ms", "Waktu aktif efektif", "ms"),
)

#: Columns of `attempt_summary` this page reads. An explicit list, not `*`, so a
#: later column cannot arrive at the template by accident.
SUMMARY_READ_COLUMNS = (
    "attempt_id,exam_id,student_id,events_seen,truncated,sources,"
    + ",".join(name for name, _, _ in FACTS)
    + ",away_short_count,away_long_count,away_unknown_count,"
    "away_max_ms,clock_drift_max_ms,clock_suspect"
)


# ── small helpers ────────────────────────────────────────────────────────────

def _as_list(value) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _number(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _measured(summary: dict) -> bool:
    """Did this sitting's own release record its events?

    A summary whose `sources` lacks the event table says *nothing* about
    connectivity, which is not the same as saying it was zero.
    """
    return attempt_summary.EVENT_TABLE in _as_list(summary.get("sources"))


def fmt(value, unit: str = "") -> str:
    """A number as a person reads it. `None` is a real answer here."""
    number = _number(value)
    if number is None:
        return "belum terukur"
    if unit == "ms":
        seconds = number / 1000.0
        if seconds >= 60:
            return f"{int(seconds // 60)} mnt {int(seconds % 60):02d} dtk"
        return f"{seconds:.0f} dtk"
    return str(int(number)) if number == int(number) else f"{number:.1f}"


# ── the room ─────────────────────────────────────────────────────────────────

def room(roster, submissions, total_questions: int | None = None) -> list[dict]:
    """Every student in the class, in the roster's own order.

    The order is the point: this page must not be skimmable as a ranking, so it is
    alphabetical and nothing here sorts on an event count.
    """
    by_id = {row.get("student_id"): row for row in submissions or []}
    out = []
    for student in roster or []:
        sid = student.get("id")
        sub = by_id.get(sid) or {}
        out.append({
            "id": sid,
            "name": student.get("full_name") or (sid or "")[:12],
            "status": sub.get("status") or "not_started",
            "answers_count": _answered(sub.get("answers")),
            "total_questions": total_questions,
            "updated_at": _stamp(sub.get("updated_at") or sub.get("submitted_at")),
        })
    out.sort(key=lambda row: (row["name"] or "").lower())
    return out


def _answered(answers) -> int:
    if isinstance(answers, str):
        try:
            answers = json.loads(answers)
        except (TypeError, ValueError):
            return 0
    if not isinstance(answers, dict):
        return 0
    count = 0
    for value in answers.values():
        if isinstance(value, dict):
            count += 1
        elif isinstance(value, str) and value:
            count += 1
    return count


def _stamp(value) -> str:
    return str(value or "").split(".")[0].replace("T", " ")


# ── the facts a student's own row carries ────────────────────────────────────

def facts_for(summary: dict | None, baseline_by_metric: dict) -> list[dict]:
    """One student's measurements, each beside the class's own reading."""
    rows = []
    for metric, label, unit in FACTS:
        row = baseline_by_metric.get(metric) or {}
        median = row.get("p50")
        p90 = row.get("p90")
        value = _number((summary or {}).get(metric))
        small = bool(row.get("small_sample"))
        rows.append({
            "metric": metric,
            "label": label,
            "value": fmt(value),
            "comparison": ("kelas terlalu kecil untuk dibandingkan" if small
                           else f"median kelas {fmt(median, unit)}"
                                f" · p90 {fmt(p90, unit)}"),
            "measured": value is not None,
            "far": is_far(value, p90) and not small,
        })
    if summary is not None:
        rows.append({
            "metric": "clock_drift_max_ms",
            "label": "Selisih jam perangkat",
            "value": fmt(summary.get("clock_drift_max_ms"), "ms"),
            "comparison": "perangkat yang jamnya bergeser mudah terlihat di sini",
            "measured": _number(summary.get("clock_drift_max_ms")) is not None,
            "far": bool(summary.get("clock_suspect")),
        })
    return rows


def is_far(value, p90) -> bool:
    """Above the class's own p90, and by enough that it is not one extra event."""
    value, p90 = _number(value), _number(p90)
    if value is None or p90 is None:
        return False
    return value >= max(p90 * FAR_MULTIPLE, p90 + FAR_FLOOR)


# ── the observations ─────────────────────────────────────────────────────────

def _observation(key, title, evidence, comparison, explanation, confidence,
                 follow_up, student_id=None) -> dict:
    return {"key": key, "title": title, "evidence": evidence,
            "comparison": comparison, "explanation": explanation,
            "confidence": confidence, "follow_up": follow_up,
            "student_id": student_id}


def observations(*, summaries, baseline, names=None, sample_size,
                 shared_window=None) -> list[dict]:
    """Every reading, each with its own headwind.

    Pure: it takes the summaries and the baseline and decides nothing about I/O, so
    the wording and the rules can be tested against fabricated rows rather than
    against whoever happened to sit an exam today.
    """
    names = names or {}
    by_metric = {row.get("metric"): row for row in baseline or []}
    found: list[dict] = []
    sample_size = int(sample_size or 0)

    if shared_window:
        share = shared_window.get("share") or 0.0
        found.append(_observation(
            SHARED_KEY, "Gangguan bersama di satu jendela waktu",
            f"{share:.0%} kelas punya kejadian antara "
            f"{_clock(shared_window.get('start'))} dan {_clock(shared_window.get('end'))}",
            "jendela ini milik kelas, bukan milik satu murid",
            "Jaringan sekolah yang goyah, satu perangkat ruangan, atau kejadian di "
            "dalam kelas — pada jam yang sama, dan bukan pilihan murid.",
            "high" if share >= 0.8 else "medium",
            "Tanyakan ke pengawas apa yang terjadi di ruangan pada jam itu sebelum "
            "membaca catatan siapa pun."))

    if sample_size < attempt_summary.MIN_BASELINE_N:
        found.append(_observation(
            SMALL_KEY, "Kelas terlalu kecil untuk dibandingkan",
            f"{sample_size} kertas masuk, dan pembanding kelas baru bermakna mulai "
            f"{attempt_summary.MIN_BASELINE_N}",
            "tidak ada pembanding yang bisa dibuat dari kelas sekecil ini",
            "Sebaran dari beberapa murid saja dipengaruhi satu orang, jadi angka "
            "yang tampak menonjol di sini bisa hanya satu kertas.",
            "high",
            "Baca kertasnya satu per satu; halaman ini tidak bisa membandingkannya."))
        return found

    for summary in summaries or []:
        sid = summary.get("student_id")
        if not _measured(summary):
            found.append(_observation(
                "unmeasured_sitting", "Sitting tanpa catatan kejadian",
                f"tidak ada baris kejadian untuk {names.get(sid, sid)}",
                "hanya penalti yang tercatat untuk kertas ini",
                "Kertas ini ditulis sebelum pencatatan kejadian berjalan, atau "
                "penyimpanan kejadiannya gagal — keduanya soal waktu, bukan perilaku.",
                "low",
                "Jika metrik konektivitas penting untuk kelas ini, ulangi ujiannya "
                "dengan rilis yang mencatat kejadian.", student_id=sid))
            continue

        for metric, label, unit in FACTS:
            row = by_metric.get(metric) or {}
            value = _number(summary.get(metric))
            if row.get("small_sample") or not is_far(value, row.get("p90")):
                continue
            found.append(_observation(
                f"far_{metric}", f"{label} jauh di atas kebiasaan kelas",
                f"{fmt(value, unit)} untuk {names.get(sid, sid)}",
                f"median kelas {fmt(row.get('p50'), unit)} · p90 {fmt(row.get('p90'), unit)}",
                "Notifikasi ponsel, panggilan masuk, popup izin, perpindahan aplikasi "
                "yang tidak disengaja, layar yang tidur, atau jaringan yang buruk di "
                "satu perangkat — semuanya menghasilkan angka seperti ini.",
                "medium" if metric in ("away_count", "away_total_ms") else "low",
                "Tanyakan ke murid apa yang terjadi pada jam itu; bandingkan dengan "
                "catatan kelas, bukan dengan satu kertas.", student_id=sid))

        if _number(summary.get("away_unknown_count")):
            found.append(_observation(
                "unknown_absence", "Ketidakhadiran yang lamanya tidak diukur",
                f"{fmt(summary.get('away_unknown_count'))} kejadian untuk "
                f"{names.get(sid, sid)}",
                "penalti dijatuhkan saat kejadian terlihat, jadi durasinya tidak dicatat",
                "Tidak ada yang salah dengan muridnya di sini: yang hilang adalah "
                "*pengukurannya*. Kejadian yang lewat tenggang dihitung seketika, dan "
                "jamnya tidak sempat berjalan.",
                "low",
                "Kalau lamanya penting, tanyakan langsung — halaman ini tidak bisa "
                "mengatakannya.", student_id=sid))

        if summary.get("truncated"):
            found.append(_observation(
                "truncated_log", "Catatan kejadian terpotong",
                f"lebih dari {attempt_summary.MAX_EVENTS} kejadian tercatat untuk "
                f"{names.get(sid, sid)}",
                "yang tersimpan adalah batas bacaannya, bukan jumlah kejadiannya",
                "Perangkat atau koneksi yang membanjiri log — sering justru tanda "
                "masalah teknis, bukan aktivitas.",
                "low",
                "Jangan membaca jumlah kejadiannya sebagai ukuran; baca urutannya.",
                student_id=sid))

    if shared_window:
        # Everything above was read inside a moment the whole class shared, so it is
        # a description of that moment rather than of whoever it names.
        found = [dict(fact, confidence="low") if fact["key"] != SHARED_KEY else fact
                 for fact in found]

    return found


def _clock(value) -> str:
    return str(value or "")[11:16] or "?"


# ── the shared moment ────────────────────────────────────────────────────────

def shared_window(events, threshold: float = SHARED_SHARE):
    """The fullest minute of the sitting, when it held at least `share` of a class.

    Returns `{"start", "end", "share", "students"}` or `None`. A minute is used
    rather than a window because "the same minute" is what a room-wide outage looks
    like from the server, and because a wider window makes coincidence easier.
    """
    rows = [row for row in events or [] if row]
    if not rows:
        return None
    minute: dict[str, set] = defaultdict(set)
    for row in rows:
        when = _stamp(row.get("server_at") or row.get("created_at"))
        who = row.get("student_id") or row.get("user_id")
        if when and who:
            minute[when[:16]].add(who)
    if not minute:
        return None
    busiest = max(minute, key=lambda key: len(minute[key]))
    inside = minute[busiest]
    seen = {r.get("student_id") or r.get("user_id") for r in rows}
    share = len(inside) / max(1, len(seen))
    if share < threshold:
        return None
    return {"start": f"{busiest}:00", "end": f"{busiest}:59",
            "share": round(share, 2), "students": len(inside)}


# ── the one call the route makes ─────────────────────────────────────────────

def review_for_exam(supabase, exam: dict) -> dict:
    """Everything the page shows for one exam, from the tables that already hold it.

    Every read degrades to an empty list on its own: a dashboard that cannot read one
    table is a dashboard with a gap, and it must not be a page that raises.
    """
    exam_id = exam.get("id")
    class_ids = exam.get("class_ids") or []
    total_q = exam.get("total_questions")
    if isinstance(total_q, str):
        total_q = int(total_q) if total_q.isdigit() else None

    roster, summaries, subs, events = [], [], [], []

    if class_ids:
        try:
            roster = (supabase.table("profiles").select("id,full_name")
                      .in_("class_id", class_ids).eq("role", "murid")
                      .execute().data) or []
        except Exception:                                       # noqa: BLE001
            logger.exception("Could not read the roster for exam=%s", exam_id)
    try:
        subs = (supabase.table("submissions")
                .select("id,student_id,status,answers,submitted_at,updated_at")
                .eq("exam_id", exam_id).execute().data) or []
    except Exception:                                           # noqa: BLE001
        logger.exception("Could not read submissions for exam=%s", exam_id)
    try:
        summaries = (supabase.table(attempt_summary.SUMMARY_TABLE)
                     .select(SUMMARY_READ_COLUMNS)
                     .eq("exam_id", exam_id).execute().data) or []
    except Exception:                                           # noqa: BLE001
        logger.exception("Could not read %s for exam=%s",
                         attempt_summary.SUMMARY_TABLE, exam_id)
    # `attempt_session_events` has no `exam_id`: it is one row per event numbered
    # per attempt, and the attempt is the only way in. Reading it by `exam_id`
    # 400s, the read degrades to empty, and the shared-disturbance rule then never
    # fires on any exam while looking perfectly healthy — a silent hole, which is
    # why the attempts are the join and the student is attached here rather than
    # asked of a table that does not carry it.
    by_attempt = {row.get("id"): row.get("student_id") for row in subs}
    attempt_ids = [row.get("id") for row in subs if row.get("id")]
    events, violations = [], []
    if attempt_ids:
        try:
            events = [dict(row, student_id=by_attempt.get(row.get("attempt_id")))
                      for row in (supabase.table(attempt_summary.EVENT_TABLE)
                                  .select("attempt_id,server_at,occurred_at,kind")
                                  .in_("attempt_id", attempt_ids)
                                  .execute().data) or []]
        except Exception:                                       # noqa: BLE001
            logger.exception("Could not read %s for exam=%s",
                             attempt_summary.EVENT_TABLE, exam_id)
    try:
        # The older half of the same story: a charged absence is a moment too, and
        # this table does carry the exam. Both are read so a release with only one
        # of them still has something to find a shared minute in.
        violations = (supabase.table(attempt_summary.VIOLATION_TABLE)
                      .select("user_id,created_at,violation_type")
                      .eq("exam_id", exam_id).execute().data) or []
    except Exception:                                           # noqa: BLE001
        logger.exception("Could not read %s for exam=%s",
                         attempt_summary.VIOLATION_TABLE, exam_id)

    by_student = {row.get("student_id"): row for row in summaries}
    names = {row.get("id"): row.get("full_name") for row in roster}
    # The class comparison is the app's own arithmetic over the summaries it already
    # read, so this GET writes nothing: a page that stores a baseline is a page whose
    # refresh changes the data it is showing.
    baseline = attempt_summary.baseline_rows(summaries, exam_id=exam_id, class_id="")
    by_metric = {row["metric"]: row for row in baseline}
    window = shared_window(events + violations)

    facts = {sid: facts_for(summary, by_metric) for sid, summary in by_student.items()}
    return {
        "exam_id": exam_id,
        "exam_title": exam.get("title", ""),
        "room": room(roster, subs, total_q),
        "facts": facts,
        "observations": observations(
            summaries=summaries, baseline=baseline, names=names,
            sample_size=len(summaries), shared_window=window),
        "shared_window": window,
        "measured": sum(1 for row in summaries if _measured(row)),
        "sittings": len(summaries),
    }
