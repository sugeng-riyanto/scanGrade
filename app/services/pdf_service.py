import os
import fitz
from flask import current_app

# Width of the page rail's thumbnails, in pixels. Wide enough to recognise a
# page at a glance, small enough that a whole exam costs less than one page of
# the full-resolution set (see ensure_page_thumbs).
THUMB_WIDTH = 180


def thumb_name(page_name: str) -> str:
    """``page_003.png`` -> ``thumb_003.png``.

    The name is derived, not stored: no column, no migration, and an exam
    uploaded before thumbnails existed still works because the route backfills
    what is missing.
    """
    return "thumb_" + (page_name[5:] if page_name.startswith("page_") else page_name)


def ensure_page_thumbs(exam_id: str, page_urls) -> int:
    """Write the rail's small copy of each page image, once per page ever.

    The page rail shows every page of the paper at the same time. Pointed at
    the 150-dpi PNGs that would mean a student downloading the whole exam a
    second time — the opposite of what a spotty connection can afford — so each
    page gets a thumbnail next to it that NGINX serves as a plain static file
    from then on. The function is idempotent by design, which is what lets the
    exam route call it on every load to fill in exams uploaded earlier.

    Returns the number of thumbnails actually created.
    """
    from PIL import Image

    exam_dir = os.path.join(current_app.root_path, "static", "uploads", "exams", exam_id)
    if not os.path.isdir(exam_dir):
        return 0
    made = 0
    for url in page_urls or []:
        name = os.path.basename(str(url))
        if not name:
            continue
        src = os.path.join(exam_dir, name)
        dst = os.path.join(exam_dir, thumb_name(name))
        if os.path.exists(dst) or not os.path.exists(src):
            continue
        try:
            with Image.open(src) as im:
                im = im.convert("RGB")
                height = max(1, round(im.height * THUMB_WIDTH / max(1, im.width)))
                im.resize((THUMB_WIDTH, height), Image.LANCZOS).save(dst, "PNG", optimize=True)
            made += 1
        except Exception as e:  # a missing thumbnail is cosmetic, never fatal
            current_app.logger.warning("Thumbnail failed for %s: %s", src, e)
    if made:
        current_app.logger.info("Created %s page thumbnail(s) for exam %s", made, exam_id)
    return made


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

    # Build the rail's copies now, so the first student to open this exam does
    # not pay for them.
    ensure_page_thumbs(exam_id, page_urls)

    return {
        "pdf_path": f"/static/uploads/exams/{exam_id}/exam.pdf",
        "page_urls": page_urls,
        "total_pages": len(page_urls),
    }
