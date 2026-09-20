"""The item analysis as a spreadsheet and as a filed report.

`item_analysis` answers what a paper's questions did; a school then has to *keep*
that answer. Two documents, because they are used differently: a CSV a teacher
opens in a spreadsheet to sort and filter, and a PDF that goes in the exam file
next to the answer sheets.

Both are built from the same `Analysis` object the screen renders, so a number in
the file and the number on the page cannot disagree — there is no second pass over
the submissions here.

Language: these are server-rendered, so the toggle's Alpine binding cannot reach
them. Each takes a `lang` and picks the label table for it, and the print documents
in this app already open in Indonesian by default; a PDF in the exam file is read
long after whoever exported it has closed the tab, so it says which language it is
in on its own header line.
"""
from __future__ import annotations

import csv
import io
from typing import Any, Mapping

#: One label table per language. Kept as pairs-by-key rather than two dicts so a
#: missing translation is a KeyError here and not an Indonesian word on a page
#: that said it was English.
_LABELS: dict[str, dict[str, str]] = {
    "id": {
        "title": "Analisis Butir Soal",
        "summary": "Ringkasan",
        "items": "Statistik butir soal",
        "people": "Kemampuan murid (skala logit)",
        "distractors": "Sebaran pilihan jawaban",
        "splits": "Daya beda kelompok atas dan bawah",
        "notes": "Catatan dan batas",
        "students": "Jumlah murid",
        "item_count": "Jumlah soal",
        "objective": "Soal objektif",
        "essays": "Soal esai",
        "papers": "Kertas dengan data",
        "mean": "Rata-rata nilai",
        "sd": "Simpangan baku nilai",
        "alpha": "Alpha Cronbach",
        "kr20": "KR-20",
        "sem": "SEM (kesalahan baku ukur)",
        "person_rel": "Reliabilitas murid",
        "person_sep": "Separasi murid",
        "item_rel": "Reliabilitas butir",
        "item_sep": "Separasi butir",
        "measure_mean": "Rata-rata logit murid",
        "measure_sd": "Simpangan baku logit murid",
        "extremes": "Murid skor ekstrem",
        "no": "No",
        "kind": "Tipe",
        "marks": "Bobot",
        "answered": "Dijawab",
        "missing": "Kosong",
        "pct": "Tingkat kesulitan (P)",
        "full": "Skor penuh",
        "disc": "Daya beda (D)",
        "pbis": "Korelasi butir-total",
        "measure": "Logit (b)",
        "se": "SE",
        "t": "t",
        "p_value": "p",
        "infit": "Infit MNSQ",
        "outfit": "Outfit MNSQ",
        "flag": "Catatan butir",
        "name": "Nama",
        "raw": "Skor",
        "possible": "Maksimal",
        "measure_p": "Logit (θ)",
        "score": "Nilai",
        "extreme": "Ekstrem",
        "option": "Pilihan",
        "count": "Dipilih",
        "key": "Kunci",
        "upper": "Kelompok atas",
        "lower": "Kelompok bawah",
        "df": "df",
        "yes": "ya",
        "no_word": "tidak",
        "empty": "belum ada data untuk dianalisis",
        "before": "Turunkan versi bahasa di atas berkas, bukan di dalamnya: berkas ini dibuat ",
        "language": "Bahasa laporan",
        "generated": "Dibuat",
        "school": "Sekolah",
        "teacher": "Guru",
        "exam": "Ujian",
    },
    "en": {
        "title": "Item Analysis",
        "summary": "Summary",
        "items": "Item statistics",
        "people": "Student ability (logit scale)",
        "distractors": "Option distribution",
        "splits": "Upper and lower group separation",
        "notes": "Notes and limits",
        "students": "Students",
        "item_count": "Questions",
        "objective": "Objective questions",
        "essays": "Essay questions",
        "papers": "Papers with data",
        "mean": "Mean mark",
        "sd": "SD of marks",
        "alpha": "Cronbach's alpha",
        "kr20": "KR-20",
        "sem": "SEM (standard error of measurement)",
        "person_rel": "Student reliability",
        "person_sep": "Student separation",
        "item_rel": "Item reliability",
        "item_sep": "Item separation",
        "measure_mean": "Mean student logit",
        "measure_sd": "SD of student logits",
        "extremes": "Extreme-score students",
        "no": "No",
        "kind": "Type",
        "marks": "Marks",
        "answered": "Answered",
        "missing": "Blank",
        "pct": "Difficulty (P)",
        "full": "Full credit",
        "disc": "Discrimination (D)",
        "pbis": "Item-total correlation",
        "measure": "Logit (b)",
        "se": "SE",
        "t": "t",
        "p_value": "p",
        "infit": "Infit MNSQ",
        "outfit": "Outfit MNSQ",
        "flag": "Item note",
        "name": "Name",
        "raw": "Score",
        "possible": "Possible",
        "measure_p": "Logit (θ)",
        "score": "Mark",
        "extreme": "Extreme",
        "option": "Option",
        "count": "Chosen",
        "key": "Key",
        "upper": "Upper group",
        "lower": "Lower group",
        "df": "df",
        "yes": "yes",
        "no_word": "no",
        "empty": "no data to analyse yet",
        "before": "Lower the language version, not the file: this report was produced ",
        "language": "Report language",
        "generated": "Generated",
        "school": "School",
        "teacher": "Teacher",
        "exam": "Exam",
    },
}

#: The question kinds, as a reader says them. Keyed by `question_types.KIND_*`.
KIND_LABELS: dict[str, dict[str, str]] = {
    "choice": {"id": "Pilihan ganda", "en": "Multiple choice"},
    "truefalse": {"id": "Benar/Salah", "en": "True/false"},
    "match": {"id": "Menjodohkan", "en": "Matching"},
    "dragdrop": {"id": "Seret & lepas", "en": "Drag and drop"},
    "ordering": {"id": "Mengurutkan", "en": "Ordering"},
    "essay": {"id": "Esai", "en": "Essay"},
}

#: One sentence per finding flag, so the file says what the number means.
FLAG_LABELS: dict[str, dict[str, str]] = {
    "ok": {"id": "Baik", "en": "Sound"},
    "weak": {"id": "Daya beda lemah", "en": "Weak discrimination"},
    "negative": {"id": "Daya beda negatif", "en": "Negative discrimination"},
    "misfit": {"id": "Menyimpang dari model", "en": "Misfits the model"},
    "extreme_easy": {"id": "Semua benar", "en": "Everyone right"},
    "extreme_hard": {"id": "Semua salah", "en": "Everyone wrong"},
    "unscored": {"id": "Belum ada jawaban", "en": "No answers yet"},
    "unkeyed": {"id": "Kunci belum diisi", "en": "No answer key"},
}

#: And one per note the module raises.
NOTE_LABELS: dict[str, dict[str, str]] = {
    "dichotomous": {
        "id": "Kalibrasi memakai skor penuh/lontok, jadi soal menjodohkan yang "
              "mendapat sebagian masih dihitung sebagai satu butir dikotomi; kolom "
              "klasik tetap memakai nilai sebagiannya.",
        "en": "The calibration is full-credit-or-nothing, so a part-credited "
              "matching question is still one dichotomous item; the classical "
              "columns do use its part credit.",
    },
    "partial_credit": {
        "id": "Ujian ini memberi nilai sebagian untuk jawaban yang sebagian benar.",
        "en": "This exam awards part marks for partly correct answers.",
    },
    "essays_teacher_marked": {
        "id": "Nilai esai berasal dari penilaian guru, bukan dari pengoreksi otomatis.",
        "en": "An essay's share is the teacher's own mark, not the auto-grader's.",
    },
    "unanswered_items": {
        "id": "Ada soal yang belum dijawab satu murid pun.",
        "en": "Some questions have not been answered by a single student.",
    },
    "unkeyed_items": {
        "id": "Ada soal objektif yang kuncinya belum diisi, sehingga belum bisa "
              "dinilai maupun dianalisis.",
        "en": "Some objective questions have no answer key, so they can be neither "
              "marked nor analysed.",
    },
    "missing_not_wrong": {
        "id": "Jawaban kosong dihitung hilang, bukan salah, dan tidak masuk "
              "perhitungan tingkat kesulitan soal itu.",
        "en": "A blank answer counts as missing, not wrong, and stays out of that "
              "question's difficulty.",
    },
    "extreme_items": {
        "id": "Ada soal yang dijawab benar atau salah oleh seluruh murid; angkanya "
              "dihitung dengan koreksi skor ekstrem.",
        "en": "Some questions were answered by every student or by none; their "
              "numbers use the extreme-score correction.",
    },
}


def language(lang: str | None) -> str:
    """`"en"` or `"id"`: this app's print documents default to Indonesian."""
    return "en" if str(lang or "").lower().startswith("en") else "id"


def labels(lang: str | None) -> dict[str, str]:
    """The label table for `lang`, defaulting to Indonesian like the print docs."""
    return _LABELS[language(lang)]


def _pair(table: Mapping[str, Mapping[str, str]], key: str, lang: str) -> str:
    entry = table.get(key) or {}
    return entry.get(lang) or entry.get("id") or key


def _num(value: Any, digits: int = 2) -> str:
    """A number for a cell, or an empty cell when there is none.

    A dash rather than a zero: an item with no data has no difficulty, and `0.00`
    in a spreadsheet is a measurement.
    """
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def analysis_rows(analysis, lang: str = "id") -> list[list[str]]:
    """The item table as rows, header first — the one shape both documents use."""
    lang = language(lang)
    t = labels(lang)
    header = [t["no"], t["kind"], t["marks"], t["answered"], t["missing"], t["pct"],
              t["full"], t["disc"], t["pbis"], t["measure"], t["se"], t["t"],
              t["p_value"], t["infit"], t["outfit"], t["flag"]]
    rows = [header]
    for item in analysis.items:
        rows.append([
            str(item.index + 1),
            _pair(KIND_LABELS, item.kind, lang),
            _num(item.marks, 2),
            str(item.answered),
            str(item.missing),
            _num(item.pct, 1),
            str(item.full),
            _num(item.discrimination, 3),
            _num(item.point_biserial, 3),
            _num(item.measure, 2),
            _num(item.se, 2),
            _num(item.t, 2),
            _num(item.p_value, 4),
            _num(item.infit, 2),
            _num(item.outfit, 2),
            _pair(FLAG_LABELS, item.flag, lang),
        ])
    return rows


def _person_rows(analysis, lang: str) -> list[list[str]]:
    lang = language(lang)
    t = labels(lang)
    rows = [[t["name"], t["raw"], t["possible"], t["measure_p"], t["se"],
             t["score"], t["extreme"]]]
    for person in analysis.people:
        rows.append([
            person.name, str(person.raw), str(person.possible),
            _num(person.measure, 2), _num(person.se, 2),
            _num(person.score, 2), t["yes"] if person.extreme else t["no_word"],
        ])
    return rows


def analysis_csv(analysis, exam: Mapping[str, Any] | None = None,
                 lang: str = "id") -> str:
    """The whole analysis as one CSV: summary, then items, then students.

    One file rather than three because the three sections are read together — a
    teacher sorts the item table and wants the paper's reliability beside it.
    """
    lang = language(lang)
    t = labels(lang)
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")

    exam = exam or {}
    writer.writerow([t["title"], str(exam.get("title") or analysis.title or "")])
    writer.writerow([t["exam"], analysis.code or str(exam.get("id") or "")])
    writer.writerow([t["language"], "English" if lang == "en" else "Bahasa Indonesia"])
    writer.writerow([])

    summary = analysis.summary
    writer.writerow([t["summary"]])
    for key, value, digits in (
        ("students", summary.students, 0),
        ("item_count", summary.items, 0),
        ("objective", summary.objective, 0),
        ("essays", summary.essays, 0),
        ("papers", summary.answered_papers, 0),
        ("mean", summary.total_mean, 2),
        ("sd", summary.total_sd, 2),
        ("alpha", summary.alpha, 3),
        ("kr20", summary.kr20, 3),
        ("sem", summary.sem, 2),
        ("person_rel", summary.person_reliability, 3),
        ("person_sep", summary.person_separation, 2),
        ("item_rel", summary.item_reliability, 3),
        ("item_sep", summary.item_separation, 2),
        ("measure_mean", summary.mean_measure, 3),
        ("measure_sd", summary.sd_measure, 3),
        ("extremes", summary.extremes, 0),
    ):
        writer.writerow([t[key], _num(value, digits)])
    writer.writerow([])

    writer.writerow([t["items"]])
    for row in analysis_rows(analysis, lang):
        writer.writerow(row)
    writer.writerow([])

    # Only the choice questions have options; the rest are absent rather than a
    # header with no rows under it.
    distractored = [item for item in analysis.items if item.distractors]
    if distractored:
        writer.writerow([t["distractors"]])
        writer.writerow([t["no"], t["option"], t["count"], t["key"]])
        for item in distractored:
            for option in item.distractors:
                writer.writerow([str(item.index + 1), option.label, str(option.count),
                                 t["yes"] if option.key else ""])
        writer.writerow([])

    if analysis.splits:
        writer.writerow([t["splits"]])
        writer.writerow([t["no"], t["upper"], t["lower"], t["t"], t["df"], t["p_value"]])
        for split in analysis.splits:
            writer.writerow([str(split.index + 1), _num(split.upper, 3),
                             _num(split.lower, 3), _num(split.t, 2),
                             _num(split.df, 1), _num(split.p, 4)])
        writer.writerow([])

    writer.writerow([t["people"]])
    for row in _person_rows(analysis, lang):
        writer.writerow(row)
    writer.writerow([])

    if analysis.notes:
        writer.writerow([t["notes"]])
        for note in analysis.notes:
            writer.writerow([_pair(NOTE_LABELS, note, lang)])
    return out.getvalue()


def filename(analysis, exam: Mapping[str, Any] | None, suffix: str) -> str:
    """`analisis-<exam>-<code>.<suffix>`, safe for a Content-Disposition header."""
    exam = exam or {}
    raw = f"{exam.get('title') or analysis.title or 'exam'}-{analysis.code or ''}"
    stem = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in raw).strip("-")
    while "--" in stem:
        stem = stem.replace("--", "-")
    return f"analisis-{stem[:60]}.{suffix}" if stem else f"analisis.{suffix}"


def analysis_pdf(analysis, exam: Mapping[str, Any] | None = None,
                 school: str = "", teacher: str = "", lang: str = "id") -> bytes:
    """The analysis as a filed report: identity, summary, items, students, notes.

    Landscape, because the item table is sixteen columns wide and a portrait A4
    would either shrink it past reading or split it across pages.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (KeepTogether, Paragraph, SimpleDocTemplate,
                                    Spacer, Table, TableStyle)

    lang = language(lang)
    t = labels(lang)
    exam = exam or {}
    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["Normal"], fontSize=8, leading=10)
    title = ParagraphStyle("title", parent=styles["Title"], fontSize=15, leading=18,
                           spaceAfter=2)
    heading = ParagraphStyle("heading", parent=styles["Heading2"], fontSize=10.5,
                             leading=13, spaceBefore=8, spaceAfter=3)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4),
                            leftMargin=12 * mm, rightMargin=12 * mm,
                            topMargin=12 * mm, bottomMargin=12 * mm,
                            title=f"{t['title']} - {exam.get('title') or analysis.title}")
    flow: list[Any] = [Paragraph(str(t["title"]), title)]

    identity = [f"{t['exam']}: <b>{exam.get('title') or analysis.title}</b>"]
    if school:
        identity.append(f"{t['school']}: {school}")
    if teacher:
        identity.append(f"{t['teacher']}: {teacher}")
    if analysis.code:
        identity.append(f"ID: {analysis.code}")
    identity.append(f"{t['language']}: {'English' if lang == 'en' else 'Bahasa Indonesia'}")
    flow.append(Paragraph(" &nbsp;|&nbsp; ".join(identity), body))

    summary = analysis.summary
    flow.append(Paragraph(str(t["summary"]), heading))
    pairs = [
        (t["students"], summary.students), (t["item_count"], summary.items),
        (t["objective"], summary.objective), (t["essays"], summary.essays),
        (t["papers"], summary.answered_papers), (t["mean"], summary.total_mean),
        (t["sd"], summary.total_sd), (t["alpha"], summary.alpha),
        (t["kr20"], summary.kr20), (t["sem"], summary.sem),
        (t["person_rel"], summary.person_reliability),
        (t["person_sep"], summary.person_separation),
        (t["item_rel"], summary.item_reliability),
        (t["item_sep"], summary.item_separation),
        (t["measure_mean"], summary.mean_measure),
        (t["measure_sd"], summary.sd_measure),
        (t["extremes"], summary.extremes),
    ]
    # Four columns of label/value pairs; a labelled grid rather than a sentence so
    # a number can be found without reading.
    cells = []
    for name, value in pairs:
        cells.append(f"<font size=7 color='#666666'>{name}</font><br/>{_num(value, 3) or '-'}")
    rows = [cells[i:i + 5] for i in range(0, len(cells), 5)]
    while len(rows[-1]) < 5:
        rows[-1].append("")
    summary_table = Table(rows, colWidths=[52 * mm] * 5)
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

    flow.append(Paragraph(str(t["items"]), heading))
    item_rows = analysis_rows(analysis, lang)
    item_table = Table(item_rows, repeatRows=1)
    item_table.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 6.5),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 7),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f7")),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#d8dee8")),
        ("ALIGN", (2, 1), (-2, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    flow.append(item_table)

    flow.append(Paragraph(str(t["splits"]), heading))
    split_rows = [[t["no"], t["upper"], t["lower"], t["t"], t["df"], t["p_value"]]]
    for split in analysis.splits:
        split_rows.append([str(split.index + 1), _num(split.upper, 3),
                           _num(split.lower, 3), _num(split.t, 2),
                           _num(split.df, 1), _num(split.p, 4)])
    if len(split_rows) == 1:
        flow.append(Paragraph(str(t["empty"]), body))
    else:
        split_table = Table(split_rows, repeatRows=1)
        split_table.setStyle(TableStyle([
            ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 6.5),
            ("FONT", (0, 1), (-1, -1), "Helvetica", 7),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f7")),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#d8dee8")),
            ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
        ]))
        flow.append(split_table)

    flow.append(Paragraph(str(t["people"]), heading))
    people_rows = _person_rows(analysis, lang)
    people_table = Table(people_rows, repeatRows=1)
    people_table.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 6.5),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 7),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f7")),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#d8dee8")),
        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
    ]))
    flow.append(people_table)

    if analysis.notes:
        flow.append(Spacer(1, 4))
        notes = [Paragraph(str(t["notes"]), heading)]
        for note in analysis.notes:
            notes.append(Paragraph("&bull; " + _pair(NOTE_LABELS, note, lang), body))
        flow.append(KeepTogether(notes))

    doc.build(flow)
    return buf.getvalue()
