# ScanGrade — Tracking Progress

**Last Updated:** 10 June 2026

---

## Overall Progress

```
██████████████████████████  97%
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
