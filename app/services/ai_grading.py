"""AI grading pipeline with cache for essay answers."""
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger("app")


def _get_cache(submission_id: str, question_index: int, supabase) -> dict:
    """Check if this essay has been graded before (cache hit)."""
    res = supabase.table("ai_grading_cache") \
        .select("*") \
        .eq("submission_id", submission_id) \
        .eq("question_index", question_index) \
        .limit(1) \
        .execute()
    if res.data:
        entry = res.data[0]
        if not entry.get("teacher_overridden"):
            return {
                "cached": True,
                "score": entry.get("ai_score"),
                "feedback": entry.get("ai_feedback"),
                "provider": entry.get("ai_provider"),
                "created_at": entry.get("created_at"),
            }
    return {"cached": False}


def _save_cache(submission_id: str, question_index: int, score: float, feedback: str,
                provider: str, prompt: str, raw_response: str, tokens: int, supabase):
    """Save AI grading result to cache."""
    try:
        supabase.table("ai_grading_cache").upsert({
            "submission_id": submission_id,
            "question_index": question_index,
            "ai_score": score,
            "ai_feedback": feedback[:1000] if feedback else "",
            "ai_provider": provider,
            "tokens_used": tokens,
            "prompt_sent": prompt[:2000] if prompt else "",
            "raw_response": raw_response[:2000] if raw_response else "",
        }, on_conflict=["submission_id", "question_index"]).execute()
    except Exception as e:
        logger.error("Failed to save AI grading cache: %s", e)


_LANG_FEEDBACK = {
    "en": (
        "Evaluate this answer at IELTS 7.5+ standard.\n\n"
        "Scoring Guidelines:\n"
        "- 85-100: Exceptional — demonstrates mastery beyond expectations\n"
        "- 70-84: Proficient — solid understanding with minor gaps\n"
        "- 55-69: Developing — adequate but needs improvement\n"
        "- 40-54: Emerging — significant gaps in understanding\n"
        "- Below 40: Insufficient — major revision needed\n\n"
        "Requirements:\n"
        "1. Be encouraging — start with what the student did well\n"
        "2. Provide specific, actionable feedback for improvement\n"
        "3. Use professional academic language (IELTS 7.5+ vocabulary)\n"
        "4. Score must be a number (0-{max_score})\n"
        "5. Feedback must be in English\n"
        "6. Include a brief 'reasoning' explaining why the score was given\n"
        "7. Include a 'confidence' score from 0.0 to 1.0 indicating how sure you are\n\n"
        "Output ONLY JSON:\n"
        '{{"score": <number>, "feedback": "<constructive feedback>", "reasoning": "<1-2 sentence reasoning>", "confidence": <0.0-1.0>}}'
    ),
    "id": (
        "Evaluasi jawaban ini dengan standar UKBI/EYD.\n\n"
        "Pedoman Penskoran:\n"
        "- 85-100: Istimewa — menguasai materi melebihi ekspektasi\n"
        "- 70-84: Baik — pemahaman solid dengan sedikit kekurangan\n"
        "- 55-69: Cukup — memadai namun perlu peningkatan\n"
        "- 40-54: Kurang — kesenjangan pemahaman yang signifikan\n"
        "- Di bawah 40: Sangat Kurang — perlu perbaikan mendasar\n\n"
        "Ketentuan:\n"
        "1. Mulai dengan apresiasi — sebutkan kelebihan jawaban\n"
        "2. Berikan saran perbaikan yang spesifik dan membangun\n"
        "3. Gunakan Bahasa Indonesia baku sesuai EYD\n"
        "4. Skor berupa angka (0-{max_score})\n"
        "5. Feedback dalam Bahasa Indonesia yang memotivasi\n"
        "6. Sertakan 'reasoning' singkat menjelaskan alasan skor\n"
        "7. Sertakan 'confidence' 0.0-1.0 seberapa yakin Anda\n\n"
        "Output HANYA JSON:\n"
        '{{"score": <number>, "feedback": "<feedback membangun>", "reasoning": "<alasan 1-2 kalimat>", "confidence": <0.0-1.0>}}'
    ),
    "zh-Hant": (
        "根據HSK 5級標準評估此答案。\n\n"
        "評分指南：\n"
        "- 85-100：優秀 — 掌握程度超出預期\n"
        "- 70-84：良好 — 理解扎實，略有不足\n"
        "- 55-69：一般 — 尚可但需加強\n"
        "- 40-54：不足 — 有明顯理解差距\n"
        "- 低於40：急需加強 — 需要大幅改進\n\n"
        "要求：\n"
        "1. 先肯定學生的優點\n"
        "2. 提供具體可行的改進建議\n"
        "3. 用正向積極的語氣\n"
        "4. 分數為數字（0-{max_score}）\n"
        "5. 反饋使用繁體中文\n"
        "6. 包含簡短'reasoning'解釋給分原因\n"
        "7. 包含'confidence' 0.0-1.0 表示確定程度\n\n"
        "僅輸出JSON：\n"
        '{{"score": <number>, "feedback": "<建設性反饋>", "reasoning": "<1-2句理由>", "confidence": <0.0-1.0>}}'
    ),
    "ar": (
        "قم بتقييم هذه الإجابة بمستوى مناسب للمرحلة المتوسطة والثانوية.\n\n"
        "مبادئ التقييم:\n"
        "- 85-100: ممتاز — يُظهر إتقانًا يتجاوز التوقعات\n"
        "- 70-84: جيد جدًا — فهم قوي مع بعض النواقص البسيطة\n"
        "- 55-69: مقبول — إجابة مناسبة لكنها تحتاج تحسينًا\n"
        "- 40-54: ضعيف — فجوات واضحة في الفهم\n"
        "- أقل من 40: غير كافٍ — يحتاج مراجعة جوهرية\n\n"
        "المتطلبات:\n"
        "1. ابدأ بالإيجابيات — اذكر ما أجاد الطالب\n"
        "2. قدم اقتراحات محددة للتحسين\n"
        "3. استخدم لغة عربية فصيحة ومشجعة\n"
        "4. الدرجة تكون رقمًا (0-{max_score})\n"
        "5. التعليق باللغة العربية\n"
        "6. تضمين 'reasoning' موجز يشرح سبب الدرجة\n"
        "7. تضمين 'confidence' 0.0-1.0 لمدى الثقة\n\n"
        "أخرج JSON فقط:\n"
        '{{"score": <number>, "feedback": "<تعليق بناء>", "reasoning": "<سبب 1-2 جملة>", "confidence": <0.0-1.0>}}'
    ),
}


def _build_prompt(question_text: str, rubric: str, diagram_context: str,
                  student_answer: str, max_score: int, lang: str = "en") -> str:
    """Build a smart prompt for AI essay grading."""
    feedback_template = _LANG_FEEDBACK.get(lang) or _LANG_FEEDBACK["en"]
    parts = ["Koreksi jawaban esai berikut dengan teliti dan adil."]
    if question_text:
        parts.append(f"\nSoal: {question_text}")
    if rubric:
        parts.append(f"\nRubrik Penilaian:\n{rubric}")
    if diagram_context:
        parts.append(f"\nKonteks Diagram/Gambar:\n{diagram_context}")
    parts.append(f"\nJawaban Siswa:\n{student_answer}")
    parts.append(f"\nSkor Maksimal: {max_score}")
    parts.append("\n" + feedback_template.format(max_score=max_score))
    return "\n".join(parts)


def grade_essay(teacher_id: str, submission_id: str, question_index: int,
                question_text: str, student_answer: str, max_score: int,
                rubric: str = "", diagram_context: str = "",
                exam_id: str = "", lang: str = "en") -> dict:
    """Grade a single essay answer with cache check.
    
    Returns dict with score, feedback, cached flag, provider.
    """
    from app.utils.auth import get_supabase
    supabase = get_supabase()

    # 1. Check cache
    cached = _get_cache(submission_id, question_index, supabase)
    if cached["cached"]:
        logger.info("Cache hit: %s Q%d", submission_id, question_index)
        return {
            "score": cached["score"],
            "feedback": cached["feedback"],
            "provider": cached["provider"],
            "cached": True,
        }

    # 2. Get active AI key
    from app.services.ai_service import _get_active_key
    key = _get_active_key(teacher_id)
    if not key:
        return {"error": "Belum ada API key aktif. Atur di Pengaturan AI."}

    # 3. Build prompt
    prompt = _build_prompt(question_text, rubric, diagram_context, student_answer, max_score, lang)

    # 4. Call AI
    try:
        from app.services.ai_service import _call_ai, _parse_ai_response
        raw = _call_ai(key, prompt)
        score, feedback, reasoning, confidence = _parse_ai_response(raw)

        # 5. Save to cache
        _save_cache(submission_id, question_index, score, feedback,
                    key.get("provider", "unknown"), prompt, raw, 0, supabase)

        # 6. Log grading
        try:
            from app.services.ai_service import _save_log
            _save_log(teacher_id, submission_id, question_index,
                      key.get("provider", "ai"), score, feedback, prompt, raw, 0)
        except Exception:
            pass

        return {
            "score": round(score, 1),
            "feedback": feedback,
            "reasoning": reasoning,
            "confidence": round(confidence, 2),
            "provider": key.get("provider", "ai"),
            "cached": False,
        }
    except Exception as e:
        logger.error("AI grading failed: %s", e)
        return {"error": f"Gagal mengoreksi: {str(e)[:150]}"}


def grade_bulk_essays(teacher_id: str, exam_id: str, submission_ids: list = None) -> dict:
    """Grade all pending essay questions for submissions."""
    from app.utils.auth import get_supabase
    supabase = get_supabase()

    exam = supabase.table("exams").select("question_types,question_texts,total_questions,question_rubrics,id").eq("id", exam_id).single().execute().data
    if not exam:
        return {"error": "Exam not found"}

    qtypes = exam.get("question_types") or {}
    if isinstance(qtypes, str):
        qtypes = json.loads(qtypes)
    qtexts = exam.get("question_texts") or {}
    if isinstance(qtexts, str):
        qtexts = json.loads(qtexts)
    total_q = exam.get("total_questions", 0)

    # Get submissions
    query = supabase.table("submissions").select("id,student_id,answers").eq("exam_id", exam_id)
    if submission_ids:
        query = query.in_("id", submission_ids)
    submissions = query.execute().data or []

    results = {"processed": 0, "cached": 0, "errors": 0, "total": len(submissions), "details": []}

    for sub in submissions:
        answers = sub.get("answers") or {}
        if isinstance(answers, str):
            try:
                answers = json.loads(answers)
            except Exception:
                answers = {}

        for i in range(total_q):
            qi = str(i)
            if qtypes.get(qi, "mcq") != "mcq":
                ans_data = answers.get(qi, {})
                if isinstance(ans_data, dict):
                    student_ans = ans_data.get("answer", "")
                else:
                    student_ans = str(ans_data) if ans_data else ""

                if not student_ans:
                    continue

                max_score = 100  # default, could be from question_weights
                question_text = qtexts.get(qi, "")
                rubric = exam.get("question_rubrics", {}).get(qi, "") if isinstance(exam.get("question_rubrics"), dict) else ""

                result = grade_essay(
                    teacher_id=teacher_id,
                    submission_id=sub["id"],
                    question_index=i,
                    question_text=question_text,
                    student_answer=student_ans,
                    max_score=max_score,
                    rubric=rubric,
                    exam_id=exam_id,
                )

                if result.get("cached"):
                    results["cached"] += 1
                elif result.get("score") is not None:
                    results["processed"] += 1
                    # Update submission score
                    _update_submission_score(sub["id"], i, result["score"], supabase)
                else:
                    results["errors"] += 1

                results["details"].append({
                    "submission_id": sub["id"],
                    "question": i + 1,
                    "score": result.get("score"),
                    "cached": result.get("cached", False),
                    "error": result.get("error"),
                })

    return results


def _update_submission_score(submission_id: str, question_index: int, score: float, supabase):
    """Update the score for a specific essay question in the submission."""
    try:
        sub = supabase.table("submissions").select("answers").eq("id", submission_id).single().execute().data
        if not sub:
            return
        answers = sub.get("answers") or {}
        if isinstance(answers, str):
            answers = json.loads(answers)

        qi = str(question_index)
        if qi in answers and isinstance(answers[qi], dict):
            answers[qi]["ai_score"] = score
        else:
            answers[qi] = {"answer": str(answers.get(qi, "")), "ai_score": score}

        supabase.table("submissions").update({"answers": answers}).eq("id", submission_id).execute()
    except Exception as e:
        logger.error("Failed to update submission score: %s", e)
