"""Who may analyse what — and the statistical report over it.

`/teacher/analytics` used to answer one question: *how did my exams go?* Its query
was pinned to `teacher_id`, which is right for a teacher and wrong for the two
other roles that reach the page. An admin of a school could open it and see an
empty page about somebody else's exams; a super admin could not open it at all.

The rule for who may analyse an exam is not new, and this module does not restate
it: `exam_access.can_manage_exam` already decides who may act on an exam, and the
scope is built by asking it. That is the difference between a page that agrees
with the routes by construction and one that agrees with them until somebody
edits one of the two queries.

The report itself borrows the machinery rather than re-deriving it: every exam's
row comes from `item_analysis.analyse`, the same function `/teacher/analysis/<id>`
renders, so the number in the table and the number on the exam's own page cannot
disagree. What is added here is the table across exams, the totals, and the three
documents (CSV, PDF, print) a school files.
"""
from __future__ import annotations

import io
import statistics
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from app.services import item_analysis
from app.services import analysis_report as report_style
from app.utils.exam_access import can_manage_exam

#: How many exams one report covers. A scope is unbounded — a super admin's is
#: every exam on the box — and a page that tries to analyse all of them is a page
#: that times out on one vCPU. The cap is printed on the page and in the PDF
#: rather than applied quietly: "the newest 40" is a fact about the report.
MAX_EXAMS = 40

#: Exams per Supabase round-trip when the submissions are fetched in bulk.
CHUNK = 25

#: The columns the per-exam analysis needs. Named rather than `*`: PostgREST
#: reads a column left out of a select as *absent* and never as an error, so a
#: forgotten column would silently change a statistic instead of failing.
EXAM_COLUMNS = ("id,title,subject,teacher_id,school_id,created_at,total_questions,"
                "question_types,question_weights,answer_key,passing_score,status")
SUBMISSION_COLUMNS = "exam_id,answers,teacher_feedback,score,final_score,status"

#: The two rows of the item table that are not "this question is measured": a
#: flag is a question that *is* measured and misbehaved, a hole is one that could
#: not be measured at all (no key, nobody answered). Counting them together would
#: let a paper full of empty questions look like a paper full of bad ones.
FLAGGED = ("weak", "negative", "misfit", "extreme_easy", "extreme_hard")
HOLES = ("unkeyed", "unscored")

SCORES = ("0-19", "20-39", "40-59", "60-79", "80-100")

#: The scope, as the page and the documents say it. `en`/`id`, like every other
#: label table in this app, because the reader's language is a request parameter.
SCOPE_LABELS: dict[str, dict[str, str]] = {
    "guru": {"id": "Ujian Anda sendiri", "en": "Your own exams"},
    "admin_sekolah": {"id": "Seluruh ujian di sekolah Anda",
                      "en": "Every exam in your school"},
    "super_admin": {"id": "Seluruh ujian di semua sekolah",
                    "en": "Every exam, across all schools"},
}

LABELS: dict[str, dict[str, str]] = {
    "id": {
        "title": "Analisis Statistik Ujian",
        "scope": "Cakupan",
        "generated": "Dibuat",
        "language": "Bahasa laporan",
        "summary": "Ringkasan",
        "exams": "Ujian",
        "participants": "Peserta",
        "mean": "Rata-rata",
        "sd": "Simpangan baku",
        "pass_rate": "Lulus (%)",
        "questions": "Soal",
        "flagged": "Soal bermasalah",
        "holes": "Soal belum terukur",
        "exams_without_key": "Ujian tanpa kunci",
        "charts": "Grafik",
        "distribution": "Sebaran nilai akhir",
        "mean_chart": "Rata-rata per ujian",
        "table": "Rincian per ujian",
        "notes": "Catatan dan batas",
        "no_data": "belum ada ujian dengan jawaban untuk dianalisis",
        "truncated": ("Dibatasi {n} ujian terbaru dari cakupan ini; urutkan dari "
                      "yang paling baru."),
        "same_numbers": ("Angka per ujian berasal dari analisis yang sama dengan "
                         "halaman analisis butir soal ujian itu."),
        "needs_two": ("Alpha, KR-20 dan kalibrasi logit memerlukan sekurangnya dua "
                      "murid dan dua soal; tanda \u201c-\u201d berarti belum bisa "
                      "dihitung."),
        "pass_mark": "KKM",
        "alpha": "Alpha",
        "kr20": "KR-20",
        "median": "Median",
        "highest": "Tertinggi",
        "lowest": "Terendah",
        "teacher": "Guru",
        "school": "Sekolah",
        "exam": "Ujian",
        "total": "Total",
        "scores": "Nilai",
        "items": "Butir soal",
        "flagged_short": "Bermasalah",
        "holes_short": "Belum terukur",
        "range": "Rentang waktu",
        "legend": ("Lulus = persentase peserta yang mencapai KKM. Alpha = Cronbach "
                   "alpha; KR-20 sama nilainya untuk soal benar/salah. Soal "
                   "bermasalah = daya beda lemah/negatif, menyimpang dari model, "
                   "atau dijawab benar/salah oleh semua murid."),
    },
    "en": {
        "title": "Exam Statistical Analysis",
        "scope": "Scope",
        "generated": "Generated",
        "language": "Report language",
        "summary": "Summary",
        "exams": "Exams",
        "participants": "Participants",
        "mean": "Mean",
        "sd": "Standard deviation",
        "pass_rate": "Pass rate (%)",
        "questions": "Questions",
        "flagged": "Flagged questions",
        "holes": "Unmeasurable questions",
        "exams_without_key": "Exams with no key",
        "charts": "Charts",
        "distribution": "Distribution of final marks",
        "mean_chart": "Mean per exam",
        "table": "Exam by exam",
        "notes": "Notes and limits",
        "no_data": "no exam has answers to analyse yet",
        "truncated": ("Limited to the newest {n} exams in this scope."),
        "same_numbers": ("Every per-exam figure comes from the same analysis the "
                         "exam's own item-analysis page shows."),
        "needs_two": ("Alpha, KR-20 and the logit calibration need at least two "
                      "students and two questions; \u201c-\u201d means it cannot be "
                      "computed yet."),
        "pass_mark": "Pass mark",
        "alpha": "Alpha",
        "kr20": "KR-20",
        "median": "Median",
        "highest": "Highest",
        "lowest": "Lowest",
        "teacher": "Teacher",
        "school": "School",
        "exam": "Exam",
        "total": "Total",
        "scores": "Marks",
        "items": "Items",
        "flagged_short": "Flagged",
        "holes_short": "Unmeasurable",
        "range": "Date range",
        "legend": ("Pass rate = the share of participants reaching the pass mark. "
                   "Alpha = Cronbach's alpha, the same number as KR-20 for "
                   "right/wrong questions. A flagged question has weak or negative "
                   "discrimination, misfits the model, or was answered by everybody "
                   "the same way."),
    },
}


def language(lang: str | None) -> str:
    """`"en"` or `"id"`, defaulting to Indonesian like the other documents."""
    return "en" if str(lang or "").lower().startswith("en") else "id"


def labels(lang: str | None) -> dict[str, str]:
    return LABELS[language(lang)]


def _pair(table: Mapping[str, Mapping[str, str]], key: str, lang: str) -> str:
    entry = table.get(key) or {}
    return entry.get(lang) or entry.get("id") or key


def scope_label(role: str, lang: str = "id") -> str:
    """The scope in words, for the header of the page and of the file.

    One language, because the documents are generated for one reader. The *page*
    must not use this: its copy is re-translated in the browser when the toggle
    moves, and a string baked here would be the one line on the page that stayed
    in the language the server happened to be started in.
    """
    return _pair(SCOPE_LABELS, role, language(lang))


def scope_pair(role: str) -> dict[str, str]:
    """Both languages at once, for the page's `t(id, en)`."""
    entry = SCOPE_LABELS.get(role) or {}
    return {"id": entry.get("id", ""), "en": entry.get("en") or entry.get("id", "")}


def _may_scope(role: str) -> bool:
    return role in SCOPE_LABELS


# ── the scope ────────────────────────────────────────────────────────────────


def exams_in_scope(supabase, role: str, user_id: str, school_id: str | None,
                   limit: int = MAX_EXAMS, *,
                   date_from: str | None = None,
                   date_to: str | None = None) -> list[dict[str, Any]]:
    """The exams this caller may analyse, newest first.

    The query narrows to what the role can reach — a teacher's own rows, a
    school's rows, or everything — and then every row is put through
    `can_manage_exam`, which is the authority. The narrowing is a cost decision;
    the predicate is the permission. A role with no scope gets nothing, and an
    admin with no school on file gets nothing, because that is what
    `can_manage_exam` says about both.

    *date_from* and *date_to* are ISO date strings (``YYYY-MM-DD``). When
    provided the query adds ``created_at >= date_from`` and
    ``created_at < date_to + 1 day`` so that a whole-day range is inclusive.
    """
    if not _may_scope(role) or not user_id:
        return []
    query = supabase.table("exams").select(EXAM_COLUMNS)
    if role == "guru":
        query = query.eq("teacher_id", user_id)
    elif role == "admin_sekolah":
        if not school_id:
            return []
        query = query.eq("school_id", school_id)
    # Date range: `created_at` is ISO-8601, so lexicographic comparison works.
    if date_from:
        query = query.gte("created_at", date_from)
    if date_to:
        # +1 day to make the end date inclusive: 2026-06-30 → >= 2026-07-01
        from datetime import datetime, timedelta
        try:
            end = ((datetime.fromisoformat(date_to).date()
                    + timedelta(days=1)).isoformat())
            query = query.lt("created_at", end)
        except (ValueError, TypeError):
            pass
    rows = (query.order("created_at", desc=True).limit(int(limit)).execute().data
            or [])
    return [row for row in rows
            if can_manage_exam(user_id, role, school_id, row)]


def _submissions(supabase, exam_ids: Sequence[str]) -> dict[str, list[dict]]:
    """Every answer in the scope, in as few round-trips as Supabase allows.

    One query per exam is forty round-trips for a forty-exam scope, on a box that
    answers in tens of milliseconds and has one core. Chunked `.in_` queries make
    it two or three.
    """
    found: dict[str, list[dict]] = {}
    ids = [str(exam_id) for exam_id in exam_ids if exam_id]
    for start in range(0, len(ids), CHUNK):
        chunk = ids[start:start + CHUNK]
        rows = (supabase.table("submissions").select(SUBMISSION_COLUMNS)
                .in_("exam_id", chunk).execute().data or [])
        for row in rows:
            found.setdefault(str(row.get("exam_id")), []).append(row)
    return found


def _names(supabase, table: str, ids: Sequence[str]) -> dict[str, str]:
    """`id -> name` for the teacher and school columns, in one query each."""
    wanted = sorted({str(i) for i in ids if i})
    if not wanted:
        return {}
    column = "full_name" if table == "profiles" else "name"
    try:
        rows = (supabase.table(table).select(f"id,{column}")
                .in_("id", wanted).execute().data or [])
    except Exception:
        return {}
    return {str(row.get("id")): (row.get(column) or "") for row in rows}


# ── the report ───────────────────────────────────────────────────────────────


def _count(value: Any) -> int:
    """A stored count, or 0.

    `total_questions` is text in rows written before the column existed, and the
    fallback below is the last line of a report that has already decided not to
    fail — an `int()` here would take the whole school's page down for one bad
    row, which is exactly the failure the fallback exists to prevent.
    """
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _mark(row: Mapping[str, Any]) -> float | None:
    """A submission's mark, or None where nobody has marked it.

    `0` is a mark and an empty cell is not; the app stores both, and the page has
    always read `final_score` before `score`, because a moderated mark is the one
    that counts.
    """
    for key in ("final_score", "score"):
        value = row.get(key)
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _score_stats(marks: Sequence[float], passing: float) -> dict[str, Any]:
    if not marks:
        return {"count": 0, "mean": None, "median": None, "min": None, "max": None,
                "sd": None, "pass_pct": None}
    ordered = sorted(marks)
    middle = len(ordered) // 2
    median = (ordered[middle] if len(ordered) % 2
              else (ordered[middle - 1] + ordered[middle]) / 2)
    return {
        "count": len(marks),
        "mean": round(sum(marks) / len(marks), 1),
        "median": round(median, 1),
        "min": round(min(marks), 1),
        "max": round(max(marks), 1),
        "sd": round(statistics.stdev(marks), 1) if len(marks) > 1 else None,
        "pass_pct": round(sum(1 for mark in marks if mark >= passing)
                          / len(marks) * 100),
    }


def _bins(marks: Sequence[float]) -> list[int]:
    edges = (20, 40, 60, 80)
    counts = [0, 0, 0, 0, 0]
    for mark in marks:
        for index, edge in enumerate(edges):
            if mark < edge:
                counts[index] += 1
                break
        else:
            counts[4] += 1
    return counts


def report(supabase, role: str, user_id: str, school_id: str | None,
           lang: str = "id", limit: int = MAX_EXAMS, *,
           date_from: str | None = None,
           date_to: str | None = None) -> dict[str, Any]:
    """The whole report: one row per exam in scope, plus the totals and the bins.

    Nothing here is cached at this level — the caller decides that, because the
    cache key depends on the caller's scope and not on the report.

    *date_from* and *date_to* are ISO date strings (``YYYY-MM-DD``) that filter
    the exams by their ``created_at`` timestamp.
    """
    lang = language(lang)
    texts = labels(lang)
    exams = exams_in_scope(supabase, role, user_id, school_id, limit,
                           date_from=date_from, date_to=date_to)
    answers = _submissions(supabase, [exam["id"] for exam in exams])
    teachers = _names(supabase, "profiles", [exam.get("teacher_id")
                                             for exam in exams])
    schools = _names(supabase, "schools", [exam.get("school_id")
                                           for exam in exams])

    rows: list[dict[str, Any]] = []
    all_marks: list[float] = []
    passed = 0
    for exam in exams:
        subs = answers.get(str(exam["id"]), [])
        marks = [mark for mark in (_mark(row) for row in subs) if mark is not None]
        passing = float(exam.get("passing_score") or 70)
        stats = _score_stats(marks, passing)
        all_marks.extend(marks)
        passed += sum(1 for mark in marks if mark >= passing)

        try:
            analysis = item_analysis.analyse(
                _decode(exam), subs if subs else [])
            summary = analysis.summary
            items = len(analysis.items)
            flagged = sum(1 for item in analysis.items if item.flag in FLAGGED)
            holes = sum(1 for item in analysis.items if item.flag in HOLES)
            # `unkeyed` on its own, not `holes`: the totals line that counts an
            # exam as "without a key" has to mean the key, and a paper nobody sat
            # is unmeasurable for a different reason. Counting every hole here
            # called an exam with no submissions an exam with no answer key.
            unkeyed = sum(1 for item in analysis.items if item.flag == "unkeyed")
            alpha, kr20 = summary.alpha, summary.kr20
        except Exception:
            # A single unreadable paper must not take the school's report with it;
            # the row says what could not be measured instead of vanishing.
            items = _count(exam.get("total_questions"))
            flagged = holes = unkeyed = 0
            alpha = kr20 = None

        rows.append({
            "id": exam["id"],
            "title": exam.get("title") or "?",
            "subject": exam.get("subject") or "",
            "teacher": teachers.get(str(exam.get("teacher_id")), ""),
            "school": schools.get(str(exam.get("school_id")), ""),
            "passing": passing,
            "items": items,
            "flagged": flagged,
            "holes": holes,
            "unkeyed": unkeyed,
            "alpha": alpha,
            "kr20": kr20,
            **stats,
        })

    totals = {
        "exams": len(exams),
        "participants": len(all_marks),
        "mean": round(sum(all_marks) / len(all_marks), 1) if all_marks else None,
        "sd": (round(statistics.stdev(all_marks), 1)
               if len(all_marks) > 1 else None),
        "pass_rate": round(passed / len(all_marks) * 100) if all_marks else None,
        "questions": sum(row["items"] for row in rows),
        "flagged": sum(row["flagged"] for row in rows),
        "holes": sum(row["holes"] for row in rows),
        "without_key": sum(1 for row in rows if row["unkeyed"]),
    }
    return {
        "lang": lang,
        "texts": texts,
        "role": role,
        "scope": scope_label(role, lang),
        # The pair the template binds, so the label follows the toggle; `scope`
        # above is the same words frozen for the CSV and the PDF.
        "scope_pair": scope_pair(role),
        "generated": datetime.now(timezone.utc).astimezone(),
        "rows": rows,
        "totals": totals,
        "bins": _bins(all_marks),
        "bin_labels": list(SCORES),
        "truncated": len(exams) >= int(limit),
        "limit": int(limit),
        "date_from": date_from or None,
        "date_to": date_to or None,
    }


def as_payload(data: Mapping[str, Any]) -> dict[str, Any]:
    """The report as JSON-safe cache material.

    The stamp is the one value in here that is not a number or a string, and a
    cache that returned it as a string would break the file name of every
    document built from the second request — which is the request that would have
    been fast.
    """
    payload = dict(data)
    payload["generated"] = data["generated"].isoformat()
    return payload


def from_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The reverse of `as_payload`, for whatever the cache hands back."""
    data = dict(payload)
    stamp = data.get("generated")
    if isinstance(stamp, str):
        try:
            data["generated"] = datetime.fromisoformat(stamp)
        except ValueError:
            data["generated"] = datetime.now(timezone.utc).astimezone()
    return data


def _decode(exam: Mapping[str, Any]) -> dict[str, Any]:
    """An exam row with its JSON columns as objects.

    `item_analysis` and the weights resolver read dicts; a JSON column that
    arrives as text is an empty weight map and a paper of questions worth
    nothing, which is a *plausible* analysis and therefore the dangerous kind of
    mistake.
    """
    import json

    exam = dict(exam)
    for field in ("question_types", "question_weights", "answer_key", "options",
                  "questions"):
        value = exam.get(field)
        if isinstance(value, str):
            try:
                exam[field] = json.loads(value)
            except (ValueError, TypeError):
                pass
    total = _count(exam.get("total_questions"))
    if not (exam.get("question_weights") or {}) and total > 0:
        from app.services.question_types import default_weights
        exam["question_weights"] = default_weights(exam.get("question_types") or {},
                                                   total)
    return exam


# ── the documents ────────────────────────────────────────────────────────────


def _cell(value: Any, digits: int = 1) -> str:
    """A number for a cell, or an empty one. A dash would be a measurement."""
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


CSV_COLUMNS: tuple[tuple[str, str], ...] = (
    ("exam", "title"), ("teacher", "teacher"), ("school", "school"),
    ("participants", "count"), ("mean", "mean"), ("median", "median"),
    ("sd", "sd"), ("lowest", "min"), ("highest", "max"),
    ("pass_mark", "passing"), ("pass_rate", "pass_pct"), ("questions", "items"),
    ("alpha", "alpha"), ("kr20", "kr20"), ("flagged_short", "flagged"),
    ("holes_short", "holes"),
)


def _range_label(data: Mapping[str, Any]) -> str:
    """A human-readable label for the date range, or ``""`` if unfiltered."""
    d_from = data.get("date_from")
    d_to = data.get("date_to")
    if d_from and d_to:
        return f"{d_from} – {d_to}"
    if d_from:
        return f">= {d_from}"
    if d_to:
        return f"<= {d_to}"
    return ""


def report_csv(data: Mapping[str, Any]) -> str:
    """The report as a spreadsheet, in the reader's language.

    A BOM, so Excel opens a teacher's name with an accent in it as UTF-8, and the
    totals as a final row rather than a separate file.
    """
    import csv

    texts = data["texts"]
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    # A header row so the reader knows which period the numbers cover.
    rng = _range_label(data)
    if rng:
        writer.writerow([f"{texts['scope']}: {data['scope']}"])
        writer.writerow([f"{texts.get('generated', 'Generated')}: {data['generated']:%Y-%m-%d} | {texts.get('range', 'Range')}: {rng}"])
        writer.writerow([])
    writer.writerow([texts[key] for key, _field in CSV_COLUMNS])
    for row in data["rows"]:
        writer.writerow([_cell(row[field]) if isinstance(row[field], float)
                         else (row[field] if row[field] is not None else "")
                         for _key, field in CSV_COLUMNS])
    totals = data["totals"]
    writer.writerow([texts["total"], "", "", totals["participants"],
                     _cell(totals["mean"]), "", _cell(totals["sd"]), "", "", "",
                     _cell(totals["pass_rate"]), totals["questions"], "", "",
                     totals["flagged"], totals["holes"]])
    return buffer.getvalue()


def filename(data: Mapping[str, Any], suffix: str) -> str:
    """`analitik-super_admin-20260920.csv` — the scope is in the name.

    When a date range is active the period is appended so the reader can tell
    two files of the same role apart: ``analitik-guru-20260920-20260601-20260630.csv``.
    """
    stem = f"analitik-{data.get('role') or 'ujian'}-{data['generated']:%Y%m%d}"
    d_from = data.get("date_from") or ""
    d_to = data.get("date_to") or ""
    if d_from or d_to:
        stem += f"-{d_from or 'start'}-{d_to or 'end'}"
    return f"{stem}.{suffix}"


def report_pdf(data: Mapping[str, Any]) -> bytes:
    """The report as the document a school files.

    Landscape, like the item analysis, and built on the same drawing primitives —
    `analysis_report` owns the house style, and a second set of charts assembled
    by hand is how two documents from one product stop looking like one product.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer, Table,
                                    TableStyle)

    texts = data["texts"]
    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["Normal"], fontSize=8, leading=10)
    title = ParagraphStyle("title", parent=styles["Title"], fontSize=15, leading=18,
                           spaceAfter=2)
    heading = ParagraphStyle("heading", parent=styles["Heading2"], fontSize=10.5,
                             leading=13, spaceBefore=8, spaceAfter=3)
    cell = ParagraphStyle("cell", parent=body, fontSize=8, leading=10)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4),
                            leftMargin=12 * mm, rightMargin=12 * mm,
                            topMargin=12 * mm, bottomMargin=12 * mm,
                            title=f"{texts['title']} - {data['scope']}")
    flow: list[Any] = [Paragraph(str(texts["title"]), title)]
    identity = [f"{texts['scope']}: <b>{data['scope']}</b>",
                f"{texts['generated']}: {data['generated']:%Y-%m-%d %H:%M}",
                f"{texts['language']}: "
                f"{'English' if data['lang'] == 'en' else 'Bahasa Indonesia'}"]
    rng = _range_label(data)
    if rng:
        identity.append(f"{texts.get('range', 'Range')}: <b>{rng}</b>")
    flow.append(Paragraph(" &nbsp;|&nbsp; ".join(identity), body))

    totals = data["totals"]
    flow.append(Paragraph(str(texts["summary"]), heading))
    pairs = [
        (texts["exams"], totals["exams"]),
        (texts["participants"], totals["participants"]),
        (texts["mean"], totals["mean"]),
        (texts["sd"], totals["sd"]),
        (texts["pass_rate"], totals["pass_rate"]),
        (texts["questions"], totals["questions"]),
        (texts["flagged"], totals["flagged"]),
        (texts["holes"], totals["holes"]),
        (texts["exams_without_key"], totals["without_key"]),
        (texts["scope"], data["scope"]),
    ]
    cells = [Paragraph(f"<font size=7 color='#666666'>{name}</font><br/>"
                       f"{_cell(value) or '-'}", cell) for name, value in pairs]
    grid = [cells[index:index + 5] for index in range(0, len(cells), 5)]
    while len(grid[-1]) < 5:
        grid[-1].append(Paragraph("", cell))
    summary_table = Table(grid, colWidths=[52 * mm] * 5)
    summary_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOX", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#e5e5e5")),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    flow.append(summary_table)

    flow.append(Paragraph(str(texts["charts"]), heading))
    charts = [
        report_style._Chart("bars", str(texts["distribution"]),
                            values=data["bins"], labels=data["bin_labels"],
                            x_label="", y_label=str(texts["participants"])),
        report_style._Chart("bars", str(texts["mean_chart"]),
                            values=[row["mean"] for row in data["rows"]
                                    if row["mean"] is not None],
                            labels=[row["title"] for row in data["rows"]
                                    if row["mean"] is not None],
                            x_label=str(texts["mean"]),
                            y_label=str(texts["mean"])),
    ]
    if any(not chart.empty() for chart in charts):
        flow.append(report_style._ChartGrid(charts, str(texts["no_data"])))
        flow.append(Spacer(1, 3))
        flow.append(Paragraph(str(texts["legend"]), body))
    else:
        flow.append(Paragraph(str(texts["no_data"]), body))

    flow.append(Paragraph(str(texts["table"]), heading))
    header = [texts[key] for key, _field in CSV_COLUMNS]
    table_rows = [header]
    for row in data["rows"]:
        table_rows.append([
            _cell(row[field]) if isinstance(row[field], float)
            else (row[field] if row[field] is not None else "")
            for _key, field in CSV_COLUMNS])
    available = doc.width
    weights = [120, 70, 80, 34, 30, 30, 30, 30, 30, 30, 34, 30, 30, 30, 34, 40]
    table = Table(table_rows, repeatRows=1,
                  colWidths=[available * w / sum(weights) for w in weights])
    table.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 6.5),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 7),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f7")),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#d8dee8")),
        ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    flow.append(table)

    flow.append(Paragraph(str(texts["notes"]), heading))
    notes = [str(texts["same_numbers"]), str(texts["needs_two"])]
    if data["truncated"]:
        notes.append(str(texts["truncated"]).format(n=data["limit"]))
    for note in notes:
        flow.append(Paragraph("&bull; " + note, body))

    doc.build(flow)
    return buffer.getvalue()
