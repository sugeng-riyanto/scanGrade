# ScanGrade - AI Agent Context

## Project Overview
Sistem koreksi ujian digital dengan fitur:
- Scan lembar jawaban pilihan ganda via kamera (seperti ZipGrade)
- Koreksi esai dengan AI similarity scoring + teacher override
- Anti-cheat: deteksi tab switch dengan penalty configurable
- Analytics dashboard interaktif untuk guru
- Export hasil ke XLSX/PDF + publish ke siswa via login/WA

## Tech Stack
| Layer | Technology | Notes |
|-------|-----------|-------|
| Backend | Flask 3.x | App factory pattern, blueprints |
| Language | Python 3.10+ | Type hints wajib |
| Database | Supabase PostgreSQL | Row Level Security (RLS) |
| Auth | Supabase Auth | Email/password + JWT |
| Storage | Supabase Storage | Bucket: exam-pdfs, student-answers |
| Frontend | Tailwind CSS + HTMX | Vanilla JS, no React/Vue |
| Charts | Chart.js 4.x | Untuk analytics dashboard |
| PDF | ReportLab + pdf.js | Export laporan + viewer soal |
| Excel | openpyxl | Export hasil ujian |
| Queue | Celery + Redis | Untuk notifikasi async |
| Tunnel | ngrok | Development exposure |

## Project Structure
```
app/
├── __init__.py          # Flask app factory
├── config.py            # Dev/Prod config classes
├── routes/              # Blueprints
│   ├── auth.py          # Login/register/me
│   ├── exam.py          # CRUD ujian + PDF upload
│   ├── admin.py         # Dashboard admin
│   ├── teacher.py       # Builder + grader UI
│   ├── student.py       # Take exam + results
│   ├── api.py           # Anti-cheat endpoints
│   ├── publish.py       # Publish scores
│   └── webhook.py       # Midtrans/Fonnte callbacks
├── services/            # Business logic
├── models/              # Supabase query helpers
├── utils/               # Decorators, security, helpers
└── templates/           # Jinja2 + Tailwind
```

## Security & Best Practices
### Environment Variables
- **WAJIB**: Jangan commit file .env ke GitHub!
- Copy .env.example ke .env dan isi nilai sebenarnya

### Row Level Security (RLS) Rules
- **profiles**: User hanya bisa baca/update profil sendiri
- **exams**:
  - Guru: CRUD exam yang teacher_id = auth.uid()
  - Siswa: READ only exam dengan status='active' + punya access code
- **submissions**:
  - Guru: READ/WRITE submission untuk exam miliknya
  - Siswa: READ only submission sendiri DAN is_published=true
- **violation_logs**:
  - Backend: INSERT via service key
  - Guru: READ logs untuk exam miliknya
- **exam_access_codes**:
  - Guru: CREATE codes untuk exam miliknya
  - Siswa: READ only code miliknya yang belum used

## Anti-Cheat Implementation Rules
- **Frontend deteksi**: visibilitychange + debounce 1500ms
- **Logging**: navigator.sendBeacon untuk reliability saat tab ditutup
- **Server validation**: Cek timestamp ±5 menit, rate limit 1 log/2 detik
- **Penalty calculation**: SELALU di backend, jangan percaya frontend
- **False positive mitigation**: Ignore blur <500ms, toleransi iOS +1 violation

## Code Style
- Python: Type hints + Google docstring
- Flask blueprints dengan prefix URL
- Error handling di tiap endpoint
- Supabase client via app.extensions

## Phase 5: Guru & Murid Dashboard + UI/UX Polish

### Key Changes
- **Redesigned `base.html`**: Blue-white theme (`primary` = blue, `surface` = slate), role-based sidebar with section titles per user type (super_admin, admin_sekolah, guru, murid), integrated toast notification system (Alpine.js `$store`-like array, auto-dismiss 4s, 4 types), loading spinner on HTMX requests, smooth transitions (slide-up, fade-in, scale-in animations), mobile-first responsive with sticky topbar + bottom nav, user dropdown menu, timezone dialog, breadcrumb nav, all using `animate-*` custom keyframes.
- **Migration `010_teacher_assignments.sql`**: `teacher_assignments` junction table (teacher_id, class_id, subject_id, school_id) with UNIQUE constraint, RLS policies for each role, indexes.
- **Teacher Dashboard** (`/teacher/dashboard`): Welcome card with gradient, stat cards (total exams, active, students, avg), assigned classes & subjects list with HTMX delete, "Tambah" button toggles inline assign form (class + subject dropdowns from school data), quick actions grid, recent exams list, HTMX-powered assignment CRUD.
- **Teacher Classes Page** (`/teacher/classes`): Full assignment management page with card grid of current assignments, inline assign form, available classes reference tags, HTMX-powered add/delete.
- **Teacher Routes**: `GET /teacher/classes` (classes management page), `GET/POST /teacher/assignments` (list/create assignments), `DELETE /teacher/assignments/<id>` (delete assignment). Dashboard route updated to fetch assignments + school's classes/subjects.
- **Student Dashboard** (`/student/dashboard`): Class & subject assignment info cards (from `profiles.class_id` + teacher_assignments count), improved available exams grid, toggleable score visibility, consistent blue-white styling.
- **Student Routes**: Dashboard updated to pass `student_class` (from `profiles.class_id` → `classes`) and `subject_count` (from teacher_assignments for school).
- **All Auth Templates**: Redesigned with blue-white gradient backgrounds, `primary-*` and `surface-*` colors (register, login, login_user, activate, forgot_password, reset_password, success pages).
- **Admin Template Updates**: Dashboard + registration_requests + admin_sekolah pages updated to new theme with gradient welcome cards.

### UI/UX Features
- Toast notifications via Alpine.js (4 types: success, error, warning, info, auto-dismiss 4s)
- Loading spinner on HTMX requests (`x-data loading`)
- Smooth animations: `animate-slide-up`, `animate-fade-in`, `animate-slide-down`, `animate-scale-in`
- Consistent card padding (p-5/p-6), border-radius (rounded-xl/rounded-2xl), shadows
- Mobile bottom nav bar with role-specific icons
- Blue-white theme: `primary-50` through `primary-900` (blue), `surface-50` through `surface-900` (slate)
- Gradient welcome cards (`from-primary-600 to-primary-700`) on all dashboards

### Relevant Files
- `supabase/migrations/010_teacher_assignments.sql` — Junction table
- `app/templates/base.html` — Complete redesign
- `app/templates/teacher/dashboard.html` — Redesigned with assignments
- `app/templates/teacher/classes.html` — New full assignment management
- `app/templates/student/dashboard.html` — Redesigned with class info
- `app/templates/admin/dashboard.html` — Updated theme
- `app/templates/admin/registration_requests.html` — Updated theme
- `app/templates/admin_sekolah/dashboard.html` — Updated theme
- `app/templates/auth/*.html` — All redesigned blue-white
- `app/routes/teacher.py` — Added `/classes`, `/assignments`, dashboard updated
- `app/routes/student.py` — Dashboard fetches class info
- `app/models/supabase_queries.py` — Added `list_teacher_assignments`, `create_teacher_assignment`, `delete_teacher_assignment`

## Key Rules
- **Anti-Cheat**: Frontend `visibilitychange` + debounce 1500ms; backend validates timestamps ±5min, rate limit 1 log/2s. Penalty always computed server-side.
- **RLS**: Service key for backend operations; anon key for public reads only.
- **CORS**: Allow localhost + ngrok URL (dynamic via env).
- **No secrets in .env committed** — use `.env.example` for templates.
- **UI Theme**: Blue-white (`primary` palette) + surface (slate) with Inter font, rounded-2xl cards, gradient headers, toast notifications, mobile-first responsive.
- **Teacher Assignments**: Use `teacher_assignments` junction table for many-to-many teacher-class-subject. Show in dashboard sidebar + classes page. HTMX for CRUD.
