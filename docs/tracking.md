# ScanGrade — Tracking Progress

**Last Updated:** 6 June 2026

---

## Overall Progress

```
█████████████████████████░  95%
```

| Area | Progress | Status |
|------|----------|--------|
| Backend (Flask routes) | 95% | ✅ Mostly stable |
| Frontend (templates + JS) | 90% | ✅ Mostly stable |
| Database (Supabase schema) | 100% | ✅ Complete |
| RLS (Row Level Security) | 100% | ✅ Complete |
| Auth & Roles | 95% | ✅ Working |
| Exam Builder | 100% | ✅ Complete |
| Student Exam | 90% | ✅ Working |
| Drawing Tools | 95% | ✅ Stable |
| Measurement Tools | 95% | ✅ Stable |
| Scientific Calculator | 95% | ✅ Stable |
| Grading | 90% | ✅ Working |
| Analytics | 85% | ✅ Working |
| Export | 85% | ✅ Working |
| Anti-Cheat | 90% | ✅ Working |
| Multi-School | 80% | ⚠️ Needs routes |
| OMR Scanning | 70% | ⚠️ Camera works, full OMR needs tuning |

---

## Known Issues

| Issue | Priority | Status |
|-------|----------|--------|
| Export XLSX/PDF tidak include canvas overlay | Medium | ✅ Done |
| Student results page timezone pakai local browser | Medium | ✅ Done |
| Admin school settings page non-fungsional | Medium | ✅ Done |
| Export PDF gunakan student_id bukan student_name | Low | ✅ Done (already fixed in rewrite) |
| Randomize questions perlu server-side mapping | Low | ✅ Done — answers keyed by origIdx |

---

## Changelog

### 10 June 2026
- feat: new `/super-admin/` slug with dedicated dashboard
- feat: schools, users, exams, audit log views (cross-school)
- docs: RBAC.md updated with super admin routes + flow diagram

### 10 June 2026
- Fixed critical: missing `render_template` import in `__init__.py` (landing page was broken)
- Fixed error: added `xhtml2pdf` to requirements.txt
- Fixed weakness: exam submit now checks is_published+active status + duplicate submission
- Fixed weakness: `_gen_password` now uses `secrets.choice` instead of `random.choices`
- Added CSRF utility (`app/utils/csrf.py`) + registered as Jinja global

### 9 June 2026
- Polish UI login/register: password visibility toggle, loading states, password strength meter, role selector, landing page, remember email
- Landing page at `/` untuk pengguna yang belum login

### 8 June 2026
- #4 Export PDF student_name: already fixed in prior export_service rewrite
- #5 Randomize questions: answers now keyed by `origIdx` so server grading matches answer_key correctly
- Admin school settings page: Alpine.js form with JSON endpoint + real-time stats

### 10 June 2026
- Auth: NISN login for students + NIP login for teachers
- Auth: custom email domain per school (`email_domain` di profil sekolah)
- Auth: auto-generate email from full name (budi.santoso@smp1.sch.id)
- Auth: NISN/NIP lookup via `get_user_by_id()` (efficient)
- CRUD: admin sekolah full CRUD for teachers & students with search/sort
- CRUD: bulk reset password + bulk delete (teachers & students)
- CRUD: flatten data backend so template field names match
- CRUD: fix all `profiles.email` references (column doesn't exist)
- CRUD: fetch auth emails via `list_users()` for display
- CRUD: edit routes now return redirect (not JSON) so page reloads
- School: logo upload, email domain setting in profile page
- Import: download XLSX template with auto-generated emails + passwords
- Import: informative import page with per-column descriptions
- Tools: all fixed (ruler, protractor, triangle, compass) — stable
- Calculator: fixed scope bug (teacher page)
- Export: XLSX/PDF now includes per-question answers + canvas drawings
- Docs: RBAC.md — full role permissions table + interaction flows
- Demos: manage.py for seed/reset demo data, separate `.env.demo`
- Demo settings: super admin can show/hide per role (toggle in dashboard)
- Super admin: new `/super-admin/` slug with dedicated dashboard
- Security: CSRF auto-inject, PDF validation, Redis rate limiter
- Audit: all critical findings fixed (45 issues total)
