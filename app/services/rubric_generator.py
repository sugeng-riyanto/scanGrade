"""Generate grading rubric for essay questions using AI."""
import json
import logging
from flask import current_app

logger = logging.getLogger("app")


def generate_rubric(question_text: str) -> list:
    """Generate a grading rubric for an essay question.
    Returns list of dicts: [{"kriteria": "...", "bobot": 20}, ...]
    Falls back to default rubric if AI is unavailable.
    """
    try:
        return _ai_rubric(question_text)
    except Exception as e:
        logger.warning("AI rubric generation failed: %s", e)
        return _default_rubric()


def _default_rubric() -> list:
    """Default 5-criteria rubric when AI is unavailable."""
    return [
        {"kriteria": "Ketepatan isi jawaban dengan soal", "bobot": 30},
        {"kriteria": "Kelengkapan penjelasan dan detail", "bobot": 25},
        {"kriteria": "Penggunaan istilah/konsep yang tepat", "bobot": 20},
        {"kriteria": "Struktur dan keruntutan jawaban", "bobot": 15},
        {"kriteria": "Tata bahasa dan ejaan", "bobot": 10},
    ]


def _ai_rubric(question_text: str) -> list:
    """Call AI to generate a custom rubric for the question."""
    from app.services.ai_service import _get_demo_key, _call_ai, _PROVIDER_MAP

    # Get active key or demo
    key = _get_demo_key()
    if not key:
        return _default_rubric()

    prompt = f"""Buat rubrik penilaian untuk soal esai berikut dalam 3-5 kriteria.
Setiap kriteria harus memiliki bobot dalam persen (total 100).

Soal: {question_text}

Format JSON (array):
[{"kriteria": "nama kriteria", "bobot": persen}]

Hanya output JSON, tanpa penjelasan tambahan."""

    try:
        raw = _call_ai(key, prompt)
        # Clean markdown code blocks
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1]
            cleaned = cleaned.rsplit("```", 1)[0]
        data = json.loads(cleaned.strip())
        if isinstance(data, list) and len(data) >= 2:
            # Normalize bobot to sum to 100
            total = sum(item.get("bobot", 0) for item in data)
            if total > 0 and total != 100:
                for item in data:
                    item["bobot"] = round(item.get("bobot", 0) / total * 100)
            return data
    except Exception as e:
        logger.debug("AI rubric parse error: %s", e)

    return _default_rubric()
