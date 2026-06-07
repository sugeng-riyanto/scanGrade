import json
import re
from flask import current_app


PROVIDERS = {
    "gemini": {"name": "Google Gemini", "env_key": None},
    "openai": {"name": "OpenAI", "env_key": None},
    "deepseek": {"name": "DeepSeek", "env_key": None},
    "groq": {"name": "Groq", "env_key": None},
}


def _get_active_key(teacher_id):
    supabase = current_app.extensions["supabase"]
    res = supabase.table("teacher_ai_keys") \
        .select("*") \
        .eq("teacher_id", teacher_id) \
        .eq("is_active", True) \
        .limit(1) \
        .execute()
    return res.data[0] if res.data else None


def _get_ai_settings(teacher_id):
    supabase = current_app.extensions["supabase"]
    res = supabase.table("teacher_ai_settings") \
        .select("*") \
        .eq("teacher_id", teacher_id) \
        .limit(1) \
        .execute()
    if res.data:
        return res.data[0]
    default_prompt = (
        'Kamu adalah asisten koreksi ujian. '
        'Koreksi jawaban esai berikut berdasarkan soal dan bobot maksimal.\n\n'
        'Soal: {question}\n'
        'Pedoman Penskoran: {rubric}\n'
        'Bobot Maksimal: {max_score} poin\n'
        'Jawaban Siswa: "{answer}"\n\n'
        'Berikan skor (0-{max_score}) dan feedback singkat dalam bahasa Indonesia.\n'
        'Format JSON: {"score": <number>, "feedback": "<string>"}'
    )
    return {"teacher_id": teacher_id, "prompt_template": default_prompt}


def _fill_prompt(template, question, answer, max_score, rubric=""):
    return template.replace("{question}", str(question)) \
        .replace("{answer}", str(answer)) \
        .replace("{max_score}", str(max_score)) \
        .replace("{rubric}", str(rubric or ""))


def _parse_ai_response(raw_text):
    try:
        cleaned = raw_text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*|```$", "", cleaned, flags=re.DOTALL).strip()
        data = json.loads(cleaned)
        score = float(data.get("score", 0))
        feedback = str(data.get("feedback", ""))
        return score, feedback
    except (json.JSONDecodeError, ValueError, TypeError):
        match = re.search(r'"score"\s*:\s*(\d+(?:\.\d+)?)', raw_text)
        score = float(match.group(1)) if match else 0
        fb_match = re.search(r'"feedback"\s*:\s*"([^"]+)"', raw_text)
        feedback = fb_match.group(1) if fb_match else raw_text[:200]
        return score, feedback


def _call_gemini(api_key, prompt):
    import google.generativeai as genai
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-2.0-flash-lite")
    resp = model.generate_content(prompt)
    return resp.text


def _call_openai(api_key, prompt):
    from openai import OpenAI
    client = OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
    )
    return resp.choices[0].message.content or ""


def _call_deepseek(api_key, prompt):
    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
    resp = client.chat.completions.create(
        model="deepseek-chat",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
    )
    return resp.choices[0].message.content or ""


def _call_groq(api_key, prompt):
    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")
    resp = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
    )
    return resp.choices[0].message.content or ""


_PROVIDER_CALLERS = {
    "gemini": _call_gemini,
    "openai": _call_openai,
    "deepseek": _call_deepseek,
    "groq": _call_groq,
}


def suggest_grade(teacher_id, question_text, student_answer, max_score, rubric=""):
    key = _get_active_key(teacher_id)
    if not key:
        return {"error": "Belum ada API key aktif. Atur di Pengaturan AI."}

    settings = _get_ai_settings(teacher_id)
    prompt = _fill_prompt(
        settings.get("prompt_template", ""),
        question_text, student_answer, max_score, rubric
    )

    provider = key["provider"]
    caller = _PROVIDER_CALLERS.get(provider)
    if not caller:
        return {"error": f"Provider {provider} tidak dikenal"}

    try:
        raw = caller(key["api_key"], prompt)
        score, feedback = _parse_ai_response(raw)
        _save_log(teacher_id, None, 0, provider, score, feedback, prompt, raw, 0)
        return {
            "score": round(score, 1),
            "feedback": feedback,
            "provider": provider,
            "prompt": prompt,
        }
    except Exception as e:
        current_app.logger.error(f"AI suggest_grade error: {e}")
        return {"error": f"Gagal: {str(e)[:120]}"}


def test_api_key(teacher_id, key_id):
    supabase = current_app.extensions["supabase"]
    res = supabase.table("teacher_ai_keys").select("*").eq("id", key_id).eq("teacher_id", teacher_id).limit(1).execute()
    if not res.data:
        return {"error": "Key tidak ditemukan"}
    key = res.data[0]
    caller = _PROVIDER_CALLERS.get(key["provider"])
    if not caller:
        return {"error": "Provider tidak dikenal"}
    sample_prompt = 'Jawab dalam satu kata: Berapa 2+2? Format JSON: {"answer": <number>}'
    try:
        raw = caller(key["api_key"], sample_prompt)
        data = json.loads(raw.strip().replace("```json", "").replace("```", "").strip())
        if data.get("answer") == 4:
            return {"success": True, "message": "✅ API Key aktif! Koneksi berhasil."}
        return {"success": True, "message": f"✅ API Key aktif. Response: {raw[:80]}"}
    except Exception as e:
        return {"error": f"❌ Gagal: {str(e)[:120]}"}


def _save_log(teacher_id, submission_id, question_index, provider, score, feedback, prompt, raw, tokens):
    try:
        supabase = current_app.extensions["supabase"]
        supabase.table("ai_grading_logs").insert({
            "teacher_id": teacher_id,
            "submission_id": submission_id or "00000000-0000-0000-0000-000000000000",
            "question_index": question_index,
            "ai_provider": provider,
            "ai_score": score,
            "ai_feedback": feedback[:500] if feedback else "",
            "prompt_sent": prompt[:1000] if prompt else "",
            "raw_response": raw[:1000] if raw else "",
            "tokens_used": tokens,
        }).execute()
    except Exception:
        pass
