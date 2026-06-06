import os
import uuid
import fitz
from flask import current_app

# PDF magic bytes: %PDF
PDF_MAGIC = b'%PDF'


def _is_valid_pdf(file_obj) -> bool:
    """Check if file starts with PDF magic bytes."""
    file_obj.seek(0)
    header = file_obj.read(4)
    file_obj.seek(0)
    return header == PDF_MAGIC


def upload_pdf(file_obj, exam_id: str) -> dict:
    """Upload PDF file and convert pages to images.

    Returns:
        dict with pdf_path, page_urls, total_pages
    Raises:
        ValueError if file is not a valid PDF
    """
    if not file_obj or not file_obj.filename:
        raise ValueError("File tidak ditemukan")

    if not _is_valid_pdf(file_obj):
        raise ValueError("File yang diupload bukan PDF valid")

    # Validate file size (50MB max)
    file_obj.seek(0, 2)
    size = file_obj.tell()
    file_obj.seek(0)
    if size > 50 * 1024 * 1024:
        raise ValueError("File terlalu besar. Maksimal 50MB")
    if size == 0:
        raise ValueError("File kosong")

    base = os.path.join(current_app.root_path, "static", "uploads", "exams", exam_id)
    os.makedirs(base, exist_ok=True)

    pdf_filename = f"{uuid.uuid4().hex[:12]}.pdf"
    pdf_path = os.path.join(base, pdf_filename)
    file_obj.save(pdf_path)

    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        os.remove(pdf_path)
        raise ValueError(f"Gagal membaca PDF: {e}")

    page_urls = []
    try:
        for i in range(len(doc)):
            page = doc.load_page(i)
            pix = page.get_pixmap(dpi=150)
            img_name = f"page_{i+1:03d}.png"
            img_path = os.path.join(base, img_name)
            pix.save(img_path)
            page_urls.append(f"/static/uploads/exams/{exam_id}/{img_name}")
    finally:
        doc.close()

    if not page_urls:
        os.remove(pdf_path)
        raise ValueError("PDF tidak memiliki halaman")

    return {
        "pdf_path": f"/static/uploads/exams/{exam_id}/{pdf_filename}",
        "page_urls": page_urls,
        "total_pages": len(page_urls),
    }
