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
