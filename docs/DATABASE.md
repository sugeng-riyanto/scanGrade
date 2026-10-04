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
| duration_minutes | INT | **0 (or NULL) = unlimited** — see below |
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

**`duration_minutes` of 0 is *Tak terbatas / Unlimited*** — the sentinel the teacher
form offers by that name. Nothing enforces an end for such a paper unless
`auto_submit_on_window_end` turns `end_at` into one, which is
`app/utils/exam_window.py::deadline()`. The same module's `duration_facts()` is the
**only** thing that decides how the value is *written* (`{"unlimited": bool,
"minutes": int}`), and every page that shows a duration reads it: the exam list, the
student dashboard, the builder's form (which selects the `Unlimited` option for a
stored 0 or NULL, and still opens a *new* paper on the form's default hour) and the
exam paper (which is handed the branch as `durationUnlimited` rather than
deciding in JavaScript). A page that derives its own fallback — `duration_minutes or
60` — turns an open-ended paper into an hour, and `tests/unit/test_duration_rule.py`
fails if one comes back.

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

**One row per `(student_id, exam_id)`, and that is the whole answer-isolation
boundary.** `submissions_student_exam_unique` (013) is unique for *every* status,
not only a live attempt, so a pupil can never own two rows for one exam and one
pupil's write can never land in another's row. All of a sitting's answers —
including the structured ones a matching, drag-and-drop or complex multiple-choice
question writes (`{"3": {"pairs": [...]}}`, `{"4": {"order": [...]}}`) — live in
that one row's `answers` JSONB, so there is no per-question row that could be
misfiled under the wrong pupil.

Every write goes through `app/services/submission_service.py`, which reads the row
by `(exam_id, student_id)` and patches it by `id` (`open_sitting`, `finish_sitting`).
No route writes `submissions` itself; `tests/unit/test_submission_row.py` and
`tests/unit/test_answer_isolation.py` fail if one starts to. A duplicate INSERT is
not an error — `finish_sitting` catches the unique violation (`23505`) and writes
into the row that won — so two tabs, or an offline poll racing a send, produce one
recorded submission rather than a 500 or a second row.

**The exam page's own `localStorage` keys are per sitter too.** The draft, the
pending submit, the clock stamp, the away stamp, the agreement flag and the tab
claim are built by `sgLS(prefix)` as `prefix + exam.id + '__' + student_key`, not
from the exam id alone: on a shared classroom device a key without the pupil's id
would hand the next pupil the previous pupil's draft. `student_key` is the render
context the `take_exam` route passes (`g.user_id`); a legacy key with no suffix is
removed on load. `tests/unit/test_answer_isolation.py` sweeps the template for a
reintroduced bare key.

### Related Tables

- **classes**: name, school_id, grade_level, school_year_id (007), teacher_id (wali kelas), created_by
- **subjects**: name, code, school_id
- **teachers**: id (FK profiles), school_id, employee_id, subject_id
- **students**: id (FK profiles), school_id, class_id, nisn, status (active/alumni/dropped)
- **teacher_assignments**: teacher_id, class_id, subject_id, school_id, school_year (TEXT, 045), school_year_id (FK, 046), status (active/inactive)
- **school_years**: name, school_id, start/end date, is_active, status (draft/active/closed, 047) — one school year; one active at a time; a closed year is read-only
- **student_enrollment** (046): school_id, student_id, class_id, school_year_id, status (aktif/naik/tinggal_kelas/pindah/lulus), note — one row per pupil per year, UNIQUE(student_id, school_year_id)

### Tahun ajaran and where a pupil was

`school_years` (007) and `classes.school_year_id` already made a class *able* to be
one year's rombongan belajar; `student_enrollment` (046) makes a pupil's membership
in it a stored fact rather than a pointer that promotion overwrites. Read it through
`app/services/enrollment.py` — `history()` for the oldest-first series, `record()`
to write one year, `default_outcome()` for the lulus-vs-naik rule. `classes.academic_year`
(TEXT, 002) is legacy and no longer read; `classes.school_year_id` is the year.

A pupil's class also lives on `students.class_id` **and** `profiles.class_id`; on the
live project they disagree (598 of 806 `students` rows versus 305 of 821 `profiles`).
`deploy/backfill_enrollment.py` merges the two pointers, names the conflicts, and
refuses to invent a year — dry run by default, `--apply` to write.

### Closing a year (`/admin-sekolah/school-years/close`)

One screen does the three things that belong together: it closes the old year
(read-only afterwards), creates the new one as a **draft** (real, but not running),
and gives every pupil an outcome (`naik`/`tinggal_kelas`/`lulus`/`pindah`) recorded
in `student_enrollment`. `app/services/academic_year.py` holds the lifecycle
(`close_year`, `create_draft_year`, `activate_year`, `editable`) and the plan
(`plan_close` decides and never writes; `apply_close` writes and returns a per-row
report). The route records the pupils **before** it closes the year, so a failure
halfway cannot leave a closed year with nobody enrolled.

### Read-only is enforced where the request is, not in the service

A closed year is read-only at every write door. `app/decorators/year_lock.py`
exposes `@open_year_required("exam_id")` / `("submission_id")`, which resolves the
year from the resource (`academic_year.year_of_exam`, preferring
`exams.school_year_id` and falling back to the classes the paper is attached to;
`year_of_submission` for a pupil's paper) and refuses with `403` (JSON) or a flash
+ redirect (form). It sits next to the route rather than in the service because a
service cannot tell a write from a read, and locking reads would hide a pupil's own
results. It is applied to the grading, exam-edit and score-recalculation routes in
`app/routes/teacher.py`, and to promotion into a year in
`app/routes/admin_sekolah.py`. `tests/unit/test_year_lock.py` **sweeps every
mutating teacher route** and fails unless each one carries the decorator or appears
in an explicit `EXEMPT` list with a reason — so a new write route cannot quietly
skip the lock.
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

## Weighted grade components (migration 057)

- **grade_component_type**: school_id, name, sort_order, is_active, created_by,
  **default_weight** (0-100, migration 058 — the school-wide default this component
  carries; the default is a policy only when it sums to 100). Unique
  `(school_id, lower(name))`. The school's own list of grade components
  ("Tugas Kelas", "UTS", …).
- **grade_weight_config**: school_id, subject_id, school_year_id, component_id,
  weight_percent (0-100), is_active, updated_by. Unique
  `(subject_id, school_year_id, component_id)` so re-saving updates the row rather
  than stacking a second one. The `SUM(weight_percent) = 100` rule for an active
  subject/year is enforced **in the application** (`grade_weighting.save_config`),
  not by a CHECK — a CHECK cannot sum rows.
- **exams.grade_component_type_id** (nullable, `ON DELETE SET NULL`): which
  weighted component a paper is counted under. NULL means uncategorised; the paper
  is excluded from a weighted total and reported as `untagged`.
- **Effective weights** are `custom subject/year config` → else the school
  `default_weight` distribution → else the simple mean (`grade_weighting.effective_config`).

## Migrations

Migration naming: `YYYYMMDD_descriptive_name.sql`

Run order:
1. `001_enable_rls_and_policies.sql`
2. `20260608_fix_rls_policies.sql`
3. `20260608_usage_tracking.sql`
4. `_COMPLETE_SETUP.sql` (full schema — run once on new projects)
