"""Answer sheet printer — every position comes from `omr_layout`.

The sheet used to be drawn from constants held in this file while the scanner
read a second, different set held in `omr_service.py`, so what came out of the
printer was not what the scanner was looking for. Nothing here computes a
coordinate any more: `SheetLayout` says where every bubble, marker and digit
lives, this file draws at those coordinates, and the scanner samples at the same
ones.

Two things about the marks are worth keeping in mind if you change them:

* they are **solid squares**, not the L-shaped corner rules they replace. A rule
  is two thin strokes whose apparent thickness changes with distance and blur; a
  square is a filled region, which survives a phone photo. The scanner finds the
  square's *centre*, and the four centres are at fixed page coordinates, so they
  define the page's coordinate system instead of being fitted to it.
* they are placed far enough inside the page (`MARKER_MARGIN_MM`) that nothing
  else is printed near them, so each keeps a quiet zone of bare paper. A marker
  with a header rule running into it stops being a square.

The QR in the header carries `layout.code()` — about twenty bytes saying how many
questions, how many options, which mark, how many columns and rows. That is what
lets a photograph be read without the reader already knowing which sheet it is,
and it is why a future change to the grid will not strand the copies already
sitting in a school's photocopier.
"""
import io

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.colors import black, white, Color
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.graphics import renderPDF

from app.services import omr_layout as L

PAGE_W, PAGE_H = A4  # 210 x 297 mm

LIGHT_GRAY = Color(0.85, 0.85, 0.85)
MID_GRAY = Color(0.5, 0.5, 0.5)
DIGIT_GRAY = Color(0.35, 0.35, 0.35)


# ── page space (top-left origin, millimetres) ⇄ reportlab (bottom-left, points) ──

def _x(mm_val: float) -> float:
    return mm_val * mm


def _y(mm_val: float) -> float:
    """Top-left millimetres to reportlab's bottom-left points."""
    return (L.PAGE_H_MM - mm_val) * mm


def _mark_type_from_arg(mark_type: str) -> str:
    return "S" if str(mark_type).lower().startswith("s") else "C"


def _mark_label(code: str) -> str:
    return "Square" if code == "S" else "Circle"


# ── pieces ───────────────────────────────────────────────────────────────────

def _draw_registration_markers(c: pdf_canvas.Canvas):
    """Four filled squares. Their centres are the page's anchor points.

    Drawn deliberately a hair larger than `MARKER_SIZE_MM` and centred on the
    same point, so that a printer's own bleed or a 1 % scale error moves the
    *edge* of a mark rather than its centre — the scanner only uses the centre.
    """
    c.saveState()
    c.setFillColor(black)
    c.setStrokeColor(black)
    half = L.MARKER_SIZE_MM / 2.0
    for (cx, cy) in L.MARKER_CORNERS_MM:
        c.rect(_x(cx - half), _y(cy + half),
               L.MARKER_SIZE_MM * mm, L.MARKER_SIZE_MM * mm,
               stroke=0, fill=1)
    c.restoreState()


def _draw_branding(c: pdf_canvas.Canvas, school_name: str):
    c.saveState()
    top = L.QR_TOP_MM
    c.setFillColor(black)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(_x(L.CONTENT_MARGIN_MM), _y(top + 7.0), "ScanGrade")

    c.setFont("Helvetica-Bold", 8)
    c.setFillColor(MID_GRAY)
    c.drawString(_x(L.CONTENT_MARGIN_MM), _y(top + 13.0), "OMR ANSWER SHEET")

    if school_name:
        c.setFont("Helvetica-Bold", 10)
        c.setFillColor(black)
        c.drawString(_x(L.CONTENT_MARGIN_MM), _y(top + 20.0), school_name[:52])
    c.restoreState()


def _draw_qr(c: pdf_canvas.Canvas, payload: str):
    """The sheet's own description, printed in the header."""
    size = L.QR_SIZE_MM
    qx = L.CONTENT_RIGHT_MM - size
    qy_top = L.QR_TOP_MM
    c.saveState()
    try:
        # The Drawing takes the widget's *own* size and the transform does the
        # scaling. Passing the target size to both double-counts it, and the
        # code is then drawn at `side²/width` points — far off the page, which
        # is silently the same as not drawing one at all.
        qr = QrCodeWidget(payload, barLevel="M")
        b = qr.getBounds()
        qw, qh = b[2] - b[0], b[3] - b[1]
        side = size * mm
        d = Drawing(qw, qh, transform=[side / qw, 0, 0, side / qh, 0, 0])
        d.add(qr)
        renderPDF.draw(d, c, _x(qx), _y(qy_top + size))
    except Exception:
        # A sheet with no readable code still scans — the scanner then falls back
        # to the layout it was asked for. Never fail the whole print for this.
        c.setStrokeColor(MID_GRAY)
        c.setLineWidth(0.4)
        c.rect(_x(qx), _y(qy_top + size), size * mm, size * mm, stroke=1, fill=0)
    c.restoreState()


def _field(c: pdf_canvas.Canvas, x_mm: float, y_mm: float, w_mm: float,
           label: str, value: str, h_mm: float = 9.0):
    c.saveState()
    c.setFillColor(white)
    c.setStrokeColor(black)
    c.setLineWidth(0.5)
    c.roundRect(_x(x_mm), _y(y_mm + h_mm), w_mm * mm, h_mm * mm, 1.2 * mm, fill=1, stroke=1)
    c.setFillColor(MID_GRAY)
    c.setFont("Helvetica-Bold", 6)
    c.drawString(_x(x_mm + 1.6), _y(y_mm + 2.9), label.upper())
    c.setFillColor(black)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(_x(x_mm + 1.6), _y(y_mm + 7.0), value[:38])
    c.restoreState()


def _draw_header_fields(c: pdf_canvas.Canvas, fields: dict):
    """Name on its own line, then class and subject beside the QR."""
    y = L.QR_TOP_MM + L.QR_SIZE_MM + 3.0
    right = L.CONTENT_RIGHT_MM - (L.QR_SIZE_MM + 4.0)
    _field(c, L.CONTENT_MARGIN_MM, y, right - L.CONTENT_MARGIN_MM,
           "Student name", fields.get("student_name", ""))
    y2 = y + 11.0
    half = (right - L.CONTENT_MARGIN_MM - 4.0) / 2.0
    _field(c, L.CONTENT_MARGIN_MM, y2, half, "Class", fields.get("class_name", ""))
    _field(c, L.CONTENT_MARGIN_MM + half + 4.0, y2, half, "Subject", fields.get("subject", ""))
    return y2 + 9.0


def _draw_rule(c: pdf_canvas.Canvas, y_mm: float):
    c.saveState()
    c.setStrokeColor(LIGHT_GRAY)
    c.setLineWidth(0.6)
    c.line(_x(L.CONTENT_MARGIN_MM), _y(y_mm), _x(L.CONTENT_RIGHT_MM), _y(y_mm))
    c.restoreState()


def _draw_answer_grid(c: pdf_canvas.Canvas, layout: L.SheetLayout,
                      page: int, mark_code: str):
    """The bubbles, at the coordinates the layout says, and nowhere else."""
    c.saveState()
    labels = layout.options_labels()
    start = page * layout.per_page
    end = min(start + layout.per_page, layout.total_questions)

    # one column header per column actually used on this page
    columns_used = max(1, (min(end, start + layout.per_page) - start + layout.n_rows - 1)
                       // layout.n_rows)
    for col in range(columns_used):
        left = layout.column_left_mm(col)
        c.setFillColor(black)
        c.setFont("Helvetica-Bold", 7)
        c.drawRightString(_x(left + L.NUMBER_COL_MM - 1.4),
                          _y(L.GRID_FIRST_ROW_MM - 1.0), "No.")
        for oi, letter in enumerate(labels):
            cx, _ = layout.option_centre_mm(start + col * layout.n_rows, oi, page)
            c.setFont("Helvetica-Bold", 7.5)
            c.drawCentredString(_x(cx), _y(L.GRID_FIRST_ROW_MM - 6.2), letter)

    r = L.OPTION_RADIUS_MM * mm
    for (q, oi, cx_mm, cy_mm) in layout.bubbles(page):
        col = (q - start) // layout.n_rows
        row = (q - start) % layout.n_rows
        if oi == 0:
            c.setFillColor(DIGIT_GRAY)
            c.setFont("Helvetica-Bold", 7)
            c.drawRightString(_x(layout.column_left_mm(col) + L.NUMBER_COL_MM - 1.4),
                              _y(cy_mm + 2.4), str(q + 1))
        c.setFillColor(white)
        c.setStrokeColor(black)
        c.setLineWidth(0.55)
        if mark_code == "S":
            side = L.OPTION_RADIUS_MM * 2
            c.rect(_x(cx_mm - L.OPTION_RADIUS_MM), _y(cy_mm + L.OPTION_RADIUS_MM),
                   side * mm, side * mm, fill=1, stroke=1)
        else:
            c.circle(_x(cx_mm), _y(cy_mm), r, fill=1, stroke=1)
        # The letter inside the bubble is what a student checks their marking
        # against; it is light enough not to read as a mark.
        c.setFillColor(Color(0.55, 0.55, 0.55))
        c.setFont("Helvetica-Bold", 5)
        c.drawCentredString(_x(cx_mm), _y(cy_mm + 1.0), labels[oi])
    c.restoreState()


def _draw_id_band(c: pdf_canvas.Canvas, layout: L.SheetLayout, mark_code: str):
    """Student-ID bubbles: ten positions across, ten values down."""
    c.saveState()
    c.setFillColor(black)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(_x(L.CONTENT_MARGIN_MM), _y(L.ID_BAND_TOP_MM - 2.0),
                 "STUDENT ID / NISN")
    c.setFont("Helvetica-Bold", 6)
    c.setFillColor(MID_GRAY)
    c.drawString(_x(L.CONTENT_MARGIN_MM + 34.0), _y(L.ID_BAND_TOP_MM - 2.0),
                 "fill one per column, left to right")

    r = L.ID_RADIUS_MM * mm
    for (d, v, cx_mm, cy_mm) in layout.id_bubbles():
        c.setFillColor(white)
        c.setStrokeColor(black)
        c.setLineWidth(0.4)
        if mark_code == "S":
            side = L.ID_RADIUS_MM * 2
            c.rect(_x(cx_mm - L.ID_RADIUS_MM), _y(cy_mm + L.ID_RADIUS_MM),
                   side * mm, side * mm, fill=1, stroke=1)
        else:
            c.circle(_x(cx_mm), _y(cy_mm), r, fill=1, stroke=1)
        if d == 0:
            c.setFillColor(DIGIT_GRAY)
            c.setFont("Helvetica-Bold", 4)
            c.drawRightString(_x(cx_mm - L.ID_RADIUS_MM - 0.8), _y(cy_mm + 0.6), str(v))
    c.restoreState()


def _draw_footer(c: pdf_canvas.Canvas, layout: L.SheetLayout, mark_code: str):
    c.saveState()
    c.setStrokeColor(LIGHT_GRAY)
    c.setLineWidth(0.4)
    c.line(_x(L.CONTENT_MARGIN_MM), _y(L.FOOTER_Y_MM - 5.0),
           _x(L.CONTENT_RIGHT_MM), _y(L.FOOTER_Y_MM - 5.0))
    c.setFillColor(Color(0.25, 0.25, 0.25))
    c.setFont("Helvetica-Bold", 7)
    c.drawString(_x(L.CONTENT_MARGIN_MM), _y(L.FOOTER_Y_MM),
                 "Use a 2B pencil \u2022 fill the mark completely \u2022 do not fold or bend")
    c.setFillColor(MID_GRAY)
    c.setFont("Helvetica-Bold", 6)
    c.drawString(_x(L.CONTENT_MARGIN_MM), _y(L.FOOTER_Y_MM + 4.0),
                 f"ScanGrade Answer Sheet \u2022 {layout.total_questions} questions "
                 f"\u2022 {layout.options} options \u2022 {_mark_label(mark_code)} "
                 f"\u2022 version {layout.version} \u2022 {layout.code()}")
    c.setFont("Helvetica-Bold", 11)
    c.setFillColor(black)
    c.drawRightString(_x(L.CONTENT_RIGHT_MM), _y(L.FOOTER_Y_MM + 1.0),
                      f"v{layout.version}")
    c.restoreState()


# ── the public entry point ───────────────────────────────────────────────────

def generate_answer_sheet(
    total_questions: int = 50,
    mark_type: str = "circle",
    student_name: str = "",
    class_name: str = "",
    subject: str = "",
    date: str = "",
    exam_version: str = "A",
    school_name: str = "",
    options: int = 5,
) -> io.BytesIO:
    """A print-ready A4 answer sheet, plus the code that describes it.

    The code is printed in the header as a QR and again, readably, in the footer,
    so a sheet can be identified even when the QR will not decode.
    """
    mark_code = _mark_type_from_arg(mark_type)
    layout = L.default_layout(
        int(total_questions), options=int(options),
        mark_type=mark_code, version=(exam_version or "A")[:4],
    )

    buf = io.BytesIO()
    c = pdf_canvas.Canvas(buf, pagesize=(PAGE_W, PAGE_H))
    c.setTitle(f"ScanGrade Answer Sheet - {layout.total_questions}Q")
    c.setAuthor("ScanGrade")
    c.setSubject(layout.code())

    for page in range(layout.pages):
        _draw_registration_markers(c)
        _draw_branding(c, school_name)
        _draw_qr(c, layout.code())
        bottom = _draw_header_fields(c, {
            "student_name": student_name, "class_name": class_name,
            "subject": subject,
        })

        # The rule sits under the header, above whatever the header actually
        # used: a long school name must push the grid down, never overlap it.
        rule_y = max(L.HEADER_RULE_MM, bottom + 3.0)
        _draw_rule(c, rule_y)

        _draw_answer_grid(c, layout, page, mark_code)
        _draw_id_band(c, layout, mark_code)
        _draw_footer(c, layout, mark_code)

        if page < layout.pages - 1:
            c.showPage()

    c.save()
    buf.seek(0)
    return buf


__all__ = ["generate_answer_sheet", "layout_from_request"]


def layout_from_request(total_questions: int, mark_type: str = "circle",
                        options: int = 5, exam_version: str = "A") -> L.SheetLayout:
    """The layout a request would print — used by tests and the preview."""
    return L.default_layout(int(total_questions), options=int(options),
                            mark_type=_mark_type_from_arg(mark_type),
                            version=(exam_version or "A")[:4])
