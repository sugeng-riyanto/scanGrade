import io
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from flask import send_file
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdf_canvas


def export_to_xlsx(submissions: list, exam_title: str = "Hasil Ujian") -> io.BytesIO:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Hasil"

    header_font = Font(bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill(start_color="4338CA", end_color="4338CA", fill_type="solid")
    thin_border = Border(
        left=Side(style="thin"), right=Side(style="thin"),
        top=Side(style="thin"), bottom=Side(style="thin"),
    )

    headers = ["No", "Siswa", "Skor", "Penalti", "Nilai Final", "Status"]
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")
        cell.border = thin_border

    for i, s in enumerate(submissions, 1):
        row_data = [
            i,
            s.get("student_id", "")[:12],
            s.get("score", 0),
            s.get("penalty", 0),
            s.get("final_score", s.get("score", 0)),
            s.get("status", "submitted"),
        ]
        for col, val in enumerate(row_data, 1):
            cell = ws.cell(row=i + 1, column=col, value=val)
            cell.border = thin_border
            cell.alignment = Alignment(horizontal="center")

    for col in range(1, 7):
        ws.column_dimensions[chr(64 + col)].width = 18

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def export_to_pdf(submissions: list, exam_title: str = "Hasil Ujian") -> io.BytesIO:
    buf = io.BytesIO()
    c = pdf_canvas.Canvas(buf, pagesize=A4)
    width, height = A4

    c.setFont("Helvetica-Bold", 16)
    c.drawString(40, height - 40, exam_title)

    c.setFont("Helvetica", 10)
    y = height - 70
    c.drawString(40, y, "No")
    c.drawString(80, y, "Siswa")
    c.drawString(200, y, "Skor")
    c.drawString(280, y, "Penalti")
    c.drawString(360, y, "Final")
    c.drawString(440, y, "Status")
    y -= 15

    c.setFont("Helvetica", 9)
    for i, s in enumerate(submissions, 1):
        c.drawString(40, y, str(i))
        c.drawString(80, y, s.get("student_id", "")[:12])
        c.drawString(200, y, str(s.get("score", 0)))
        c.drawString(280, y, str(s.get("penalty", 0)))
        c.drawString(360, y, str(s.get("final_score", s.get("score", 0))))
        c.drawString(440, y, s.get("status", "-"))
        y -= 14
        if y < 40:
            c.showPage()
            y = height - 40

    c.save()
    buf.seek(0)
    return buf


def generate_bubble_sheet_pdf(
    title: str,
    total_questions: int = 20,
    subject: str = "",
    student_name: str = "",
    options: int = 5,
) -> io.BytesIO:
    """Generate bubble sheet PDF like ZipGrade."""
    buf = io.BytesIO()
    c = pdf_canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    margin = 30 * mm

    c.setFont("Helvetica-Bold", 18)
    c.drawString(margin, height - 30 * mm, title)

    c.setFont("Helvetica", 11)
    c.drawString(margin, height - 40 * mm, f"Mata Pelajaran: {subject}")

    if student_name:
        c.drawString(margin, height - 48 * mm, f"Nama: {student_name}")

    c.drawString(margin, height - 56 * mm, f"Jumlah Soal: {total_questions}")
    c.line(margin, height - 60 * mm, width - margin, height - 60 * mm)

    label_y = height - 68 * mm
    c.setFont("Helvetica-Bold", 9)
    c.drawString(margin + 20 * mm, label_y, "Jawaban")
    for j, opt in enumerate(["A", "B", "C", "D", "E"][:options]):
        cx = margin + 38 * mm + j * 14 * mm
        c.drawString(cx, label_y, opt)

    bubble_y = label_y - 6 * mm
    c.setFont("Helvetica", 8)
    for i in range(total_questions):
        y_pos = bubble_y - i * 10 * mm
        c.drawString(margin, y_pos + 2 * mm, str(i + 1))

        for j in range(options):
            cx = margin + 38 * mm + j * 14 * mm
            c.circle(cx + 3 * mm, y_pos + 2 * mm, 2.5 * mm, fill=0, stroke=1)

        if (i + 1) % 25 == 0 or i == total_questions - 1:
            c.showPage()
            if i < total_questions - 1:
                c.setFont("Helvetica-Bold", 18)
                c.drawString(margin, height - 30 * mm, title)
                c.setFont("Helvetica-Bold", 9)
                c.drawString(margin + 20 * mm, label_y, "Jawaban")
                for j, opt in enumerate(["A", "B", "C", "D", "E"][:options]):
                    cx = margin + 38 * mm + j * 14 * mm
                    c.drawString(cx, label_y, opt)
                bubble_y = label_y - 6 * mm

    c.save()
    buf.seek(0)
    return buf
