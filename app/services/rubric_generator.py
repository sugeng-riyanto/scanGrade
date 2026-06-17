"""Generate grading rubric for essay questions using AI."""
import json
import logging
from flask import current_app

logger = logging.getLogger("app")


_LANG_MAP = {
    "en": {
        "prompt": (
            "You are an experienced IB/A-Level teacher creating an assessment rubric. "
            "For the following essay question, create 4-5 evaluation criteria at IELTS 7.5+ standard.\n\n"
            "Question: {question}\n\n"
            "Each criterion must:\n"
            "- Be precisely defined with measurable descriptors\n"
            "- Have a weight percentage (total = 100)\n"
            "- Use professional academic language\n"
            "- Include positive framing (what student demonstrates, not what's missing)\n\n"
            "Output ONLY valid JSON array:\n"
            '[{{"criterion": "Precise criterion name", "weight": percentage, "descriptor": "What meeting this looks like"}}]\n\n'
            "Use English at IELTS 7.5+ vocabulary level."
        ),
        "default": [
            {"criterion": "Content Accuracy & Relevance", "weight": 30, "descriptor": "Demonstrates thorough understanding with precise, relevant information"},
            {"criterion": "Critical Analysis & Depth", "weight": 25, "descriptor": "Presents well-reasoned arguments with evidence and examples"},
            {"criterion": "Structure & Logical Flow", "weight": 20, "descriptor": "Ideas are organized coherently with clear progression"},
            {"criterion": "Terminology & Academic Language", "weight": 15, "descriptor": "Uses subject-specific vocabulary accurately and appropriately"},
            {"criterion": "Clarity & Expression", "weight": 10, "descriptor": "Communicates ideas clearly with correct grammar and spelling"},
        ],
        "key": "criterion",
        "bobot": "weight",
    },
    "id": {
        "prompt": (
            "Anda adalah guru berpengalaman yang membuat rubrik penilaian dengan standar UKBI/EYD. "
            "Untuk soal esai berikut, buat 4-5 kriteria penilaian yang terukur dan membangun.\n\n"
            "Soal: {question}\n\n"
            "Setiap kriteria harus:\n"
            "- Dirumuskan secara spesifik dan terukur\n"
            "- Memiliki bobot persentase (total = 100)\n"
            "- Menggunakan EYD dan istilah pendidikan yang baku\n"
            "- Bersifat positif dan memotivasi\n\n"
            "Output HANYA JSON array:\n"
            '[{{"kriteria": "Nama kriteria", "bobot": persen, "deskripsi": "Indikator pencapaian"}}]\n\n'
            "Gunakan Bahasa Indonesia baku sesuai EYD."
        ),
        "default": [
            {"kriteria": "Ketepatan dan Relevansi Isi", "bobot": 30, "deskripsi": "Menunjukkan pemahaman mendalam dengan informasi yang tepat dan relevan sesuai soal"},
            {"kriteria": "Kedalaman Analisis", "bobot": 25, "deskripsi": "Menyajikan argumen yang logis dengan didukung bukti dan contoh konkret"},
            {"kriteria": "Sistematika Penyampaian", "bobot": 20, "deskripsi": "Gagasan disusun secara teratur dan runtut dengan alur berpikir yang jelas"},
            {"kriteria": "Penggunaan Istilah", "bobot": 15, "deskripsi": "Menggunakan kosakata dan istilah bidang ilmu secara tepat dan konsisten"},
            {"kriteria": "Kebahasaan", "bobot": 10, "deskripsi": "Menggunakan tata bahasa dan ejaan yang sesuai dengan EYD"},
        ],
    },
    "zh-Hant": {
        "prompt": (
            "您是一位經驗豐富的高中教師，正在創建評分標準（rubric）。根據HSK 5級標準，為以下論述題建立4-5個評分標準。\n\n"
            "題目：{question}\n\n"
            "每個標準必須：\n"
            "- 具體明確且可測量\n"
            "- 有權重百分比（總計100）\n"
            "- 使用繁體中文\n"
            "- 用正向積極的方式描述\n\n"
            "僅輸出JSON陣列：\n"
            '[{{"criterion": "標準名稱", "weight": 權重, "descriptor": "達成此標準的表現"}}]\n\n'
            "請使用繁體中文（正體字），語言程度相當於HSK 5級或以上。"
        ),
        "default": [
            {"criterion": "內容準確性與相關性", "weight": 30, "descriptor": "展現充分理解，提供準確且相關的資訊"},
            {"criterion": "分析深度與批判思考", "weight": 25, "descriptor": "提出合理論點，並有證據和案例支持"},
            {"criterion": "組織結構與邏輯性", "weight": 20, "descriptor": "思路清晰，結構完整，層次分明"},
            {"criterion": "專業術語運用", "weight": 15, "descriptor": "正確且恰當地使用學科專業詞彙"},
            {"criterion": "語言表達", "weight": 10, "descriptor": "文法正確，詞彙豐富，表達流暢"},
        ],
        "key": "criterion",
        "bobot": "weight",
    },
    "ar": {
        "prompt": (
            "أنت معلم خبير تقوم بإنشاء سلم تقييم (rubric) بمستوى مناسب للمرحلة المتوسطة والثانوية. "
            "لسؤال المقال التالي، قم بإنشاء 4-5 معايير تقييم.\n\n"
            "السؤال: {question}\n\n"
            "كل معيار يجب أن:\n"
            "- يكون محددًا وقابلًا للقياس\n"
            "- له وزن مئوي (المجموع = 100)\n"
            "- يستخدم لغة عربية فصيحة ومناسبة\n"
            "- يكون إيجابيًا ومشجعًا\n\n"
            "أخرج JSON فقط:\n"
            '[{{"criterion": "اسم المعيار", "weight": الوزن, "descriptor": "وصف التحقيق"}}]\n\n'
            "استخدم اللغة العربية الفصيحة بمستوى مناسب للتعليم المتوسط والثانوي."
        ),
        "default": [
            {"criterion": "دقة المحتوى وملاءمته", "weight": 30, "descriptor": "يظهر فهماً كاملاً بمعلومات دقيقة ومناسبة للسؤال"},
            {"criterion": "عمق التحليل والتفكير", "weight": 25, "descriptor": "يقدم حججاً منطقية مدعومة بالأدلة والأمثلة"},
            {"criterion": "تنظيم الأفكار وتسلسلها", "weight": 20, "descriptor": "الأفكار منظمة بشكل متسلسل وواضح"},
            {"criterion": "استخدام المصطلحات", "weight": 15, "descriptor": "يستخدم المصطلحات العلمية بشكل صحيح ومناسب"},
            {"criterion": "السلامة اللغوية", "weight": 10, "descriptor": "يستخدم قواعد اللغة والإملاء بشكل سليم"},
        ],
        "key": "criterion",
        "bobot": "weight",
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

    key = _get_demo_key()
    if not key:
        return _default_rubric(lang)

    lang_cfg = _LANG_MAP.get(lang) or _LANG_MAP["en"]
    prompt = lang_cfg["prompt"].format(question=question_text)

    try:
        raw = _call_ai(key, prompt)
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1]
            cleaned = cleaned.rsplit("```", 1)[0]
        data = json.loads(cleaned.strip())
        if isinstance(data, list) and len(data) >= 2:
            total = sum(item.get("bobot") or item.get("weight") or 0 for item in data)
            if total > 0 and total != 100:
                for item in data:
                    w = item.get("bobot") or item.get("weight") or 0
                    bk = "bobot" if "bobot" in item else "weight"
                    item[bk] = round(w / total * 100)
            result = []
            for item in data:
                result.append({
                    "kriteria": item.get("kriteria") or item.get("criterion") or "",
                    "bobot": item.get("bobot") or item.get("weight") or 0,
                    "deskripsi": item.get("deskripsi") or item.get("descriptor") or "",
                })
            return result
    except Exception as e:
        logger.debug("AI rubric parse error: %s", e)

    return _default_rubric(lang)
