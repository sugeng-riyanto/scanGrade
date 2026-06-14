import os
import uuid
import fitz
from flask import current_app


def _upload_to_supabase(file_data: bytes, path: str, mime: str) -> str:
    """Upload file to Supabase Storage bucket exam-pdfs, return public URL."""
    from app.utils.auth import get_supabase
    supabase = get_supabase()
    supabase.storage.from_("exam-pdfs").upload(path, file_data, {"content-type": mime})
    # Return public URL
    base = current_app.config.get("SUPABASE_URL", "").rstrip("/")
    if "/rest/v1" in base:
        base = base.split("/rest/v1")[0]
    return f"{base}/storage/v1/object/public/exam-pdfs/{path}"


def upload_pdf(file_obj, exam_id: str) -> dict:
    """Upload PDF file to Supabase Storage, convert pages to PNG images.

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

    pdf_filename = f"{exam_id}/{uuid.uuid4().hex[:12]}.pdf"
    page_urls = []

    try:
        # Upload original PDF
        pdf_url = _upload_to_supabase(raw, pdf_filename, "application/pdf")

        # Convert each page to PNG and upload
        for i in range(len(doc)):
            page = doc.load_page(i)
            pix = page.get_pixmap(dpi=150)
            img_bytes = pix.tobytes("png")
            img_name = f"{exam_id}/page_{i+1:03d}.png"
            img_url = _upload_to_supabase(img_bytes, img_name, "image/png")
            page_urls.append(img_url)
    finally:
        doc.close()

    if not page_urls:
        raise ValueError("PDF tidak memiliki halaman")

    return {
        "pdf_path": pdf_url,
        "page_urls": page_urls,
        "total_pages": len(page_urls),
    }
