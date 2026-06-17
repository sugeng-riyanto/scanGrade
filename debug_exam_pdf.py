"""Usage: python3 debug_exam_pdf.py <exam_id>"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from app import create_app
from app.utils.auth import get_supabase
app = create_app()
exam_id = sys.argv[1] if len(sys.argv) > 1 else input("Exam ID: ")
with app.app_context():
    s = get_supabase()
    e = s.table("exams").select("id,pdf_url,pdf_page_urls,title").eq("id", exam_id).single().execute().data
    print("Title:", e.get("title"))
    print("pdf_url:", e.get("pdf_url"))
    print("pdf_page_urls:", e.get("pdf_page_urls"))
    d = os.path.join(app.root_path, "static", "uploads", "exams", exam_id)
    print("Exam dir:", d)
    print("Dir exists:", os.path.isdir(d))
    if os.path.isdir(d):
        print("Files:", os.listdir(d))
