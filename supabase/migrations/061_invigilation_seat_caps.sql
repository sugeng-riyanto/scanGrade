-- ──────────────────────────────────────────────────────────────
-- Migration 061: dua pengawas per ruangan, dua ruangan per guru
-- ──────────────────────────────────────────────────────────────
-- Jalankan lewat deploy/apply_migration.py (dry run dulu, baru --commit).
--
-- Latar belakang
-- ──────────────
-- Migrasi 059 memasang dua constraint unik yang berarti *satu*:
--
--   invigilation_duty_room_slot_key     satu ruangan = satu pengawas per slot
--   invigilation_duty_teacher_slot_key  satu guru = satu ruangan per slot
--
-- Aturan sekolah sekarang: satu ruangan boleh dijaga **1 sampai 2** guru, dan
-- satu guru boleh menjaga **1 sampai 2** ruangan pada slot yang sama. Constraint
-- UNIQUE tidak bisa menyatakan "paling banyak dua" — ia hanya bisa menyatakan
-- "unik" — jadi batasnya ditegakkan trigger, sementara keunikan yang tersisa
-- (guru yang sama tidak boleh tercatat dua kali di ruangan yang sama) tetap
-- dipegang sebuah UNIQUE, karena itu memang keunikan, bukan batas jumlah.
--
-- Kenapa trigger dan bukan sekadar pemeriksaan aplikasi
-- ─────────────────────────────────────────────────────
-- Aplikasi memeriksa lebih dulu supaya pesannya ramah ("ruangan ini sudah dua
-- pengawas"). Tapi pemeriksaan aplikasi bisa kalah lomba di antara dua POST
-- nyaris bersamaan, jadi database tetap harus jadi yang menahan. Fungsi di
-- bawah mengunci baris `exam_period` slot itu lebih dulu (`FOR UPDATE`), sehingga
-- dua insert untuk sesi yang sama berjalan berurutan dan hitungannya melihat
-- baris yang sudah tersimpan — bukan dua-duanya melihat angka lama.
--
-- Idempoten dan tidak merusak: kolom memakai IF NOT EXISTS, constraint lama
-- di-drop dengan IF EXISTS, dan tidak ada tabel, kolom, atau baris yang dihapus.
-- Baris yang sudah ada (maksimal satu per ruangan/guru dari 059) tetap valid di
-- bawah batas baru.
--
-- Sengaja TANPA BEGIN;/COMMIT; — `deploy/apply_migration.py` yang memiliki
-- transaksinya (dry run = satu transaksi yang di-rollback).

-- ── 1. batas lama yang berarti "satu" dicabut, keunikan sel dipertahankan ────

ALTER TABLE public.invigilation_duty
    DROP CONSTRAINT IF EXISTS invigilation_duty_room_slot_key;
ALTER TABLE public.invigilation_duty
    DROP CONSTRAINT IF EXISTS invigilation_duty_teacher_slot_key;

-- Guru yang sama tidak perlu tercatat dua kali di ruangan yang sama pada slot
-- yang sama — itu duplikat, bukan penugasan kedua. Ini keunikan, bukan batas.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'invigilation_duty_cell_key'
          AND conrelid = 'public.invigilation_duty'::regclass
    ) THEN
        ALTER TABLE public.invigilation_duty
            ADD CONSTRAINT invigilation_duty_cell_key
            UNIQUE (school_id, exam_date, period_id, room_id, teacher_id);
    END IF;
END $$;

-- ── 2. batas "paling banyak dua", ditegakkan database ──────────────────────

CREATE OR REPLACE FUNCTION public.invigilation_duty_seat_cap()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    room_count INTEGER;
    teacher_count INTEGER;
BEGIN
    -- Serialkan insert untuk sesi yang sama supaya dua POST bersamaan tidak
    -- sama-sama melihat angka lama. Kunci baris periode, bukan baris duty.
    PERFORM 1 FROM public.exam_period WHERE id = NEW.period_id FOR UPDATE;

    SELECT COUNT(*) INTO room_count
      FROM public.invigilation_duty
     WHERE school_id = NEW.school_id
       AND exam_date = NEW.exam_date
       AND period_id = NEW.period_id
       AND room_id = NEW.room_id
       AND id <> COALESCE(NEW.id, '00000000-0000-0000-0000-000000000000'::uuid);

    IF room_count >= 2 THEN
        RAISE EXCEPTION 'invigilation_room_full'
            USING ERRCODE = '23505', HINT = 'room_full';
    END IF;

    SELECT COUNT(*) INTO teacher_count
      FROM public.invigilation_duty
     WHERE school_id = NEW.school_id
       AND exam_date = NEW.exam_date
       AND period_id = NEW.period_id
       AND teacher_id = NEW.teacher_id
       AND id <> COALESCE(NEW.id, '00000000-0000-0000-0000-000000000000'::uuid);

    IF teacher_count >= 2 THEN
        RAISE EXCEPTION 'invigilation_teacher_full'
            USING ERRCODE = '23505', HINT = 'teacher_full';
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS invigilation_duty_seat_cap_trigger ON public.invigilation_duty;
CREATE TRIGGER invigilation_duty_seat_cap_trigger
    BEFORE INSERT OR UPDATE ON public.invigilation_duty
    FOR EACH ROW EXECUTE FUNCTION public.invigilation_duty_seat_cap();

-- ── 3. RLS tetap sama: policy 059 tidak disentuh oleh migrasi ini ───────────
-- Tabel `invigilation_duty` sudah mengaktifkan RLS dan sudah punya policy baca
-- sekolah/guru-sendiri serta policy tulis penyusun jadwal. Batas jumlah bukan
-- urusan RLS, jadi tidak ada policy baru di sini.
