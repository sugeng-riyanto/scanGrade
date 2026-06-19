import json
import re
from flask import current_app


def _get_demo_key():
    """Return demo key config if available."""
    key = current_app.config.get("DEMO_AI_KEY", "")
    if not key:
        return None
    return {"id": "demo", "provider": "gemini", "api_key": key, "label": "Demo (gratis)", "is_active": True, "is_demo": True}


def _get_active_key(teacher_id):
    """Get active API key for teacher, falling back to demo key."""
    supabase = current_app.extensions["supabase"]
    res = supabase.table("teacher_ai_keys") \
        .select("*") \
        .eq("teacher_id", teacher_id) \
        .eq("is_active", True) \
        .limit(1) \
        .execute()
    if res.data:
        return res.data[0]
    # Fallback to demo key
    return _get_demo_key()


def _get_ai_settings(teacher_id):
    supabase = current_app.extensions["supabase"]
    res = supabase.table("teacher_ai_settings") \
        .select("*") \
        .eq("teacher_id", teacher_id) \
        .limit(1) \
        .execute()
    if res.data:
        return res.data[0]
    return {"teacher_id": teacher_id, "prompt_template": "", "prompts": [], "active_prompt_id": None}


def _get_active_prompt(settings):
    prompts = settings.get("prompts")
    if isinstance(prompts, str):
        prompts = json.loads(prompts)
    if not prompts:
        return "Koreksi jawaban esai berikut.\nSoal: {question}\nBobot: {max_score}\nJawaban: {answer}\nBerikan skor (0-{max_score}) dan feedback.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"
    active_id = settings.get("active_prompt_id")
    for p in prompts:
        if p.get("id") == active_id:
            return p.get("template", "")
    return prompts[0].get("template", "")


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
        reasoning = str(data.get("reasoning", ""))
        confidence = float(data.get("confidence", 0.5))
        return score, feedback, reasoning, confidence
    except (json.JSONDecodeError, ValueError, TypeError):
        match = re.search(r'"score"\s*:\s*(\d+(?:\.\d+)?)', raw_text)
        score = float(match.group(1)) if match else 0
        fb_match = re.search(r'"feedback"\s*:\s*"([^"]+)"', raw_text)
        feedback = fb_match.group(1) if fb_match else raw_text[:200]
        rc_match = re.search(r'"reasoning"\s*:\s*"([^"]+)"', raw_text)
        reasoning = rc_match.group(1) if rc_match else ""
        cf_match = re.search(r'"confidence"\s*:\s*(\d+(?:\.\d+)?)', raw_text)
        confidence = float(cf_match.group(1)) if cf_match else 0.5
        return score, feedback, reasoning, confidence


def _call_gemini(api_key, prompt):
    """Call Gemini API — supports AIzaSy (standard) and AQ. (auth) keys."""
    # Auth keys (AQ.) require the new genai.Client() API
    # Standard keys (AIzaSy) work with both old and new API
    if api_key.startswith("AQ"):
        return _call_gemini_client(api_key, prompt)
    # Standard keys: try old API first, fallback to new API
    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.0-flash-lite")
        resp = model.generate_content(prompt)
        return resp.text
    except Exception:
        return _call_gemini_client(api_key, prompt)


def _call_gemini_client(api_key, prompt):
    """Call Gemini API using the new genai.Client() — works with auth keys (AQ.)."""
    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        resp = client.models.generate_content(
            model="gemini-2.0-flash-lite",
            contents=prompt,
        )
        return resp.text
    except Exception as e:
        err_msg = str(e)
        # Try REST API for better error diagnostics
        rest_error = _test_gemini_rest(api_key)
        if rest_error:
            raise ValueError(rest_error)
        raise ValueError(
            "Gagal terhubung ke Gemini API.\n\n"
            f"Pesan error: {err_msg[:150]}"
        )


def _test_gemini_rest(api_key):
    """Test Gemini key via direct REST API and return user-friendly error."""
    try:
        import requests
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-lite:generateContent?key={api_key}"
        resp = requests.post(url, json={"contents": [{"parts": [{"text": "test"}]}]}, timeout=10)
        if resp.status_code == 200:
            return None  # Works fine
        err = resp.json()
        error_msg = err.get("error", {}).get("message", "") or err.get("error", {}).get("status", "")
        if "not found" in error_msg.lower() or "disabled" in error_msg.lower():
            return (
                "❌ Gemini API belum diaktifkan untuk project ini.\n\n"
                "1. Buka: https://console.cloud.google.com/apis/library/generativelanguage.googleapis.com\n"
                "2. Di pojok atas, pilih project: 'My Project (557976847295)' atau nama project key kamu\n"
                "3. Klik tombol 'ENABLE'\n"
                "4. Tunggu 2-5 menit, lalu coba Test lagi"
            )
        if "API key not found" in error_msg:
            return "❌ API Key tidak ditemukan. Copy ulang key dari https://aistudio.google.com/apikey — pastikan tidak ada spasi."
        return f"❌ Error API: {error_msg[:150]}"
    except Exception as e:
        return None  # Fallback to library error


def _call_openai_like(api_key, prompt, base_url=None, model=None):
    from openai import OpenAI
    kwargs = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    client = OpenAI(**kwargs)
    resp = client.chat.completions.create(
        model=model or "gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
    )
    return resp.choices[0].message.content or ""


_PROVIDER_MAP = {
    "gemini": {"caller": _call_gemini},
    "openai": {"caller": lambda k, p: _call_openai_like(k, p, None, "gpt-4o-mini")},
    "deepseek": {"caller": lambda k, p: _call_openai_like(k, p, "https://api.deepseek.com", "deepseek-chat")},
    "groq": {"caller": lambda k, p: _call_openai_like(k, p, "https://api.groq.com/openai/v1", "llama-3.1-8b-instant")},
}


def _call_ai(key, prompt):
    provider = key.get("provider", "")
    caller_entry = _PROVIDER_MAP.get(provider)
    if caller_entry:
        return caller_entry["caller"](key["api_key"], prompt)
    # Custom provider — use OpenAI client with custom base_url and model
    base_url = key.get("base_url") or None
    model = key.get("model_name") or "gpt-4o-mini"
    return _call_openai_like(key["api_key"], prompt, base_url, model)


def suggest_grade(teacher_id, question_text, student_answer, max_score, rubric=""):
    key = _get_active_key(teacher_id)
    if not key:
        return {"error": "Belum ada API key aktif. Atur di Pengaturan AI."}

    settings = _get_ai_settings(teacher_id)
    prompt_template = _get_active_prompt(settings)
    prompt = _fill_prompt(prompt_template, question_text, student_answer, max_score, rubric)

    try:
        raw = _call_ai(key, prompt)
        score, feedback, reasoning, confidence = _parse_ai_response(raw)
        _save_log(teacher_id, None, 0, key["provider"], score, feedback, prompt, raw, 0)
        return {"score": round(score, 1), "feedback": feedback, "reasoning": reasoning, "confidence": round(confidence, 2), "provider": key["provider"], "prompt": prompt}
    except Exception as e:
        current_app.logger.error(f"AI suggest_grade error: {e}")
        return {"error": f"Gagal: {str(e)[:120]}"}


def test_api_key(teacher_id, key_id):
    supabase = current_app.extensions["supabase"]
    res = supabase.table("teacher_ai_keys").select("*").eq("id", key_id).eq("teacher_id", teacher_id).limit(1).execute()
    if not res.data:
        return {"error": "Key tidak ditemukan"}
    key = res.data[0]
    return _test_key_internal(key)


def _test_key_internal(key):
    """Test an API key and return result with user-friendly messages."""
    provider = key.get("provider", "")
    api_key = key.get("api_key", "")

    # For Gemini keys, try REST API test first for better diagnostics
    if provider == "gemini":
        rest_result = _test_gemini_rest(api_key)
        if rest_result:
            return {"error": rest_result}

    sample_prompt = 'Jawab dalam satu kata: Berapa 2+2? Format JSON: {"answer": <number>}'
    try:
        raw = _call_ai(key, sample_prompt)
        data = json.loads(raw.strip().replace("```json", "").replace("```", "").strip())
        if data.get("answer") == 4:
            return {"success": True, "message": "✅ API Key aktif! Koneksi berhasil."}
        return {"success": True, "message": f"✅ API Key aktif. Response: {raw[:80]}"}
    except Exception as e:
        err = str(e)
        # Try REST API for better error (Gemini only)
        if provider == "gemini":
            rest_err = _test_gemini_rest(api_key)
            if rest_err:
                return {"error": rest_err}
        if "VALIDATION_ERROR" in err or "API_KEY_INVALID" in err:
            return {"error": "❌ API Key tidak valid. Ikuti langkah Enable di panduan langkah 3."}
        return {"error": f"❌ Gagal: {err[:150]}"}
        if "quota" in err.lower() or "rate" in err.lower():
            return {"error": "❌ Kuota API habis. Tunggu beberapa saat atau gunakan key lain."}
        return {"error": f"❌ Gagal: {err[:120]}"}


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


def get_teacher_ai_status(teacher_id):
    """Return teacher's AI setup status for the wizard."""
    supabase = current_app.extensions["supabase"]
    keys = supabase.table("teacher_ai_keys") \
        .select("id,provider,label,is_active") \
        .eq("teacher_id", teacher_id) \
        .execute().data or []
    active = supabase.table("teacher_ai_keys") \
        .select("id,provider,label") \
        .eq("teacher_id", teacher_id) \
        .eq("is_active", True) \
        .limit(1) \
        .execute().data or []
    settings = supabase.table("teacher_ai_settings") \
        .select("*") \
        .eq("teacher_id", teacher_id) \
        .limit(1) \
        .execute().data or []
    demo_available = bool(_get_demo_key())
    demo_used = 0
    if demo_available:
        from datetime import datetime, timezone, timedelta
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        logs = supabase.table("ai_grading_logs") \
            .select("id", count="exact") \
            .eq("ai_provider", "demo") \
            .gte("created_at", today.isoformat()) \
            .execute()
        demo_used = logs.count or 0
    return {
        "has_keys": len(keys) > 0,
        "has_active": len(active) > 0,
        "active_key": active[0] if active else None,
        "keys_count": len(keys),
        "demo_available": demo_available,
        "demo_used": demo_used,
        "demo_limit": 10,
        "daily_grading_count": demo_used,
        "has_prompts": bool(settings and settings[0].get("prompts")),
    }
