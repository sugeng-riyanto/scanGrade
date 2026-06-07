# ScanGrade — Tracking Progress

**Last Updated:** 1 July 2026

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
