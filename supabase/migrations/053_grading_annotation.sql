-- ──────────────────────────────────────────────────────────────
-- Migration 053: lapisan anotasi guru di atas jawaban murid
-- ──────────────────────────────────────────────────────────────
-- Jalankan lewat deploy/apply_migration.py (dry run dulu, baru --commit).
--
-- Masalah yang dijawab
-- ────────────────────
-- Bank komentar (migrasi 052) adalah kalimat **milik guru** yang dipakai ulang
-- lintas kertas. Anotasi adalah separuh lainnya: tanda **pada kata-kata anak
-- ini** — kalimat ini argumennya, klaim itu bertentangan sendiri, yang ini
-- mendapat poin.
--
-- Seluruh desainnya bersandar pada satu aturan: **jawaban murid tidak pernah
-- diubah.** Sorotan adalah baris yang menamai rentang teks jawaban; teksnya
-- sendiri tak tersentuh, jadi menghapus tanda mengembalikan persis apa yang
-- ditulis anak itu, dan tidak ada laporan yang bisa menampilkan kata yang tidak
-- mereka ketik. Itulah sebabnya rentangnya adalah `start_offset`/`end_offset`
-- ke dalam jawaban **sebagaimana tersimpan**, dan kenapa tabel ini menulis ke
-- dirinya sendiri dan bukan ke `submissions.answers`.
--
-- Kosakata yang tertutup
-- ──────────────────────
-- `kind` hanya boleh `highlight`, `strike`, atau `comment` — tiga hal yang bisa
-- dilakukan seorang guru pada sepotong teks. Warna juga dibatasi kosakata kecil,
-- bukan hex bebas, supaya "apa arti warna ini" punya jawaban yang sama di semua
-- halaman.
--
-- Idempoten dan tidak merusak: `IF NOT EXISTS` pada tabel dan indeks, policy
-- di-drop lebih dulu, dan tidak ada satu pernyataan pun yang menghapus tabel,
-- kolom, atau baris. Dijalankan dua kali hasilnya sama.
--
-- Sengaja TANPA `BEGIN;`/`COMMIT;`: `deploy/apply_migration.py` yang memiliki
-- transaksinya (dry run = satu transaksi yang di-rollback).

CREATE TABLE IF NOT EXISTS public.grading_annotation (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    attempt_id UUID NOT NULL REFERENCES public.submissions(id) ON DELETE CASCADE,
    question_index INTEGER NOT NULL,
    kind TEXT NOT NULL
        CHECK (kind IN ('highlight', 'strike', 'comment')),
    -- Rentang ke dalam teks jawaban. Batas atasnya (panjang teks) diperiksa di
    -- aplikasi, yang memegang teksnya; di sini yang ditegakkan hanya bentuknya.
    start_offset INTEGER NOT NULL CHECK (start_offset >= 0),
    end_offset INTEGER NOT NULL CHECK (end_offset > start_offset),
    -- Catatan opsional: untuk `comment` inilah isinya; untuk `highlight`/`strike`
    -- boleh kosong (tandanya sudah berbicara sendiri).
    note TEXT CHECK (note IS NULL OR length(note) <= 2000),
    color TEXT CHECK (color IS NULL OR color IN ('amber', 'rose', 'sky', 'emerald', 'violet')),
    created_by UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_grading_annotation_attempt
    ON public.grading_annotation (attempt_id, question_index, start_offset);

CREATE INDEX IF NOT EXISTS idx_grading_annotation_owner
    ON public.grading_annotation (created_by);

-- ── RLS: milik guru yang membuatnya ─────────────────────────────────────────
ALTER TABLE public.grading_annotation ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "grading_annotation_owner" ON public.grading_annotation;
CREATE POLICY "grading_annotation_owner" ON public.grading_annotation
    FOR ALL TO authenticated
    USING (created_by = auth.uid())
    WITH CHECK (created_by = auth.uid());
