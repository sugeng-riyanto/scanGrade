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

# Imported here rather than inside `analysis_pdf`, where the rest of reportlab is
# loaded: a flowable has to exist as a *class* before the module finishes, and
# `reportlab` is a hard requirement of this app either way.
from reportlab.platypus import Flowable

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
        "charts": "Grafik",
        "item_map": "Peta butir soal",
        "map_hint": "Kanan = dikerjakan hampir semua murid; atas = membedakan murid kuat dan lemah.",
        "difficulty_chart": "Kesulitan soal (logit)",
        "difficulty_hint": "Nol = soal pada rata-rata ujian ini; makin ke kiri makin mudah.",
        "people_chart": "Sebaran kemampuan murid",
        "people_hint": "Satu batang satu rentang logit; murid berskor sempurna atau nol "
                       "tidak punya taksiran hingga.",
        "distractor_chart": "Sebaran pilihan jawaban",
        "distractor_hint": "Satu kolom satu soal, dibagi menurut pilihan yang "
                            "menghasilkan tiap jawaban; potongan bertanda adalah kunci "
                            "soal itu.",
        "pct_axis": "Tingkat kesulitan (%)",
        "disc_axis": "Daya beda (D)",
        "logit_axis": "Logit",
        "chosen_count": "Jumlah pemilih",
        "item_no_axis": "No. soal",
        "no_chart": "belum ada data untuk digambar",
        # The paper table has sixteen columns; their long names are the same
        # sixteen words a teacher reads on the screen, where there is room for
        # them. On A4 the header is the screen's short symbols and the names go
        # into one legend line, because a wrapped header column is a header nobody
        # can match to a number.
        "h_no": "Soal",
        "h_full": "Penuh",
        "h_flag": "Catatan",
        "legend": "P = tingkat kesulitan; D = daya beda; r = korelasi butir-total; "
                  "b = kesulitan dalam logit; SE = galat baku; t/p = beda kelompok "
                  "atas dan bawah; Infit/Outfit = kecocokan dengan model.",
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
        "charts": "Charts",
        "item_map": "Item map",
        "map_hint": "Right = answered by nearly everyone; up = separates the strong from "
                     "the weak.",
        "difficulty_chart": "Question difficulty (logits)",
        "difficulty_hint": "Zero = a question of average difficulty for this paper; further "
                            "left is easier.",
        "people_chart": "Spread of student ability",
        "people_hint": "One bar per logit range; a paper scoring everything or nothing has "
                        "no finite estimate.",
        "distractor_chart": "Option distribution",
        "distractor_hint": "One column per question, split by the option that earned "
                            "each answer; the marked slice is that question's key.",
        "pct_axis": "Difficulty (%)",
        "disc_axis": "Discrimination (D)",
        "logit_axis": "Logit",
        "chosen_count": "Chosen",
        "item_no_axis": "Question",
        "no_chart": "no data to draw yet",
        "h_no": "No",
        "h_full": "Full",
        "h_flag": "Note",
        "legend": "P = difficulty; D = discrimination; r = item-total correlation; "
                  "b = difficulty in logits; SE = standard error; t/p = upper-versus-lower "
                  "difference; Infit/Outfit = fit to the model.",
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


def analysis_rows(analysis, lang: str = "id", compact: bool = False) -> list[list[str]]:
    """The item table as rows, header first — the one shape both documents use.

    `compact` swaps the header for the symbols the screen prints (P, D, r, b) with
    the words in one legend line. Sixteen full names on a landscape A4 are five
    columns of wrapped text that no reader can line up with the numbers under
    them, and a header that overlaps its neighbour is worse than an abbreviation.
    """
    lang = language(lang)
    t = labels(lang)
    if compact:
        header = [t["h_no"], t["kind"], t["marks"], t["answered"], t["missing"],
                  "P", "Penuh" if lang == "id" else "Full", "D", "r", "b", "SE", "t",
                  "p", "Infit", "Outfit", t["h_flag"]]
    else:
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


# ── the drawings ─────────────────────────────────────────────────────────────
#
# A filed analysis is read as a shape before it is read as a number: "which
# questions were too hard" is a picture first. reportlab's own chart classes are
# configured through a dozen attributes and fail *quietly* — a chart with no data
# renders an empty box, and a wrong axis range renders a plausible one — so these
# four drawings are made here, straight onto the canvas, from the same numbers the
# tables and the screen use. Every range comes from the data, an axis that nobody
# set is not invented, and a chart with nothing to draw says so in words.

#: Ink. Deliberately few: a chart that needs a legend to be read is a table.
_ACCENT = "#c2410c"      # bars and dots — the measurements
_KEY = "#0f766e"        # the option a question is keyed to
_MUTED = "#64748b"      # axes, ticks, captions
_GRID = "#e2e8f0"       # the frame


def _round_tick(value: float) -> str:
    """A tick label: integers as integers, otherwise one decimal."""
    return f"{value:.0f}" if abs(value - round(value)) < 1e-9 else f"{value:.1f}"


class _Chart:
    """One drawing, described by its data rather than by hundreds of attributes."""

    def __init__(self, kind: str, title: str, values=None, labels=None, keys=None,
                 x_label: str = "", y_label: str = "", points=None, columns=None,
                 column_keys=None):
        self.kind = kind          # "bars" | "hist" | "scatter" | "stacked"
        self.title = title
        self.values = list(values or [])
        self.labels = list(labels or [])
        self.keys = list(keys or [])          # True where a bar is a question's key
        self.x_label = x_label
        self.y_label = y_label
        self.points = list(points or [])      # (x, y, label) for the scatter
        # One entry per column, each a list of values stacked bottom-up, with a
        # matching `column_keys` saying which entry is the key. Per column and
        # not one flag list for the whole chart: a matching question and a
        # four-option multiple choice do not have the same options in the same
        # order, so a single list would mark the wrong slice on one of them.
        self.columns = [list(column) for column in (columns or [])]
        self.column_keys = [list(flags) for flags in (column_keys or [])]

    def empty(self) -> bool:
        if self.kind == "scatter":
            return not self.points
        if self.kind == "stacked":
            return not any(any(column) for column in self.columns)
        return not self.values


class _ChartGrid(Flowable):
    """A row of charts, drawn as one flowable.

    One flowable rather than a Table of Drawings: a Table decides a cell's size
    from its own idea of the flowable, and a chart that disagrees with that is a
    chart that silently overlaps the next cell.
    """

    def __init__(self, charts: list[_Chart], empty_text: str, columns: int = 2,
                 box_height: float = 132.0, gap: float = 10.0):
        super().__init__()
        self.charts = [c for c in charts if not c.empty()]
        self.empty_text = empty_text
        self.columns = max(1, columns)
        self.box_height = box_height
        self.gap = gap
        self.width = 0.0
        self.rows = (len(self.charts) + self.columns - 1) // self.columns

    def wrap(self, avail_width, avail_height):
        self.width = avail_width
        self.height = (self.rows * self.box_height
                       + max(0, self.rows - 1) * self.gap) if self.charts else 0.0
        return self.width, self.height

    def draw(self):
        if not self.charts:
            return
        box_w = (self.width - (self.columns - 1) * self.gap) / self.columns
        for index, chart in enumerate(self.charts):
            row, col = divmod(index, self.columns)
            x = col * (box_w + self.gap)
            y = self.height - (row + 1) * self.box_height - row * self.gap
            self._draw_box(chart, x, y, box_w, self.box_height)

    # ── the frame every chart shares ─────────────────────────────────────────

    def _draw_box(self, chart: _Chart, x0: float, y0: float, w: float, h: float):
        canvas = self.canv
        canvas.saveState()
        canvas.setStrokeColor(_GRID)
        canvas.setLineWidth(0.4)
        canvas.rect(x0, y0, w, h, stroke=1, fill=0)
        canvas.setFont("Helvetica-Bold", 7.5)
        canvas.setFillColor("#334155")
        canvas.drawString(x0 + 5, y0 + h - 10, chart.title)
        if chart.x_label:
            canvas.setFont("Helvetica", 6)
            canvas.setFillColor(_MUTED)
            canvas.drawRightString(x0 + w - 5, y0 + h - 10, chart.x_label)
        # The plot area, inside a margin that leaves room for the tick labels.
        px0, px1 = x0 + 26, x0 + w - 8
        py0, py1 = y0 + 15, y0 + h - 20
        if chart.kind == "scatter":
            self._scatter(chart, px0, py0, px1, py1)
        elif chart.kind == "stacked":
            self._stacked(chart, px0, py0, px1, py1)
        else:
            self._bars(chart, px0, py0, px1, py1)
        canvas.restoreState()

    def _axes(self, canvas, values, px0, py0, px1, py1) -> tuple[float, float]:
        """Vertical scale for a bar chart: 0 is always in range, and inked."""
        low = min(0.0, min(values))
        high = max(0.0, max(values))
        if high - low < 1e-9:
            high = low + 1
        span = high - low
        low -= span * 0.06
        high += span * 0.06

        def y(value):
            return py0 + (value - low) / (high - low) * (py1 - py0)

        canvas.setStrokeColor(_GRID)
        canvas.setLineWidth(0.3)
        canvas.line(px0, y(0), px1, y(0))
        canvas.setStrokeColor(_MUTED)
        canvas.setFont("Helvetica", 5.5)
        canvas.setFillColor(_MUTED)
        for tick in (low, (low + high) / 2, high):
            canvas.line(px0 - 2, y(tick), px0, y(tick))
            canvas.drawRightString(px0 - 3, y(tick) - 1.8, _round_tick(tick))
        return y(0), y

    def _bars(self, chart: _Chart, px0, py0, px1, py1):
        canvas = self.canv
        canvas.setFont("Helvetica", 6)
        _, y = self._axes(canvas, chart.values, px0, py0, px1, py1)
        count = len(chart.values)
        step = (px1 - px0) / count
        bar_w = min(step * 0.72, 22.0)
        # Every label when they fit, otherwise every k-th: a row of overlapping
        # numbers reads as noise, which is worse than a sparse axis.
        every = 1 if count <= 16 else max(1, count // 12)
        label_y = py0 - 6
        for index, value in enumerate(chart.values):
            centre = px0 + step * (index + 0.5)
            top, base = y(value), y(0)
            key = bool(chart.keys[index]) if index < len(chart.keys) else False
            canvas.setFillColor(_KEY if key else _ACCENT)
            canvas.rect(centre - bar_w / 2, min(top, base), bar_w, abs(top - base),
                        stroke=0, fill=1)
            if index % every == 0 and index < len(chart.labels):
                canvas.setFillColor(_MUTED)
                canvas.drawCentredString(centre, label_y,
                                         str(chart.labels[index])[:6])
        canvas.setStrokeColor(_MUTED)
        canvas.line(px0, py0 - 2, px1, py0 - 2)
        if chart.y_label:
            canvas.drawString(px0 - 22, py1 + 2, chart.y_label)

    def _stacked(self, chart: _Chart, px0, py0, px1, py1):
        """One column per question, split by the option that earned each answer.

        A forty-question paper has two hundred options. Drawn as two hundred bars
        side by side — which is what this used to do — each one is a two-point
        hairline and the picture is a grey wall: every question looks identical
        because every bar is the same width, not because the answers are. Drawn as
        forty columns, one per question, the same numbers read: a thick orange
        slice is a distractor that took a real share of the class, a thin one is a
        distractor nobody chose, and a key that does not dominate its column is a
        question whose key is being argued with.

        Counts, not shares, so the column heights also say how many sat the
        question — a question half the class skipped is short, and that is worth
        seeing next to one everybody answered.
        """
        canvas = self.canv
        totals = [sum(column) for column in chart.columns]
        _, y = self._axes(canvas, totals, px0, py0, px1, py1)
        count = len(chart.columns)
        step = (px1 - px0) / count
        bar_w = min(step * 0.78, 26.0)
        for index, column in enumerate(chart.columns):
            centre = px0 + step * (index + 0.5)
            cursor = 0.0
            for option, value in enumerate(column):
                if not value:
                    continue
                flags = (chart.column_keys[index]
                         if index < len(chart.column_keys) else [])
                keyed = bool(flags[option]) if option < len(flags) else False
                canvas.setFillColor(_KEY if keyed else _ACCENT)
                bottom, top = y(cursor), y(cursor + value)
                canvas.rect(centre - bar_w / 2, bottom, bar_w, top - bottom,
                            stroke=0, fill=1)
                cursor += value
            # A hairline between the slices: two shades of one colour stacked with
            # no seam read as one tall bar, which is exactly the reading to avoid.
            if len([v for v in column if v]) > 1:
                canvas.setStrokeColor("#ffffff")
                canvas.setLineWidth(0.5)
                cursor = 0.0
                for value in column[:-1]:
                    cursor += value
                    if cursor:
                        canvas.line(centre - bar_w / 2, y(cursor),
                                    centre + bar_w / 2, y(cursor))
        # Numbered where the numbers fit and not where they would touch: the width
        # of a two-digit label decides how many questions it can sit under.
        canvas.setFont("Helvetica", 6)
        canvas.setFillColor(_MUTED)
        widest = max((canvas.stringWidth(str(label) + " ", "Helvetica", 6)
                      for label in chart.labels), default=0.0)
        every = max(1, int((widest + 1.0) / step) + 1)
        for index in range(0, count, every):
            if index < len(chart.labels):
                canvas.drawCentredString(px0 + step * (index + 0.5), py0 - 6,
                                         str(chart.labels[index]))
        canvas.setStrokeColor(_MUTED)
        canvas.line(px0, py0 - 2, px1, py0 - 2)
        if chart.y_label:
            canvas.drawString(px0 - 22, py1 + 2, chart.y_label)

    def _scatter(self, chart: _Chart, px0, py0, px1, py1):
        """Difficulty (x, 0-100) against discrimination (y).

        Both ranges are fixed by what the numbers mean rather than by the data:
        0-100% is what a difficulty *is*, and 0-1 what a discrimination can be
        (0.2 is the usual floor for a useful item). A range fitted to the data
        would make two questions look far apart when they are nearly identical.
        """
        canvas = self.canv
        xs = [p[0] for p in chart.points]
        ys = [p[1] for p in chart.points]
        x_low, x_high = 0.0, 100.0
        y_low = min(0.0, min(ys))
        y_high = max(0.5, max(ys))

        def x(v):
            return px0 + (v - x_low) / (x_high - x_low) * (px1 - px0)

        def y(v):
            return py0 + (v - y_low) / (y_high - y_low) * (py1 - py0)

        canvas.setStrokeColor(_GRID)
        canvas.setLineWidth(0.3)
        canvas.line(px0, y(0), px1, y(0))            # "no discrimination"
        canvas.line(x(50), py0, x(50), py1)          # a half-right question
        canvas.setStrokeColor(_MUTED)
        canvas.setFont("Helvetica", 5.5)
        canvas.setFillColor(_MUTED)
        for tick in (x_low, 50.0, x_high):
            canvas.line(x(tick), py0, x(tick), py0 - 2)
            canvas.drawCentredString(x(tick), py0 - 8, _round_tick(tick))
        for tick in (y_low, (y_low + y_high) / 2, y_high):
            canvas.line(px0 - 2, y(tick), px0, y(tick))
            canvas.drawRightString(px0 - 3, y(tick) - 1.8, _round_tick(tick))
        # Labels are placed into the first free of four positions, and dropped
        # rather than overlapped when none is free. Two questions in the same
        # corner of the map — which is *normal*, a hard paper clusters there — used
        # to print two numbers on top of each other, and a tangle of digits reads
        # as neither item.
        taken: list[tuple[float, float, float, float]] = []
        for xv, yv, label in chart.points:
            canvas.setFillColor(_ACCENT)
            canvas.circle(x(xv), y(yv), 2.0, stroke=0, fill=1)
            canvas.setFont("Helvetica", 5)
            text = str(label)[:4]
            width = canvas.stringWidth(text, "Helvetica", 5) + 1
            for dx, dy, anchor in ((2.4, -1.6, "left"), (-2.4 - width, -1.6, "left"),
                                   (0, 3.2, "centre"), (0, -5.6, "centre")):
                left = x(xv) + dx if anchor == "left" else x(xv) + dx - width / 2
                box = (left, y(yv) + dy, left + width, y(yv) + dy + 4.2)
                if any(box[0] < t[2] and t[0] < box[2]
                       and box[1] < t[3] and t[1] < box[3] for t in taken):
                    continue
                taken.append(box)
                canvas.setFillColor(_MUTED)
                if anchor == "left":
                    canvas.drawString(box[0], box[1] + 1, text)
                else:
                    canvas.drawCentredString(x(xv) + dx, box[1] + 1, text)
                break
        if chart.y_label:
            canvas.drawString(px0 - 22, py1 + 2, chart.y_label)


def _chart_specs(analysis, lang: str) -> list[_Chart]:
    """The four drawings, from the analysis the screen renders.

    A question with no calibration has no place on the logit chart and no point
    on the item map, so it is left out of *those* drawings and still appears in
    the item table — the same decision `_chart_payload` makes for the page.
    """
    t = labels(lang)
    items = analysis.items
    charts = [
        _Chart("scatter", t["item_map"], x_label=t["pct_axis"],
               y_label=t["disc_axis"],
               points=[(item.pct, item.discrimination, item.index + 1)
                       for item in items
                       if item.pct is not None and item.discrimination is not None]),
        _Chart("bars", t["difficulty_chart"], x_label=t["logit_axis"],
               y_label=t["logit_axis"],
               values=[item.measure for item in items if item.measure is not None],
               labels=[item.index + 1 for item in items if item.measure is not None]),
        _Chart("hist", t["people_chart"], x_label=t["logit_axis"],
               y_label=t["chosen_count"],
               values=[count for _centre, count in analysis.person_bins],
               labels=[_round_tick(centre) for centre, _count in analysis.person_bins]),
    ]
    # The distractor bars read as one question's options in a row: "3A" is option
    # A of question 3, and the keyed option is filled in the other colour, because
    # the shape of a distractor is "chosen by the weak" only next to its key.
    columns, names, keys = [], [], []
    for item in items:
        if not item.distractors:
            continue
        columns.append([option.count for option in item.distractors])
        names.append(item.index + 1)
        keys.append([bool(option.key) for option in item.distractors])
    if columns:
        charts.append(_Chart("stacked", t["distractor_chart"],
                             x_label=t["item_no_axis"], y_label=t["chosen_count"],
                             columns=columns, labels=names, column_keys=keys))
    return charts


def analysis_pdf(analysis, exam: Mapping[str, Any] | None = None,
                 school: str = "", teacher: str = "", lang: str = "id") -> bytes:
    """The analysis as a filed report: identity, summary, charts, items, students.

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
    # A labelled grid rather than a sentence, so a number can be found without
    # reading. Each cell is a **Paragraph**, not a string: reportlab renders a
    # plain string literally, so the markup below used to arrive in the PDF as
    # text — `<font size=7 ...>Students</font><br/>1` printed verbatim, which both
    # looked broken and pushed the last column past the right margin.
    cell_style = ParagraphStyle("cell", parent=body, fontSize=8, leading=10)
    cells = [
        Paragraph(f"<font size=7 color='#666666'>{name}</font><br/>"
                  f"{_num(value, 3) or '-'}", cell_style)
        for name, value in pairs
    ]
    rows = [cells[i:i + 5] for i in range(0, len(cells), 5)]
    while len(rows[-1]) < 5:
        rows[-1].append(Paragraph("", cell_style))
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

    # ── the drawings ─────────────────────────────────────────────────────────
    # Before the tables, and that order is the point: a teacher opens this file
    # to see *which* questions misbehaved, and the answer is a shape. The tables
    # that follow are where the shape gets checked against its numbers.
    charts = _chart_specs(analysis, lang)
    flow.append(Paragraph(str(t["charts"]), heading))
    if any(not chart.empty() for chart in charts):
        flow.append(_ChartGrid(charts, str(t["no_chart"])))
        flow.append(Spacer(1, 3))
        flow.append(Paragraph(" &nbsp;|&nbsp; ".join(
            str(t[key]) for key in ("map_hint", "difficulty_hint", "people_hint",
                                    "distractor_hint")), body))
    else:
        flow.append(Paragraph(str(t["no_chart"]), body))

    flow.append(Paragraph(str(t["items"]), heading))
    flow.append(Paragraph(str(t["legend"]), body))
    item_rows = analysis_rows(analysis, lang, compact=True)
    # Explicit widths, scaled to the content box. Left to itself reportlab sizes a
    # column to its longest cell, so one long "Negative discrimination" or a wide
    # Indonesian flag label pushed the table past the page margin — the defect the
    # summary grid had as well, one column over.
    weights = [16, 62, 30, 36, 30, 34, 30, 38, 42, 34, 24, 26, 30, 34, 36, 96]
    available = doc.width
    item_widths = [available * w / sum(weights) for w in weights]
    item_table = Table(item_rows, repeatRows=1, colWidths=item_widths)
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
        # Percentages, not fractions: reportlab reads a bare number as *points*
        # (only `"30%"` and `"*"` are relative), so `[0.09, 0.22, …]` collapsed
        # every column into the same half-point and printed the table on top of
        # itself.
        split_table = Table(split_rows, repeatRows=1,
                            colWidths=["9%", "22%", "22%", "16%", "13%", "18%"])
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
    people_table = Table(people_rows, repeatRows=1,
                         colWidths=["34%", "11%", "11%", "11%", "11%", "11%", "11%"])
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
