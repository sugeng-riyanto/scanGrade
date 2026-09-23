"""One learner's report as a file: a PDF for a family, a workbook, a CSV.

The page at `/teacher/analysis/<exam>/report/student/<student>` is the document a
teacher reads, and until now the only way to hand it to anybody was the browser's
print dialog — which produces whatever the *screen* happens to be, cannot be
produced at all on a phone, and gives every download the same name. These three
files are built on the server from the same payload the page renders, so the sheet
in a parent's hand and the screen it came from cannot disagree about a number.

What makes that true is the input: `exam_report.learner()` — the object the page
was handed — and nothing else. Every mark here is the learner's own share from
`item_analysis`, so this module does not grade a paper a second way; it lays out
numbers that already exist. The one thing it *derives* is the filename.

Three rules, each a promise the page already makes:

* **A shared copy carries no answer key.** `public=True` is the copy a parent
  builds from a share link: the key is absent from the CSV column list, from the
  PDF's table and from the workbook's sheet. It is decided by these builders rather
  than by the caller, so a route that forgets cannot publish it — and the payload a
  link resolves was already built `with_key=False`, which is the belt to this
  braces.
* **A learner with nothing scoreable draws no bar.** `share` is `None` when a level
  had no marks to divide (an unmarked essay, a question with no key), and `None` is
  not `0`: a zero-length bar beside the class's says "got none of it", which is a
  different sentence from "was not measured".
* **Nothing is invented to fill a table.** A missing class mean, an unset KKM and
  an empty strengths list are printed as what they are, because a generated
  document about a child is read by somebody who will quote it.

The words come from `analysis_report` — the same table the class file reads, via
its public `language()`/`labels()`/`SHEET_NAMES` — so the shared vocabulary
(`exam`, `school`, `marks`, `key`, `kkm`) cannot be spelled one way in the class
report and another way in the child's.
"""
from __future__ import annotations

import csv
import io
import unicodedata
from datetime import datetime
from typing import Any, Mapping, Sequence

from app.services import analysis_report as ar

#: The six outcomes, in the order a reader met them on the page. Named here rather
#: than read out of the payload's dict, which has no order.
_OUTCOMES = ("full", "part", "none", "blank", "unmarked", "unkeyed")

#: `(payload key, label key)` for the three lists of sentences, in the order the
#: page prints them.
_ADVICE = (("strengths", "learner_strengths"),
           ("weaknesses", "learner_weaknesses"),
           ("remediation", "learner_next"))


def _words(lang: str) -> dict[str, str]:
    return ar.labels(ar.language(lang))


def _sheet(key: str, lang: str) -> str:
    """The worksheet name for `key`, from the table the class workbook names its
    own sheets with — so the two workbooks call the same sheet the same thing."""
    return ar.SHEET_NAMES[key][lang]


def _pair(value: Any, lang: str) -> str:
    """One `(id, en)` pair from the payload, in the report's language.

    The payload hands back *pairs* (a band's name, a level's name, every sentence
    in strengths and weaknesses) while the label table hands back *keys* — so this
    takes a pair where `analysis_report`'s own helper takes a table. One of the two
    has to exist, and this is the one the payload needs.
    """
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return str(value[0] if lang == "id" else value[1])
    return "" if value is None else str(value)


def _lines(values: Any, lang: str) -> list[str]:
    return [_pair(line, lang) for line in (values or ())]


def _num(value: Any, digits: int = 2) -> str:
    """A number for a cell, or an empty one — never `None`, never `nan`.

    Trailing zeros are dropped *after* the decimal point and never before it, so
    `30.0` prints `30` and `100.0` prints `100`. Stripping the whole string is how
    a percentage of 100 becomes 1.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    text = f"{number:.{digits}f}"
    whole, dot, frac = text.partition(".")
    if not dot:
        return whole
    frac = frac.rstrip("0")
    return f"{whole}.{frac}" if frac else whole


def _mark(value: Any) -> str:
    """A mark, as a reader writes it: `12.5`, `7`, and `—` when there is none."""
    return "—" if value is None else (_num(value, 2) or "0")


def _stamp(who: Mapping[str, Any]) -> str:
    when = who.get("generated")
    return when.strftime("%Y-%m-%d %H:%M") if isinstance(when, datetime) else ""


# ── the payload, read one way ────────────────────────────────────────────────

def _cover_rows(who: Mapping[str, Any], exam: Mapping[str, Any],
                lang: str) -> list[tuple[str, str]]:
    """The identity lines: which paper, which child, which school.

    A line with nothing behind it is *omitted*, the way the class report's own
    identity block is: `Teacher: —` on a shared copy is a line that says a teacher
    exists and was withheld, which is more information than the blank is worth.

    Deliberately blind to which copy this is. What a shared copy withholds is the
    key, and the key is not on the cover — the teacher's name is, and the shared
    route blanks it on the row it hands in (`cover["teacher_name"] = ""`), which is
    where the redaction rule already lives. A second switch here would be a second
    place for that rule to be half-applied.
    """
    t = _words(lang)
    person = who.get("who") or {}
    band = person.get("band")
    candidates = [
        (t["exam"], str(exam.get("title") or "")),
        (t["learner"], str(person.get("name") or "")),
        (t["learner_final"], _mark(person.get("pct"))),
        (t["band"], f"{getattr(band, 'letter', '')} · "
                    f"{_pair(getattr(band, 'name', None), lang)}" if band else ""),
        (t["learner_class"], str(exam.get("class_name") or "")),
        (t["learner_subject"], str(exam.get("subject") or "")),
        (t["school"], str(exam.get("school_name") or "")),
        (t["teacher"], str(exam.get("teacher_name") or "")),
        ("ID", str(exam.get("id") or "")),
        (t["language"], "English" if lang == "en" else "Bahasa Indonesia"),
        (t["generated"], _stamp(who)),
    ]
    return [(name, value) for name, value in candidates if value.strip()]


def _position_rows(who: Mapping[str, Any], lang: str) -> list[tuple[str, str]]:
    """Where this mark sits: rank, percentile, the class, the standard, the totals."""
    t = _words(lang)
    person = who.get("who") or {}
    klass = who.get("class") or {}
    totals = who.get("totals") or {}
    tied = " (tied)" if person.get("tied") else ""
    rows = [
        (t["learner_rank"],
         f"{person.get('rank', '—')} {t['learner_of']} {klass.get('learners', 0)}{tied}"),
        (t["learner_percentile"], _mark(person.get("percentile_rank"))),
        (t["class_mean"], _mark(klass.get("mean"))),
        (t["learner_standard"],
         _mark(klass.get("kkm")) if klass.get("configured") else t["learner_unset"]),
        (t["measure"], _mark(person.get("measure"))),
        (t["learner_earned"], _mark(totals.get("credited"))),
        (t["learner_possible"], _mark(totals.get("possible"))),
        (t["full"], str((who.get("counts") or {}).get("full", 0))),
    ]
    # The payload's own sentence, which already carries the number: printing the
    # points beside it as well said "+40.0 — +40.0 points above the class average".
    gap = who.get("gap") or {}
    if gap.get("text") and gap.get("points") is not None:
        rows.append((t["learner_gap"], _pair(gap["text"], lang)))
    return rows


def _outcome_rows(who: Mapping[str, Any], lang: str) -> list[tuple[str, str]]:
    """The six outcomes and how many questions came to each.

    All six, including the zeroes: a paper with no blanks should say so, and a
    table that prints only what happened reads as if the rest were not counted.
    """
    counts = who.get("counts") or {}
    states = who.get("states") or {}
    return [(_pair(states.get(state), lang), str(counts.get(state, 0)))
            for state in _OUTCOMES]


def _level_rows(who: Mapping[str, Any], lang: str) -> list[dict[str, Any]]:
    """The level table as data, so all three documents read one shape.

    `share` and `class_share` stay ``None`` where there was nothing to divide. The
    PDF's bars are drawn from *these* two numbers rather than from a second pass
    over the questions, which is what keeps a bar from disagreeing with the figure
    printed beside it.
    """
    rows = []
    for level in who.get("levels") or ():
        rows.append({
            "name": _pair(level.get("name"), lang),
            "questions": level.get("questions", 0),
            "share": level.get("share"),
            "class_share": level.get("class_share"),
            "marks": level.get("marks", 0),
            "possible": level.get("possible", 0),
        })
    return rows


def _question_rows(who: Mapping[str, Any], lang: str, public: bool) -> list[dict[str, Any]]:
    """One row per question, in the order the paper asked them.

    `public` is checked here as well as at the caller, because this is the function
    that would print a key.
    """
    rows = []
    for question in who.get("questions") or ():
        rows.append({
            "no": question.get("no"),
            "kind": str(question.get("kind") or ""),
            "level": _pair(question.get("level_name"), lang),
            "earned": question.get("earned"),
            "mark": question.get("marks"),
            "state": _pair(question.get("state_name"), lang),
            "answer": str(question.get("answered") or ""),
            "key": "" if public else str(question.get("key") or ""),
            "class_pct": question.get("class_pct"),
        })
    return rows


def _method_lines(t: Mapping[str, str], public: bool) -> list[str]:
    """The method note, and the one line that depends on which copy this is."""
    return [t["learner_method_marks"], t["learner_method_blank"],
            t["learner_method_excluded"],
            t["learner_method_shared"] if public else t["learner_method_key"]]


def _statement_rows(who: Mapping[str, Any], lang: str) -> list[str]:
    statement = who.get("statement") or {}
    return [_pair(statement.get("head"), lang), _pair(statement.get("body"), lang)]


# ── the CSV ──────────────────────────────────────────────────────────────────

def learner_csv(who: Mapping[str, Any], exam: Mapping[str, Any] | None = None,
                lang: str = "id", public: bool = False) -> str:
    """The learner's report as one CSV: cover, position, level table, every question.

    One file with sections rather than four files, for the same reason the class
    export is one file: the parts are read together — a teacher scrolls from the
    mark down to the question that cost it. The BOM is added by the route, so Excel
    reads an accented name as a name.
    """
    exam = exam or {}
    lang = ar.language(lang)
    t = _words(lang)
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")

    writer.writerow([t["learner_title"]])
    for name, value in _cover_rows(who, exam, lang):
        writer.writerow([name, value])
    if public:
        writer.writerow([t["learner_shared"]])
    writer.writerow([])

    writer.writerow([t["learner_position"]])
    for name, value in _position_rows(who, lang):
        writer.writerow([name, value])
    writer.writerow([])

    writer.writerow([t["learner_outcomes"]])
    for name, count in _outcome_rows(who, lang):
        writer.writerow([name, count])
    writer.writerow([])

    writer.writerow([t["learner_levels"]])
    writer.writerow([t["level"], t["learner_q_short"], t["learner_share"],
                     t["learner_class_share"], t["learner_mark"]])
    for row in _level_rows(who, lang):
        writer.writerow([row["name"], str(row["questions"]), _num(row["share"], 1),
                         _num(row["class_share"], 1),
                         f"{_mark(row['marks'])} / {_mark(row['possible'])}"])
    writer.writerow([])

    writer.writerow([t["learner_questions"]])
    header = [t["no"], t["kind"], t["level"], t["learner_mark"], t["learner_state"],
              t["learner_answer"], t["key"], t["learner_class_pct"]]
    if public:
        header.remove(t["key"])
    writer.writerow(header)
    for row in _question_rows(who, lang, public):
        cells = [str(row["no"]), row["kind"], row["level"],
                 f"{_mark(row['earned'])} / {_mark(row['mark'])}", row["state"],
                 row["answer"], row["key"], _num(row["class_pct"], 1)]
        if public:
            del cells[6]
        writer.writerow(cells)
    writer.writerow([])

    for key, title in _ADVICE:
        writer.writerow([t[title]])
        for line in _lines(who.get(key), lang):
            writer.writerow([line])
        writer.writerow([])

    writer.writerow([t["learner_statement"]])
    for line in _statement_rows(who, lang):
        writer.writerow([line])
    writer.writerow([])

    writer.writerow([t["learner_method"]])
    for line in _method_lines(t, public):
        writer.writerow([line])
    return out.getvalue()


# ── the workbook ─────────────────────────────────────────────────────────────

#: The level table's columns, as (header label key, row key, number format).
#: Declared as data so the chart's references can name a column by what it holds
#: rather than by a letter kept in step by hand — the same reason the class
#: workbook declares its item columns.
_LEVEL_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("level", "name", "@"),
    ("learner_q_short", "questions", "0"),
    ("learner_share", "share", "0.0"),
    ("learner_class_share", "class_share", "0.0"),
    ("learner_mark", "marks", "0.00"),
    ("learner_possible", "possible", "0.00"),
)

_LEVEL_COLUMN: dict[str, int] = {row_key: index + 1
                                 for index, (_t, row_key, _fmt) in
                                 enumerate(_LEVEL_COLUMNS)}


def learner_xlsx(who: Mapping[str, Any], exam: Mapping[str, Any] | None = None,
                 lang: str = "id", public: bool = False) -> bytes:
    """The learner's report as a workbook: the summary sheet, then every question.

    Two sheets and one drawing. The drawing is the level table as a chart — this
    learner's share beside the class's on the same 0–100 axis — because there is
    exactly one comparison in this document that a picture settles faster than a
    table, and a workbook that plots marks per question is a workbook the teacher
    already has from the class report. It is a *native* chart anchored on the cells
    it plots, so correcting a cell moves the bar; the figures are real numbers for
    the same reason, with a number format for display.
    """
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, Reference
    from openpyxl.styles import Alignment, Font, PatternFill

    exam = exam or {}
    lang = ar.language(lang)
    t = _words(lang)
    head = Font(bold=True)
    shade = PatternFill("solid", fgColor="eef2f7")

    book = Workbook()

    # ── the summary sheet ────────────────────────────────────────────────────
    sheet = book.active
    sheet.title = _sheet("summary", lang)
    sheet.append([t["learner_title"]])
    sheet.cell(row=sheet.max_row, column=1).font = head
    cover_first = sheet.max_row + 1
    for name, value in _cover_rows(who, exam, lang):
        sheet.append([name, value])
        sheet.cell(row=sheet.max_row, column=1).font = head
    if public:
        sheet.append([t["learner_shared"]])
    for row in sheet.iter_rows(min_row=cover_first, max_row=sheet.max_row,
                               min_col=2, max_col=2):
        row[0].alignment = Alignment(wrap_text=True, vertical="top")

    def section(title: str) -> None:
        sheet.append([])
        sheet.append([title])
        cell = sheet.cell(row=sheet.max_row, column=1)
        cell.font = head
        cell.fill = shade

    section(t["learner_position"])
    for name, value in _position_rows(who, lang):
        sheet.append([name, value])

    section(t["learner_outcomes"])
    for name, count in _outcome_rows(who, lang):
        sheet.append([name, _as_number(count)])

    section(t["learner_levels"])
    header_row = sheet.max_row + 1
    sheet.append([t[label] for label, _row, _fmt in _LEVEL_COLUMNS])
    for cell in sheet[header_row]:
        cell.font = head
        cell.fill = shade
    first_level_row = sheet.max_row + 1
    for row in _level_rows(who, lang):
        sheet.append([row[row_key] for _label, row_key, _fmt in _LEVEL_COLUMNS])
        for column, (_label, row_key, _fmt) in enumerate(_LEVEL_COLUMNS, start=1):
            cell = sheet.cell(row=sheet.max_row, column=column)
            if row_key != "name" and isinstance(cell.value, (int, float)):
                cell.number_format = _fmt
    last_level_row = sheet.max_row
    assert _LEVEL_COLUMN["share"] < _LEVEL_COLUMN["class_share"], (
        "the chart plots the two share columns as adjacent series")

    for key, title in _ADVICE:
        section(t[title])
        for line in _lines(who.get(key), lang):
            sheet.append([line])

    section(t["learner_statement"])
    for line in _statement_rows(who, lang):
        sheet.append([line])

    section(t["learner_method"])
    for line in _method_lines(t, public):
        sheet.append([line])

    sheet.column_dimensions["A"].width = 36
    sheet.column_dimensions["B"].width = 64

    # ── the drawing ──────────────────────────────────────────────────────────
    # Only when there is a level to plot: a chart with no series renders as an empty
    # frame, which reads as a rendering fault rather than as "nothing was scoreable".
    if last_level_row >= first_level_row:
        chart = BarChart()
        chart.type = "col"
        chart.title = t["learner_levels"]
        chart.y_axis.title = t["share"]
        chart.x_axis.title = t["level"]
        # The axis is the whole scale, not the data's own range: two learners' files
        # laid side by side have to be comparable, and an axis that rescales says a
        # level at 40% is nearly full.
        chart.y_axis.scaling.min = 0
        chart.y_axis.scaling.max = 100
        data = Reference(sheet, min_col=_LEVEL_COLUMN["share"],
                         max_col=_LEVEL_COLUMN["class_share"],
                         min_row=header_row, max_row=last_level_row)
        labels = Reference(sheet, min_col=_LEVEL_COLUMN["name"],
                           min_row=first_level_row, max_row=last_level_row)
        chart.add_data(data, titles_from_data=True)
        chart.set_categories(labels)
        chart.height, chart.width = 8, 16
        sheet.add_chart(chart, f"G{header_row - 1}")

    # ── the question sheet ───────────────────────────────────────────────────
    items = book.create_sheet(_sheet("items", lang))
    columns = [t["no"], t["kind"], t["level"], t["learner_mark"], t["learner_state"],
               t["learner_answer"], t["learner_class_pct"]]
    if not public:
        columns.append(t["key"])
    items.append(columns)
    for cell in items[1]:
        cell.font = head
        cell.fill = shade
    for row in _question_rows(who, lang, public):
        line = [row["no"], row["kind"], row["level"], row["earned"], row["state"],
                row["answer"], row["class_pct"]]
        if not public:
            line.append(row["key"])
        items.append(line)
        # Both figures are numbers with a format, so the column can be summed: a
        # reader who adds the marks column up must reach the total the cover prints.
        for column in (4, 7):
            cell = items.cell(row=items.max_row, column=column)
            if isinstance(cell.value, (int, float)):
                cell.number_format = "0.00" if column == 4 else "0.0"
    for column, width in zip("ABCDEFGH", (6, 20, 20, 12, 18, 34, 12, 34)):
        items.column_dimensions[column].width = width
    items.freeze_panes = "A2"

    buf = io.BytesIO()
    book.save(buf)
    return buf.getvalue()


def _as_number(text: str) -> Any:
    """A count for a workbook cell, so a column of them can be summed."""
    try:
        return int(text)
    except (TypeError, ValueError):
        return text


# ── the learner's pages ──────────────────────────────────────────────────────
#
# The document a family reads is built as *flowables* rather than as a file, so the
# same pages can be laid into a second document: the class analysis carries them as
# an appendix, and `learner_pdf` wraps the same story in its own A4 portrait sheet.
# Two builders would be two documents about one child that could disagree, which is
# the defect this whole module exists to avoid.

#: How many learners one document carries. A scope is unbounded and a school's
#: paper is not; past this the appendix and the zip would each be a multi-minute
#: build on one vCPU, and both say out loud that they were cut rather than ending
#: quietly at a number that looks like the class's size.
MAX_FILES = 120


def learner_story(who: Mapping[str, Any], exam: Mapping[str, Any] | None = None,
                  lang: str = "id", public: bool = False, *,
                  wide: float | None = None) -> list[Any]:
    """The learner's report as flowables, laid out for a content width.

    Portrait by construction, and the *width* is the only thing a caller may
    choose: this document is eight columns wide and is read by a person holding
    it, so its prose lines and its bars are sized for a narrow page — a caller
    that lays it into a wider frame gets the same document, not a stretched one.

    `wide` defaults to A4 portrait's content width (the sheet `learner_pdf`
    builds), and the appendix passes portrait too: the class report is landscape,
    but a child's page is not, and mixing the two in one file is what a paper
    report has always done.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (CondPageBreak, Flowable, KeepTogether,
                                    Paragraph, Spacer, Table, TableStyle)

    class Shares(Flowable):
        """Two bars for one level: what this learner got, and what the class did.

        reportlab's own chart classes are configured through a dozen attributes and
        fail *quietly*; this draws two rectangles, which is all the comparison
        needs. The two lengths are the two numbers printed in the same row of the
        table — handed in, not re-derived — so a bar cannot disagree with the figure
        beside it. A ``None`` share draws the empty track and nothing in it, which
        is the whole reason this is a drawing rather than a filled cell: a
        zero-length bar and a "not measured" level would otherwise look identical.
        """

        def __init__(self, share, class_share, width: float, height: float = 8.0):
            Flowable.__init__(self)
            self.share = share
            self.class_share = class_share
            self.width = width
            self.height = height

        def wrap(self, availWidth, availHeight):  # noqa: N802 - reportlab's spelling
            return (self.width, self.height)

        def draw(self):
            half = self.height / 2.0
            for index, (value, ink) in enumerate(
                    ((self.share, ar.INK["student"]),
                     (self.class_share, ar.INK["muted"]))):
                bottom = self.height - (index + 1) * half
                self.canv.setFillColor(colors.HexColor(ar.INK["grid"]))
                self.canv.rect(0, bottom, self.width, half - 0.7, stroke=0, fill=1)
                if value is None:
                    continue
                filled = max(0.0, min(float(value), 100.0)) / 100.0 * self.width
                self.canv.setFillColor(colors.HexColor(ink))
                self.canv.rect(0, bottom, filled, half - 0.7, stroke=0, fill=1)

    exam = dict(exam or {})
    if wide is None:
        wide = A4[0] - 28 * mm

    lang = ar.language(lang)
    t = _words(lang)
    styles = getSampleStyleSheet()
    body = ParagraphStyle("lbody", parent=styles["Normal"], fontSize=8.5, leading=11)
    small = ParagraphStyle("lsmall", parent=body, fontSize=7.5, leading=9.5)
    title = ParagraphStyle("ltitle", parent=styles["Title"], fontSize=16, leading=19,
                           spaceAfter=2)
    heading = ParagraphStyle("lheading", parent=styles["Heading2"], fontSize=10.5,
                             leading=13, spaceBefore=9, spaceAfter=3)
    header_style = ParagraphStyle("lhead", parent=body, fontSize=7.5, leading=9.5,
                                  textColor=colors.HexColor(ar.INK["muted"]))
    value_style = ParagraphStyle("lvalue", parent=body, fontSize=9, leading=11.5)
    frame = colors.HexColor("#cccccc")
    inner = colors.HexColor("#e5e5e5")
    rule = colors.HexColor(ar.INK["rule"])

    def grid(rows: list[list[Any]], widths: list[float], valign: str = "MIDDLE",
             **extra) -> Table:
        """A bordered grid, which is what every block in this document is."""
        table = Table(rows, colWidths=widths, **extra)
        table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), valign),
            ("BOX", (0, 0), (-1, -1), 0.4, frame),
            ("INNERGRID", (0, 0), (-1, -1), 0.3, inner),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        return table

    flow: list[Any] = [Paragraph(str(t["learner_title"]), title)]

    cover = [[Paragraph(str(name), header_style), Paragraph(str(value), value_style)]
             for name, value in _cover_rows(who, exam, lang)]
    if public:
        cover.append([Paragraph(str(t["learner_shared"]), header_style),
                      Paragraph("", value_style)])
    flow.append(grid(cover, [38 * mm, wide - 38 * mm]))

    # ── position and totals ─────────────────────────────────────────────────
    flow.append(Paragraph(str(t["learner_position"]), heading))
    cells = [Paragraph(f"<font size=7 color='{ar.INK['muted']}'>{name}</font><br/>"
                       f"{value}", body) for name, value in _position_rows(who, lang)]
    rows = [cells[index:index + 4] for index in range(0, len(cells), 4)]
    while len(rows[-1]) < 4:
        rows[-1].append(Paragraph("", body))
    flow.append(grid(rows, [wide / 4.0] * 4, valign="TOP"))

    # ── the six outcomes ────────────────────────────────────────────────────
    flow.append(Paragraph(str(t["learner_outcomes"]), heading))
    outcomes = [[Paragraph(str(name), small), Paragraph(str(count), small)]
                for name, count in _outcome_rows(who, lang)]
    flow.append(grid([outcomes], [wide / 6.0] * 6))

    # ── the level table, with its two bars ──────────────────────────────────
    flow.append(Paragraph(str(t["learner_levels"]), heading))
    levels = _level_rows(who, lang)
    if levels:
        bars = 40 * mm
        rows = [[t["level"], t["learner_q_short"],
                 f"{t['learner_share']} / {t['learner_class_share']}",
                 t["learner_mark"]]]
        for row in levels:
            rows.append([Paragraph(row["name"], small), str(row["questions"]),
                         Shares(row["share"], row["class_share"], bars - 4 * mm),
                         f"{_mark(row['marks'])} / {_mark(row['possible'])}"])
        table = Table(rows,
                      colWidths=[wide - 14 * mm - bars - 32 * mm, 14 * mm, bars, 32 * mm],
                      rowHeights=[None] + [11] * len(levels))
        table.setStyle(TableStyle([
            ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7),
            ("FONT", (0, 1), (-1, -1), "Helvetica", 7.5),
            ("TEXTCOLOR", (0, 0), (-1, 0), rule),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f7")),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#d8dee8")),
            ("ALIGN", (1, 1), (1, -1), "CENTER"),
            ("ALIGN", (3, 1), (3, -1), "RIGHT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        flow.append(table)
        # Which bar is whose, in words: a reader who has to guess has been shown a
        # decoration rather than a comparison.
        flow.append(Spacer(1, 2))
        flow.append(Paragraph(
            f"<font color='{ar.INK['student']}'>■</font> {t['learner_share']} &nbsp;&nbsp;"
            f"<font color='{ar.INK['muted']}'>■</font> {t['learner_class_share']} "
            f"&nbsp;({t['share']}, 0–100)", small))
    else:
        flow.append(Paragraph(str(t["empty"]), body))

    # ── every question ──────────────────────────────────────────────────────
    flow.append(CondPageBreak(80))
    flow.append(Paragraph(str(t["learner_questions"]), heading))
    questions = _question_rows(who, lang, public)
    if questions:
        header = [t["no"], t["kind"], t["level"], t["learner_mark"],
                  t["learner_state"], t["learner_answer"]]
        if not public:
            header.append(t["key"])
        header.append(t["learner_class_pct"])
        rows = [header]
        for row in questions:
            line = [f"Q{row['no']}", row["kind"], row["level"],
                    f"{_mark(row['earned'])} / {_mark(row['mark'])}", row["state"],
                    row["answer"]]
            if not public:
                line.append(row["key"])
            line.append(_num(row["class_pct"], 1))
            rows.append(line)
        # Explicit widths, scaled to the content box: left to itself reportlab sizes
        # a column to its longest cell, so one long essay answer pushes the table
        # past the right margin — the defect the class item table had too.
        weights = ([7, 17, 19, 13, 17, 34, 13] if public
                   else [7, 16, 18, 12, 16, 26, 15, 10])
        table = Table(rows, repeatRows=1,
                      colWidths=[wide * weight / sum(weights) for weight in weights])
        table.setStyle(TableStyle([
            ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 6.5),
            ("FONT", (0, 1), (-1, -1), "Helvetica", 7),
            ("TEXTCOLOR", (0, 0), (-1, 0), rule),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f7")),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#d8dee8")),
            ("ALIGN", (3, 1), (3, -1), "RIGHT"),
            ("ALIGN", (-1, 1), (-1, -1), "RIGHT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        flow.append(table)
    else:
        flow.append(Paragraph(str(t["empty"]), body))

    # ── what to do with it ──────────────────────────────────────────────────
    for key, title_key in _ADVICE:
        lines = _lines(who.get(key), lang)
        block = [Paragraph(str(t[title_key]), heading),
                 Paragraph("&nbsp;• " + "<br/>&nbsp;• ".join(lines) if lines
                           else str(t["empty"]), body)]
        flow.append(KeepTogether(block))

    flow.append(Paragraph(str(t["learner_statement"]), heading))
    statement = _statement_rows(who, lang)
    flow.append(Paragraph(f"<b>{statement[0]}</b>", body))
    flow.append(Paragraph(statement[1], body))

    flow.append(Paragraph(str(t["learner_method"]), heading))
    for line in _method_lines(t, public):
        flow.append(Paragraph("&nbsp;• " + line, small))

    return flow


def learner_pdf(who: Mapping[str, Any], exam: Mapping[str, Any] | None = None,
                school: str = "", teacher: str = "", lang: str = "id",
                public: bool = False) -> bytes:
    """The learner's report as the sheet a family reads: A4 portrait.

    One A4 page size, one frame, and the same story the appendix lays into the
    class document — so the copy that is handed over and the copy filed inside
    the class report are the same pages, built once.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate

    exam = dict(exam or {})
    # A caller that resolved the school and teacher from the exam row passes them
    # here; one that already has them on the row hands the row. Neither is required
    # to carry both, so each fills what the other left empty.
    if school and not exam.get("school_name"):
        exam["school_name"] = school
    if teacher and not exam.get("teacher_name"):
        exam["teacher_name"] = teacher

    t = _words(ar.language(lang))
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=14 * mm, rightMargin=14 * mm,
                            topMargin=13 * mm, bottomMargin=13 * mm,
                            title=f"{t['learner_title']} - "
                                  f"{(who.get('who') or {}).get('name', '')}")
    doc.build(learner_story(who, exam, lang, public))
    return buf.getvalue()


# ── the appendix, and the zip ────────────────────────────────────────────────

def carried(learners: Sequence[Mapping[str, Any]]) -> tuple[list, int]:
    """The learners one document carries, and how many it had to leave out.

    Returns the cut list and the number omitted, so the caller can *say* that a
    document holds 120 of a school's 400 papers. A silently truncated appendix is
    a lie about the size of the class, which is worse than a short one.
    """
    rows = list(learners)
    return rows[:MAX_FILES], max(0, len(rows) - MAX_FILES)


def appendix_flow(learners: Sequence[Mapping[str, Any]],
                  exam: Mapping[str, Any] | None = None, lang: str = "id",
                  public: bool = False, *, cut: int = 0) -> list[Any]:
    """Every learner's own page, as the appendix of the class report.

    A class report answers "how did the class do"; the appendix answers "how did
    *this child* do", for each child, in the same file — because the alternative
    is a school filing one document and stapling thirty to it.

    The appendix opens with an index, which is the one part a reader with a
    printed copy actually needs: thirty pages in, there is no other way to find
    a name. The index carries band, rank and mark, and *which copy this is* — an
    appendix inside a shared report would publish a page per child, so it is
    never built with `public=True` (the class PDF refuses the appendix outright on
    the shared route; this is the belt).
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (CondPageBreak, PageBreak, Paragraph, Spacer,
                                    Table, TableStyle)

    exam = dict(exam or {})
    lang = ar.language(lang)
    t = _words(lang)
    rows, cut = carried(learners)
    styles = getSampleStyleSheet()
    body = ParagraphStyle("abody", parent=styles["Normal"], fontSize=8.5, leading=11)
    heading = ParagraphStyle("ahead", parent=styles["Heading2"], fontSize=12,
                             leading=15, spaceBefore=0, spaceAfter=4)
    title = ParagraphStyle("atitle", parent=styles["Title"], fontSize=16, leading=19,
                           spaceAfter=2)
    small = ParagraphStyle("asmall", parent=body, fontSize=7.5, leading=9.5,
                           textColor=colors.HexColor(ar.INK["muted"]))
    wide = A4[0] - 28 * mm

    flow: list[Any] = [Paragraph(str(t["appendix"]), title),
                       Paragraph(str(t["appendix_note"]), body),
                       Spacer(1, 6)]
    index = [[t["learner"], t["band"], t["learner_rank"], t["learner_final"]]]
    for who in rows:
        person = who.get("who") or {}
        klass = who.get("class") or {}
        band = person.get("band")
        index.append([
            str(person.get("name") or ""),
            f"{getattr(band, 'letter', '')} · {_pair(getattr(band, 'name', None), lang)}"
            if band else "",
            f"{person.get('rank', '—')} {t['learner_of']} {klass.get('learners', 0)}"
            if person.get("rank") else "",
            _mark(person.get("pct")),
        ])
    table = Table(index, repeatRows=1,
                  colWidths=[wide - 92 * mm, 44 * mm, 30 * mm, 18 * mm])
    table.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.5),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 8),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor(ar.INK["muted"])),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f7")),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#d8dee8")),
        ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    flow.append(table)
    # The cut, named. Nothing else in this document can say it: the index simply
    # ends, and an index that ends looks like a class of that size.
    if cut:
        flow.append(Spacer(1, 4))
        flow.append(Paragraph(str(t["appendix_cut"]).format(n=len(rows), total=len(rows) + cut),
                              small))
    if public:
        # A promise, not a branch: this appendix is never built for a shared report
        # (the class document on the share route refuses it), and a page-per-child
        # appendix that leaked onto a public link is the worst failure this file
        # could have. Saying so in the document is the only place a reader would
        # see it if somebody wired it up anyway.
        flow.append(Spacer(1, 4))
        flow.append(Paragraph(str(t["learner_shared"]), small))

    for who in rows:
        flow.append(PageBreak())
        flow.extend(learner_story(who, exam, lang, public))
    return flow


def learners_zip(learners: Sequence[Mapping[str, Any]],
                 exam: Mapping[str, Any] | None = None, lang: str = "id",
                 public: bool = False) -> bytes:
    """Every learner's own file, as one download: a zip of `laporan-*.pdf`.

    The other half of "one document instead of thirty": the class PDF carries the
    pages for filing, and this hands out thirty separate files — one per child,
    each named after them — from a single click. Built from `learner_pdf`, so each
    file in the zip is byte-for-byte the file its own door serves.
    """
    import zipfile

    exam = dict(exam or {})
    lang = ar.language(lang)
    t = _words(lang)
    rows, cut = carried(learners)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as bundle:
        used: dict[str, int] = {}
        for who in rows:
            name = filename(who, exam, "pdf")
            # Two learners can fold to one name (`Ñoño` and `Nono` do). A zip is
            # the one place that silently keeps the last of them, so a collision
            # is numbered instead — losing a child's report to a filename is not a
            # failure a teacher could notice.
            if name in used:
                used[name] += 1
                name = name[:-4] + f"-{used[name]}.pdf"
            else:
                used[name] = 1
            bundle.writestr(name, learner_pdf(who, exam, lang=lang, public=public))
        if cut:
            bundle.writestr("catatan.txt", str(t["appendix_cut"]).format(
                n=len(rows), total=len(rows) + cut))
    return buf.getvalue()


# ── what the file is called ──────────────────────────────────────────────────

def filename(who: Mapping[str, Any], exam: Mapping[str, Any] | None = None,
             suffix: str = "pdf") -> str:
    """`laporan-<learner>-<exam>-<code>.<suffix>`, safe in a header.

    The class export is named after the *exam*; this one has to name the person,
    because a teacher downloading thirty of them into one folder is the case that
    matters and thirty files called `analisis-matematika-7A.pdf` are thirty files
    with one name between them.

    The name is folded to ASCII rather than dropped when it cannot be spelled in
    it: a family reading the file list should see their child's name, and `Ñoño`
    becoming `Nono` is a better filename than `laporan.pdf`. What matters for the
    header is that no separator, quote or control character survives the fold —
    `NFKD`, then "keep ASCII alphanumerics and `-_`", leaves a stem a browser and a
    `Content-Disposition` line both read literally.
    """
    exam = exam or {}
    person = str((who.get("who") or {}).get("name") or "")
    code = str(exam.get("code") or exam.get("id") or "")
    folded = unicodedata.normalize(
        "NFKD", f"{person}-{exam.get('title') or ''}-{code}")
    stem = "".join(ch if ((ch.isascii() and ch.isalnum()) or ch in "-_")
                   else "" for ch in folded).strip("-")
    while "--" in stem:
        stem = stem.replace("--", "-")
    return f"laporan-{stem[:70]}.{suffix}" if stem else f"laporan.{suffix}"
