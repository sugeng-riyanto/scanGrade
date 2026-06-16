# Rencana Implementasi: AI Essay Grading — 30 Guru, 300 Murid, Gratis

## Fase 1: Kemudahan Setup API Key
**Goal:** Guru dapat API key gratis dalam 2 menit, tanpa kebingungan.

### 1.1 Demo Key (Zero Setup)
- Sediakan 1 API key demo global (`DEMO_AI_KEY` di env)
- Terbatas: 10 grading/hari untuk seluruh sekolah
- Guru bisa langsung coba fitur grading tanpa setup
- Popup: "Nikmati 10 grading gratis hari ini. Daftarkan API Key-mu sendiri untuk unlimited."

### 1.2 Wizard 3 Langkah (Modal)
Ganti halaman `/teacher/ai-settings` yang rame dengan wizard popup:

**Langkah 1 — Pilih Provider:**
- 4 card: **Gemini** ✅ (rekomendasi, gratis, paling mudah), **Groq**, **DeepSeek**, **OpenAI**
- Masing-masing ada badge "Gratis ✅" atau "Berbayar 💳"
- Tombol "Cara Dapatkan" → buka panduan sesuai provider

**Langkah 2 — Dapatkan Key:**
- Tampil step-by-step dengan screenshot:
  - ① Klik tombol "Buka Google AI Studio" → buka tab baru
  - ② Klik "Get API Key" → Create API Key
  - ③ Copy key (AIza...)
  - ④ Kembali ke sini, paste
- Ada tombol **"Buka Halaman API Key"** (link langsung)
- Input field besar dengan placeholder: "Tempel API Key-mu di sini..."
- Tombol **"Test Koneksi"** — hijau/merah dengan pesan bahasa Indonesia

**Langkah 3 — Selesai:**
- 🎉 "API Key siap! Sekarang kamu bisa mengoreksi essay secara otomatis."
- Tombol "Coba Koreksi Essay" → langsung ke halaman grading

### 1.3 File Changes
| File | Perubahan |
|------|-----------|
| `app/templates/teacher/ai_settings.html` | Restructure jadi wizard, tambah demo key info |
| `app/services/ai_service.py` | Tambah fallback ke DEMO_AI_KEY kalau tidak ada active key |
| `app/config.py` | Tambah `DEMO_AI_KEY` env var |
| `app/routes/teacher.py` | Tambah endpoint `/teacher/ai-settings/test-demo` |

---

## Fase 2: Auto-Deteksi Essay + Rubrik dari PDF
**Goal:** Guru upload PDF → AI otomatis deteksi essay, generate rubrik, set bobot.

### 2.1 PDF Processing Pipeline

```
Guru upload PDF → /teacher/exam/create-with-pdf
  │
  ├─ PyMuPDF: Extract teks + gambar
  │   ├─ Teks → parse per nomor soal
  │   └─ Gambar → simpan sebagai PNG
  │
  ├─ Deteksi tipe soal (ML rule-based, gratis):
  │   ├─ "Jelaskan", "Uraikan", "Analisislah", "Sebutkan" → ESSAY
  │   ├─ "Pilihlah", "A.", "B.", "C." → MCQ
  │   └─ "Jodohkan", "Pasangkan" → MATCHING
  │
  ├─ Generate rubrik (Gemini API, 1 call per soal essay):
  │   Prompt: "Buat rubrik penilaian untuk soal berikut dalam 3-5 kriteria.
  │            Soal: {question_text}. Format JSON: [{"kriteria":"...", "bobot":20}]"
  │
  ├─ Simpan ke DB:
  │   ├─ question_text
  │   ├─ question_type (mcq/essay)
  │   ├─ rubric (JSON dari AI)
  │   └─ embedding (sentence-transformers, lokal, gratis)
  │
  └─ Set bobot otomatis:
      ├─ MCQ% = (mcq_count / total_q) × 100
      └─ Essay% = (essay_count / total_q) × 100
```

### 2.2 UI: Upload PDF → Review → Simpan

```
┌──────────────────────────────────────────┐
│ 📤 Upload PDF Soal                       │
│ [Drag & drop atau klik untuk upload]     │
│                                          │
│ Setelah upload:                          │
│ ✅ AI mendeteksi 40 MCQ + 5 Essay        │
│ ✅ Rubrik untuk 5 essay telah digenerate │
│ ✅ Bobot otomatis: MCQ 80% / Essay 20%   │
│                                          │
│ [📝 Review & Edit] [✅ Langsung Simpan]   │
└──────────────────────────────────────────┘
```

### 2.3 File Changes
| File | Perubahan |
|------|-----------|
| `app/services/ai_embedding.py` | **BARU** — extract teks PDF, detect question type, generate embedding |
| `app/services/pdf_parser.py` | **BARU** — PyMuPDF extract teks + gambar dari PDF |
| `app/services/rubric_generator.py` | **BARU** — panggil AI untuk generate rubrik |
| `app/routes/teacher.py` | Tambah `POST /teacher/exam/create-with-pdf` |
| `app/templates/teacher/exam_form.html` | Tambah opsi upload PDF + review sebelum simpan |

---

## Fase 3: AI Grading Pipeline + Cache
**Goal:** Grading cepat, murah, bisa batch.

### 3.1 Alur Grading

```
Siswa submit jawaban essay
  ↓ (background, via Celery)
  ↓
1. Cek cache: ai_grading_cache (submission_id + question_index)
  ├─ Ada? → return cached (0 API call, instant)
  └─ Tidak ada? → lanjut
  ↓
2. Load question_embedding + rubric dari DB
  ↓
3. Build smart prompt:
  "Koreksi jawaban esai berikut.
   Konteks: {question_text}
   Rubrik: {rubric}
   Diagram: {diagram_context jika ada}
   Jawaban: {student_answer}
   Skor maks: {max_score}
   Format JSON: {\"score\": ..., \"feedback\": \"...\"}"
  ↓
4. Panggil AI provider (Gemini/Groq/OpenAI)
  ├─ Sukses → parse score + feedback
  └─ Gagal → retry 2x, log error
  ↓
5. Simpan ke ai_grading_cache
  ↓
6. Update score di submission
  ↓
7. Trigger WebSocket (kalau ada) → notifikasi guru
```

### 3.2 Batch Grading (Satu Klik)

```
Tombol: [✨ Koreksi Semua Essay] di halaman grade_detail
  ↓
POST /api/ai/grade-bulk-essays
  ↓
Progress bar real-time:
  ████████░░░░░░ 4/8 selesai (2 cached, 2 baru)
  ↓
Selesai: ✅ 8 essay terkoreksi (3 dari cache, 5 baru)
```

### 3.3 File Changes
| File | Perubahan |
|------|-----------|
| `app/services/ai_grading.py` | **BARU** — grading pipeline with cache |
| `app/services/grading_service.py` | Ubah `grade_essay()` jadi panggil AI grading |
| `app/services/ai_service.py` | Tambah batch grading, cache check |
| `app/routes/api.py` | Tambah `POST /api/ai/grade-essay`, `POST /api/ai/grade-bulk` |
| `app/celery_app.py` | Import tasks (grading bisa async via Celery) |

---

## Fase 4: UI/UX Koreksi Essay
**Goal:** Guru bisa koreksi dengan nyaman, cepat, tanpa stress.

### 4.1 Halaman Grade Detail — Tab Essay

Pisahkan tab "MCQ Review" dan "Essay Review" dengan tampilan:
- **Rubrik Checklist** — centang/dicentang, score menyesuaikan
- **Score Slider** — geser, feedback otomatis menyesuaikan
- **Feedback Area** — bisa diedit langsung, auto-save
- **Cached Badge** — badge "🔄 Cached" untuk essay yang sudah dikoreksi

### 4.2 Batch Button
- Tombol "✨ Koreksi Semua Essay" di sidebar
- Progress bar real-time saat processing
- Cache hit ditandai, tidak perlu proses ulang

### 4.3 File Changes
| File | Perubahan |
|------|-----------|
| `app/templates/teacher/grade_detail.html` | Tambah tab Essay, score slider, rubrik checklist |
| `app/static/js/grade_detail.js` | Slider logic, batch grading, auto-save |
| `app/routes/teacher.py` | Tambah endpoint simpan feedback essay |

---

## Fase 5: Database & Migration

### 5.1 Tabel Baru

```sql
-- Question embeddings & context
CREATE TABLE IF NOT EXISTS question_embeddings (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  exam_id UUID NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
  question_index INT NOT NULL,
  question_text TEXT NOT NULL,
  question_type TEXT DEFAULT 'mcq',
  rubric JSONB DEFAULT '[]',
  diagram_context TEXT DEFAULT '',
  embedding JSONB,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(exam_id, question_index)
);

-- AI grading cache
CREATE TABLE IF NOT EXISTS ai_grading_cache (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  submission_id UUID NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
  question_index INT NOT NULL,
  ai_score NUMERIC(5,2),
  ai_feedback TEXT,
  ai_provider VARCHAR(20),
  tokens_used INT DEFAULT 0,
  model_used VARCHAR(50),
  prompt_sent TEXT,
  raw_response TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(submission_id, question_index)
);

ALTER TABLE ai_grading_cache ADD COLUMN IF NOT EXISTS teacher_overridden BOOLEAN DEFAULT FALSE;
ALTER TABLE ai_grading_cache ADD COLUMN IF NOT EXISTS teacher_score NUMERIC(5,2);
ALTER TABLE ai_grading_cache ADD COLUMN IF NOT EXISTS teacher_feedback TEXT;
```

### 5.2 Indexes
```sql
CREATE INDEX IF NOT EXISTS idx_question_embeddings_exam ON question_embeddings(exam_id);
CREATE INDEX IF NOT EXISTS idx_ai_grading_cache_submission ON ai_grading_cache(submission_id);
```

---

## Fase 6: Dependencies & Deploy

### requirements.txt
```txt
sentence-transformers>=3.0.0
torch>=2.0.0
```

### Instalasi VPS
```bash
pip install sentence-transformers torch --no-cache-dir
```

---

## Timeline Estimasi

| Fase | Isi | Estimasi |
|------|-----|----------|
| **Fase 1** | Demo key + Wizard API Key | **2 hari** |
| **Fase 2** | Auto-detect essay + rubrik dari PDF | **3 hari** |
| **Fase 3** | Grading pipeline + cache + batch | **3 hari** |
| **Fase 4** | UI/UX koreksi essay (tab, slider, dll) | **3 hari** |
| **Fase 5** | DB migration + testing | **1 hari** |
| **Fase 6** | Deploy + monitoring | **1 hari** |
| **Total** | | **~13 hari** |

## Biaya

| Komponen | Biaya |
|----------|-------|
| Sentence-transformers (embedding) | **Gratis** (lokal, nol API call) |
| PyMuPDF (extract PDF) | **Gratis** (sudah terinstall) |
| Tesseract (OCR) | **Gratis** (sudah terinstall) |
| Gemini API (rubrik + grading) | **Gratis** (1500 req/hari/guru) |
| Storage (embedding + cache) | **~50MB** |
| **Total per bulan** | **Rp 0** 🎉 |
