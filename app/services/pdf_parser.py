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

    md_for_ai = markdown[:20000]  # reduced from 30k

    prompt = f"Analyze exam. For each question: classify as 'mcq' or 'essay'. Output JSON.\n\n{md_for_ai}"

    for attempt in range(3):
        try:
            from app.services.ai_service import _call_ai
            raw = _call_ai({"api_key": api_key, "provider": provider or "groq"}, prompt)
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0]
            questions = json.loads(cleaned.strip())
            if isinstance(questions, list) and len(questions) > 0:
                return questions
        except Exception as e:
            err = str(e)
            if "429" in err or "rate_limit" in err.lower():
                logger.warning("Classify rate limited (attempt %d/3), retry 5s", attempt + 1)
                import time
                time.sleep(5)
                continue
            logger.warning("AI classification failed: %s", err)
            break

    return classify_heuristic(markdown)


def classify_heuristic(markdown: str) -> List[Dict]:
    """Classify questions from markdown. Strips Quarto formatting first."""
    # Remove page markers and formatting
    clean = re.sub(r'\\newpage|## Page \d+|# Exam Paper', '', markdown)
    questions = []
    # Match "1." or "1)" or "1 (a)" at start of line, capture text until next question number
    pattern = re.compile(
        r'(?:^|\n)\s*(\d+)[\.\)\s]\s*(?:\([a-fA-F]\)\s*)?(.*?)(?=\n\s*\d+\s*[\.\)\s(]|\Z)',
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


def _simple_parse(text: str) -> List[Dict]:
    """Line-by-line fallback for question detection."""
    questions, current, parts = [], None, []
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        m = re.match(r'^(\d+)\s*(?:\([a-fA-F]\)\s*)?(.*)', line)
        if m:
            if current is not None:
                current["type"] = _heuristic_type("\n".join(parts))
                questions.append(current)
            current = {
                "number": int(m.group(1)),
                "text": m.group(2).strip()[:200],
                "type": "mcq",
                "full_text": m.group(2).strip(),
            }
            parts = [m.group(2).strip()]
        elif current is not None:
            parts.append(line)
            current["full_text"] = "\n".join(parts)
    if current is not None:
        current["type"] = _heuristic_type("\n".join(parts))
        questions.append(current)
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


def generate_answer_key(markdown: str, questions: List[Dict], api_key: str = None, provider: str = "groq", lang: str = "en") -> Dict:
    """Generate answer key — MCQ→letter, Essay→answer. Retries on rate limit."""
    if not api_key or not questions:
        return {}

    q_list = ""
    for q in questions:
        num = q.get("number", 0)
        text = (q.get("full_text") or q.get("text", ""))[:500]
        q_list += f"Q{num}: {text}\n\n"
    q_list = q_list[:30000]

    lang_hint = "Answer in English." if lang.startswith("en") else f"Answer in {lang}."
    prompt = f"{lang_hint} Output JSON with question numbers as keys. For MCQ, answer with letter (A/B/C/D). Only output valid JSON.\n\n{q_list}"

    for attempt in range(3):
        try:
            from app.services.ai_service import _call_ai
            key_dict = {"api_key": api_key, "provider": provider or "groq"}
            raw = _call_ai(key_dict, prompt)
            cleaned = raw.strip()
            import re
            json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', raw)
            if json_match:
                cleaned = json_match.group(1).strip()
            else:
                cleaned = raw.strip()
            if not cleaned:
                raise ValueError("AI returned empty response")
            parsed = json.loads(cleaned)
            if not isinstance(parsed, dict):
                raise ValueError("AI response is not a JSON object")

            if isinstance(parsed, dict):
                result = {}
                for k, v in parsed.items():
                    sv = str(v).strip()
                    if sv in ("A", "B", "C", "D"):
                        result[str(k)] = sv
                    elif sv and len(sv) > 1:
                        result[str(k)] = sv[:200]
                return result
        except Exception as e:
            err = str(e)
            if "429" in err or "rate_limit" in err.lower():
                logger.warning("Rate limit (attempt %d/3), retry 5s...", attempt + 1)
                import time
                time.sleep(5)
                continue
            logger.warning("Answer key failed: %s", err)
            return {"_error": f"AI gagal: {err[:150]}"}
    else:
        # All retries exhausted — rate limited
        return {"_error": "Kuota Groq API habis. Tunggu beberapa saat atau ganti provider AI di Pengaturan AI."}

    return {}


def generate_preview_html(parsed: Dict) -> str:
    if parsed.get("error"):
        return f'<p class="text-red-500">{parsed["error"]}</p>'
    html = (f'<p class="text-sm text-surface-600 mb-3">Ditemukan {parsed["page_count"]} halaman, '
            f'{parsed["mcq_count"] + parsed["essay_count"]} soal</p>')
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
