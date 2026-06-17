"""Generate grading rubric for essay questions using AI."""
import json
import logging
from flask import current_app

logger = logging.getLogger("app")


_LANG_MAP = {
    "en": {
        "prompt": "Create a grading rubric for the following essay question with 3-5 criteria. Each criterion must have a weight percentage (total 100).\n\nQuestion: {question}\n\nOutput ONLY valid JSON array:\n[{\"kriteria\": \"criterion name\", \"bobot\": weight}]\n\nUse English for all criteria names.",
        "default": [
            {"kriteria": "Accuracy of content", "bobot": 30},
            {"kriteria": "Completeness of explanation", "bobot": 25},
            {"kriteria": "Use of correct terminology", "bobot": 20},
            {"kriteria": "Structure and coherence", "bobot": 15},
            {"kriteria": "Grammar and spelling", "bobot": 10},
        ],
        "key": "kriteria",
    },
    "id": {
        "prompt": "Buat rubrik penilaian untuk soal esai berikut dalam 3-5 kriteria. Setiap kriteria harus memiliki bobot dalam persen (total 100).\n\nSoal: {question}\n\nOutput HANYA JSON array:\n[{\"kriteria\": \"nama kriteria\", \"bobot\": persen}]\n\nGunakan Bahasa Indonesia.",
        "default": [
            {"kriteria": "Ketepatan isi jawaban dengan soal", "bobot": 30},
            {"kriteria": "Kelengkapan penjelasan dan detail", "bobot": 25},
            {"kriteria": "Penggunaan istilah/konsep yang tepat", "bobot": 20},
            {"kriteria": "Struktur dan keruntutan jawaban", "bobot": 15},
            {"kriteria": "Tata bahasa dan ejaan", "bobot": 10},
        ],
    },
    "zh": {
        "prompt": "为以下论文问题创建3-5个评分标准。每个标准必须有权重百分比（总共100）。\n\n问题: {question}\n\n只输出JSON数组:\n[{\"kriteria\": \"标准名称\", \"bobot\": 权重}]\n\n请使用中文。",
        "default": [
            {"kriteria": "内容准确性", "bobot": 30},
            {"kriteria": "解释完整性", "bobot": 25},
            {"kriteria": "术语使用正确性", "bobot": 20},
            {"kriteria": "结构与连贯性", "bobot": 15},
            {"kriteria": "语法与拼写", "bobot": 10},
        ],
    },
    "ar": {
        "prompt": "إنشاء معايير تقييم لسؤال المقال التالي بـ3-5 معايير. كل معيار يجب أن يكون له وزن مئوي (المجموع 100).\n\nالسؤال: {question}\n\nأخرج JSON فقط:\n[{\"kriteria\": \"اسم المعيار\", \"bobot\": الوزن}]\n\nاستخدم اللغة العربية.",
        "default": [
            {"kriteria": "دقة المحتوى", "bobot": 30},
            {"kriteria": "اكتمال الشرح", "bobot": 25},
            {"kriteria": "استخدام المصطلحات الصحيحة", "bobot": 20},
            {"kriteria": "الهيكل والترابط", "bobot": 15},
            {"kriteria": "القواعد والإملاء", "bobot": 10},
        ],
    },
}


def generate_rubric(question_text: str, lang: str = "en") -> list:
    """Generate a grading rubric for an essay question.
    Returns list of dicts: [{"kriteria": "...", "bobot": 20}, ...]
    Falls back to default rubric if AI is unavailable.
    """
    try:
        return _ai_rubric(question_text, lang)
    except Exception as e:
        logger.warning("AI rubric generation failed: %s", e)
        return _default_rubric(lang)


def _default_rubric(lang: str = "en") -> list:
    lang_cfg = _LANG_MAP.get(lang) or _LANG_MAP["en"]
    return lang_cfg["default"]


def _ai_rubric(question_text: str, lang: str = "en") -> list:
    """Call AI to generate a custom rubric for the question."""
    from app.services.ai_service import _get_demo_key, _call_ai

    # Get active key or demo
    key = _get_demo_key()
    if not key:
        return _default_rubric(lang)

    lang_cfg = _LANG_MAP.get(lang) or _LANG_MAP["en"]
    prompt = lang_cfg["prompt"].format(question=question_text)

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
