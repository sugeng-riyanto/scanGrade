# ScanGrade — Tracking Progress

**Last Updated:** 7 June 2026

---

## Overall Progress

```
██████████████████████████  99%
```

| Area | Progress | Status |
|------|----------|--------|
| Backend (Flask routes) | 98% | ✅ Stable |
| Frontend (templates + JS) | 95% | ✅ Stable |
| Database (Supabase schema) | 100% | ✅ Complete |
| RLS (Row Level Security) | 100% | ✅ Complete |
| Auth & Roles | 98% | ✅ Stable |
| Exam Builder | 100% | ✅ Complete |
| Student Exam | 95% | ✅ Stable |
| Drawing Tools | 100% | ✅ Stable |
| Measurement Tools | 100% | ✅ Stable |
| Scientific Calculator | 100% | ✅ Stable |
| Grading | 95% | ✅ Stable |
| Analytics | 90% | ✅ Working |
| Export | 95% | ✅ Stable |
| Anti-Cheat | 95% | ✅ Stable |
| Subscription & Payment | 95% | ✅ Working |
| AI Essay Grading | 90% | ✅ Service + UI + 11 Prompts |
| Multi-School | 85% | ⚠️ Needs routes |
| OMR Scanning | 70% | ⚠️ Camera works |

---

## Known Issues

| Issue | Priority | Status |
|-------|----------|--------|
| Exam access codes belum di-enforce | Low | 🔴 Open |
| Multi-worker rate limiter (non-Redis) | Low | 🟡 Memory only |
| OMR tuning | Low | 🟡 Works basic |

---

## Changelog

### 7 July 2026 — Essay Text Display & Final Verification
- **Essay text answer display**: Student's paragraph answer now visible in result detail page and PDF download
- **DB verification**: 17 tables confirmed (`schools` to `ai_grading_logs`)
- **API verification**: 12 endpoints confirmed (violation, scan, sync, grade, payment, AI)
- **Blueprint verification**: All 10 blueprints registered (auth, teacher, student, admin, super_admin, etc.)
- **Anti-cheat**: Graduated penalty, auto-submit, rate limiting verified intact
- **Class/subject CRUD**: Admin sekolah — full CRUD. Teacher — read-only. Student — exam only.

### 12 July 2026 — Invoice System & Final Polish
- **Invoice system**: Auto-generate invoices on payment/cash activation, invoice page with PDF download
- **Invoice PDF**: Professional format with school info, plan details, payment method, activation code
- **Invoice watermark**: "ScanGrade" centered light watermark (xhtml2pdf compatible)
- **Invoice footer**: Verification message + print date
- **Activation code format**: `SG-XXXX-XXXX-XXXX` (12 chars in 3 groups)
- **Admin tutorial**: Updated with Invoice & Billing step
- **Bug fix**: `_create_user()` not setting `school_id` on profiles (parameter vs dict mismatch)

### 10 July 2026 — Demo Data Refresh & Relationship Audit
- **Demo data refreshed**: 3 schools (SMP/SMA/SMK), all synchronized — classes, subjects, teachers, students, assignments, school years, subscriptions
- **School_id on exams**: Exam creation now includes `school_id` for proper multi-school isolation
- **Admin sekolah access**: Grading center & results now support admin_sekolah (by school_id fallback)
- **Student exam filter**: Dashboard & exam list now filter by school_id (security fix)
- **Teacher students filter**: Student list now filtered by school_id (security fix)
- **Demo page**: Updated with complete user list per school, copy-to-clipboard on emails

### 8 July 2026 — Role Simulation & Bugfix Audit
- **Admin sekolah bugs fixed**:
  - `classes.html`: `t.name` → `t.full_name` (profiles table uses `full_name`)
  - `classes.html`: `school_years` → `years` variable mismatch
  - `promote route`: Added `student_count` and `school_year_name` to class objects
  - `classes route`: Added `wali_kelas_id` and `student_count` to class dicts
- **Teacher audit**: All 15 templates verified, 0 syntax errors, 0 variable mismatches
- **Student audit**: All 6 templates verified, routes pass all required variables
- **Flask verification**: 173 routes registered, app starts without errors
- **Overall**: Core features stable, all role-based CRUD verified

### 6 July 2026 — Critical Bugfixes & UI Modernization
- **MCQ answer "True" fix**: Canvas drawing no longer overwrites MCQ answer with boolean `true` — only letter answers (A/B/C/D/E) are stored for MCQ
- **Essay text auto-save**: Added `onAnswerInput()` function so textarea content triggers draft save
- **Draft restore fix**: `loadDraft()` now restores `answersText` for essay_text questions
- **Violation count safety**: Query wrapped in try/except to prevent submit crash
- **Exam page UI**: Updated top bar, agreement modal, sidebar — primary color scheme, gradient buttons
- **Student dashboard UI**: Gradient stat cards, hover animations, modern table
- **Student exam list UI**: Card hover + shadow + gradient buttons
- **Student results UI**: Status icons, filter toggle, modern table
- **Progress**: 99% complete, all core features stable and verified

### 4 July 2026 — UI/UX Modernization & Landing Page Refresh
- **Landing page refresh**: 8 feature cards (3 tipe soal, AI grading, anti-cheat, alat ukur, analitik, multi-sekolah, pembayaran, kalkulator), modern CTA section
- **Auth pages UI**: Decorative background blur elements, deeper card shadows, hover animation on buttons, smoother transitions
- **Tutorial pages UI**: Added decorative gradient circles, consistent styling across guru/murid/admin tutorials
- **Copy-to-clipboard**: Click-to-copy icon on all demo emails with toast notification "✅ Tersalin!"
- **Fake NPSN**: All demo NPSN replaced with dummy numbers (99887711/22/33)
- **Progress**: 99% complete, core features stable

### 3 July 2026 — Demo Page Redesign & Final Polish
- **Demo page redesign**: Modern card UI with gradient icons, hover effects, shadow animations
- **Click-to-copy**: Copy icon on all demo emails (not passwords) with toast notification
- **Per-tutorial toggle**: Super admin can show/hide each tutorial button individually
- **Fake NPSN**: All demo NPSN replaced with dummy numbers (99887711/22/33)
- **Role verification**: All decorators correct (super_admin, admin_sekolah, guru, murid)
- **CRUD verification**: Admin sekolah — full CRUD classes/subjects. Teacher — read-only. Student — exam only.
- **Blueprint registration**: All 10 blueprints registered correctly
- **DB schema**: 17 tables verified (all CREATE TABLE IF NOT EXISTS)

### 1 July 2026 — Admin Tutorial & CRUD Verification
- **Admin tutorial page**: `/tutorial/admin-sekolah` — 5 steps covering profile, classes, subjects, import, subscriptions, teacher assignments
- **Landing page**: Added "Tutorial Admin" button alongside Guru & Siswa
- **CRUD verified**: Admin sekolah — full CRUD classes & subjects. Teacher — read-only view. Student — exam/results only.
- **Duplicate routes**: None found. All endpoints unique.
- **Auth redirects**: admin_sekolah → `/admin-sekolah/dashboard`, guru/murid → `/auth/login-user`

### 30 June 2026 — Final Audit & Demo Data Refresh
- **Database schema verified**: All columns exist (`active_prompt_id`, `prompts`, `base_url`, `model_name`)
- **Auth redirect fixed**: admin_sekolah → `/admin-sekolah/dashboard` (not `/admin/dashboard`)
- **GET exemption**: `@subscription_write_required` now allows GET (read-only mode)
- **Student class filter**: Exams filtered by student's class_id
- **Demo data updated**: 3 schools (SMP, SMA, SMK) with different NPSN, teacher assignments, subscriptions
- **Demo page**: Complete user list per school with login buttons
- **Tutorial pages**: Updated with AI grading, essay text, 3 question types
- **Anti-cheat verified**: Graduated penalty, auto-submit, rate limiting — all intact

### 28 June 2026 — Custom Provider & Subject Prompts
- **Custom AI provider**: Any OpenAI-compatible API with custom base URL and model name
- **11 subject-specific prompts**: IPA/Sains, Matematika, Bahasa, IPS, ICT, Agama, PJOK, IoT + Default/Ketat/Ringan
- **Multiple prompt tabs**: Tab UI with add/remove, radio selector for active prompt
- **Auto-upgrade legacy data**: Old teacher settings auto-upgraded to 11 prompts
- **API isolation**: AI keys per-teacher account, never shared across accounts

### 25 June 2026 — AI Essay Grading System
- **AI service**: 4 provider support (Gemini, OpenAI, DeepSeek, Groq) + custom provider with base URL
- **Teacher AI Settings**: `/teacher/ai-settings` — CRUD API keys, test button, tutorial
- **11 subject-specific prompts**: IPA/Sains, Matematika, Bahasa, IPS/Sosial, ICT/Coding, Agama, PJOK, IoT + Default/Ketat/Ringan
- **Multiple prompt templates**: Tabs UI, add/remove/switch, save all, reset to default
- **Custom AI provider**: Any OpenAI-compatible API (Claude, Mistral, Together, etc.)
- **Prompt editor**: Editable per-subject grading prompt with variables
- **Esai Teks (Paragraph)**: New question type in exam builder
- **Student textarea**: Large textarea for paragraph answers
- **AI grading button**: "Koreksi AI" per soal in teacher grading page
- **Anti-cheat**: Verified intact after all changes

### 20 June 2026 — Role Refactor & Audit Fixes
- **Centralized CRUD**: Only admin_sekolah manages classes & subjects. Teachers view their assigned classes/subjects only.
- **Student exam filter**: Exam list & submission now filtered by student's `class_id` matching exam's `class_ids`.
- **Duplicate protection**: Classes/subjects checked for duplicate names within same school before insert.
- **Created by tracking**: `created_by` column on classes & subjects tracks who created the record.
- **GET exemption**: `@subscription_write_required` now allows GET requests (read-only when expired).
- **Admin sekolah redirect**: Fixed from `/admin/dashboard` → `/admin-sekolah/dashboard`.
- **Critical bugfixes**: Missing `import json`, `import current_app`, `max_attempts` undefined in exam edit.
- **Embedded Snap widget**: Payment page uses `snap.embed()` instead of popup, close button manual.

### 15 June 2026 — Subscription & Payment System
- **Midtrans integration**: Snap API for payment processing, sandbox/production toggle
- **Subscription plans**: 10 configurable plans (1mo–lifetime) with CRUD by super admin
- **Pricing models**: Flat (fixed per school) or Scaled (based on student count) toggleable by super admin
- **Activation codes**: Auto-generated on successful Midtrans payment or manual cash activation
- **Activation code management**: Super admin can generate by NPSN, regenerate, activate cash
- **Admin sekolah redeem**: Hidden toggle section to enter activation code → activate subscription
- **Activation code hidden by default**: Show/hide toggle with eye icon, copy on reveal
- **Read-only mode on expiry**: Teachers/students can login & view history but cannot create/update/delete
- **Subscription decorator**: `@subscription_write_required` blocks write routes when expired
- **Midtrans webhook**: Payment notification handler stores VA numbers, payment details
- **Transaction status API**: `/api/transaction/status` checks Midtrans directly for real-time status
- **Admin fee**: Configurable flat fee + percentage passed to customer (itemized in Snap)
- **Embedded Snap widget**: `snap.embed()` renders payment directly on page, no popup/new tab
- **Payment status page**: 3 states (pending/success/failure) with auto-polling
- **NPSN data reset**: Super admin can delete all data for a school by NPSN
- **Registration requests**: Full CRUD (list, detail, approve, reject, delete)
- **Trial settings**: Configurable trial duration by super admin
- **Bugfixes**: Handle None plan_id in cash activation, None subscription_plans join in templates

### 13 June 2026 — Penalty & PDF Polish
- **Penalty fix**: `calculate_graduated_penalty` now checks `anti_cheat_enabled is False` (not `not get()`), so `None` (missing column) defaults to enabled
- **Timestamp tolerance**: 300s → 900s to handle client clock drift
- **Logging**: Added warning logs in `validate_violation_log` for rejected violations
- **PDF header**: Removed blue border line from header/footer, kept `border-radius: 10px`

### 10 June 2026 — Final Polish
- **PDF Result**: Logo + nama sekolah (36pt) + alamat (20pt) center, info lines per field
- **Penalty**: Muncul di dashboard murid, results list, dan detail
- **MCQ answer overwrite**: Canvas drawing tidak timpa jawaban A/B/C/D
- **Auto-submit**: Cek `submitted` flag sebelum submit (cegah double submit)
- **Score 0.0**: Tidak jadi `None` di override_score
- **Recalculate**: Override score sekarang panggil `_recalculate_scores`
- **JSON parse**: `answers` & `teacher_feedback` di-parse otomatis di route
- **Anti-cheat default ON**: Form guru proper read DB value
- **Reset demo data**: Manage.py `reset-data` — user tetap, data dihapus
- **Teacher feedback string fix**: Parse JSON di grade_detail route

### 7 June 2026 — Rate Limiter Tuning & WhatsApp Badge Fix
- **Rate limiter**: Increased register limit from 3/3600s to 10/600s; rate-limited non-JSON requests now return HTML page instead of raw JSON
- **WhatsApp badge**: Moved from `scripts` block to `content_noauth` block in register.html (fix: badge not appearing); replaced Alpine.js x-cloak with pure JS; placed only on `/auth/register` page via base.html (then reverted to register-only); Sembunyikan/Tutup now use simple DOM remove with no persistence (badge always reappears on revisit)

### 10 June 2026 — Feature Complete
- Auth: NISN/NIP login, custom email domain, auto-generate email
- CRUD: Full search/sort, bulk reset/delete, email display from auth
- School: Logo upload, email domain setting
- Import: XLSX template with auto-generated emails + passwords
- Export: XLSX/PDF with per-question answers + canvas drawings
- Tools: Ruler 30cm, protractor 180°, set square 10cm, compass — stable
- Super admin: `/super-admin/` slug + dedicated dashboard
- Security: CSRF, PDF validation, Redis rate limiter
- Demo: `manage.py` seed/reset, demo settings toggle per role
- Docs: `RBAC.md`, `PRD.md`, `plan.md`, `tracking.md`
