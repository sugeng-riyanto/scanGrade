-- Seed data for development
INSERT INTO profiles (id, full_name, role) VALUES
    ('00000000-0000-0000-0000-000000000001', 'Admin User', 'admin'),
    ('00000000-0000-0000-0000-000000000002', 'Teacher User', 'teacher'),
    ('00000000-0000-0000-0000-000000000003', 'Student User', 'student')
ON CONFLICT (id) DO NOTHING;
