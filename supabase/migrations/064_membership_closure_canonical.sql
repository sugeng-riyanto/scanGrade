-- ──────────────────────────────────────────────────────────────
-- Migration 064: 'closed' menjadi SATU-SATUNYA status penutupan
-- ──────────────────────────────────────────────────────────────
-- Jalankan lewat deploy/apply_migration.py (dry run dulu, baru --commit), atau
-- tempel di Supabase SQL Editor. Dry run wajib: berkas ini mengubah data.
--
-- MASALAH YANG DIJAWAB
-- ────────────────────
-- Migrasi 044 punya status 'inactive' (keanggotaan dimatikan admin sekolah).
-- Migrasi 063 menambah 'closed' (penutupan total dari alur permintaan bergabung).
-- Dua nama untuk satu keadaan adalah cacat, bukan fitur: halaman admin yang
-- membaca keduanya akan menampilkan DUA jenis penutupan untuk satu peristiwa yang
-- sama, dan setiap jalan baca baru harus ingat untuk menyaring dua nilai. Aturan
-- yang bergantung pada "ingat menyaring" adalah aturan yang akan bocor.
--
-- KEPUTUSAN (dari pemilik produk): 'closed' adalah status KANONIK untuk SETIAP
-- penutupan keanggotaan — bukan hanya yang berasal dari alur permintaan baru.
-- Konsekuensinya tiga, dan ketiganya dikerjakan di sini atau di kode:
--   1. baris lama berstatus 'inactive' DIUBAH menjadi 'closed' (di bawah);
--   2. `school_membership.deactivate()` menulis 'closed' (bukan 'inactive');
--   3. aturan "hanya admin_sekolah boleh membuka kembali" berlaku SERAGAM untuk
--      semua jenis penutupan, bukan hanya alur ini.
--
-- BERKAS INI TIDAK BERGANTUNG PADA 063 SUDAH DITERAPKAN — DAN ITU BUKAN ASUMSI
-- ─────────────────────────────────────────────────────────────────────────────
-- Ditemukan pada dry run PERTAMA berkas ini: `closed_at` belum ada di database
-- (dry run 063 tidak pernah di-commit), sehingga `UPDATE ... SET closed_at = ...`
-- gagal `UndefinedColumn`. Sebuah migrasi kanonikalisasi adalah tempat yang salah
-- untuk gagal hanya karena urutan penerapan — jadi DDL yang dibutuhkannya (kolom
-- penutupan + kosakata status yang mengizinkan 'closed') ditulis di sini juga,
-- semuanya idempoten (`ADD COLUMN IF NOT EXISTS`, `DROP CONSTRAINT IF EXISTS` lalu
-- `ADD`). Kalau 063 sudah diterapkan, pernyataan-pernyataan itu menjadi no-op.
--
-- KENAPA 'inactive' TIDAK DIBUANG DARI CONSTRAINT
-- ──────────────────────────────────────────────
-- CHECK mengizinkan ('active', 'invited', 'inactive', 'closed'). Nilai 'inactive'
-- tetap diizinkan SESENGAJA: satu baris yang entah bagaimana masih memuatnya tidak
-- boleh membuat migrasi gagal, dan maknanya bagi akses sudah benar
-- (`status != 'active'` berarti tanpa akses, lihat memberships_for /
-- is_active_member). Yang penting, dan yang ditegakkan test, adalah TIDAK ADA kode
-- yang MENULIS 'inactive' lagi — sehingga kanonikalisasi ini tidak bocor lewat
-- jalur tulis yang tertinggal.
--
-- IDEMPOTEN: baris UPDATE di bawah memindahkan 'inactive' -> 'closed' dan pada
-- eksekusi kedua tidak menemukan apa pun untuk dipindah (no-op). Tidak ada
-- DROP TABLE/COLUMN, tidak ada penghapusan baris.
--
-- Sengaja TANPA `BEGIN;`/`COMMIT;`: `deploy/apply_migration.py` yang memiliki
-- transaksinya (dry run = satu transaksi yang di-rollback).

-- ── 1. kosakata status: 'closed' harus diizinkan ────────────────────────────
-- (No-op bila 063 sudah diterapkan: constraint-nya di-drop lalu dibuat ulang
-- dengan daftar yang sama.)

ALTER TABLE public.teacher_school_membership
    DROP CONSTRAINT IF EXISTS teacher_school_membership_status_check;
ALTER TABLE public.teacher_school_membership
    ADD CONSTRAINT teacher_school_membership_status_check
    CHECK (status IN ('active', 'invited', 'inactive', 'closed'));

-- ── 2. kolom penutupan/pembukaan ────────────────────────────────────────────

ALTER TABLE public.teacher_school_membership
    ADD COLUMN IF NOT EXISTS closed_by UUID REFERENCES public.profiles(id) ON DELETE SET NULL;
ALTER TABLE public.teacher_school_membership
    ADD COLUMN IF NOT EXISTS closed_at TIMESTAMPTZ;
ALTER TABLE public.teacher_school_membership
    ADD COLUMN IF NOT EXISTS reopened_by UUID REFERENCES public.profiles(id) ON DELETE SET NULL;
ALTER TABLE public.teacher_school_membership
    ADD COLUMN IF NOT EXISTS reopened_at TIMESTAMPTZ;

COMMENT ON COLUMN public.teacher_school_membership.closed_by IS
    'Siapa yang menutup keanggotaan ini. Hanya admin_sekolah/principal/vice_principal sekolah itu yang boleh menulisnya (ditegakkan di rute + service + test adversarial), dan penutupan berarti akses TOTAL tertutup ke sekolah itu — bukan read-only.';
COMMENT ON COLUMN public.teacher_school_membership.reopened_by IS
    'Siapa yang membuka kembali keanggotaan ini. HANYA admin_sekolah sekolah itu — principal dan vice_principal sengaja TIDAK berwenang membuka kembali, sekalipun mereka berwenang menyetujui permintaan baru. Asimetri ini keputusan produk yang eksplisit, bukan bug.';

-- "keanggotaan yang ditutup di sekolah ini" — pertanyaan halaman admin.
CREATE INDEX IF NOT EXISTS idx_teacher_school_membership_closed
    ON public.teacher_school_membership(school_id, closed_at DESC)
    WHERE status = 'closed';

-- ── 3. data: satu keadaan, satu nama ────────────────────────────────────────
-- `closed_at` diisi dari `updated_at` (saat penutupan itu sebenarnya terjadi),
-- bukan NOW(): menjalankan migrasi ini bulan depan tidak boleh memberi tahu
-- sekolah bahwa keanggotaan ditutup bulan depan. `closed_by` dibiarkan apa
-- adanya — penutupan lama tidak menyimpan pelakunya, dan mengarang satu nama
-- untuk baris yang tidak punya adalah kebohongan yang tercatat permanen.

UPDATE public.teacher_school_membership
   SET status    = 'closed',
       closed_at = COALESCE(closed_at, updated_at, created_at, NOW())
 WHERE status = 'inactive';

COMMENT ON COLUMN public.teacher_school_membership.status IS
    'Status keanggotaan. ''active'' = punya akses; ''invited'' = undangan belum diterima; ''closed'' = SATU-SATUNYA status penutupan (kanonik sejak migrasi 064 — ''inactive'' dari 044 dipindahkan ke sini dan tidak lagi ditulis kode mana pun). Setiap nilai selain ''active'' berarti tanpa akses, jadi penutupan tidak butuh jalan baca kedua.';
