"""Modern Answer Sheet / Lembar Jawaban Generator with Circle & Square variants."""
import io
import hashlib
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.colors import black, white, Color
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.graphics import renderPDF

# ── Page setup ────────────────────────────────────
PAGE_W, PAGE_H = A4  # 210 x 297 mm (portrait)

MARGIN = 12 * mm

# ── Bubble sizing ─────────────────────────────────
CIRCLE_R = 3.0 * mm
SQUARE_SIZE = 5.6 * mm

# ── Spacing ───────────────────────────────────────
BUBBLE_GAP_X = 7.2 * mm
BUBBLE_GAP_Y = 7.0 * mm
ROW_H = BUBBLE_GAP_Y

# NISN grid
ID_CIRCLE_R = 1.6 * mm
ID_COL_W = 4.8 * mm
ID_GAP_X = 0.6 * mm
ID_GAP_Y = 4.0 * mm

# Corner markers
MARKER_SIZE = 8 * mm
MARKER_THICK = 2.8 * mm

# Colors
LIGHT_GRAY = Color(0.85, 0.85, 0.85)
MID_GRAY = Color(0.5, 0.5, 0.5)


def _get_page_size(total_q: int):
    """Return (w, h) — always portrait A4."""
    return (PAGE_W, PAGE_H)


def _draw_corner_markers(c: pdf_canvas.Canvas, pw: float, ph: float):
    """Draw 4 enhanced L-shaped corner markers for scan detection."""
    c.saveState()
    c.setStrokeColor(black)
    c.setLineWidth(MARKER_THICK)
    m = MARKER_SIZE
    gap = 1.0 * mm
    # Top-left
    c.line(MARGIN - gap, ph - MARGIN + gap, MARGIN + m, ph - MARGIN + gap)
    c.line(MARGIN - gap, ph - MARGIN + gap, MARGIN - gap, ph - MARGIN - m)
    # Top-right
    c.line(pw - MARGIN + gap, ph - MARGIN + gap, pw - MARGIN - m, ph - MARGIN + gap)
    c.line(pw - MARGIN + gap, ph - MARGIN + gap, pw - MARGIN + gap, ph - MARGIN - m)
    # Bottom-left
    c.line(MARGIN - gap, MARGIN - gap, MARGIN + m, MARGIN - gap)
    c.line(MARGIN - gap, MARGIN - gap, MARGIN - gap, MARGIN + m)
    # Bottom-right
    c.line(pw - MARGIN + gap, MARGIN - gap, pw - MARGIN - m, MARGIN - gap)
    c.line(pw - MARGIN + gap, MARGIN - gap, pw - MARGIN + gap, MARGIN + m)
    c.restoreState()


def _draw_school_branding(c: pdf_canvas.Canvas, pw: float, ph: float, school_name: str = ""):
    """Draw school branding: logo placeholder + school name, ScanGrade on far left."""
    c.saveState()
    ly = ph - MARGIN - 8 * mm

    # ScanGrade on the far left
    c.setFont("Helvetica-Bold", 18)
    c.setFillColor(black)
    c.drawString(MARGIN, ly, "ScanGrade")

    sg_w = c.stringWidth("ScanGrade", "Helvetica-Bold", 18)

    # Logo placeholder
    logo_x = MARGIN + sg_w + 4 * mm
    logo_size = 15 * mm
    c.setStrokeColor(MID_GRAY)
    c.setLineWidth(0.5)
    c.setDash(2, 2)
    c.rect(logo_x, ly - 2 * mm, logo_size, logo_size, stroke=1, fill=0)
    c.setDash()
    c.setFont("Helvetica-Bold", 5)
    c.setFillColor(MID_GRAY)
    c.drawCentredString(logo_x + logo_size / 2, ly + logo_size / 2 - 4 * mm, "Logo")

    # School name
    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(black)
    c.drawString(logo_x + logo_size + 2 * mm, ly + 1 * mm, school_name if school_name else "")

    # OMR Answer Sheet subtitle
    c.setFont("Helvetica-Bold", 8)
    c.setFillColor(MID_GRAY)
    c.drawString(MARGIN, ph - MARGIN - 13 * mm, "OMR Answer Sheet")

    c.restoreState()


def _draw_qr_code(c: pdf_canvas.Canvas, data: str, pw: float, ph: float):
    """Draw QR code in top-right corner."""
    c.saveState()
    qr_size = 18 * mm
    qrx = pw - MARGIN - qr_size
    qry = ph - MARGIN - qr_size
    try:
        qr = QrCodeWidget(data, barLevel="M", barWidth=0.25 * mm)
        bounds = qr.getBounds()
        qw = bounds[2] - bounds[0]
        qh = bounds[3] - bounds[1]
        scale = qr_size / max(qw, qh)
        d = Drawing(qr_size, qr_size, transform=[scale, 0, 0, scale, 0, 0])
        d.add(qr)
        renderPDF.draw(d, c, qrx, qry)
    except Exception:
        c.setFont("Helvetica-Bold", 6)
        c.drawString(qrx, qry + qr_size / 2, "[QR]")
    c.restoreState()
    return qrx  # return right edge of QR for alignment


def _draw_header_fields(c: pdf_canvas.Canvas, fields: dict, pw: float, ph: float, qr_right_x: float):
    """Draw header: Student Name (full width) + Class/Subject/Date."""
    c.saveState()

    y_start = ph - MARGIN - 16 * mm + 2 * mm
    row_h = 10 * mm
    row_gap = 3 * mm
    left_x = MARGIN + 3 * mm
    right_x = qr_right_x

    # Row 1: Student Name (full width)
    bw = right_x - left_x
    c.setFillColor(white)
    c.roundRect(left_x, y_start - row_h, bw, row_h, 1.5 * mm, fill=1, stroke=1)
    c.setFillColor(black)
    c.setFont("Helvetica-Bold", 7.5)
    c.drawString(left_x + 2 * mm, y_start - row_h + 7 * mm, "Student Name:")
    c.setFont("Helvetica-Bold", 10)
    display_val = fields.get("student_name", "") if fields.get("student_name") else ""
    c.drawString(left_x + 2 * mm, y_start - row_h + 3.5 * mm, display_val)

    # Row 2: Class + Subject + Date
    y2 = y_start - row_h - row_gap
    row = [
        ("Class", fields.get("class_name", ""), 0.22),
        ("Subject", fields.get("subject", ""), 0.40),
        ("Date", fields.get("date", ""), 0.22),
    ]
    x = left_x
    c.setStrokeColor(black)
    c.setLineWidth(0.5)
    for label, value, w_ratio in row:
        bw = (right_x - left_x) * w_ratio
        c.setFillColor(white)
        c.roundRect(x, y2 - row_h, bw, row_h, 1.5 * mm, fill=1, stroke=1)
        c.setFillColor(black)
        c.setFont("Helvetica-Bold", 7.5)
        c.drawString(x + 2 * mm, y2 - row_h + 7 * mm, label + ":")
        c.setFont("Helvetica-Bold", 10)
        display_val = value if value else ""
        c.drawString(x + 2 * mm, y2 - row_h + 3.5 * mm, display_val)
        x += bw + 3 * mm

    c.restoreState()
    return y2 - row_h


def _draw_student_id_grid(c: pdf_canvas.Canvas, x: float, y: float, mark_type: str = "circle"):
    """Draw compact NISN bubble grid in the gap between answer columns."""
    c.saveState()
    num_positions = 10

    # Label
    c.setFont("Helvetica-Bold", 6)
    c.setFillColor(black)
    c.drawCentredString(x + num_positions * (ID_COL_W + ID_GAP_X) / 2, y + 2 * mm, "NISN")

    grid_top = y - 2 * mm

    for pos in range(num_positions):
        px = x + pos * (ID_COL_W + ID_GAP_X)
        cx = px + ID_COL_W / 2
        # Header circle/square (replaces 1-10 numbers)
        hdr_y = grid_top + 0.5 * mm
        if mark_type == "square":
            sz = ID_COL_W * 0.7
            c.setFillColor(white)
            c.setStrokeColor(black)
            c.setLineWidth(0.4)
            c.rect(cx - sz / 2, hdr_y - sz / 2, sz, sz, fill=1, stroke=1)
        else:
            hdr_r = ID_COL_W * 0.35
            c.setFillColor(white)
            c.setStrokeColor(black)
            c.setLineWidth(0.4)
            c.circle(cx, hdr_y, hdr_r, fill=1, stroke=1)

        for digit in range(10):
            cy = grid_top - (digit + 1) * ID_GAP_Y
            c.setFillColor(white)
            c.setStrokeColor(black)
            c.setLineWidth(0.4)
            c.circle(cx, cy, ID_CIRCLE_R, fill=1, stroke=1)
            c.setFont("Helvetica-Bold", 3.5)
            c.setFillColor(Color(0.3, 0.3, 0.3))
            c.drawCentredString(cx, cy - 0.5 * mm, str(digit))

    c.restoreState()
    return grid_top - 10 * ID_GAP_Y  # bottom of the grid


def _draw_exam_version_bottom(c: pdf_canvas.Canvas, pw: float, ph: float, version: str):
    """Draw exam version in the bottom-left corner."""
    c.saveState()
    vx = MARGIN + 3 * mm
    vy = MARGIN + 18 * mm
    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(MID_GRAY)
    c.drawString(vx, vy, "Version")
    c.setFont("Helvetica-Bold", 28)
    c.setFillColor(black)
    c.drawString(vx, vy - 12 * mm, version)
    c.restoreState()


def _draw_answer_bubbles(
    c: pdf_canvas.Canvas,
    x: float,
    y: float,
    total_questions: int,
    mark_type: str = "circle",
    options: int = 5,
    pw: float = PAGE_W,
):
    """Draw the answer grid with question numbers and bubbles."""
    c.saveState()
    opt_labels = ["A", "B", "C", "D", "E", "F", "G", "H"][:options]
    questions_per_col = 25
    cols = (total_questions + questions_per_col - 1) // questions_per_col

    usable_right = pw - MARGIN - 3 * mm
    col_width = (usable_right - x) / cols

    remaining = total_questions
    for col_idx in range(cols):
        col_count = min(remaining, questions_per_col)
        col_x = x + col_idx * col_width + (col_width - options * BUBBLE_GAP_X) / 2
        col_start = total_questions - remaining

        c.setFont("Helvetica-Bold", 8)
        for j, opt in enumerate(opt_labels):
            ox = col_x + j * BUBBLE_GAP_X
            if mark_type == "circle":
                c.drawCentredString(ox + CIRCLE_R, y - 2.5 * mm, opt)
            else:
                c.drawCentredString(ox + SQUARE_SIZE / 2, y - 2.5 * mm, opt)

        for i in range(col_count):
            q_num = col_start + i + 1
            row_y = y - (i + 1) * ROW_H - 2 * mm

            c.setFont("Helvetica-Bold", 7)
            c.setFillColor(Color(0.2, 0.2, 0.2))
            c.drawRightString(col_x - 2 * mm, row_y + 2.5 * mm, str(q_num))

            for j in range(options):
                bx = col_x + j * BUBBLE_GAP_X
                by = row_y
                if mark_type == "square":
                    sz = SQUARE_SIZE
                    c.setFillColor(white)
                    c.setStrokeColor(black)
                    c.setLineWidth(0.5)
                    c.rect(bx, by, sz, sz, fill=1, stroke=1)
                    c.setFont("Helvetica-Bold", 5)
                    c.setFillColor(Color(0.3, 0.3, 0.3))
                    c.drawCentredString(bx + sz / 2, by + 1.5 * mm, opt_labels[j])
                else:
                    cx = bx + CIRCLE_R
                    cy = by + CIRCLE_R
                    c.setFillColor(white)
                    c.setStrokeColor(black)
                    c.setLineWidth(0.5)
                    c.circle(cx, cy, CIRCLE_R, fill=1, stroke=1)
                    c.setFont("Helvetica-Bold", 5)
                    c.setFillColor(Color(0.3, 0.3, 0.3))
                    c.drawCentredString(cx, cy - 0.5 * mm, opt_labels[j])

        remaining -= col_count

    c.restoreState()


def _draw_footer(c: pdf_canvas.Canvas, pw: float, ph: float, total_questions: int, mark_type: str):
    """Draw footer with instructions."""
    c.saveState()
    c.setStrokeColor(LIGHT_GRAY)
    c.setLineWidth(0.3)
    c.line(MARGIN + 2 * mm, MARGIN + 14 * mm, pw - MARGIN - 2 * mm, MARGIN + 14 * mm)

    c.setFont("Helvetica-Bold", 7)
    c.setFillColor(Color(0.2, 0.2, 0.2))
    c.drawCentredString(pw / 2, MARGIN + 9 * mm,
                        "Use 2B pencil \u2022 Fill the circle completely \u2022 Do not fold or bend the sheet")

    c.setFont("Helvetica-Bold", 5.5)
    c.setFillColor(MID_GRAY)
    c.drawString(MARGIN + 2 * mm, MARGIN + 3 * mm,
                 f"ScanGrade Answer Sheet | {total_questions} Questions | Mark: {mark_type.title()}")
    c.drawRightString(pw - MARGIN - 2 * mm, MARGIN + 3 * mm,
                      "scan-grade.app")
    c.restoreState()


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
    """Generate a print-ready answer sheet PDF (A4 Portrait).

    Args:
        total_questions: Number of MCQ questions (supports 1-100+)
        mark_type: 'circle' or 'square'
        student_name: pre-filled student name
        class_name: pre-filled class
        subject: pre-filled subject
        date: pre-filled date
        exam_version: A, B, C, D, or E
        school_name: school name for branding
        options: number of options per question (2-8)

    Returns:
        BytesIO buffer containing the PDF
    """
    QUESTIONS_PER_PAGE = 50
    num_pages = max(1, (total_questions + QUESTIONS_PER_PAGE - 1) // QUESTIONS_PER_PAGE)
    pw, ph = _get_page_size(total_questions)

    buf = io.BytesIO()
    c = pdf_canvas.Canvas(buf, pagesize=(pw, ph))
    c.setTitle(f"ScanGrade Answer Sheet - {total_questions}Q")
    c.setAuthor("ScanGrade")
    c.setSubject(f"{total_questions} Questions, Version {exam_version}")

    qr_seed = f"{total_questions}|{mark_type}|{exam_version}"
    qr_hash = hashlib.sha256(qr_seed.encode()).hexdigest()[:16]
    qr_data = f"SG:{qr_hash}:{total_questions}Q:v{exam_version}"

    for page_idx in range(num_pages):
        q_start = page_idx * QUESTIONS_PER_PAGE + 1
        q_end = min((page_idx + 1) * QUESTIONS_PER_PAGE, total_questions)
        page_q_count = q_end - q_start + 1

        _draw_corner_markers(c, pw, ph)
        _draw_school_branding(c, pw, ph, school_name)
        qr_right_x = _draw_qr_code(c, qr_data + f"|p{page_idx+1}", pw, ph)

        fields = {
            "student_name": student_name,
            "class_name": class_name,
            "subject": subject,
            "date": date,
        }
        header_bottom = _draw_header_fields(c, fields, pw, ph, qr_right_x)

        grid_y = header_bottom - 2 * mm

        nisn_x = MARGIN + 3 * mm
        nisn_y = grid_y - 10 * mm
        _draw_student_id_grid(c, nisn_x, nisn_y, mark_type)

        nisn_right = nisn_x + 10 * (ID_COL_W + ID_GAP_X) + 4 * mm
        _draw_answer_bubbles(c, nisn_right, grid_y, page_q_count, mark_type, options=options, pw=pw)

        _draw_exam_version_bottom(c, pw, ph, exam_version)
        _draw_footer(c, pw, ph, total_questions, mark_type)

        if page_idx < num_pages - 1:
            c.showPage()

    c.save()
    buf.seek(0)
    return buf
