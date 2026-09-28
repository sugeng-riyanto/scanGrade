-- ──────────────────────────────────────────────────────────────
-- Migration 039: policy baca berlingkup sekolah untuk principal & vice_principal
-- ──────────────────────────────────────────────────────────────
-- Jalankan lewat deploy/apply_migration.py (dry run dulu, baru --commit), atau
-- tempel di Supabase SQL Editor.
--
-- Migrasi 038 membuka *nama* kedua peran pengawas di `profiles_role_check`.
-- Migrasi ini memberi mereka policy — lapisan kedua yang di 038 sengaja ditunda.
--
-- Yang dijawab policy ini bukan "apakah backend boleh membaca", sebab backend
-- memakai service key dan service role menembus RLS sepenuhnya. Pertanyaannya
-- yang lain, dan itulah satu-satunya yang bisa dijawab RLS: **kalau kunci publik
-- yang ikut terkirim di setiap halaman dipakai tanpa sesi, apa yang terbaca?**
-- Sebelum berkas ini, jawabannya untuk kedua peran itu adalah "tidak ada apa-apa",
-- dan itu bukan karena aman melainkan karena kedua nama itu tidak muncul di satu
-- pun policy. Akibatnya satu-satunya yang menjaga kepala sekolah A dari daftar
-- murid sekolah B adalah kode Flask-nya: satu query baru yang lupa menyaring, dan
-- tidak ada lapisan kedua untuk menahannya.
--
-- Aturan yang dipegang seluruh berkas ini:
--   * **hanya baca.** `FOR SELECT` saja, tanpa INSERT/UPDATE/DELETE/ALL. Peran ini
--     ada untuk mengawasi; wewenang tulis tetap milik `admin_sekolah`, dan policy
--     tulis di sini akan menyerahkan wewenang itu lewat pintu belakang API.
--   * **hanya sekolahnya sendiri.** Setiap predikat membandingkan sekolah baris
--     dengan `public._user_school_id()` milik pemanggil. Tidak ada satu pun policy
--     di sini yang bisa membaca lintas NPSN, termasuk lewat `submissions` yang
--     harus menempuh `exam_id`.
--   * **peran yang sama dengan dekoratornya.** Kedua nama peran ditulis berdampingan
--     di setiap policy, persis seperti `school_official_required` di
--     `app/utils/auth.py` memakai `role_required(*OFFICIAL_ROLES)`. Policy yang
--     hanya menyebut salah satunya akan membuat kepala sekolah dan wakilnya
--     berbeda hak di tingkat database, padahal di tingkat route keduanya satu.
--
-- Daftar tabelnya mengikuti matriks di docs/RBAC.md dan docs/SECURITY_RLS_MATRIX.md:
-- sekolahnya, daftar orang di dalamnya (profiles, teachers, students), struktur
-- akademiknya (classes, subjects, teacher_assignments), ujian dan kertas
-- jawabannya. **`audit_logs` sengaja tidak ada di sini**: log mentah menjawab "siapa
-- melakukan apa", dan itu bukan laporan pengawasan — super admin yang membacanya.
-- `pengumuman` juga tidak, karena wewenang wakil kepala sekolah di sana adalah
-- membuat draf dan menyetujuinya (tulis), dan itu fase tersendiri yang butuh policy
-- tulisnya sendiri, bukan tambahan pada berkas baca-saja ini.
--
-- Idempoten dan tidak merusak: setiap policy di-drop lebih dulu (DROP POLICY IF
-- EXISTS), dan tidak ada satu pernyataan pun yang menghapus tabel, kolom, atau
-- baris. Dijalankan dua kali hasilnya sama.
--
-- Sengaja TANPA `BEGIN;`/`COMMIT;`: `deploy/apply_migration.py` yang memiliki
-- transaksinya (dry run = satu transaksi yang di-rollback), dan kontrol transaksi
-- di dalam berkas membuat dry run menolak berkas ini karena rollback-nya tidak
-- lagi bisa membatalkan apa pun. Semua migrasi lain di folder ini juga begitu.
--
-- `TO authenticated` ditulis eksplisit di setiap policy. Policy tanpa klausa `TO`
-- berlaku untuk `PUBLIC` — termasuk `anon`, kunci yang tidak rahasia — dan itu
-- tepat lubang yang ditutup migrasi 028. Predikatnya sendiri memang sudah menuntut
-- sesi (`_is_role` dan `_user_school_id` membaca `auth.uid()`), tetapi menyebut
-- pemanggilnya membuat niatnya terbaca, bukan disimpulkan.

-- ── 1. sekolahnya sendiri ───────────────────────────────────────────────────
-- Halaman pengawas dibuka dengan baris sekolah ini; kolom kuncinya `id`, bukan
-- `school_id`, sebab barisnya *adalah* sekolahnya.
DROP POLICY IF EXISTS "schools_select_school_official" ON schools;
CREATE POLICY "schools_select_school_official" ON schools
    FOR SELECT TO authenticated
    USING (
        (public._is_role('principal') OR public._is_role('vice_principal'))
        AND id = public._user_school_id()
    );

-- ── 2. orang-orang di dalamnya ──────────────────────────────────────────────
-- `profiles` memuat setiap peran di platform, admin sekolah dan super admin
-- termasuk. Yang membatasi peran ini pada bacaannya adalah dua syarat di bawah:
-- hanya kedua nama itu, dan hanya sekolah yang sama.
DROP POLICY IF EXISTS "profiles_select_school_official" ON profiles;
CREATE POLICY "profiles_select_school_official" ON profiles
    FOR SELECT TO authenticated
    USING (
        (public._is_role('principal') OR public._is_role('vice_principal'))
        AND school_id = public._user_school_id()
    );

DROP POLICY IF EXISTS "teachers_select_school_official" ON teachers;
CREATE POLICY "teachers_select_school_official" ON teachers
    FOR SELECT TO authenticated
    USING (
        (public._is_role('principal') OR public._is_role('vice_principal'))
        AND school_id = public._user_school_id()
    );

DROP POLICY IF EXISTS "students_select_school_official" ON students;
CREATE POLICY "students_select_school_official" ON students
    FOR SELECT TO authenticated
    USING (
        (public._is_role('principal') OR public._is_role('vice_principal'))
        AND school_id = public._user_school_id()
    );

-- ── 3. struktur akademiknya ─────────────────────────────────────────────────
DROP POLICY IF EXISTS "classes_select_school_official" ON classes;
CREATE POLICY "classes_select_school_official" ON classes
    FOR SELECT TO authenticated
    USING (
        (public._is_role('principal') OR public._is_role('vice_principal'))
        AND school_id = public._user_school_id()
    );

DROP POLICY IF EXISTS "subjects_select_school_official" ON subjects;
CREATE POLICY "subjects_select_school_official" ON subjects
    FOR SELECT TO authenticated
    USING (
        (public._is_role('principal') OR public._is_role('vice_principal'))
        AND school_id = public._user_school_id()
    );

DROP POLICY IF EXISTS "teacher_assignments_select_school_official" ON teacher_assignments;
CREATE POLICY "teacher_assignments_select_school_official" ON teacher_assignments
    FOR SELECT TO authenticated
    USING (
        (public._is_role('principal') OR public._is_role('vice_principal'))
        AND school_id = public._user_school_id()
    );

-- ── 4. ujian dan kertas jawabannya ──────────────────────────────────────────
-- `exams` punya `school_id` sendiri. Kunci jawabannya hidup di baris yang sama,
-- jadi policy ini adalah yang menjaga `answer_key` tetap di dalam sekolahnya —
-- bentuk yang sama dengan `exams_select_admin_sekolah` dan, tidak seperti
-- `exams_select_active` yang dibuang di 028, ia menuntut sesi.
DROP POLICY IF EXISTS "exams_select_school_official" ON exams;
CREATE POLICY "exams_select_school_official" ON exams
    FOR SELECT TO authenticated
    USING (
        (public._is_role('principal') OR public._is_role('vice_principal'))
        AND school_id = public._user_school_id()
    );

-- `submissions` tidak punya `school_id`; sekolahnya datang dari ujiannya. Karena
-- itu syaratnya harus menempuh `exam_id` — dan `exam_id` itu bisa datang dari
-- mana saja, termasuk permintaan yang dibuat tangan lewat PostgREST.
DROP POLICY IF EXISTS "submissions_select_school_official" ON submissions;
CREATE POLICY "submissions_select_school_official" ON submissions
    FOR SELECT TO authenticated
    USING (
        (public._is_role('principal') OR public._is_role('vice_principal'))
        AND EXISTS (SELECT 1 FROM exams e WHERE e.id = exam_id
                    AND e.school_id = public._user_school_id())
    );
