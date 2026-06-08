# ScanGrade — Execution Plan

## Fase 1: Foundation ✅ (Selesai)

| Task | Status | Notes |
|------|--------|-------|
| Flask app factory + blueprints | ✅ | `app/__init__.py` |
| Supabase client (service + anon) | ✅ | `app/utils/auth.py` |
| Auth routes (login, register, logout) | ✅ | `app/routes/auth.py` |
| Role decorators | ✅ | `guru_required`, `murid_required`, dll |
| Base template + sidebar navigation | ✅ | `app/templates/base.html` |
| Schema + migrations SQL | ✅ | `supabase/_COMPLETE_SETUP.sql` |

## Fase 2: Exam Builder ✅ (Selesai)

| Task | Status | Notes |
|------|--------|-------|
| Exam form (create + edit) | ✅ | `app/templates/teacher/exam_form.html` |
| MCQ + Essay question management | ✅ | Tambah/hapus soal via Alpine |
| PDF upload + conversion | ✅ | PyMuPDF → page images |
| Answer key + bobot nilai | ✅ | JSONB per-soal |
| Anti-cheat settings | ✅ | 11 kolom di exams table |
| Randomize questions/options | ✅ | Fisher-Yates shuffle di client |

## Fase 3: Exam Tools ✅ (Selesai)

| Task | Status | Notes |
|------|--------|-------|
| Drawing tools (pen, line, eraser, text) | ✅ | Canvas 2D |
| Ruler 30cm (CIE/IB standard) | ✅ | Transparent, fixed scale, drag/rotate on surface |
| Protractor 0-180° | ✅ | 1° accuracy, angle display live |
| Set square 10cm (right triangle) | ✅ | 45°, scale di kedua sisi |
| Compass | ✅ | Klik=pusat, drag=radius |
| Scientific calculator | ✅ | trig, log, memory, DEG/RAD |

## Fase 4: Student Exam ✅ (Selesai)

| Task | Status | Notes |
|------|--------|-------|
| Offline-first (localStorage + sync) | ✅ | `take_exam.html` |
| Countdown timer + auto-submit | ✅ | |
| Anti-cheat (tab switch, graduated penalty) | ✅ | visibilitychange detection |
| Watermark overlay | ✅ | 24 stamp, rotated, 7% opacity |
| MCQ options + canvas per soal | ✅ | |
| Essay canvas + text boxes | ✅ | Multi-font, orientation |

## Fase 5: Grading ✅ (Selesai)

| Task | Status | Notes |
|------|--------|-------|
| Teacher grading page | ✅ | `grade_detail.html` |
| Canvas overlay di atas PDF | ✅ | Student + teacher layers |
| Teacher drawing tools | ✅ | Pen, eraser, text, ruler, protractor, triangle |
| Score + comment per question | ✅ | |
| Publish nilai | ✅ | Per-exam atau per-submission |
| Retraction requests | ✅ | Approve/reject |

## Fase 6: Results & Analytics ✅ (Selesai)

| Task | Status | Notes |
|------|--------|-------|
| Results table + filter per exam | ✅ | `teacher/results.html` |
| Export XLSX | ✅ | openpyxl |
| Export PDF (summary) | ✅ | ReportLab |
| Export bubble sheet (LJK) | ✅ | `generate_answer_sheet.html` |
| Analytics dashboard | ✅ | Chart.js (distribusi, per-band) |

## Fase 7: Database & RLS ✅ (Selesai)

| Task | Status | Notes |
|------|--------|-------|
| All tables created | ✅ | schools, profiles, exams, submissions, dll |
| Missing columns added | ✅ | anti_cheat, allow_calculator, question_weights, audio, canvas |
| RLS policies per role | ✅ | super_admin, admin_sekolah, guru, murid |
| Storage buckets | ✅ | exam-pdfs, student-answers |
| Triggers (auto-profile, role change) | ✅ | |

## Fase 8: Subscription & Payment ✅ (Selesai)

| Task | Status | Notes |
|------|--------|-------|
| Midtrans integration (Snap API) | ✅ | Sandbox/production toggle |
| Subscription plans CRUD | ✅ | Super admin manages 10 plans |
| Pricing model toggle (flat/scaled) | ✅ | By student count with tiers |
| Activation codes (auto + manual) | ✅ | Generated on payment success or cash |
| Super admin code management | ✅ | Generate by NPSN, regenerate, activate cash |
| Admin sekolah redeem code | ✅ | Hidden toggle, input + validate |
| Read-only mode on expiry | ✅ | `@subscription_write_required` decorator |
| Midtrans webhook handler | ✅ | Stores VA numbers, payment details |
| Transaction status API | ✅ | Real-time check from Midtrans |
| Admin fee passed to customer | ✅ | Configurable flat + percentage |
| Payment status page (3 states) | ✅ | Pending/success/failure with auto-poll |
| NPSN data reset | ✅ | Super admin deletes all school data by NPSN |

## Fase 9: Role Refactor & Audit ✅ (Selesai)

| Task | Status | Notes |
|------|--------|-------|
| Classes/subjects CRUD centralized to admin_sekolah | ✅ | Teachers view-only assigned data |
| Student exam filter by class_id | ✅ | Exams filtered by student's class |
| Duplicate protection on create | ✅ | Same name check within school |
| Created_by tracking | ✅ | Records who created each class/subject |
| GET exemption in subscription decorator | ✅ | Read-only mode allows GET |
| Fixed admin_sekolah redirect URL | ✅ | `/admin/dashboard` → `/admin-sekolah/dashboard` |
| Fixed missing imports | ✅ | `json`, `current_app`, `max_attempts` |
| Embedded Snap payment widget | ✅ | `snap.embed()` no popup |

## Fase 10: AI Essay Grading ✅ (Selesai)

| Task | Status | Notes |
|------|--------|-------|
| AI service with 4 providers | ✅ | Gemini, OpenAI, DeepSeek, Groq |
| Teacher API key CRUD | ✅ | Add, activate, test, delete keys |
| Tutorial for getting free API keys | ✅ | Step-by-step for each provider |
| Editable prompt template | ✅ | Variables: question, answer, max_score, rubric |
| Esai Teks question type | ✅ | New type in exam builder dropdown |
| Student textarea for essay answers | ✅ | Large textarea with char counter |
| AI grading button in teacher UI | ✅ | Auto-fills score + feedback per essay |
| API endpoint ai-suggest | ✅ | Returns score + feedback |
| API endpoint test-key | ✅ | Validates key connection |
| Database tables | ✅ | teacher_ai_keys, teacher_ai_settings, ai_grading_logs |

---

## Milestones

| Milestone | Target | Status |
|-----------|--------|--------|
| M1: Auth + Base template | Week 1 | ✅ |
| M2: Exam builder + PDF upload | Week 2 | ✅ |
| M3: Student exam + offline-first | Week 3 | ✅ |
| M4: Tools (ruler, protractor, triangle, compass) | Week 4 | ✅ |
| M5: Teacher grading + calculator | Week 5 | ✅ |
| M6: Analytics + export | Week 6 | ✅ |
| M7: Multi-school + RLS | Week 7 | ✅ |
| M8: Final integration + testing | Week 8 | ✅ Complete |
| M9: Subscription & Payment (Midtrans) | Week 9 | ✅ Complete |
| M10: Role refactor & audit fixes | Week 10 | ✅ Complete |
| M11: AI Essay Grading | Week 11 | ✅ Complete |
| M12: RLS Security (Stage 1) | Week 12 | ✅ Complete |
| M13: Error Handling + Sentry (Stage 2) | Week 12 | ✅ Complete |
| M14: Bulk Import + Tier Enforcement + Pricing (Stage 3-5) | Week 12 | ✅ Complete |
| M15: OMR Security + API + DevOps | Week 12 | ✅ Complete |

---

## Completed Stages

### Stage 1 — RLS Security
- `@require_school_access` decorator applied to 25+ routes
- RLS migration for teacher_ai_keys, invoices, payment_transactions, etc.
- Docs: `SECURITY_AUDIT.md`, `SECURITY_RLS_MATRIX.md`, `SECURITY_CHECKLIST.md`

### Stage 2 — Error Handling & Sentry
- `sentry_sdk` integration in app factory with FlaskIntegration
- Custom exception classes (`FileTooLargeError`, `AIProcessingError`, etc.)
- Structured JSON logging via `app/utils/logger.py`
- Centralized error handlers for all HTTP error codes
- Response helpers (`success_response`, `error_response`)

### Stage 3 — Bulk CSV Import
- CSV validation + batch import with duplicate NISN detection
- Drag-drop upload UI with progress + error detail

### Stage 4 — Usage Tier Enforcement
- `@require_subscription(feature)` decorator
- Tier limits: trial (5 exams/yr), basic (10/yr), pro (unlimited), enterprise
- Applied to exam creation route

### Stage 5 — Pricing & Landing Pages
- Pricing page with 3-tier comparison + FAQ
- Landing page demo request form with AJAX submission
- `/api/demo-request` endpoint backed by audit_logs

### Stage 6 — OMR Security + API + DevOps
- OMR preprocessing pipeline (deskew, CLAHE, threshold, denoise)
- File upload security (extension, MIME, EXIF, UUID)
- Confidence scoring with `needs_review` flag
- `POST /api/students/import` — pandas CSV bulk import
- `GET /api/exams/<exam_id>/report` — stats + Excel export
- Flask-Limiter (auth: 5/m, OMR: 20/m, API: 100/m)
- Sentry 100% errors / 10% traces
- `deploy/scangrade.service` + `deploy/deploy.sh`
- Demo seed: JSONB double-encode fix, FK-safe reset
