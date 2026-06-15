# ScanGrade — Tracking Progress

**Last Updated:** 8 June 2026

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

### 8 June 2026 — Rate Limiter Tuning & WhatsApp Badge Fix
- **Rate limiter**: Increased register limit from 3/3600s to 10/600s; rate-limited non-JSON requests now return HTML page instead of raw JSON
- **WhatsApp badge**: Moved from `scripts` block to `content_noauth` block in register.html (fix: badge not appearing); replaced Alpine.js x-cloak with pure JS; placed only on `/auth/register` page via base.html (then reverted to register-only); Sembunyikan/Tutup now use simple DOM remove with no persistence (badge always reappears on revisit)

### 8 June 2026 — RLS Security Overhaul (Stage 1)
- **Decorator**: Created `app/decorators/security.py` with `@require_school_access(table, resource_id_param, school_join)` — verifies user's `school_id` matches resource's `school_id` before route handler runs; supports direct lookup (exams, classes, subjects) and chained lookup (submissions → exam_id → exams → school_id)
- **Applied decorator** to 20+ critical routes:
  - Teacher: `exam_detail`, `preview_exam`, `publish_exam`, `upload_exam_pdf`, `toggle_status`, `toggle_visibility`, `delete_exam`, `duplicate_exam`, `grade_detail`, `override_score`, `approve_retraction`, `reject_retraction`
  - Admin sekolah: `toggle_school_year`, `delete_school_year`, `edit_class`, `delete_class`, `admin_subject_delete`, `edit_teacher`, `delete_teacher`, `reset_teacher_password`, `edit_student`, `delete_student`, `reset_student_password`, `admin_reset_user_password`, `download_invoice_pdf`
- **RLS migration**: Created `supabase/migrations/20260608_fix_rls_policies.sql` — adds `school_id` column + RLS to `teacher_ai_keys`, `teacher_ai_settings`; adds RLS to `invoices`, `payment_transactions`, `school_subscriptions`, `activation_codes`, `ai_grading_logs`, `violation_logs`
- **Docs**: Created `docs/SECURITY_AUDIT.md` (per-table audit with risk assessment), `docs/SECURITY_RLS_MATRIX.md` (policy overview matrix), `docs/SECURITY_CHECKLIST.md` (pre-merge checklist)
- **Tests**: Created `tests/test_rls_security.py` with unit tests for decorator (direct match, mismatch, chained) + API-level integration tests (mocked Supabase)

### 8 June 2026 — Error Handling & Sentry Monitoring (Stage 2)
- **Custom exceptions**: Created `app/errors.py` — 10 exception classes (`ScanGradeException`, `FileTooLargeError`, `InvalidPDFError`, `AIProcessingError`, `GradingError`, `NotFoundError`, `ForbiddenError`, `ValidationError`, `PaymentError`, `SubscriptionError`) with error_code, user_message (Bahasa Indonesia), details dict
- **Sentry integration**: Initialized `sentry_sdk` in `create_app()` with `FlaskIntegration`, 10% trace sampling; configurable via `SENTRY_DSN` + `SENTRY_ENVIRONMENT` env vars; auto-tags `app` + `version`
- **Structured logging**: Created `app/utils/logger.py` with JSON formatter (timestamp, level, message, logger, extra fields); all request logging now structured (not debug-only)
- **Error handlers**: Created `app/handlers/error_handlers.py` — centralized handlers for `ScanGradeException`, 400/401/403/404/413/429/500; all JSON responses with `success`, `error`, `message`, `timestamp`; 500 errors return user-friendly "Tim kami sedang menanganinya" in production
- **Response helpers**: Created `app/utils/responses.py` — `success_response()` and `error_response()` with consistent format
- **Sentry context**: Created `app/utils/sentry_context.py` — helpers for setting exam/student/school context
- **Route updates**: `api.py` (ai-test-key, ai-suggest raise typed exceptions); `exam.py` (upload-pdf validates file type/size, raises `FileTooLargeError`/`InvalidPDFError`)
- **Config**: Added `SENTRY_ENVIRONMENT`, `APP_VERSION` environment variables
- **Tests**: Created `tests/test_error_handling.py` — unit tests for all exception classes, response helpers, error handlers (404, 500, ScanGradeException)

### 8 June 2026 — Bulk Import + Subscription Tiers + Landing (Stage 3-5)
- **Stage 3 — CSV Bulk Import**: Created `app/services/student_import.py` (CSV validation + batch import with duplicate NISN check, class resolution, auto-email generation); `app/routes/students.py` (import page, CSV upload endpoint, template download); `app/templates/teacher/import_students.html` (drag-drop UI with progress + error details); `manage.py generate-csv` command for sample CSV
- **Stage 4 — Usage Tier Enforcement**: Created `app/services/subscription_service.py` with `TIER_LIMITS` dict (trial/basic/pro/enterprise — exams/year, AI grading, student quotas); `app/decorators/subscription.py` with `@require_subscription(feature)` decorator; applied to exam creation route in teacher.py; created `supabase/migrations/20260608_usage_tracking.sql` with `usage_tracking`, `demo_requests` tables + `tier` column on `school_subscriptions`
- **Stage 5 — Pricing & Landing Pages**: Created `app/templates/pricing.html` (3-tier comparison, feature matrix, FAQ accordion, CTA); updated `app/templates/landing.html` (add pricing link, demo request form with AJAX submission); created `app/routes/public.py` (pricing page route + `/api/demo-request` endpoint); registered new blueprints in `__init__.py`
- **Tests**: Created `tests/test_stage345.py` — CSV validation unit tests, tier limit structure tests, public page response tests, demo request endpoint tests

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

### 8 June 2026 — OMR Security + API Import/Report + DevOps + Demo Fixes
- **OMR Security**: File upload validation (extension/MIME/Pillow verify/EXIF strip); `preprocess_scan()` pipeline (deskew → CLAHE → adaptive threshold → denoise); `cv2.error` try-except wrapping; `needs_review` flag for confidence < 0.6; `opencv-python` → `opencv-python-headless`; added `python-magic`
- **API routes**: `POST /api/students/import` (pandas CSV, chunk 100, duplicate NISN skip); `GET /api/exams/<exam_id>/report` (stats mean/median/highest/lowest, `?format=excel` with Indonesian column names via openpyxl)
- **DevOps**: Sentry 100% error / 10% traces; Flask-Limiter (auth:5/m, OMR:20/m, API:100/m) with Redis/memory:// fallback; `deploy/scangrade.service` systemd template; `deploy/deploy.sh` script
- **Bug fixes**: `json.loads()` for `class_ids` handles single-quote lists; demo seed passes dicts directly for JSONB columns (no double-encode); FK-safe reset order in `_reset_demo_data()`

### 8 June 2026 — Anti-Cheat Overhaul & Penalty Fix
- **Standalone anti-cheat**: Added independent `alert()`-based anti-cheat script that works outside Alpine.js component; sends violations to `/api/violation/log` via `sendBeacon`; triggers auto-submit at max violations via Alpine's `submitExam()` + direct `sendBeacon` fallback
- **Penalty persistence**: Fixed `/api/violation/log` endpoint to save penalty to `submissions.penalty` (was logging to `violation_logs` but never updating the submission); penalties now visible in student dashboard, results, PDF, and teacher grading/results pages
- **Attempt check**: `take_exam` route now validates `max_attempts` before rendering exam page; redirects with flash error if already maxed out
- **Anti-cheat defaults**: Route-level defaults for all anti-cheat fields (handle NULL/missing columns); pre-rendered `anti_cheat_config` JSON from Python (bypasses fragile Jinja2 `tojson` on dict)

### 15 June 2026 — Final Production Setup & PWA
- **Domain & SSL**: Setup `scangrade.web.id` via Biznet DNS → Certbot SSL → HTTP→HTTPS redirect + HSTS security headers
- **PWA**: `manifest.json`, `sw.js` (network-first API, cache-first static), SVG icon, meta theme-color, apple-touch-icon
- **Mobile**: Bottom navigation per role (super_admin/admin_sekolah/guru/murid), `touch-action:none` canvas, `env(safe-area-inset-bottom)`, `100dvh` iOS fix
- **Self-host assets**: Tailwind CSS via CLI (no CDN), Font Awesome + HTMX + Chart.js → `/static/vendor/`, `crypto.randomUUID` polyfill for HTTP
- **Performance**: Workers 2 (optimal 1GB RAM), Redis shared rate limiter, pagination students/teachers, SQL indexes
- **Load test**: 600 concurrent users → avg 524ms, error 0%, timeout 0
- **QC**: Lighthouse (Performance 49, Accessibility 85, Best Practices 77, SEO 82), data isolation role check ✅
- **File Management**: New `/super-admin/file-management` page — migrate local PDFs to Supabase Storage, ZIP download per school (JSON+XLSX+TXT), delete local/storage/DB records per-exam or per-school
- **Backup VPS**: `deploy/backup.sh` → cron 03:00 → tar config files (.env, nginx, service, SSL) → upload Supabase Storage → retensi 7 hari
- **Monitoring**: `deploy/health-check.sh` → cron tiap 5 menit → auto-restart service/down → log `/var/log/scangrade-health.log`
- **Bootstrap**: `deploy/bootstrap.sh` — auto-setup from scratch (git clone → .env → venv → pip → systemd → NGINX)
- **Bugs fixed**: `import os` missing in super_admin.py, orphaned `except` block (SyntaxError), ZIP download empty (data extraction + profiles join), `violation_logs.id` UUID vs SERIAL, Supabase Storage clear_storage recursive delete, nginx.conf `server_name _` → `scangrade.web.id`
- **Rating API**: `POST /api/transaction/status` — check payment transaction status
