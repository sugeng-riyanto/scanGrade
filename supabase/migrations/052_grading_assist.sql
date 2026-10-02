-- ──────────────────────────────────────────────────────────────
-- Migration 052: alat bantu koreksi esai — bank komentar, penanda tinjau ulang,
-- dan jejak audit nilai
-- ──────────────────────────────────────────────────────────────
-- Jalankan lewat deploy/apply_migration.py (dry run dulu, baru --commit).
--
-- Masalah yang dijawab
-- ────────────────────
-- Koreksi satu esai tidak istimewa; mengoreksi **200** esai untuk satu soal yang
-- sama adalah pekerjaan lain sama sekali — dan tiga hal yang membuatnya tertahankan
-- belum punya tempat di skema:
--
--   1. **Komentar yang dipakai berulang.** Kesalahan yang sama muncul di puluhan
--      kertas. Sekarang guru mengetik ulang. `grading_comment_bank` menyimpannya
--      sekali per guru, dengan `uses` supaya yang paling sering dipakai naik ke
--      atas, dan `score_delta` opsional supaya menandai **dan** mengurangi nilai
--      jadi satu aksi.
--
--   2. **"Nanti saya lihat lagi."** Kasus ragu perlu ditandai, bukan diingat.
--      `grading_flag` adalah satu baris per (kertas, soal): menandai dua kali
--      tetap satu penanda, kalau tidak daftar tinjau ulang menghitung kertas dua
--      kali.
--
--   3. **Nilai yang berubah tanpa jejak.** `submissions.final_score` ditimpa
--      setiap penyimpanan. Kalau orang tua bertanya "kenapa nilainya turun",
--      tidak ada yang bisa dibaca. `grading_audit_log` mencatat nilai lama dan
--      baru — **hanya saat benar-benar berubah**, supaya menyimpan ulang kertas
--      yang tidak disentuh tidak mengarang riwayat.
--
-- Keterkaitan dengan pekerjaan sebelumnya
-- ───────────────────────────────────────
-- `grading_flag` dan `grading_audit_log` menunjuk ke `submissions` (kertas satu
-- murid), bukan ke `exam_target_student`: pengecualian target menentukan siapa
-- yang **boleh mengerjakan**, sedangkan penanda ini milik kertas yang **sudah**
-- dikerjakan. Murid yang dikecualikan setelah mengerjakan tetap punya kertas dan
-- tetap perlu bisa ditandai.
--
-- Idempoten dan tidak merusak: setiap tabel/index memakai IF NOT EXISTS, setiap
-- policy di-drop lebih dulu, dan tidak ada satu pernyataan pun yang menghapus
-- tabel, kolom, atau baris. Dijalankan dua kali hasilnya sama.
--
-- Sengaja TANPA `BEGIN;`/`COMMIT;`: `deploy/apply_migration.py` yang memiliki
-- transaksinya (dry run = satu transaksi yang di-rollback).

-- ── 1. Bank komentar ────────────────────────────────────────────────────────
-- Milik satu guru, bukan sekolah: dua guru menilai dengan kata-kata yang berbeda,
-- dan bank yang bercampur akan menawarkan kalimat yang bukan miliknya.
CREATE TABLE IF NOT EXISTS public.grading_comment_bank (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    teacher_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    -- Mapel dan nomor soal keduanya opsional: komentar bisa berlaku umum untuk
    -- semua mapel yang diajar guru ini, atau khusus satu soal.
    subject TEXT,
    question_index INTEGER,
    body TEXT NOT NULL
        CONSTRAINT grading_comment_bank_body_check CHECK (length(btrim(body)) BETWEEN 1 AND 2000),
    -- Opsional: pengurangan yang menemani komentar, supaya menandai + mengurangi
    -- jadi satu aksi. NULL berarti komentar tidak mengubah nilai.
    score_delta INTEGER
        CONSTRAINT grading_comment_bank_delta_check CHECK (score_delta IS NULL OR score_delta BETWEEN -100 AND 100),
    -- Berapa kali komentar ini diterapkan; dasar urutan "paling sering dipakai".
    uses INTEGER NOT NULL DEFAULT 0
        CONSTRAINT grading_comment_bank_uses_check CHECK (uses >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_grading_comment_bank_owner
    ON public.grading_comment_bank (teacher_id, subject, question_index);

-- ── 2. Penanda tinjau ulang ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.grading_flag (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    attempt_id UUID NOT NULL REFERENCES public.submissions(id) ON DELETE CASCADE,
    question_index INTEGER NOT NULL,
    teacher_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    reason TEXT
        CONSTRAINT grading_flag_reason_check CHECK (reason IS NULL OR length(reason) <= 280),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Satu penanda per (kertas, soal). Tanpa kunci ini, menandai dua kali menambah
-- baris kedua dan daftar tinjau ulang menghitung kertas yang sama dua kali.
CREATE UNIQUE INDEX IF NOT EXISTS idx_grading_flag_one_per_question
    ON public.grading_flag (attempt_id, question_index);

CREATE INDEX IF NOT EXISTS idx_grading_flag_owner
    ON public.grading_flag (teacher_id);

-- ── 3. Jejak audit nilai ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.grading_audit_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    attempt_id UUID NOT NULL REFERENCES public.submissions(id) ON DELETE CASCADE,
    question_index INTEGER NOT NULL,
    teacher_id UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
    -- NULL = belum pernah dinilai (nilai pertama), bukan nol.
    old_score NUMERIC(6, 2),
    new_score NUMERIC(6, 2),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_grading_audit_log_attempt
    ON public.grading_audit_log (attempt_id, question_index, created_at);

-- ── RLS ─────────────────────────────────────────────────────────────────────
-- Bank komentar milik gurunya sendiri; hanya dia yang membaca dan mengubahnya.
ALTER TABLE public.grading_comment_bank ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "grading_comment_bank_owner" ON public.grading_comment_bank;
CREATE POLICY "grading_comment_bank_owner" ON public.grading_comment_bank
    FOR ALL TO authenticated
    USING (teacher_id = auth.uid())
    WITH CHECK (teacher_id = auth.uid());

-- Penanda dan jejak audit dibaca guru pembuatnya, dan ditulis guru tersebut.
ALTER TABLE public.grading_flag ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "grading_flag_owner" ON public.grading_flag;
CREATE POLICY "grading_flag_owner" ON public.grading_flag
    FOR ALL TO authenticated
    USING (teacher_id = auth.uid())
    WITH CHECK (teacher_id = auth.uid());

ALTER TABLE public.grading_audit_log ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "grading_audit_log_owner" ON public.grading_audit_log;
CREATE POLICY "grading_audit_log_owner" ON public.grading_audit_log
    FOR ALL TO authenticated
    USING (teacher_id = auth.uid())
    WITH CHECK (teacher_id = auth.uid());
