# Database Schema

## Overview

ScanGrade uses Supabase (PostgreSQL) with 22+ tables. All data is isolated by `school_id`.

## Tables

### schools
| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK, gen_random_uuid() |
| name | TEXT | NOT NULL |
| npsn | TEXT | UNIQUE, 8-12 digit |
| address, province, city | TEXT | |
| status | TEXT | active/inactive |
| tz_offset | INT | Default 7 (WIB) |

### profiles (extends auth.users)
| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK, references auth.users |
| full_name | TEXT | |
| role | TEXT | super_admin / admin_sekolah / guru / murid |
| school_id | UUID | FK → schools.id |
| status | TEXT | active/inactive/suspended |
| nisn | TEXT | Student ID (for murid) |
| nuptk | TEXT | Teacher ID (for guru) |

### exams
| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| title | TEXT | Exam name |
| subject | TEXT | |
| school_id | UUID | FK → schools.id |
| teacher_id | UUID | FK → profiles.id |
| total_questions | INT | |
| duration_minutes | INT | |
| passing_score | INT | Default 70 |
| status | TEXT | draft/active |
| is_published | BOOLEAN | |
| question_types | JSONB | {"0": "mcq", "1": "essay_text", ...} |
| answer_key | JSONB | {"0": "A", "1": "essay", ...} |
| question_weights | JSONB | {"0": 20, ...} |
| anti_cheat_enabled | BOOLEAN | Default true |
| penalty_per_violation | INT | Default 5 |
| max_violations | INT | Default 5 |
| class_ids | JSONB | ["class-uuid", ...] |
| max_attempts | INT | Default 1 |
| start_at | TIMESTAMPTZ | Scheduled start |
| is_template | BOOLEAN | |

### submissions (student answers)
| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| exam_id | UUID | FK → exams.id |
| student_id | UUID | FK → profiles.id |
| answers | JSONB | {"0": "A", "1": "text answer", ...} |
| score | DECIMAL | MCQ score |
| final_score | DECIMAL | Score after penalty |
| penalty | DECIMAL | Total anti-cheat penalty |
| status | TEXT | draft/submitted/graded/published |
| teacher_feedback | JSONB | Per-question scores + comments |
| is_published | BOOLEAN | Grades visible to student |
| submitted_at | TIMESTAMPTZ | |

### Related Tables

- **classes**: name, school_id, grade_level
- **subjects**: name, code, school_id
- **teachers**: id (FK profiles), school_id, employee_id, subject_id
- **students**: id (FK profiles), school_id, class_id, nisn
- **teacher_assignments**: teacher_id, class_id, subject_id, school_id
- **school_years**: name, school_id, start/end date, is_active
- **violation_logs**: exam_id, user_id, violation_type, metadata
- **exam_access_codes**: exam_id, code, student_id, is_used
- **teacher_ai_keys**: teacher_id, provider, api_key (encrypted)
- **teacher_ai_settings**: teacher_id, prompt_template, prompts JSONB
- **invoices**: school_id, invoice_number, amount, status, plan_id
- **payment_transactions**: school_id, order_id, gross_amount, status
- **school_subscriptions**: school_id, plan_id, status, trial dates
- **subscription_plans**: name, duration_days, price, sort_order
- **usage_tracking**: school_id, metric, count, period
- **audit_logs**: user_id, action, entity_type, old_data, new_data

## RLS Policies

All tables have Row Level Security enabled. See `docs/SECURITY_RLS_MATRIX.md` for full matrix.

## Migrations

Migration naming: `YYYYMMDD_descriptive_name.sql`

Run order:
1. `001_enable_rls_and_policies.sql`
2. `20260608_fix_rls_policies.sql`
3. `20260608_usage_tracking.sql`
4. `_COMPLETE_SETUP.sql` (full schema — run once on new projects)
