-- Migration: Add school, classes, NISN/NIS support
-- Run this in Supabase SQL Editor (Dashboard > SQL Editor > New Query)

-- 1. Create classes table (must be before profiles alter)
CREATE TABLE IF NOT EXISTS classes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    grade INT,
    academic_year TEXT DEFAULT '2025/2026',
    teacher_id UUID,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. Create school_settings table (single-row)
CREATE TABLE IF NOT EXISTS school_settings (
    id INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    school_name TEXT,
    npsn TEXT,
    address TEXT,
    province TEXT,
    city TEXT,
    district TEXT,
    academic_year TEXT DEFAULT '2025/2026',
    principal_name TEXT,
    logo_url TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Seed school_settings single row
INSERT INTO school_settings (id, school_name) VALUES (1, 'ScanGrade School')
ON CONFLICT (id) DO NOTHING;

-- 3. Add NISN, NIS, class_id columns to profiles
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS nisn TEXT;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS nis TEXT;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS class_id UUID REFERENCES classes(id) ON DELETE SET NULL;

-- 4. Add teacher_id FK to classes
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_name = 'classes_teacher_id_fkey' AND table_name = 'classes'
    ) THEN
        ALTER TABLE classes ADD CONSTRAINT classes_teacher_id_fkey
            FOREIGN KEY (teacher_id) REFERENCES profiles(id) ON DELETE SET NULL;
    END IF;
END $$;

-- 5. Indexes
CREATE INDEX IF NOT EXISTS idx_classes_teacher ON classes(teacher_id);
CREATE INDEX IF NOT EXISTS idx_profiles_class ON profiles(class_id);
CREATE INDEX IF NOT EXISTS idx_profiles_nisn ON profiles(nisn);

-- 6. Enable RLS on new tables
ALTER TABLE classes ENABLE ROW LEVEL SECURITY;
ALTER TABLE school_settings ENABLE ROW LEVEL SECURITY;

-- 7. RLS Policies
DROP POLICY IF EXISTS "school_select_all" ON school_settings;
CREATE POLICY "school_select_all" ON school_settings FOR SELECT USING (true);
DROP POLICY IF EXISTS "school_update_service" ON school_settings;
CREATE POLICY "school_update_service" ON school_settings FOR UPDATE USING (true);
DROP POLICY IF EXISTS "school_insert_service" ON school_settings;
CREATE POLICY "school_insert_service" ON school_settings FOR INSERT WITH CHECK (true);

DROP POLICY IF EXISTS "classes_select_all" ON classes;
CREATE POLICY "classes_select_all" ON classes FOR SELECT USING (true);
DROP POLICY IF EXISTS "classes_insert_service" ON classes;
CREATE POLICY "classes_insert_service" ON classes FOR INSERT WITH CHECK (true);
DROP POLICY IF EXISTS "classes_update_service" ON classes;
CREATE POLICY "classes_update_service" ON classes FOR UPDATE USING (true);
DROP POLICY IF EXISTS "classes_delete_service" ON classes;
CREATE POLICY "classes_delete_service" ON classes FOR DELETE USING (true);

-- 8. Updated_at triggers
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS set_classes_updated_at ON classes;
CREATE TRIGGER set_classes_updated_at
    BEFORE UPDATE ON classes
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

DROP TRIGGER IF EXISTS set_school_settings_updated_at ON school_settings;
CREATE TRIGGER set_school_settings_updated_at
    BEFORE UPDATE ON school_settings
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- Done! Verify with:
-- SELECT * FROM school_settings;
-- SELECT * FROM classes;
-- SELECT id, full_name, nisn, nis, class_id, tz_offset, role FROM profiles LIMIT 5;

-- 9. Add timezone offset columns
ALTER TABLE school_settings ADD COLUMN IF NOT EXISTS tz_offset INT DEFAULT 7;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS tz_offset INT DEFAULT 7;
