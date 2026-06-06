-- ============================================================
-- ScanGrade — DEMO SEED DATA
-- Jalankan di Supabase SQL Editor SETELAH _COMPLETE_SETUP.sql
-- Semua password: demo123
-- ============================================================

-- 1. Super Admin
-- Dibuat via Auth API (seed.py), di sini hanya profile
INSERT INTO profiles (id, full_name, role, status) VALUES
    ('00000000-0000-0000-0000-000000000001', 'Super Admin ScanGrade', 'super_admin', 'active')
ON CONFLICT (id) DO NOTHING;

-- 2. Schools
INSERT INTO schools (id, name, npsn, address, city, province, status) VALUES
    ('10000000-0000-0000-0000-000000000001', 'SMP Negeri 1 ScanGrade', '20623248', 'Jl. Pendidikan No. 1', 'Jakarta', 'DKI Jakarta', 'active'),
    ('20000000-0000-0000-0000-000000000002', 'SMA Negeri 1 ScanGrade', '69893227', 'Jl. Merdeka No. 10', 'Jakarta', 'DKI Jakarta', 'active')
ON CONFLICT (id) DO NOTHING;

-- 3. Classes SMP
INSERT INTO classes (name, school_id, grade_level) VALUES
    ('VII-A', '10000000-0000-0000-0000-000000000001', 'VII'),
    ('VIII-A', '10000000-0000-0000-0000-000000000001', 'VIII'),
    ('IX-A', '10000000-0000-0000-0000-000000000001', 'IX')
ON CONFLICT DO NOTHING;

-- 4. Classes SMA
INSERT INTO classes (name, school_id, grade_level) VALUES
    ('X-A', '20000000-0000-0000-0000-000000000002', 'X'),
    ('XI-A', '20000000-0000-0000-0000-000000000002', 'XI'),
    ('XII-A', '20000000-0000-0000-0000-000000000002', 'XII')
ON CONFLICT DO NOTHING;

-- 5. Subjects SMP
INSERT INTO subjects (name, school_id, code) VALUES
    ('Matematika', '10000000-0000-0000-0000-000000000001', 'MTK'),
    ('IPA', '10000000-0000-0000-0000-000000000001', 'IPA'),
    ('Bahasa Indonesia', '10000000-0000-0000-0000-000000000001', 'BIN')
ON CONFLICT DO NOTHING;

-- 6. Subjects SMA
INSERT INTO subjects (name, school_id, code) VALUES
    ('Matematika', '20000000-0000-0000-0000-000000000002', 'MTK'),
    ('Fisika', '20000000-0000-0000-0000-000000000002', 'FIS'),
    ('Kimia', '20000000-0000-0000-0000-000000000002', 'KIM'),
    ('Biologi', '20000000-0000-0000-0000-000000000002', 'BIO')
ON CONFLICT DO NOTHING;

-- Note: Auth users (profiles with linked auth.users) must be created via
-- Supabase Auth API (seed.py), not via SQL INSERT.
-- Run: .venv\Scripts\python seed.py
