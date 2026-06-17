import os
import fitz
from flask import current_app


def upload_pdf(file_obj, exam_id: str) -> dict:
    """Convert PDF to local page images for student canvas.

    Saves the original PDF + PNG pages to ``/static/uploads/exams/<exam_id>/``
    so NGINX can serve them directly — no dependency on Supabase Storage.

    Returns:
        dict with pdf_path, page_urls, total_pages
    Raises:
        ValueError if file is not a valid PDF
    """
    if not file_obj or not file_obj.filename:
        raise ValueError("File tidak ditemukan")

    raw = file_obj.read()
    if len(raw) < 4 or raw[:4] != b'%PDF':
        raise ValueError("File yang diupload bukan PDF valid")
    if len(raw) > 50 * 1024 * 1024:
        raise ValueError("File terlalu besar. Maksimal 50MB")
    if len(raw) == 0:
        raise ValueError("File kosong")

    try:
        doc = fitz.open(stream=raw, filetype="pdf")
    except Exception as e:
        raise ValueError(f"Gagal membaca PDF: {e}")

    # Create exam directory inside static/uploads/exams/
    exam_dir = os.path.join(current_app.root_path, "static", "uploads", "exams", exam_id)
    os.makedirs(exam_dir, exist_ok=True)

    try:
        # Save original PDF
        pdf_local = os.path.join(exam_dir, "exam.pdf")
        with open(pdf_local, "wb") as f:
            f.write(raw)

        # Convert each page to PNG
        page_urls = []
        for i in range(len(doc)):
            page = doc.load_page(i)
            pix = page.get_pixmap(dpi=150)
            img_bytes = pix.tobytes("png")
            img_name = f"page_{i+1:03d}.png"
            img_path = os.path.join(exam_dir, img_name)
            with open(img_path, "wb") as f:
                f.write(img_bytes)
            page_urls.append(f"/static/uploads/exams/{exam_id}/{img_name}")
    finally:
        doc.close()

    if not page_urls:
        raise ValueError("PDF tidak memiliki halaman")

    return {
        "pdf_path": f"/static/uploads/exams/{exam_id}/exam.pdf",
        "page_urls": page_urls,
        "total_pages": len(page_urls),
    }
