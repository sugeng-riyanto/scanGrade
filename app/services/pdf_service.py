import os
import uuid
import fitz
from flask import current_app


def upload_pdf(file_obj, exam_id: str) -> dict:
    base = os.path.join(current_app.root_path, "static", "uploads", "exams", exam_id)
    os.makedirs(base, exist_ok=True)

    pdf_filename = f"{uuid.uuid4().hex[:12]}.pdf"
    pdf_path = os.path.join(base, pdf_filename)
    file_obj.seek(0)
    file_obj.save(pdf_path)

    doc = fitz.open(pdf_path)
    page_urls = []
    for i in range(len(doc)):
        page = doc.load_page(i)
        pix = page.get_pixmap(dpi=150)
        img_name = f"page_{i+1:03d}.png"
        img_path = os.path.join(base, img_name)
        pix.save(img_path)
        page_urls.append(f"/static/uploads/exams/{exam_id}/{img_name}")
    doc.close()

    return {
        "pdf_path": f"/static/uploads/exams/{exam_id}/{pdf_filename}",
        "page_urls": page_urls,
        "total_pages": len(page_urls),
    }
