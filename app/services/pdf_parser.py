"""PDF → Quarto markdown. Uses pdftotext (poppler) for best-quality text extraction."""
import re
import json
import subprocess
import tempfile
import os
import logging
from typing import List, Dict

logger = logging.getLogger("app")


def pdf_to_markdown(file_bytes: bytes) -> Dict:
    """Extract ALL text from PDF using pdftotext -layout. Verifies page count vs PyMuPDF."""
    expected_pages = 0
    try:
        import fitz
        pdf_check = fitz.open(stream=file_bytes, filetype="pdf")
        expected_pages = len(pdf_check)
        pdf_check.close()
    except:
        pass

    pages_raw = []
    total_pages = 0

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(file_bytes)
        pdf_path = tmp.name

    try:
        result = subprocess.run(
            ["pdftotext", "-layout", pdf_path, "-"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            full_text = result.stdout
        else:
            raise RuntimeError("pdftotext error: " + str(result.stderr))
    except FileNotFoundError:
        logger.warning("pdftotext not installed, falling back to PyMuPDF")
        os.unlink(pdf_path)
        return _fallback_fitz(file_bytes)
    except Exception as e:
        logger.warning("pdftotext failed: %s, falling back to PyMuPDF", e)
        os.unlink(pdf_path)
        return _fallback_fitz(file_bytes)

    try:
        os.unlink(pdf_path)
    except:
        pass

    pages_raw = full_text.split("\f")
    total_pages = len(pages_raw)

    # Verify page count
    if expected_pages and total_pages < expected_pages:
        logger.warning(
            "pdftotext: %d pages vs PyMuPDF: %d — using fallback",
            total_pages, expected_pages)
        return _fallback_fitz(file_bytes)
    pages_md = []

    for idx, page_text in enumerate(pages_raw):
        lines = page_text.split("\n")
        md_lines = []
        in_equation = False
        eq_buf = []

        for line in lines:
            text = line.rstrip()
            stripped = text.strip()

            # Preserve blank lines for paragraph separation
            if not stripped:
                if eq_buf:
                    continue
                md_lines.append("")
                continue

            # ── Skip page numbers and headers (lines that are just numbers or "Page X") ──
            if re.match(r'^\s*\d+\s*$', stripped) and len(stripped) <= 6:
                continue
            if re.match(r'^Page\s+\d+|^Halaman\s+\d+', stripped, re.IGNORECASE):
                continue

            # ── Equation detection ──
            # Lines with math symbols, equations, formulas
            has_math = bool(re.search(r'[=×÷±√∑∫∞πθΔλμσΩωαβγ\^_{}≥≤≈≠±·∂∇∈∉⊂⊃∪∩∧∃∀]', stripped))
            starts_q = bool(re.match(r'^\d+[\.\)]\s', stripped))
            is_option = bool(re.match(r'^[A-D][\.\)]\s', stripped))
            is_bullet = stripped.startswith(("- ", "• ", "* ", "– "))

            # Multi-line equation: consecutive math lines
            if has_math and not starts_q and not is_option and len(stripped) < 80:
                eq_buf.append(stripped)
                in_equation = True
                continue
            elif in_equation and eq_buf:
                eq = " ".join(eq_buf)
                md_lines.append(f"$$ {eq} $$")
                md_lines.append("")
                eq_buf = []
                in_equation = False

            # ── MCQ option ──
            if is_option:
                md_lines.append(f"  {stripped}")
                continue

            # ── Numbered question ──
            if starts_q:
                md_lines.append(stripped)
                continue

            # ── Bullet ──
            if is_bullet:
                md_lines.append(f"- {stripped[2:].lstrip()}")
                continue

            # ── Regular text ──
            md_lines.append(stripped)

        # Flush equation buffer
        if eq_buf:
            eq = " ".join(eq_buf)
            md_lines.append(f"$$ {eq} $$")
            md_lines.append("")

        # Build clean page text
        page_md = "\n".join(md_lines)
        page_md = re.sub(r'\n{4,}', '\n\n\n', page_md)

        pages_md.append(page_md)

    markdown = "\n\n---\n\n".join(pages_md)

    return {
        "markdown": markdown,
        "raw_text": full_text,
        "pages": pages_md,
        "page_count": total_pages,
        "images": [],
        "questions": [],
        "mcq_count": 0,
        "essay_count": 0,
    }


def _fallback_fitz(file_bytes: bytes) -> Dict:
    """Fallback: use PyMuPDF when pdftotext is unavailable."""
    try:
        import fitz
    except ImportError:
        return {"error": "PyMuPDF tidak tersedia. Install: pip install pymupdf"}
    pdf = fitz.open(stream=file_bytes, filetype="pdf")
    pages_md = []
    raw_text = ""
    for i in range(len(pdf)):
        text = pdf[i].get_text()
        raw_text += f"\n\n--- Page {i+1} ---\n\n{text}"
        lines = text.split("\n")
        md = []
        for line in lines:
            s = line.strip()
            if not s:
                md.append("")
                continue
            if re.match(r'^[A-D][\.\)]\s', s):
                md.append(f"  {s}")
            elif re.match(r'^\d+[\.\)]\s', s):
                md.append(s)
            else:
                md.append(s)
        pages_md.append("\n".join(md))
    pdf.close()
    return {
        "markdown": "\n\n---\n\n".join(pages_md),
        "raw_text": raw_text, "pages": pages_md,
        "page_count": len(pdf), "images": [],
        "questions": [], "mcq_count": 0, "essay_count": 0,
    }


# ── Question classification (AI + heuristic) ──

def classify_with_ai(markdown: str, api_key: str = None, provider: str = "groq") -> List[Dict]:
    if not api_key:
        return classify_heuristic(markdown)

    # Limit to 20k chars for AI
    md_for_ai = markdown[:20000]

    prompt = f"""Analyze this exam paper. Identify ALL questions.
For EACH question, classify as "mcq" (multiple choice with A B C D options) or "essay" (written answer).

Rules:
- MCQ: has answer choices A. B. C. D.
- Essay: has sub-parts (a)(b)(c), mark allocations [2] [Total: 8], command words (Explain, Describe, Calculate)
- CIE Paper 1: 40 questions with A B C D = ALL MCQ
- CIE Paper 2-5: fewer questions, (a)(b)(c), marks = essays

Output ONLY JSON array:
[{{"number": 1, "type": "mcq", "text": "first 100 chars..."}}, ...]

Exam:
{md_for_ai}
"""
    try:
        from app.services.ai_service import _call_ai
        raw = _call_ai({"api_key": api_key, "provider": "groq"}, prompt)
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0]
        questions = json.loads(cleaned.strip())
        if isinstance(questions, list) and len(questions) > 0:
            return questions
    except Exception as e:
        logger.warning("AI classification failed: %s", e)
    return classify_heuristic(markdown)


def classify_heuristic(markdown: str) -> List[Dict]:
    """Classify questions from markdown. Strips Quarto formatting first."""
    # Remove page markers and formatting
    clean = re.sub(r'\\newpage|## Page \d+|# Exam Paper', '', markdown)
    questions = []
    # Match "1." or "1)" at start of line, capture text until next number
    pattern = re.compile(
        r'(?:^|\n)\s*(\d+)[\.\)]\s*(.*?)(?=\n\s*\d+[\.\)]|\Z)',
        re.DOTALL)
    matches = pattern.findall(clean)

    if not matches:
        return _simple_parse(clean)

    for num_str, q_text in matches:
        q_text = q_text.strip()
        if len(q_text) < 5:
            continue
        # Skip if this is actually a page number like "Page 2"
        if q_text.replace(" ", "").lower().startswith("page") or \
           re.match(r'^\d+\s*$', q_text):
            continue
        questions.append({
            "number": int(num_str),
            "text": q_text[:200],
            "type": _heuristic_type(q_text, len(matches)),
            "full_text": q_text,
        })

    return questions


def _heuristic_type(text: str, total_q: int = None) -> str:
    score = 0
    if re.search(r'\([a-fA-F]\)', text): score += 4
    if re.search(r'\[\d+\]|\[Total\s*:?\s*\d+\]', text): score += 3
    if any(text.lower().startswith(c) for c in
           ["explain", "describe", "discuss", "evaluate", "suggest",
            "outline", "define", "state", "calculate", "determine"]): score += 3
    if '?' in text and len(text) > 50: score += 1
    opt = len(re.findall(r'\b[ABCD][\.\)]\s+\S', text))
    if opt >= 3: score -= 4
    elif opt >= 2: score -= 2
    if total_q and total_q >= 30: score -= 3
    return "mcq" if score <= 0 else "essay"


def _simple_parse(text: str) -> List[Dict]:
    questions, current, parts = [], None, []
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line: continue
        m = re.match(r'^(\d+)[\.\)]\s*(.*)', line)
        if m:
            if current is not None:
                current["type"] = _heuristic_type("\n".join(parts))
                questions.append(current)
            current = {"number": int(m.group(1)), "text": m.group(2).strip()[:200],
                       "type": "mcq", "full_text": m.group(2).strip()}
            parts = [m.group(2).strip()]
        elif current is not None:
            parts.append(line)
            current["full_text"] = "\n".join(parts)
    if current is not None:
        current["type"] = _heuristic_type("\n".join(parts))
        questions.append(current)
    return questions


def generate_preview_html(parsed: Dict) -> str:
    if parsed.get("error"):
        return f'<p class="text-red-500">{parsed["error"]}</p>'
    html = (f'<p class="text-sm text-surface-600 mb-3">Ditemukan {parsed["page_count"]} halaman, '
            f'{parsed["mcq_count"]} MCQ, {parsed["essay_count"]} Essay</p>')
    html += '<div class="space-y-1 max-h-80 overflow-y-auto">'
    for q in parsed.get("questions", []):
        badge = "MCQ" if q.get("type") == "mcq" else "Essay"
        bc = "bg-blue-100 text-blue-700" if q.get("type") == "mcq" else "bg-amber-100 text-amber-700"
        html += (f'<div class="flex items-center gap-2 p-2 rounded-lg bg-surface-50">'
                 f'<span class="text-sm font-bold text-surface-400 w-8">{q.get("number","?")}.</span>'
                 f'<span class="text-sm font-bold text-surface-600 flex-1 truncate">{q.get("text","")[:100]}</span>'
                 f'<span class="text-[10px] font-bold px-2 py-0.5 rounded-full {bc}">{badge}</span></div>')
    html += "</div>"
    return html
