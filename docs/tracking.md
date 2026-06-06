# ScanGrade — Tracking Progress

**Last Updated:** 6 June 2026

---

## Overall Progress

```
████████████████████████░░  90%
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
| Student results page timezone pakai local browser | Medium | 🔴 Open |
| Admin school settings page non-fungsional | Medium | 🔴 Open |
| Export PDF gunakan student_id bukan student_name | Low | 🔴 Open |
| Randomize questions perlu server-side mapping | Low | 🟡 Client-side only |

---

## Changelog

### 6 June 2026
- Ruler 30cm CIE/IB: transparent, fixed scale, drag/rotate handle di permukaan
- Protractor 0-180°: 1° accuracy, arc path (bukan full circle), angle display live
- Set square 10cm: transparent, cm scale di kedua sisi, zoom −/+
- Scientific calculator teacher: fixed scope bug (gradeApp vs gradeQuestion)
- Randomize questions/options: implemented Fisher-Yates shuffle
- Complete Supabase setup SQL with proper RLS policies (public schema)
- All migrations combined into `_COMPLETE_SETUP.sql`
- Fixed ruler resize crash (undefined `newCm` variable)
- Fixed calculator template extra quotes
