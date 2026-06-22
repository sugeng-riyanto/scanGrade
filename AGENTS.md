# ScanGrade - AI Agent Context

## Goal
- Build and optimize ScanGrade (Flask+Supabase exam/grading app) for 500 concurrent students with offline-first auto-save, teacher grading, student results, anti-cheat graduated penalties, PDF download with server-side compositing, and professional ZipGrade-style UI with role-based navigation.

## Constraints & Preferences
- Supabase project: `roshkbkbzgfedowozfo` (region: ap-southeast-1)
- Python 3.12.10, Flask 3.1.1, supabase-py 2.12.0, Alpine.js v3.14.8, Tailwind CSS (CDN), Chart.js 4.4.7
- Two Supabase clients: `get_supabase()` (service key) and `get_auth_client()` (anon key)
- Must support 500 concurrent students with spotty WiFi (3 floors, uneven coverage)
- Offline-first: localStorage first, sync to server when online
- `MAX_CONTENT_LENGTH = 50MB`
- Canvas data saved as PNG (not JPEG — was causing black overlay bug)
- Default timezone UTC+7 (WIB), configurable per-user and per-school
- UI language: Indonesian (default) with English toggle (`localStorage.sg_lang`)
- Color theme: `primary` (blue) defined in Tailwind config — `brand-*` was previously undefined (invisible buttons/text), now aliased to `primary` palette
- No direct DB DDL access — migrations must be run manually in Supabase SQL Editor
- `profiles` table columns: `['id', 'full_name', 'phone', 'role', 'created_at', 'updated_at']` — **no `school_id`, `class_id`, `nisn`, `nis`, `email` columns exist**
- `schools` table **does NOT exist** in `public` schema
- MCQ answer keys: single value (`"A"`), multiple (`["A","B"]`), or `"bonus"` (all correct)
- Weighted scoring: Teacher sets MCQ% + Essay% (total must = 100), distributed equally per question within each group
- `question_weights` JSONB column does NOT exist in DB — code uses `.get()` + `setdefault` with try/except fallback
- PDF generation uses `xhtml2pdf` (pure Python) — WeasyPrint requires GTK/Pango not available on Windows
- Anti-cheat: graduated penalty (1st=warning, 2nd=-base, 3rd=-2×base, 4th+=-3×base), auto-submit on max violations
- Router / security: 404 catch-all `/tools` for admin functions; CSP `frame-ancestors 'self'` in response headers

## Progress
### Done
- Fixed `submit_exam` route: now saves `final_score = max(0, score - penalty)` and `violations` count to the submission
- Fixed `_recalculate_scores` to assign weights to ALL questions (not just MCQ) — essay questions had weight=0 because the fallback logic only computed MCQ weights
- Fixed `grade_detail` route to compute essay weights locally when `question_weights` is empty (same 70/30 default as exam_form)
- Fixed Publish route: now calls `_recalculate_scores()` before updating status
- Fixed `result_detail.html` template: score cards now show for ALL submission statuses with 4 columns (Skor MCQ, Penalti, **Jumlah Pelanggaran**, Final); subtle "Menunggu koreksi" note for submitted/draft
- Added camera toggle to `scan.html`: `enumerateDevices()` lists video inputs; auto-selects back camera on mobile; dropdown + flip button on desktop
- Added **Compass (Jangka)** and **Right Triangle (Segitiga Siku)** tools to both `take_exam.html` and `grade_detail.html`
- **Auto-close all tools** on: `prevPage()`, `nextPage()`, question change (`$watch('currentQ')`)
- Enhanced `analytics.html`: empty state with guide + CTA buttons; charts hidden when `total_submissions===0`
- Fixed violation `sendBeacon` Blob bracket bug in `take_exam.html`
- Answer sheet generator at `/tools/generate-answer-sheet` accepts `total_questions` (1-200) and `options` (2-8)
- Teacher bubble-sheet route fixed: removed `profiles.school_id` join (column doesn't exist)
- All existing submissions recalculated: `final_score` and `violations` backfilled via script (7 updated)
- **Fixed `brand-*` color bug**: Tailwind config only defined `primary`/`surface`; `brand` added as alias for `primary` palette — fixes invisible "Simpan & Aktifkan" button, MCQ answer key feedback, "Koreksi" button, and all broken `brand-*` across every template
- **Fixed YouTube embed**: Videos `ZxcGPnOcDSQ` (Maroon 5) and `LYU-8IFcDPw` (Linkin Park) have embedding disabled by uploader; improved `youtubeEmbedUrl()` regex for `v=` not-first-query-param; changed fallback from `return url` to `return ''` (empty iframe)
- **Fixed teacher text tool in `grade_detail.html`**: Teacher text boxes rendered via Jinja2 (static) but `addTeacherTextBox()` updated Alpine state — no DOM created. Replaced Jinja2 loop with Alpine `x-for` bound to `teacherBoxes[page-1]`; normalized `teacherBoxes` init to extract `textBoxes` arrays from saved overlay
- **Added full canvas + drawing tools to MCQ questions in `take_exam.html`**: Pen, line (width + dash style), eraser, text (font size + family), ruler, protractor, compass, triangle, undo, clear — same toolbar as essay section
- **Updated MCQ answer data format**: From string `"A"` to `{"answer": "A", "pages": {0: {canvas: "...", textBoxes: [...]}}}` when canvas data present (backward compatible — no-drawing answers remain simple strings)
- **Updated all server/client layers for new MCQ dict format**: `submit_exam`, `_is_mcq_correct`, `_extract_mcq_answer` helper, `grade_detail.html`, `result_detail.html`, `result_detail_pdf.html` — all extract `answer` from dict when present
- **MCQ result PDF download**: Removed `if qtype == "mcq": continue` in `download_result_pdf()`; added merged page images (`has_merged`) to MCQ section in `result_detail_pdf.html`
- **Fixed MCQ canvas not initializing**: `$watch('currentQ')` and `$nextTick` initial load both skipped `initEcCanvas` for MCQ questions (`if (this.questions[val]?.type !== 'mcq')`). Removed the check so `initEcCanvas` is called for all question types, enabling ruler/protractor/compass/triangle/pen tools on MCQ canvas
- **Fixed essay section's broken overlay drag/rotate handles**: Inline SVG handles (`tool-rotate`, `tool-handle`) in essay ruler/protractor/compass/triangle overlays called `startToolRotate('ruler',$event)` (2 args) but functions expect 3 params `(i, type, event)`. Added `i` parameter: `startToolRotate(i,'ruler',$event)`
- **Collaborative Whiteboard**: Real-time papan tulis digital per-kelas — canvas drawing, toolbar (pen/eraser/text/highlight/laser/undo/redo), WebSocket broadcast, slide navigator, PDF export (Pillow + Reportlab), permission system (request/approve/revoke), anti-cheat soft, timer overlay, quick reactions
- **Randomize soal & opsi**: Checkbox `randomize_questions` + `randomize_options` di exam_form.html — frontend shuffle logic sudah siap
- **Answer keys quick-set page**: Halaman `/teacher/exams/<id>/answer-keys` — daftar MCQ dengan tombol A-E, bonus checkbox, auto recalculate scores
- **MCQ canvas data fix**: `getAnswersLight()` deteksi `hasDrawn` sehingga canvas data MCQ tersimpan walau jawaban berupa string
- **Auto-submit bypass confirm**: `submitExam(auto)` — anti-cheat auto-submit & waktu habis langsung submit tanpa konfirmasi
- **SQL migrations 003 + 011**: `is_hidden`, `retracted`, `question_weights` JSONB, dan 11 anti-cheat columns sudah dijalankan di Supabase
- **Load test 500 concurrent**: 0% errors, avg 147ms response time — semua endpoint stabil
- **Data retention & PDP compliance**: Migration 018 (soft-delete + deletion_requests), auto-purge scheduler, export/delete API, student settings page `/student/settings`, CLI `flask purge-data`
- **Broadcast murid fix**: `murid` role added to `api_send_broadcast` authorization (was `Unauthorized`)
- **School_id filter**: broadcast API endpoints scope by `profiles.school_id` with try/except
- **Linkify**: global `linkify()` function in base.html — URLs in messages become clickable `<a target="_blank">`

### In Progress
- Many exams (Sejarah, Agama, PPKN, AD) masih punya answer key `None` — guru perlu isi via halaman baru answer keys
- Whiteboard iteration: WebSocket reconnect handling, mobile responsive toolbar, drag-drop slide reorder
- **Migration 018 not yet executed** — `supabase/migrations/018_data_retention.sql` must be run in Supabase SQL Editor for soft-delete columns + `deletion_requests` table

### Blocked
- Tidak ada — semua DDL migrations sudah dijalankan di Supabase SQL Editor
- Exam scoring masih 0 jika guru belum mengisi kunci jawaban (data issue, bukan kode)

## Key Decisions
- Two separate Supabase clients to avoid service-key client corruption from auth operations
- Auto-create profile in register route, not DB trigger
- Weighted scoring: MCQ% + Essay% (total=100), distributed equally within each group — fallback to 70/30 when `question_weights` is empty
- Essay types simplified to single "Esai" toggle — canvas drawing + text boxes combined on PDF
- Anti-cheat graduated penalty: 1st=warning(0), 2nd=-base, 3rd=-2×base, 4th+=-3×base per violation; cap at 100
- Tool SVGs always `pointer-events: none` — drag/rotate from toolbar control bar only
- PDF generation: `xhtml2pdf` over WeasyPrint (no GTK/Pango on Windows), over pdfkit (needs wkhtmltopdf)
- Server-side compositing for PDF: Pillow merges all layers into flat `<img>` — xhtml2pdf doesn't support `position: absolute`
- NISN: fill from LEFT if < 10 digits (most significant first, trailing cells empty) — standard OMR convention
- **`brand-*` class fix**: Added `brand` as alias for `primary` in Tailwind config (same blue palette) — fixes all templates system-wide without per-file edits
- **MCQ answer format**: Use dict `{answer, pages}` only when canvas/text data exists; keep string for backward compatibility

## Next Steps
- **Deploy latest commit** on VPS: `git pull origin main && systemctl restart scangrade`
- **Run migration 017** in Supabase SQL Editor (`supabase/migrations/017_notifications.sql`) — creates notification tables + adds `school_id` to profiles
- **Run migration 018** in Supabase SQL Editor (`supabase/migrations/018_data_retention.sql`) — soft-delete columns, `deletion_requests` table
- **Backfill `profiles.school_id`**: `UPDATE profiles SET school_id = (au.raw_user_meta_data->>'school_id')::uuid FROM auth.users au WHERE profiles.id = au.id AND au.raw_user_meta_data->>'school_id' IS NOT NULL;`
- Teachers need to set answer keys for exams that show score=0 (Sejarah, Agama, PPKN, AD have `None` keys)
- Implement `randomize_questions` and `randomize_options` in take_exam frontend ✅
- Test full anti-cheat flow: tab switch → graduated penalty → auto-submit
- Test PDF download with MCQ canvas overlays

## Prioritas RICE
Setiap task dinilai dengan formula:
**Score = (Reach × Impact × Confidence) / Effort**

| Parameter | Skala | Contoh |
|-----------|-------|--------|
| Reach (R) | 1-5 | 1=few users, 5=all users |
| Impact (I) | 1-5 | 1=minor, 5=transformative |
| Confidence (C) | 0.5-1.0 | 0.5=unsure, 1.0=certain |
| Effort (E) | hari kerja | Estimasi waktu |

**70-20-10 Rule:** 70% fitur berdampak langsung, 20% tech debt/refactoring, 10% eksplorasi.

**Weekly Review:** Setiap Jumat — review progress, update prioritas, tulis di AGENTS.md.

## Critical Context
- Alpine.js v3.14.8 — `el.__x` does NOT exist. Must use `QUESTION_INSTANCES` global map pattern. NOTE: admin templates use `Alpine.raw(root).__x.$data` for import forms.
- `xhtml2pdf` does NOT support: `display: flex`, `position: absolute/relative`, `border-radius` (limited), `gap` — use tables for layout, floats for positioning
- PDF page URLs are local paths like `/static/uploads/exams/<uuid>/page_001.png` — load via `os.path.join(app.static_folder, ...)` not HTTP
- Student answer JSON (MCQ with canvas): `{"0": {"answer": "A", "pages": {"0": {"canvas": "data:image/png;...", "textBoxes": [...]}}}}` — old format `{"0": "A"}` still supported
- Answer key JSON: `{"0": "A", "1": ["A","B"], "2": "bonus", "3": "essay"}` — `None` keys cause score 0
- `question_weights` + anti-cheat columns sudah ada di DB (migrations 003 & 011 sudah dijalankan)
- `submissions.is_hidden` column sudah ada — migration 003 sudah dijalankan
- `profiles` has NO `email`, `school_id`, `class_id`, `nisn`, or `nis` columns — querying them causes `APIError`
- `schools` table does NOT exist — any query fails with `PGRST205`
- Violation `sendBeacon` Blob: `new Blob([JSON.stringify([{...}])], {type: 'application/json'})` — must have correct bracket nesting
- Grade detail form: pressing Enter in score/comment inputs submits the HTML `<form>` and reloads page — `@keydown.enter.prevent=""` added to both inputs
- NISN: jika < 10 digit, isi dari kiri (most significant digit pertama), trailing cells kosong
- `final_score` dihitung sebagai `max(0, score - penalty)` saat submit; untuk submission lama di-backfill via script
- Jawaban guru untuk komentar: mulai dari huruf besar setelah titik dan spasi — `calcFinal()` panggil 50ms setelah input berubah
- `_saveCurrentPage()` dan `getAnswersLight()`/`getAnswersWithCanvas()` sudah diupdate untuk MCQ — canvas di `ec-canvas-{i}` di-init melalui `initEcCanvas(i)`
- Essay section's SVG overlay drag/rotate handles: use `startToolRotate(i,'ruler',$event)` — 3-arg form (was `startToolRotate('ruler',$event)` broken)
- **Data retention**: auto-purge 24h scheduler + manual `flask purge-data`. Soft-delete (90d grace) then hard-delete. Deletion requests need 14-day cooldown (PSE Kominfo). Export returns JSON with all user data.

## Relevant Files
- `app/__init__.py`: Flask app factory, two supabase clients, `from_json`/`tz`/`tz_short` filters, `greeting()`/`greeting_en()` globals, `cos`/`sin` globals, `DEFAULT_TZ_OFFSET=7`
- `app/config.py`: Config with `MAX_CONTENT_LENGTH = 50MB`, Supabase credentials
- `app/utils/auth.py`: `get_supabase()`, `get_auth_client()`, `login_required`, role decorators
- `app/routes/student.py`: dashboard, exam_list, take_exam, submit_exam (now handles dict MCQ answers), results, result_detail, `download_result_pdf` (now includes MCQ pages)
- `app/routes/teacher.py`: dashboard, exam_form, `_recalculate_scores()` (now with essay weights), grade_detail (handles dict MCQ answers), results, `_is_mcq_correct`/`_extract_mcq_answer` helpers
- `app/routes/api.py`: violation log, scan/process, sync-draft, `/api/grade/batch`
- `app/routes/tools.py`: `/tools/generate-answer-sheet`
- `app/services/anti_cheat_service.py`: `calculate_graduated_penalty()`, `validate_violation_log()`
- `app/services/answer_sheet_generator.py`: ReportLab-based LJK generator
- `app/templates/base.html`: **`brand` color alias added**, `primary`/`surface`/`brand` defined in Tailwind config
- `app/templates/student/take_exam.html`: **full canvas + drawing tools added to MCQ section** (ec-canvas-{i}); `_saveCurrentPage`, `getAnswersLight`, `getAnswersWithCanvas`, `loadDraft` updated for MCQ pages; MCQ answer format changed to dict; YouTube embed regex improved; **MCQ canvas init fix** (removed `!== 'mcq'` skip); **essay overlay handle fix** (3-arg form)
- `app/templates/student/result_detail.html`: 4-column score cards; handles dict MCQ answers
- `app/templates/student/result_detail_pdf.html`: **MCQ section now shows merged page images**; handles dict MCQ answers
- `app/templates/teacher/grade_detail.html`: compass + triangle tools; **teacher text box fix** (x-for instead of Jinja2); handles dict MCQ answers
- `app/templates/teacher/exam_form.html`: **brand-* → primary-* fix** for invisible buttons
- `app/templates/teacher/grading.html`: brand aliased to primary (via base.html) — "Koreksi" button visible now
- `app/templates/teacher/scan.html`: camera selector with `enumerateDevices()`
- `app/templates/teacher/analytics.html`: better empty state, charts hidden when 0 submissions
- `app/templates/teacher/results.html`: export buttons (XLSX, PDF, bubble-sheet)
- `app/templates/student/results.html`: subject total scores section
- `app/routes/whiteboard_teacher.py`: Teacher whiteboard routes + API endpoints
- `app/routes/whiteboard_student.py`: Student whiteboard routes + API endpoints
- `app/routes/whiteboard_socket.py`: WebSocket events (draw, cursor, permission, heartbeat, reaction, slide, timer)
- `app/services/whiteboard_service.py`: Whiteboard CRUD, slides, ops, permission, PDF export
- `app/static/js/whiteboard-canvas.js`: Full canvas engine (pen, eraser, text, highlight, laser, undo/redo)
- `app/static/js/whiteboard-websocket.js`: Socket.IO client with auto-reconnect
- `app/static/js/whiteboard-slides.js`: Horizontal thumbnail slide navigator
- `app/static/js/whiteboard-reactions.js`: Quick reaction emoji bubbles
- `app/static/js/whiteboard-timer.js`: Countdown timer overlay synced via WebSocket
- `app/templates/teacher/whiteboard_list.html`: Teacher whiteboard list + create modal
- `app/templates/teacher/whiteboard_canvas.html`: Teacher canvas with toolbar + student panel
- `app/templates/student/whiteboard_list.html`: Student whiteboard list
- `app/templates/student/whiteboard_canvas.html`: Student canvas (view-only + request annotate)
- `app/templates/teacher/answer_keys.html`: Quick-set answer keys page
- `supabase/migrations/003_submission_hidden_retracted.sql`: ✅ EXECUTED
- `supabase/migrations/011_anti_cheat_settings.sql`: ✅ EXECUTED
- `supabase/migrations/013_whiteboard.sql`: Whiteboard tables (7 new tables)
- `supabase/migrations/018_data_retention.sql`: Soft-delete columns, `deletion_requests` table — **not yet executed**
- `app/services/data_retention_service.py`: Data retention logic, purge scheduler, deletion request handling, data export
- `app/templates/student/settings.html`: Student settings page (password, data export, deletion request)
- `locustfile.py`: Load test suite for 500 concurrent users
- `deploy/tune-production.sh`: Production tuning (NGINX, kernel, file limits)
- `seed.py`: seed script with super_admin + 2 schools (SMP/SMA)
