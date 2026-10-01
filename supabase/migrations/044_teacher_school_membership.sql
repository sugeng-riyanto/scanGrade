-- ──────────────────────────────────────────────────────────────
-- Migration 044: keanggotaan guru lintas sekolah (satu akun, banyak NPSN)
-- ──────────────────────────────────────────────────────────────
-- Jalankan lewat deploy/apply_migration.py (dry run dulu, baru --commit), atau
-- tempel di Supabase SQL Editor.
--
-- Masalah yang dijawab
-- ────────────────────
-- Sampai sekarang satu akun = satu `profiles.school_id`. Seorang guru yang
-- mengajar di dua sekolah harus punya dua akun (dua email), dua kali aktivasi,
-- dan tidak ada cara berpindah sekolah tanpa keluar-masuk. Tabel ini memisahkan
-- **identitas** (satu baris `profiles`, satu email) dari **keanggotaan** (banyak
-- baris, satu per sekolah), persis keputusan desain yang sudah diambil:
-- "satu akun, banyak keanggotaan sekolah".
--
-- `profiles.school_id` **tidak dihapus** dan tetap berarti "sekolah rumah" guru
-- itu. Rute yang belum sadar keanggotaan tetap membaca kolom lama lewat
-- `auth._fetch_session`, jadi tidak ada satu pun halaman yang berubah perilaku
-- sebelum resolusi sekolah-aktif menanganinya. Migrasi ini murni aditif.
--
-- Kenapa status di sini, bukan di `profiles`
-- ──────────────────────────────────────────
-- Status keanggotaan berbeda per sekolah: seorang guru boleh aktif di SMP A dan
-- nonaktif di SMA B. Menaruhnya di `profiles.status` akan membuat satu sekolah
-- bisa mematikan yang lain.
--
-- Yang dijawab RLS di sini, dan yang tidak
-- ────────────────────────────────────
-- Backend memakai service key dan menembus RLS. Policy di bawah bukan yang menjaga
-- rute Flask (yang menjaganya `app/services/school_membership.py` dan resolusi
-- sekolah-aktif di `app/utils/auth.py`), melainkan jawaban atas pertanyaan yang
-- hanya bisa dijawab database: **kalau kunci publik yang ikut terkirim di setiap
-- halaman dipakai tanpa sesi, keanggotaan siapa yang terbaca?**
--
-- Aturannya: seorang pengguna membaca keanggotaannya sendiri, dan pengurus
-- sekolah membaca keanggotaan sekolahnya; hanya admin sekolah yang menulis. Tidak
-- ada policy yang bisa membaca keanggotaan pengguna di lintas NPSN.
--
-- Idempoten dan tidak merusak: setiap tabel/index/policy memakai IF NOT EXISTS
-- atau DROP ... IF EXISTS, tidak ada DROP TABLE/COLUMN, dan backfill memakai
-- `ON CONFLICT DO NOTHING`. Dijalankan dua kali hasilnya sama.
--
-- Sengaja TANPA `BEGIN;`/`COMMIT;`: `deploy/apply_migration.py` yang memiliki
-- transaksinya.

-- ── 1. tabel keanggotaan ─────────────────────────────────────────────────────
--
-- The helper function that reads this table is created *after* it, not before:
-- a `LANGUAGE sql` body is validated at CREATE time (check_function_bodies is on
-- by default), so creating the function first fails with `relation
-- "public.teacher_school_membership" does not exist` before the table exists.
-- Measured against the live project: this migration could never apply until the
-- order was flipped.

CREATE TABLE IF NOT EXISTS public.teacher_school_membership (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    school_id UUID NOT NULL REFERENCES public.schools(id) ON DELETE CASCADE,
    school_role TEXT NOT NULL DEFAULT 'guru'
        CONSTRAINT teacher_school_membership_school_role_check
        CHECK (school_role IN ('guru', 'principal', 'vice_principal')),
    status TEXT NOT NULL DEFAULT 'active'
        CONSTRAINT teacher_school_membership_status_check
        CHECK (status IN ('active', 'invited', 'inactive')),
    -- Siapa yang menambahkan baris ini: admin sekolah pengundang, atau guru itu
    -- sendiri saat menyetujui undangan. NULL untuk backfill dari profil lama.
    invited_by UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
    joined_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Satu orang hanya boleh punya satu baris per sekolah; ini yang membuat
    -- "apakah user X anggota sekolah Y" satu lookup, bukan satu pencarian.
    CONSTRAINT teacher_school_membership_pair_key UNIQUE (user_id, school_id)
);

-- "sekolah-sekolah milik user X" — dipakai setiap request untuk guru lintas
-- sekolah, jadi ia harus satu index seek, bukan scan.
CREATE INDEX IF NOT EXISTS idx_teacher_school_membership_user
    ON public.teacher_school_membership(user_id, status);
CREATE INDEX IF NOT EXISTS idx_teacher_school_membership_school
    ON public.teacher_school_membership(school_id, status);

-- ── 2. helper: sekolah-sekolah yang dianggotai pemanggil ─────────────────────
-- Dibuat setelah tabelnya ada, karena body `LANGUAGE sql` divalidasi saat
-- CREATE (lihat catatan di atas).

CREATE OR REPLACE FUNCTION public._user_school_ids()
RETURNS SETOF UUID AS $$
    SELECT school_id FROM public.teacher_school_membership
    WHERE user_id = auth.uid() AND status = 'active';
$$ LANGUAGE sql STABLE SECURITY DEFINER;

-- ── 3. backfill: setiap guru/pengurus yang sudah ada menjadi anggota aktif ────
-- Hanya tiga peran yang bisa lintas sekolah. Murid dan admin sekolah tetap satu
-- sekolah lewat `profiles.school_id` dan tidak mendapat baris di sini.

INSERT INTO public.teacher_school_membership (user_id, school_id, school_role, status, joined_at)
SELECT p.id, p.school_id, p.role, 'active', NOW()
FROM public.profiles p
WHERE p.role IN ('guru', 'principal', 'vice_principal')
  AND p.school_id IS NOT NULL
ON CONFLICT (user_id, school_id) DO NOTHING;

-- ── 4. RLS: sendiri boleh baca, pengurus boleh baca, admin sekolah boleh tulis ─

ALTER TABLE public.teacher_school_membership ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "membership_select_own" ON public.teacher_school_membership;
CREATE POLICY "membership_select_own" ON public.teacher_school_membership
    FOR SELECT TO authenticated
    USING (user_id = auth.uid());

-- Kepala sekolah dan wakilnya dibaca berdampingan dengan admin sekolah, persis
-- seperti `school_official_required` memakai `role_required(*OFFICIAL_ROLES)`:
-- policy yang menyebut salah satunya saja membuat dua peran itu berbeda hak di
-- tingkat database padahal di tingkat rute keduanya satu.
DROP POLICY IF EXISTS "membership_select_school_admin" ON public.teacher_school_membership;
CREATE POLICY "membership_select_school_admin" ON public.teacher_school_membership
    FOR SELECT TO authenticated
    USING (
        (public._is_role('admin_sekolah') OR public._is_role('principal')
         OR public._is_role('vice_principal'))
        AND school_id = public._user_school_id()
    );

DROP POLICY IF EXISTS "membership_write_school_admin" ON public.teacher_school_membership;
CREATE POLICY "membership_write_school_admin" ON public.teacher_school_membership
    FOR ALL TO authenticated
    USING (
        public._is_role('admin_sekolah')
        AND school_id = public._user_school_id()
    )
    WITH CHECK (
        public._is_role('admin_sekolah')
        AND school_id = public._user_school_id()
    );

-- Seorang guru menyetujui undangannya sendiri: baris untuk dirinya di sekolah
-- yang diundang, yang tadinya `invited`, menjadi `active`. WITH CHECK menahan
-- baris *hasil* tetap milik dirinya, jadi persetujuan tidak bisa dipakai
-- memindahkan baris milik orang lain.
DROP POLICY IF EXISTS "membership_accept_own_invite" ON public.teacher_school_membership;
CREATE POLICY "membership_accept_own_invite" ON public.teacher_school_membership
    FOR UPDATE TO authenticated
    USING (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());
