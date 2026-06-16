import os
import uuid
import json
import fitz
from io import BytesIO
from datetime import datetime, timezone
from flask import current_app, g
from PIL import Image, ImageDraw
from app.utils.auth import get_supabase

UPLOAD_DIR = "app/static/uploads/whiteboard"


def _ensure_upload_dir(whiteboard_id: str) -> str:
    path = os.path.join(UPLOAD_DIR, whiteboard_id)
    os.makedirs(path, exist_ok=True)
    return path


def _school_id() -> str | None:
    sid = g.get("user_school_id")
    if not sid or sid == "None":
        return None
    return sid


# ── Session CRUD ──

def create_whiteboard(title: str, class_id: str, student_ids: list = None) -> dict:
    """Create a new whiteboard session."""
    sid = _school_id()
    if not sid:
        raise ValueError("School ID not found")

    supabase = get_supabase()
    data = {
        "school_id": sid,
        "teacher_id": g.user_id,
        "class_id": class_id,
        "title": title,
        "status": "active",
    }
    result = supabase.table("whiteboards").insert(data).execute()
    wb = result.data[0]

    if student_ids:
        members = [
            {"whiteboard_id": wb["id"], "student_id": sid}
            for sid in student_ids
        ]
        supabase.table("whiteboard_members").insert(members).execute()

    return wb


def get_whiteboard(whiteboard_id: str) -> dict | None:
    supabase = get_supabase()
    try:
        result = supabase.table("whiteboards").select("*").eq("id", whiteboard_id).single().execute()
    except Exception:
        return None
    if result.data:
        ds = result.data.get("display_settings")
        if isinstance(ds, str):
            try: result.data["display_settings"] = json.loads(ds)
            except: result.data["display_settings"] = {}
        elif not isinstance(ds, dict):
            result.data["display_settings"] = {"board_mode":"white","grid_enabled":False,"grid_spacing":50,"grid_logarithmic":False}
    return result.data if result.data else None


def list_whiteboards(role: str = "teacher") -> list:
    """List whiteboards for teacher or student."""
    supabase = get_supabase()
    sid = _school_id()
    if not sid:
        return []

    if role == "teacher":
        result = supabase.table("whiteboards").select("*").eq("school_id", sid).eq("teacher_id", g.user_id).order("created_at", desc=True).execute()
    else:
        profile = supabase.table("profiles").select("class_id").eq("id", g.user_id).single().execute().data
        class_id = profile.get("class_id") if profile else None
        if not class_id:
            return []
        result = supabase.table("whiteboards").select("*").eq("school_id", sid).eq("class_id", class_id).order("created_at", desc=True).execute()

    return result.data or []


def update_whiteboard(whiteboard_id: str, data: dict) -> dict | None:
    supabase = get_supabase()
    result = supabase.table("whiteboards").update(data).eq("id", whiteboard_id).execute()
    return result.data[0] if result.data else None


def delete_whiteboard(whiteboard_id: str) -> bool:
    """Delete whiteboard and all associated files."""
    supabase = get_supabase()
    supabase.table("whiteboards").delete().eq("id", whiteboard_id).execute()

    path = os.path.join(UPLOAD_DIR, whiteboard_id)
    if os.path.exists(path):
        for f in os.listdir(path):
            os.remove(os.path.join(path, f))
        os.rmdir(path)
    return True


# ── Members / Permission ──

def add_members(whiteboard_id: str, student_ids: list) -> list:
    supabase = get_supabase()
    members = [
        {"whiteboard_id": whiteboard_id, "student_id": sid}
        for sid in student_ids
    ]
    result = supabase.table("whiteboard_members").insert(members).execute()
    return result.data or []


def remove_member(whiteboard_id: str, student_id: str) -> bool:
    supabase = get_supabase()
    supabase.table("whiteboard_members").delete().eq("whiteboard_id", whiteboard_id).eq("student_id", student_id).execute()
    return True


def get_members(whiteboard_id: str) -> list:
    """Get members with student info."""
    supabase = get_supabase()
    result = supabase.table("whiteboard_members").select("*, profiles(full_name)").eq("whiteboard_id", whiteboard_id).execute()
    return result.data or []


def set_permission(whiteboard_id: str, student_id: str, can_annotate: bool) -> bool:
    supabase = get_supabase()
    supabase.table("whiteboard_members").update({"can_annotate": can_annotate}).eq("whiteboard_id", whiteboard_id).eq("student_id", student_id).execute()
    return True


def bulk_set_permission(whiteboard_id: str, can_annotate: bool):
    supabase = get_supabase()
    supabase.table("whiteboard_members").update({"can_annotate": can_annotate}).eq("whiteboard_id", whiteboard_id).execute()


def is_member(whiteboard_id: str, student_id: str) -> bool:
    supabase = get_supabase()
    result = supabase.table("whiteboard_members").select("id").eq("whiteboard_id", whiteboard_id).eq("student_id", student_id).single().execute()
    return result.data is not None


def can_annotate(whiteboard_id: str, student_id: str) -> bool:
    supabase = get_supabase()
    result = supabase.table("whiteboard_members").select("can_annotate").eq("whiteboard_id", whiteboard_id).eq("student_id", student_id).single().execute()
    return result.data.get("can_annotate", False) if result.data else False


# ── Slides ──

def add_slide(whiteboard_id: str, slide_number: int = None, background_url: str = None) -> dict:
    supabase = get_supabase()
    if slide_number is None:
        result = supabase.table("whiteboard_slides").select("slide_number").eq("whiteboard_id", whiteboard_id).order("slide_number", desc=True).limit(1).execute()
        slide_number = (result.data[0]["slide_number"] + 1) if result.data else 1

    data = {"whiteboard_id": whiteboard_id, "slide_number": slide_number, "background_url": background_url}
    result = supabase.table("whiteboard_slides").insert(data).execute()
    return result.data[0]


def delete_slide(whiteboard_id: str, slide_number: int) -> bool:
    supabase = get_supabase()
    supabase.table("whiteboard_slides").delete().eq("whiteboard_id", whiteboard_id).eq("slide_number", slide_number).execute()
    supabase.table("whiteboard_ops").delete().eq("whiteboard_id", whiteboard_id).eq("slide_number", slide_number).execute()
    return True


def reorder_slides(whiteboard_id: str, slide_order: list) -> bool:
    """slide_order: [{slide_number: 3, new_order: 1}, ...]"""
    supabase = get_supabase()
    for item in slide_order:
        supabase.table("whiteboard_slides").update({"slide_number": item["new_order"]}).eq("whiteboard_id", whiteboard_id).eq("slide_number", item["slide_number"]).execute()
    return True


def list_slides(whiteboard_id: str) -> list:
    supabase = get_supabase()
    result = supabase.table("whiteboard_slides").select("*").eq("whiteboard_id", whiteboard_id).order("slide_number").execute()
    return result.data or []


def upload_slide_background(whiteboard_id: str, file_obj) -> dict:
    """Upload PDF/Image as slide background. Save locally. Return per-page URLs."""
    from werkzeug.utils import secure_filename

    _ensure_upload_dir(whiteboard_id)
    filename = secure_filename(file_obj.filename or f"slide_{uuid.uuid4().hex[:8]}")
    raw = file_obj.read()

    if raw[:4] == b"%PDF":
        doc = fitz.open(stream=raw, filetype="pdf")
        pages = []
        for i in range(len(doc)):
            page = doc.load_page(i)
            pix = page.get_pixmap(dpi=150)
            img_name = f"slide_{i+1:03d}.png"
            img_path = os.path.join(UPLOAD_DIR, whiteboard_id, img_name)
            pix.save(img_path)
            pages.append({
                "slide_number": i + 1,
                "background_url": f"/static/uploads/whiteboard/{whiteboard_id}/{img_name}",
            })
        doc.close()
        return {"type": "pdf", "pages": pages, "total": len(pages)}
    else:
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "png"
        if ext not in ("png", "jpg", "jpeg", "gif", "webp"):
            ext = "png"
        img_name = f"slide_001.{ext}"
        img_path = os.path.join(UPLOAD_DIR, whiteboard_id, img_name)
        with open(img_path, "wb") as f:
            f.write(raw)
        return {
            "type": "image",
            "pages": [{"slide_number": 1, "background_url": f"/static/uploads/whiteboard/{whiteboard_id}/{img_name}"}],
            "total": 1,
        }


# ── Drawing Ops ──

def log_op(whiteboard_id: str, slide_number: int, op_type: str, data: dict, timestamp: int) -> dict:
    supabase = get_supabase()
    result = supabase.table("whiteboard_ops").select("seq_number").eq("whiteboard_id", whiteboard_id).eq("slide_number", slide_number).order("seq_number", desc=True).limit(1).execute()
    seq = (result.data[0]["seq_number"] + 1) if result.data else 1

    row = {
        "whiteboard_id": whiteboard_id,
        "slide_number": slide_number,
        "user_id": g.user_id,
        "op_type": op_type,
        "data": json.dumps(data),
        "timestamp": timestamp,
        "seq_number": seq,
    }
    supabase.table("whiteboard_ops").insert(row).execute()
    return {"seq": seq, "user_id": g.user_id}


def get_ops(whiteboard_id: str, slide_number: int, since_seq: int = None) -> list:
    supabase = get_supabase()
    query = supabase.table("whiteboard_ops").select("*").eq("whiteboard_id", whiteboard_id).eq("slide_number", slide_number).order("seq_number")
    if since_seq:
        query = query.gt("seq_number", since_seq)
    result = query.execute()
    for op in result.data or []:
        if isinstance(op.get("data"), str):
            op["data"] = json.loads(op["data"])
    return result.data or []


# ── Reactions ──

def log_reaction(whiteboard_id: str, emoji: str) -> dict:
    supabase = get_supabase()
    data = {"whiteboard_id": whiteboard_id, "user_id": g.user_id, "emoji": emoji}
    result = supabase.table("whiteboard_reactions").insert(data).execute()
    return result.data[0] if result.data else data


# ── Anti-cheat Log ──

def log_anti_cheat(whiteboard_id: str, event_type: str, event_data: dict = None):
    supabase = get_supabase()
    data = {
        "whiteboard_id": whiteboard_id,
        "user_id": g.user_id,
        "event_type": event_type,
        "event_data": json.dumps(event_data or {}),
    }
    supabase.table("whiteboard_anti_cheat_log").insert(data).execute()


# ── Snapshots ──

def save_snapshot(whiteboard_id: str, slide_number: int, image_data: str) -> dict:
    """Save a canvas snapshot (base64 PNG) to local storage."""
    import base64
    from werkzeug.utils import secure_filename

    _ensure_upload_dir(whiteboard_id)
    img_data = base64.b64decode(image_data.split(",")[1] if "," in image_data else image_data)
    filename = f"snapshot_s{slide_number:03d}_{uuid.uuid4().hex[:8]}.png"
    path = os.path.join(UPLOAD_DIR, whiteboard_id, filename)
    with open(path, "wb") as f:
        f.write(img_data)

    supabase = get_supabase()
    url = f"/static/uploads/whiteboard/{whiteboard_id}/{filename}"
    data = {"whiteboard_id": whiteboard_id, "slide_number": slide_number, "image_url": url}
    supabase.table("whiteboard_snapshots").insert(data).execute()
    return {"url": url}


def list_snapshots(whiteboard_id: str) -> list:
    supabase = get_supabase()
    result = supabase.table("whiteboard_snapshots").select("*").eq("whiteboard_id", whiteboard_id).order("created_at", desc=True).execute()
    return result.data or []


# ── PDF Export ──

def _replay_ops_on_background(background_path: str, ops: list, output_path: str):
    """Render drawing ops on a background image using Pillow."""
    img = Image.open(background_path).convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    for op in ops:
        d = op.get("data", {})
        if not isinstance(d, dict):
            continue
        color = d.get("color", "#000000")
        width = d.get("width", 3)
        opacity = int(d.get("opacity", 255))
        try:
            r, g, b = tuple(int(color.lstrip("#")[i:i+2], 16) for i in (0, 2, 4))
        except (ValueError, AttributeError):
            r, g, b = 0, 0, 0

        if op["op_type"] == "line":
            points = d.get("points", [])
            if len(points) > 1:
                for i in range(len(points) - 1):
                    draw.line([points[i][0], points[i][1], points[i+1][0], points[i+1][1]],
                              fill=(r, g, b, opacity), width=width)
        elif op["op_type"] == "text":
            text = d.get("text", "")
            x, y = d.get("x", 0), d.get("y", 0)
            font_size = d.get("fontSize", 16)
            try:
                from PIL import ImageFont
                font = ImageFont.truetype("app/static/vendor/inter/Inter-Regular.ttf", font_size)
            except (OSError, IOError):
                font = ImageFont.load_default()
            draw.text((x, y), text, fill=(r, g, b, opacity), font=font)

    img = Image.alpha_composite(img, overlay).convert("RGB")
    img.save(output_path, "PNG")


def export_pdf(whiteboard_id: str) -> BytesIO:
    """Export whiteboard as PDF: render ops on backgrounds → Reportlab PDF."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as pdf_canvas
    from reportlab.lib.utils import ImageReader

    supabase = get_supabase()
    wb = supabase.table("whiteboards").select("title").eq("id", whiteboard_id).single().execute().data
    title = wb.get("title", "Papan Tulis") if wb else "Papan Tulis"
    slides = supabase.table("whiteboard_slides").select("*").eq("whiteboard_id", whiteboard_id).order("slide_number").execute().data or []

    buf = BytesIO()
    pdf = pdf_canvas.Canvas(buf, pagesize=A4)
    pdf_w, pdf_h = A4

    for slide in slides:
        slide_num = slide["slide_number"]
        bg_url = slide.get("background_url")
        ops = get_ops(whiteboard_id, slide_num)

        # Determine local path
        bg_path = None
        if bg_url and bg_url.startswith("/static/"):
            bg_path = os.path.join(current_app.static_folder, bg_url.replace("/static/", "", 1))
            if os.path.exists(bg_path):
                pass
            else:
                bg_path = None

        render_path = os.path.join(UPLOAD_DIR, whiteboard_id, f"_render_s{slide_num:03d}.png")
        try:
            if bg_path and ops:
                _replay_ops_on_background(bg_path, ops, render_path)
                img_path = render_path
            elif bg_path:
                img_path = bg_path
            else:
                img_path = None

            if img_path and os.path.exists(img_path):
                img = ImageReader(img_path)
                iw, ih = Image.open(img_path).size
                scale = min(pdf_w / iw, pdf_h / ih)
                dw, dh = iw * scale, ih * scale
                x = (pdf_w - dw) / 2
                y = (pdf_h - dh) / 2
                pdf.drawImage(img, x, y, dw, dh)
            else:
                pdf.setFont("Helvetica", 14)
                pdf.drawString(50, pdf_h - 50, f"Slide {slide_num}")
        finally:
            if os.path.exists(render_path):
                os.remove(render_path)

        pdf.showPage()

    pdf.save()
    buf.seek(0)
    return buf
