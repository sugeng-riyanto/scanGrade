"""Upgraded ZipGrade-like LJK bubble sheet generator."""
import io
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.units import mm, inch
from reportlab.lib.colors import black, white
from reportlab.pdfgen import canvas as pdf_canvas


MARK_SIZE = 8 * mm
BUBBLE_R = 2.8 * mm
BUBBLE_GAP = 6.5 * mm
ROW_H = 8.5 * mm
QUESTIONS_PER_COL = 25
ID_BUBBLE_R = 2.0 * mm
ID_DIGIT_GAP = 4.2 * mm


def _draw_registration_marks(c: pdf_canvas.Canvas, w: float, h: float):
    c.saveState()
    margin = 10 * mm
    m = MARK_SIZE / 2
    for cx, cy in [
        (margin, margin),
        (w - margin, margin),
        (margin, h - margin),
        (w - margin, h - margin),
        (w / 2, h - margin),
    ]:
        c.setStrokeColor(black)
        c.setFillColor(black)
        c.setLineWidth(1.5)
        c.rect(cx - m, cy - m, MARK_SIZE, MARK_SIZE, fill=1, stroke=1)
        c.setFillColor(white)
        c.rect(cx - m * 0.4, cy - m * 0.4, MARK_SIZE * 0.4, MARK_SIZE * 0.4, fill=1, stroke=0)
    c.restoreState()


def _draw_student_id_area(c: pdf_canvas.Canvas, x: float, y: float, digits: int = 8):
    r = ID_BUBBLE_R
    gap = ID_DIGIT_GAP
    col_w = gap + r * 2
    c.saveState()
    c.setFont("Helvetica-Bold", 10)
    c.drawString(x, y + 10 * mm, "NISN")
    for d in range(digits):
        dx = x + d * col_w
        c.setFont("Helvetica-Bold", 7)
        c.drawCentredString(dx + r, y + 7 * mm, str(d + 1))
        for n in range(10):
            bx = dx
            by = y - n * (r * 2 + 0.6 * mm)
            c.circle(bx + r, by + r, r, fill=0, stroke=1)
            c.setFont("Helvetica", 5.5)
            c.drawCentredString(bx + r, by - 0.5 * mm, str(n))
    c.restoreState()


def _draw_key_version(c: pdf_canvas.Canvas, x: float, y: float):
    r = ID_BUBBLE_R * 1.3
    gap = ID_DIGIT_GAP + 1 * mm
    c.saveState()
    c.setFont("Helvetica-Bold", 10)
    c.drawString(x, y + 8 * mm, "Kunci")
    for i, opt in enumerate(["A", "B", "C", "D", "E"]):
        bx = x + i * gap
        by = y
        c.circle(bx + r, by + r, r, fill=0, stroke=1)
        c.setFont("Helvetica-Bold", 8)
        c.drawCentredString(bx + r, by - 3 * mm, opt)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(x, y - 10 * mm, "Ver")
    for v in range(5):
        vx = x + v * gap
        vy = y - 13 * mm
        c.circle(vx + r, vy + r, r, fill=0, stroke=1)
        c.setFont("Helvetica", 7)
        c.drawCentredString(vx + r, vy - 3 * mm, str(v + 1))
    c.restoreState()


def _draw_answer_grid(
    c: pdf_canvas.Canvas,
    start_x: float,
    start_y: float,
    question_start: int,
    count: int,
    options: int = 5,
    col_label: str = "",
):
    opt_labels = ["A", "B", "C", "D", "E"][:options]
    c.setFont("Helvetica-Bold", 7)
    c.drawString(start_x, start_y + 2 * mm, col_label)
    for j, opt in enumerate(opt_labels):
        ox = start_x + j * BUBBLE_GAP + BUBBLE_GAP / 2
        c.setFont("Helvetica-Bold", 7)
        c.drawString(ox - 1.5 * mm, start_y + 4 * mm, opt)
    for i in range(count):
        q_num = question_start + i
        y_pos = start_y - i * ROW_H
        c.setFont("Helvetica", 6)
        c.drawString(start_x - 5 * mm, y_pos + 1.5 * mm, str(q_num))
        for j in range(options):
            bx = start_x + j * BUBBLE_GAP
            by = y_pos
            c.circle(bx + BUBBLE_R, by + BUBBLE_R, BUBBLE_R, fill=0, stroke=1)
            c.setFont("Helvetica", 5)
            c.drawCentredString(bx + BUBBLE_R, by - 1.5 * mm, opt_labels[j])


def _wrap_title(c: pdf_canvas.Canvas, text: str, max_width: float, font_name: str, font_size: float):
    c.setFont(font_name, font_size)
    words = text.split()
    lines = []
    current = ""
    for w in words:
        test = current + (" " if current else "") + w
        if c.stringWidth(test, font_name, font_size) <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = w
    if current:
        lines.append(current)
    return lines if lines else [text]


def generate_bubble_sheet_pdf(
    title: str = "UJIAN",
    total_questions: int = 50,
    subject: str = "",
    student_name: str = "",
    options: int = 5,
    page_size: str = "A4",
) -> io.BytesIO:
    pagesize = A4 if page_size == "A4" else letter
    pw, ph = pagesize
    buf = io.BytesIO()
    c = pdf_canvas.Canvas(buf, pagesize=pagesize)
    margin = 15 * mm
    usable_w = pw - 2 * margin
    is_letter = ph < 290

    _draw_registration_marks(c, pw, ph)

    # ---- Vertical positions (mm from top) ----
    if is_letter:
        t_title = 16
        t_nama_top = 26
        t_nama_bot = 36
        t_row2_top = 39
        t_row2_bot = 47
        t_content = 60
        t_instr_start = 36
        t_footer = 8
    else:
        t_title = 18
        t_nama_top = 28
        t_nama_bot = 40
        t_row2_top = 44
        t_row2_bot = 53
        t_content = 70
        t_instr_start = 42
        t_footer = 12

    # Convert to ReportLab y (y=0 bottom)
    rl = lambda mm_from_top: ph - mm_from_top * mm

    c.saveState()
    # Title
    title_max_w = usable_w - 20 * mm
    title_lines = _wrap_title(c, title, title_max_w, "Helvetica-Bold", 18)
    ty = rl(t_title)
    c.setFont("Helvetica-Bold", 18)
    for line in title_lines:
        c.drawCentredString(pw / 2, ty, line)
        ty -= 7 * mm

    # Nama box (full width, bordered)
    c.setStrokeColor(black)
    c.setLineWidth(0.8)
    nama_top_rl = rl(t_nama_top)
    nama_h = (t_nama_bot - t_nama_top) * mm
    c.rect(margin + 2 * mm, nama_top_rl, usable_w - 4 * mm, nama_h, fill=0, stroke=1)
    c.setFont("Helvetica", 10)
    c.drawString(margin + 5 * mm, nama_top_rl + 3.5 * mm, "Nama Lengkap:")
    ul_x = margin + 38 * mm
    ul_w = usable_w - 43 * mm
    c.setLineWidth(0.5)
    c.line(ul_x, nama_top_rl + 2 * mm, ul_x + ul_w, nama_top_rl + 2 * mm)
    c.line(ul_x, nama_top_rl + 5 * mm, ul_x + ul_w, nama_top_rl + 5 * mm)

    # Kelas | Mapel | Tanggal
    row2_top_rl = rl(t_row2_top)
    row2_h = (t_row2_bot - t_row2_top) * mm
    avail = usable_w - 4 * mm
    c1_w = 32 * mm
    c2_w = min(65 * mm, avail - c1_w - 2 * mm)
    c3_w = avail - c1_w - c2_w - 4 * mm

    bx1 = margin + 2 * mm
    c.rect(bx1, row2_top_rl, c1_w, row2_h, fill=0, stroke=1)
    c.setFont("Helvetica", 8)
    c.drawString(bx1 + 2 * mm, row2_top_rl + 2 * mm, "Kelas: _______")

    bx2 = bx1 + c1_w + 2 * mm
    c.rect(bx2, row2_top_rl, c2_w, row2_h, fill=0, stroke=1)
    c.drawString(bx2 + 2 * mm, row2_top_rl + 2 * mm, f"Mapel: {subject or '__________________'}")

    bx3 = bx2 + c2_w + 2 * mm
    c.rect(bx3, row2_top_rl, c3_w, row2_h, fill=0, stroke=1)
    c.drawString(bx3 + 2 * mm, row2_top_rl + 2 * mm, "Tgl: ____/____/______")

    c.restoreState()

    # Left: NISN + Key/Version
    left_x = margin + 2 * mm
    nisn_y = rl(t_content)
    _draw_student_id_area(c, left_x, nisn_y, digits=8)
    key_y = nisn_y - 100 * mm
    _draw_key_version(c, left_x, key_y)

    # Right: Answer grid
    grid_x = margin + 50 * mm
    grid_top_y = rl(t_content)
    cols_needed = max(1, (total_questions + QUESTIONS_PER_COL - 1) // QUESTIONS_PER_COL)
    col_width = (pw - grid_x - margin) / cols_needed
    remaining = total_questions
    for col in range(cols_needed):
        col_count = min(remaining, QUESTIONS_PER_COL)
        col_start = grid_x + col * col_width + (col_width - BUBBLE_GAP * options) / 2
        _draw_answer_grid(
            c, col_start, grid_top_y,
            question_start=(total_questions - remaining),
            count=col_count,
            options=options,
            col_label=f"Soal {total_questions - remaining + 1}-{total_questions - remaining + col_count}",
        )
        remaining -= col_count

    # Petunjuk Pengisian
    instr_y = t_instr_start * mm
    c.setFont("Helvetica", 7)
    for line in [
        "Petunjuk Pengisian:",
        "  \u2022 Gunakan pensil 2B atau pulpen gelap untuk mengisi bulatan",
        "  \u2022 Isi penuh bulatan \u2014 jangan menggunakan centang atau silang",
        "  \u2022 Hapus kesalahan hingga bersih \u2014 tidak boleh ada noda",
        "  \u2022 Jangan melipat atau merusak lembar ini",
    ]:
        c.drawString(margin + 10 * mm, instr_y, line)
        instr_y -= 3.5 * mm

    # Footer
    c.setFont("Helvetica", 6)
    c.drawString(margin + 10 * mm, t_footer * mm, f"ScanGrade LJK | {title} | {total_questions} soal")
    c.drawRightString(pw - margin, t_footer * mm, "scan-grade.app")

    c.showPage()
    c.save()
    buf.seek(0)
    return buf
