"""Extract text, images, and detect question types from PDF exam files."""
import io
import re
import logging
from typing import List, Dict, Optional

logger = logging.getLogger("app")

# Keywords that indicate essay questions
ESSAY_KEYWORDS = [
    "jelaskan", "uraikan", "analisislah", "sebutkan", "tuliskan",
    "terangkan", "deskripsikan", "ceritakan", "simpulkan", "berikan pendapat",
    "evaluate", "describe", "explain", "analyze", "discuss", "elaborate",
    "mengapa", "bagaimana", "apa yang dimaksud", "apa perbedaan",
    "bandingkan", "hubungkan", "kaitkan", "kemukakan", "argumentasikan",
]

# Keywords that indicate MCQ
MCQ_KEYWORDS = [
    "pilihlah", "pilih salah satu", "berilah tanda silang",
]

# Patterns for MCQ options
OPTION_PATTERNS = re.compile(r'^[A-Ea-e][\.\)]\s+\S', re.MULTILINE)


def _is_essay_line(line: str) -> bool:
    """Check if a line of text looks like an essay question."""
    lower = line.strip().lower()
    for kw in ESSAY_KEYWORDS:
        if lower.startswith(kw) or kw in lower[:80]:
            return True
    return False


def _extract_images(pdf_document) -> List[bytes]:
    """Extract images from PDF pages as PNG bytes."""
    images = []
    for page_num in range(len(pdf_document)):
        page = pdf_document[page_num]
        for img_index, img in enumerate(page.get_images(full=True)):
            xref = img[0]
            base_image = pdf_document.extract_image(xref)
            if base_image:
                images.append(base_image["image"])
    return images


def parse_pdf(file_bytes: bytes) -> Dict:
    """Parse a PDF exam file and extract:
    - full_text: raw text from all pages
    - questions: list of detected questions with type and text
    - images: embedded images from the PDF
    - page_count: number of pages
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        logger.error("PyMuPDF (fitz) not installed")
        return {"error": "PyMuPDF tidak tersedia"}

    pdf = fitz.open(stream=file_bytes, filetype="pdf")
    full_text = ""
    pages_text = []

    for page_num in range(len(pdf)):
        page = pdf[page_num]
        text = page.get_text()
        pages_text.append(text)
        full_text += f"\n--- Halaman {page_num + 1} ---\n{text}"

    images = _extract_images(pdf)
    pdf.close()

    # Parse questions using numbered pattern
    questions = _parse_questions(full_text)

    return {
        "full_text": full_text,
        "pages_text": pages_text,
        "questions": questions,
        "images": images,
        "page_count": len(pdf) if hasattr(pdf, '__len__') else len(pages_text),
        "mcq_count": sum(1 for q in questions if q["type"] == "mcq"),
        "essay_count": sum(1 for q in questions if q["type"] == "essay"),
    }


def _parse_questions(text: str) -> List[Dict]:
    """Detect and classify questions by number pattern."""
    questions = []
    # Match question number patterns like "1.", "1)", "1)", "Soal 1"
    pattern = re.compile(r'(?:^|\n)\s*(?:Soal\s+)?(\d+)[\.\)]\s*(.*?)(?=\n\s*(?:Soal\s+)?\d+[\.\)]|\Z)', re.DOTALL)
    matches = pattern.findall(text)

    if not matches:
        # Fallback: split by lines and detect
        lines = text.strip().split("\n")
        current_q = None
        current_text = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            m = re.match(r'^(\d+)[\.\)]\s*(.*)', line)
            if m:
                if current_q is not None:
                    questions.append(current_q)
                qnum = int(m.group(1))
                qtext = m.group(2).strip()
                qtype = "essay" if _is_essay_line(qtext) else "mcq"
                current_q = {"number": qnum, "text": qtext, "type": qtype, "full_text": qtext}
                current_text = [qtext]
            elif current_q is not None:
                current_text.append(line)
                current_q["full_text"] = "\n".join(current_text)
                # Re-evaluate type based on longer text
                if _is_essay_line("\n".join(current_text)):
                    current_q["type"] = "essay"
        if current_q is not None:
            questions.append(current_q)
        return questions

    for num_str, q_text in matches:
        qnum = int(num_str)
        q_text = q_text.strip()
        if not q_text or len(q_text) < 5:
            continue
        # Check for MCQ options
        has_options = bool(OPTION_PATTERNS.search(q_text))
        # Check for essay keywords
        is_essay = _is_essay_line(q_text)
        # Heuristic: if it has A. B. C. D. patterns, it's MCQ
        if has_options and not is_essay:
            qtype = "mcq"
        elif is_essay:
            qtype = "essay"
        else:
            # Default heuristic: short text = MCQ, long text = essay
            qtype = "essay" if len(q_text) > 120 else "mcq"

        questions.append({
            "number": qnum,
            "text": q_text[:200],
            "type": qtype,
            "full_text": q_text,
        })

    return questions


def generate_preview_html(parsed: Dict) -> str:
    """Generate HTML preview of parsed questions for teacher review."""
    if parsed.get("error"):
        return f'<p class="text-red-500">{parsed["error"]}</p>'
    html = f'<p class="text-xs text-surface-500 mb-3">Ditemukan {parsed["page_count"]} halaman, {parsed["mcq_count"]} MCQ, {parsed["essay_count"]} Essay</p>'
    html += '<div class="space-y-1 max-h-80 overflow-y-auto">'
    for q in parsed.get("questions", []):
        badge = "MCQ" if q["type"] == "mcq" else "Essay"
        badge_class = "bg-blue-100 text-blue-700" if q["type"] == "mcq" else "bg-amber-100 text-amber-700"
        html += f'<div class="flex items-center gap-2 p-2 rounded-lg bg-surface-50"><span class="text-xs font-bold text-surface-400 w-6">{q["number"]}.</span><span class="text-xs font-bold text-surface-600 flex-1 truncate">{q["text"][:80]}</span><span class="text-[10px] font-bold px-2 py-0.5 rounded-full {badge_class}">{badge}</span></div>'
    html += "</div>"
    return html
