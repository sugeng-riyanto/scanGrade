import io
import openpyxl
from flask import send_file


def export_to_xlsx(submissions: list) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Student", "Score", "Status"])
    for s in submissions:
        ws.append([s.get("student_id"), s.get("score"), s.get("status")])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
