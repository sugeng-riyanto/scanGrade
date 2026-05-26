import os
import uuid
from flask import current_app


def upload_pdf(file_obj, exam_id: str) -> str:
    ext = "pdf"
    filename = f"{uuid.uuid4()}.{ext}"
    supabase = current_app.extensions["supabase"]
    res = supabase.storage.from_("exam-pdfs").upload(filename, file_obj.read())
    return f"{filename}"


def get_pdf_url(path: str) -> str:
    supabase = current_app.extensions["supabase"]
    return supabase.storage.from_("exam-pdfs").get_public_url(path)
