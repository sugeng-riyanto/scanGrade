-- ──────────────────────────────────────────────────────────────
-- Migration 068: saran AI untuk penilaian esai (`ai_grading_suggestion`)
-- ──────────────────────────────────────────────────────────────
-- Jalankan lewat deploy/apply_migration.py (dry run dulu, baru --commit), atau
-- tempel di Supabase SQL Editor. Dry run wajib: berkas ini mengubah skema.
--
-- MENGAPA TABEL BARU, BUKAN `ai_grading_cache`
-- ──────────────────────────────────────────────
-- Sampai migrasi ini, hasil AI disimpan di `ai_grading_cache` (migrasi 014) dan —
-- di jalur massal — ditulis LANGSUNG ke `submissions.answers` lewat
-- `ai_grading.py::_update_submission_score`. Itu berarti model pihak ketiga bisa
-- menetapkan nilai akhir seorang murid tanpa seorang guru pun menekan tombol. Tiga
-- akibatnya, dan ketiganya alasan tabel ini ada:
--
--   1. Tidak ada tempat untuk menyatakan "ini saran, belum nilai". `ai_score` dan
--      nilai guru tinggal di kolom yang sama pada baris yang sama, jadi lupa
--      membedakannya berarti nilai AI sudah berlaku.
--   2. Tidak ada jejak keputusan. Kolom `teacher_overridden`/`teacher_score` di 014
--      tidak pernah ditulis oleh kode mana pun — kolom mati yang membuat skemanya
--      *terlihat* sudah punya alur persetujuan padahal tidak.
--   3. Cache adalah memo, bukan catatan. Cache yang benar-benar berfungsi adalah
--      cache yang boleh dibuang; membuang barisnya tidak boleh berarti membuang
--      bukti bahwa seorang murid pernah disarankan nilai tertentu.
--
-- Jadi `ai_grading_cache` tetap ada dan tetap menjadi memo (dipakai apa adanya),
-- dan tabel ini adalah catatan keputusannya. Satu saran = satu baris.
--
-- ALUR STATUS (dan status mana yang boleh menjadi apa)
-- ─────────────────────────────────────────────────────
--   queued   → running → ready → applied | rejected
--                      ↘ failed → queued (retry oleh guru)
--
-- `applied` dan `rejected` adalah keputusan MANUSIA. Tidak ada kode yang menulis
-- keduanya selain rute yang dipanggil guru, dan `applied` tidak menulis nilai ke
-- `submissions` sama sekali: guru tetap menekan simpan pada alur koreksi yang
-- sudah ada. Yang dicatat di sini adalah "guru ini menerima saran ini", yaitu
-- bukti yang dibutuhkan saat sebuah nilai dipertanyakan.
--
-- MENGAPA `max_score` DISIMPAN DI SETIAP BARIS
-- ────────────────────────────────────────────
-- Karena skor saran hanya bermakna terhadap poin soal yang berlaku SAAT sarannya
-- dibuat. `question_weights` bisa diubah guru setelahnya; tanpa kolom ini, sebuah
-- saran "8 dari 10" akan terbaca sebagai "8 dari 40" pada laporan berikutnya.
--
-- TIDAK ADA IDENTITAS MURID DI SINI YANG PERLU DIKIRIM KE PROVIDER AI
-- ────────────────────────────────────────────────────────────────
-- `student_id` sengaja TIDAK ada. Baris ini dikaitkan ke pekerjaan lewat
-- `submission_id`, dan nama/NISN murid dibaca dari `submissions` di sisi ScanGrade
-- bila suatu saat perlu ditampilkan. Payload yang dikirim ke provider AI hanya
-- berisi teks soal, rubrik, dan jawaban — lihat `app/services/ai_prompt.py`.
--
-- IDEMPOTEN: seluruh berkas aman dijalankan dua kali.

-- ── 1. tabel ─────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.ai_grading_suggestion (
    id                UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    submission_id     UUID NOT NULL REFERENCES public.submissions(id) ON DELETE CASCADE,
    exam_id           UUID,
    school_id         UUID,
    question_index    INT  NOT NULL,
    teacher_id        UUID NOT NULL,

    status            TEXT NOT NULL DEFAULT 'queued',

    -- Poin yang berlaku saat saran dibuat, bukan saat dibaca. Skor sudah dijepit
    -- ke rentang ini oleh server sebelum ditulis (ai_prompt.parse_suggestion).
    max_score         NUMERIC(7,2),
    score_suggested   NUMERIC(7,2),
    confidence        NUMERIC(4,3),

    feedback          TEXT,
    reasoning         TEXT,

    -- Teks yang menggantikan jawaban tersimpan, HANYA untuk jalur OCR canvas:
    -- guru membaca tulisan tangan siswa, dan teks bacaannya itu yang dinilai.
    -- Null berarti "pakai `submissions.answers`", yang merupakan jalur biasa.
    -- Disimpan di sini, bukan dikirim ulang ke prompt, karena task Celery harus
    -- menyusun prompt dari baris ini dan bukan dari apa pun yang dikirim browser.
    answer_override   TEXT,

    -- Apa yang server harus perbaiki dari jawaban model: skor di luar rentang,
    -- field skor bernama lain, confidence 95 alih-alih 0,95, jawaban terpotong.
    -- Disimpan sebagai daftar, bukan dibuang, karena "model membalas 999 untuk soal
    -- 10 poin" adalah fakta tentang satu kali penilaian yang perlu bisa ditelusuri.
    anomalies         JSONB NOT NULL DEFAULT '[]'::jsonb,

    provider          TEXT,
    model_used        TEXT,
    prompt_version    TEXT,
    error             TEXT,

    -- Berapa karakter jawaban yang benar-benar dikirim. Lebih kecil dari panjang
    -- jawaban asli berarti jawabannya terpotong (MAX_ANSWER_CHARS), dan guru perlu
    -- tahu itu sebelum menerima skornya.
    answer_chars      INT,

    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at       TIMESTAMPTZ,
    resolved_by       UUID
);

-- ── 2. kosakata status ───────────────────────────────────────────────────────
-- Ditulis sebagai CHECK yang longgar (bukan enum) supaya status baru di rilis
-- berikutnya tidak menuntut migrasi yang mengunci tabel ini saat deploy.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ai_grading_suggestion_status_check'
    ) THEN
        ALTER TABLE public.ai_grading_suggestion
            ADD CONSTRAINT ai_grading_suggestion_status_check
            CHECK (status IN ('queued', 'running', 'ready', 'failed', 'applied', 'rejected'));
    END IF;
END $$;

-- ── 3. indeks ────────────────────────────────────────────────────────────────
-- Satu permintaan per (pekerjaan, soal, guru) yang masih hidup. Ini yang membuat
-- tombol "minta saran" yang ditekan dua kali tidak menghasilkan dua tagihan API:
-- rute mencari baris `queued`/`running`/`ready` yang sudah ada sebelum membuat
-- yang baru. Indeks parsial, jadi riwayat `applied`/`rejected` tidak ikut dibatasi.
CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_grading_suggestion_open
    ON public.ai_grading_suggestion (submission_id, question_index, teacher_id)
    WHERE status IN ('queued', 'running', 'ready');

CREATE INDEX IF NOT EXISTS idx_ai_grading_suggestion_submission
    ON public.ai_grading_suggestion (submission_id, question_index);

CREATE INDEX IF NOT EXISTS idx_ai_grading_suggestion_exam_status
    ON public.ai_grading_suggestion (exam_id, status);

CREATE INDEX IF NOT EXISTS idx_ai_grading_suggestion_teacher_created
    ON public.ai_grading_suggestion (teacher_id, created_at DESC);

-- ── 4. updated_at ────────────────────────────────────────────────────────────
-- Trigger, bukan kepercayaan pada setiap penulis: baris ini ditulis oleh worker
-- Celery dan oleh dua rute HTTP, dan satu penulis yang lupa memperbarui kolomnya
-- membuat "kapan saran ini selesai" tidak bisa dijawab.

CREATE OR REPLACE FUNCTION public._ai_grading_suggestion_touch()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_ai_grading_suggestion_touch ON public.ai_grading_suggestion;
CREATE TRIGGER trg_ai_grading_suggestion_touch
    BEFORE UPDATE ON public.ai_grading_suggestion
    FOR EACH ROW EXECUTE FUNCTION public._ai_grading_suggestion_touch();

-- ── 5. RLS ───────────────────────────────────────────────────────────────────
-- Guru membaca saran untuk pekerjaan yang ia nilai. Penulisan sepenuhnya lewat
-- service key setelah rute memeriksa kepemilikan `submission_id` → `exams` → guru
-- (lihat `_guard_exam` di app/routes/teacher.py), sama seperti tabel permintaan
-- keanggotaan di migrasi 063: sebuah saran dinilai adalah data penilaian, dan
-- kunci publik tidak boleh bisa mengarangnya.

ALTER TABLE public.ai_grading_suggestion ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "ai_grading_suggestion_read_own" ON public.ai_grading_suggestion;
CREATE POLICY "ai_grading_suggestion_read_own" ON public.ai_grading_suggestion
    FOR SELECT TO authenticated
    USING (teacher_id = auth.uid());

-- Sengaja TIDAK ada policy INSERT/UPDATE/DELETE untuk `anon`/`authenticated`:
-- satu klien yang bisa menulis tabel ini bisa menerbitkan "saran AI" palsu untuk
-- dirinya sendiri, dan seluruh pagar di `ai_prompt.py` menjadi tidak relevan.

-- ── 6. catatan untuk pembaca berikutnya ──────────────────────────────────────
COMMENT ON TABLE public.ai_grading_suggestion IS
    'Saran nilai AI per soal. Saran, BUKAN nilai: hanya guru yang mengubahnya menjadi nilai lewat alur koreksi.';
COMMENT ON COLUMN public.ai_grading_suggestion.max_score IS
    'Poin soal yang berlaku saat saran dibuat; skor_suggested sudah dijepit ke rentang ini oleh server.';
COMMENT ON COLUMN public.ai_grading_suggestion.anomalies IS
    'Koreksi yang dilakukan server atas balasan model (skor di luar rentang, confidence 0-100, jawaban terpotong).';
COMMENT ON COLUMN public.ai_grading_suggestion.answer_chars IS
    'Panjang jawaban yang benar-benar dikirim ke provider; lebih pendek dari jawaban asli berarti terpotong.';
