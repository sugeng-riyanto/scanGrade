-- ──────────────────────────────────────────────────────────────
-- Migration 067: koreksi esai dengan saran AI — prompt per mapel, saran
-- tersimpan, dan asal-usul sebuah nilai di jejak audit
-- ──────────────────────────────────────────────────────────────
-- Jalankan lewat deploy/apply_migration.py (dry run dulu, baru --commit).
--
-- Masalah yang dijawab
-- ────────────────────
-- Halaman /teacher/ai-settings sudah lama menyimpan prompt di
-- `teacher_ai_settings.prompts`, tetapi:
--
--   1. bentuknya satu baris per guru dengan satu `active_prompt_id`, jadi
--      "prompt berbeda untuk setiap mapel" hanya bisa menjadi satu prompt aktif
--      untuk semua mapel; dan
--   2. `ai_grading._build_prompt` tidak pernah membacanya — prompt guru ditulis,
--      disimpan, dan tidak dipakai saat esai benar-benar dikoreksi.
--
-- `ai_grading_prompt` memberi prompt itu tempat yang sesuai dengan yang
-- dijanjikan halaman: satu baris per (guru, mapel), berversi, dengan catatan
-- siapa mengubahnya. `subject_key` adalah teks mapel yang dinormalkan dari
-- `exams.subject` (kolom itu memang teks bebas), sedangkan `subject_id`
-- opsional menunjuk katalog `subjects` bila namanya dapat dicocokkan — kunci
-- pencocokannya tetap `subject_key`, karena sebuah ujian menyimpan teks, bukan
-- id katalog.
--
-- `ai_grading_suggestion` adalah saran, bukan nilai. Satu baris per
-- (kertas, soal) yang menyimpan skor saran dalam POIN soal, alasan, keyakinan,
-- dan versi prompt yang dipakai. Nilai murid tidak pernah ditulis dari sini:
-- guru menekan "Terapkan", field guru terisi, dan penyimpanan nilai tetap
-- melewati jalur `teacher_feedback.scores` yang sudah ada.
--
-- `grading_audit_log` (migrasi 052) mencatat nilai lama → baru. Sekarang ia
-- juga mencatat SIAPA/APA yang mengusulkan nilai itu: `source` bernilai
-- `manual` (ketikan guru) atau `ai_suggestion` (saran AI yang diterapkan), dan
-- `suggestion_id` menunjuk baris saran yang diterapkan. Akuntabilitas tetap di
-- guru, dan jejaknya bisa dibaca.
--
-- Idempoten dan tidak merusak: setiap tabel/index memakai IF NOT EXISTS,
-- setiap kolom memakai ADD COLUMN IF NOT EXISTS, policy di-drop lebih dulu,
-- dan tidak ada pernyataan yang menghapus tabel, kolom, atau baris. Dijalankan
-- dua kali hasilnya sama.
--
-- Sengaja TANPA `BEGIN;`/`COMMIT;`: `deploy/apply_migration.py` yang memiliki
-- transaksinya (dry run = satu transaksi yang di-rollback).

-- ── 1. Prompt penilaian per mapel, per guru ─────────────────────────────────
CREATE TABLE IF NOT EXISTS public.ai_grading_prompt (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    teacher_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    -- Mapel yang dinormalkan (huruf kecil, spasi dirapatkan). `default` adalah
    -- rubrik cadangan untuk mapel yang belum punya prompt sendiri.
    subject_key TEXT NOT NULL
        CONSTRAINT ai_grading_prompt_subject_key_check
        CHECK (length(btrim(subject_key)) BETWEEN 1 AND 120),
    -- Label yang dilihat guru, apa adanya seperti yang diketik/dipilih.
    subject_label TEXT
        CONSTRAINT ai_grading_prompt_subject_label_check
        CHECK (subject_label IS NULL OR length(subject_label) <= 160),
    -- Katalog mapel sekolah, bila namanya bisa dicocokkan. NULL berarti prompt
    -- ini berlaku untuk teks mapel itu tanpa baris katalog.
    subject_id UUID REFERENCES public.subjects(id) ON DELETE SET NULL,
    prompt_text TEXT NOT NULL
        CONSTRAINT ai_grading_prompt_text_check
        CHECK (length(btrim(prompt_text)) BETWEEN 1 AND 4000),
    -- Dinaikkan setiap kali isinya berubah. Saran AI menyimpan versi yang
    -- dipakai, sehingga sebuah saran bisa dipertanggungjawabkan terhadap rubrik
    -- yang benar-benar berlaku saat ia dibuat.
    version INTEGER NOT NULL DEFAULT 1
        CONSTRAINT ai_grading_prompt_version_check CHECK (version >= 1),
    updated_by UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Satu prompt per guru per mapel: menyimpan dua kali adalah memperbarui, bukan
-- menambah baris kedua yang tidak pernah bisa dipilih.
CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_grading_prompt_one_per_subject
    ON public.ai_grading_prompt (teacher_id, subject_key);

CREATE INDEX IF NOT EXISTS idx_ai_grading_prompt_owner
    ON public.ai_grading_prompt (teacher_id);

-- ── 2. Saran AI untuk satu jawaban esai ────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.ai_grading_suggestion (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    attempt_id UUID NOT NULL REFERENCES public.submissions(id) ON DELETE CASCADE,
    question_index INTEGER NOT NULL
        CONSTRAINT ai_grading_suggestion_question_check CHECK (question_index >= 0),
    -- Antrean pekerjaan: queued → processing → done | failed. `ignored` berarti
    -- saran sudah ada tetapi guru memilih tidak memakainya.
    status TEXT NOT NULL DEFAULT 'queued'
        CONSTRAINT ai_grading_suggestion_status_check
        CHECK (status IN ('queued', 'processing', 'done', 'failed', 'ignored')),
    -- Skor saran dalam POIN soal (0 .. poin_maks), bukan persen. Field nilai
    -- guru menyimpan persen dari bobot soal, jadi konversinya satu tempat di
    -- service — bukan di browser.
    skor_saran NUMERIC(6, 2)
        CONSTRAINT ai_grading_suggestion_score_check
        CHECK (skor_saran IS NULL OR skor_saran >= 0),
    poin_maks NUMERIC(6, 2) NOT NULL
        CONSTRAINT ai_grading_suggestion_max_check CHECK (poin_maks > 0),
    alasan TEXT,
    tingkat_keyakinan NUMERIC(3, 2)
        CONSTRAINT ai_grading_suggestion_confidence_check
        CHECK (tingkat_keyakinan IS NULL
               OR (tingkat_keyakinan >= 0 AND tingkat_keyakinan <= 1)),
    prompt_version INTEGER,
    prompt_subject TEXT,
    provider TEXT,
    model TEXT,
    -- Catatan anomali: skor AI di luar rentang dipotong ke rentang soal dan
    -- alasannya disimpan di sini, bukan diterima mentah.
    anomaly TEXT,
    error_message TEXT,
    batch_id UUID,
    requested_by UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
    -- Kapan guru menekan "Terapkan skor ini", dan skor persen yang ia terima.
    applied_at TIMESTAMPTZ,
    applied_by UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
    applied_score NUMERIC(6, 2),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Satu saran terkini per kertas per soal. Meminta ulang memperbarui baris yang
-- sama, sehingga daftar status tidak menghitung permintaan yang sama dua kali.
CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_grading_suggestion_one_per_question
    ON public.ai_grading_suggestion (attempt_id, question_index);

CREATE INDEX IF NOT EXISTS idx_ai_grading_suggestion_batch
    ON public.ai_grading_suggestion (batch_id);

CREATE INDEX IF NOT EXISTS idx_ai_grading_suggestion_owner_status
    ON public.ai_grading_suggestion (requested_by, status);

-- ── 3. Asal-usul nilai di jejak audit ──────────────────────────────────────
-- `manual` adalah perilaku lama, jadi baris yang sudah ada tetap benar tanpa
-- ditulis ulang.
ALTER TABLE public.grading_audit_log
    ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'manual';

ALTER TABLE public.grading_audit_log
    ADD COLUMN IF NOT EXISTS suggestion_id UUID
        REFERENCES public.ai_grading_suggestion(id) ON DELETE SET NULL;

ALTER TABLE public.grading_audit_log
    ADD COLUMN IF NOT EXISTS note TEXT;

-- Batas nilai `source` ditegakkan longgar: nilai baru di masa depan tidak boleh
-- membuat penulisan audit gagal dan menghilangkan jejak yang justru dicari.
DO $$ BEGIN
    ALTER TABLE public.grading_audit_log
        ADD CONSTRAINT grading_audit_log_source_check
        CHECK (source IN ('manual', 'ai_suggestion'));
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

-- ── 4. RLS ──────────────────────────────────────────────────────────────────
-- Prompt milik gurunya sendiri: dibaca dan diubah hanya oleh dia. Sekolah tidak
-- membaca rubrik pribadi seorang guru, dan tidak ada jalur anon.
ALTER TABLE public.ai_grading_prompt ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "ai_grading_prompt_owner" ON public.ai_grading_prompt;
CREATE POLICY "ai_grading_prompt_owner" ON public.ai_grading_prompt
    FOR ALL TO authenticated
    USING (teacher_id = auth.uid())
    WITH CHECK (teacher_id = auth.uid());

-- Saran dibaca oleh guru yang memintanya. Penulisan dari worker memakai service
-- key (yang melewati RLS), sama seperti cache koreksi yang sudah ada.
ALTER TABLE public.ai_grading_suggestion ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "ai_grading_suggestion_requester" ON public.ai_grading_suggestion;
CREATE POLICY "ai_grading_suggestion_requester" ON public.ai_grading_suggestion
    FOR ALL TO authenticated
    USING (requested_by = auth.uid())
    WITH CHECK (requested_by = auth.uid());

-- ── verify ──────────────────────────────────────────────────────────────────
-- Setelah menerapkan, setiap baris harus `t`:
--
--   SELECT to_regclass('public.ai_grading_prompt')     IS NOT NULL AS prompts,
--          to_regclass('public.ai_grading_suggestion') IS NOT NULL AS suggestions,
--          (SELECT count(*) FROM information_schema.columns
--             WHERE table_name = 'grading_audit_log'
--               AND column_name IN ('source', 'suggestion_id', 'note')) = 3
--                                                              AS audit_columns;
