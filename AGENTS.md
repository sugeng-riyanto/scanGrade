# ScanGrade - AI Agent Context

## Goal
- Complete notification system with WhatsApp-like percakapan (threaded 1-on-1 chat), pengumuman (broadcast), and UU PDP data retention compliance, with strict RBAC separating student↔teacher messaging (no inter-student conversations or broadcasts).

## Constraints & Preferences
- Supabase project: `roshkbkbzgfedowozfo` (region: ap-southeast-1)
- Python 3.12.10, Flask 3.1.1, supabase-py 2.12.0, Alpine.js v3.14.8, Tailwind CSS (CDN), Chart.js 4.4.7
- Two Supabase clients: `get_supabase()` (service key) and `get_auth_client()` (anon key)
- Must support 500 concurrent students with spotty WiFi (3 floors, uneven coverage)
- Offline-first: localStorage first, sync to server when online
- `MAX_CONTENT_LENGTH = 50MB`
- Canvas data saved as PNG (not JPEG — was causing black overlay bug)
- **Tailwind CSS**: Local compiled CSS (`npm run css:build` after template changes). NOT CDN — users have spotty WiFi.
- **Inter font**: Local TTF files in `/static/vendor/inter/`, NOT Google Fonts CDN
- Default timezone UTC+7 (WIB), configurable per-user and per-school
- UI language: Indonesian (default) with English toggle (`localStorage.sg_lang`)
- Color theme: `primary` (blue) defined in Tailwind config — `brand-*` was previously undefined (invisible buttons/text), now aliased to `primary` palette
- No direct DB DDL access — migrations must be run manually in Supabase SQL Editor
- `profiles` table columns: `['id', 'full_name', 'phone', 'role', 'created_at', 'updated_at', 'nisn', 'nis', 'class_id', 'school_id', 'tz_offset']` (migrations 002+013 applied)
- `schools` table exists in `public` schema (migration 013 applied)
- `school_settings` table (legacy, single-row INT id=1) exists alongside `schools` (UUID id)
- `submissions` table: `started_at` column added (migration 013); `is_hidden`, `retracted` status added (migration 003); unique constraint `(student_id, exam_id)` added (migration 013)
- `pengumuman.school_id` is currently INT referencing `school_settings(id)` — migration 024 fixes to UUID
- MCQ answer keys: single value (`"A"`), multiple (`["A","B"]`), or `"bonus"` (all correct)
- Weighted scoring: Teacher sets MCQ% + Essay% (total must = 100), distributed equally per question within each group
- `question_weights` JSONB column exists on `exams` table (migration 003 applied)
- Anti-cheat columns (11 columns) exist on `exams` table (migration 011 applied)
- PDF generation uses `xhtml2pdf` (pure Python) — WeasyPrint requires GTK/Pango not available on Windows
- Anti-cheat: graduated penalty (1st=warning, 2nd=-base, 3rd=-2×base, 4th+=-3×base), auto-submit on max violations
- Router / security: 404 catch-all `/tools` for admin functions; CSP `frame-ancestors 'self'` in response headers
- **Session timeouts** (OWASP + UU PDP): super_admin idle=15m/abs=4h, admin_sekolah=30m/8h, guru=60m/12h, murid=120m/24h

## Pertukaran Pesan — RBAC Matrix

| Fitur | super_admin | admin_sekolah | guru | murid |
|-------|-------------|---------------|------|-------|
| **Kirim Pengumuman** (broadcast) | ✅ All roles | ✅ Guru & Murid di sekolahnya | ✅ Hanya Murid | ❌ Dilarang |
| **Lihat Pengumuman** | ✅ Semua | ✅ Semua | ✅ Semua | ✅ Semua |
| **Kirim Pesan 1-on-1** (Percakapan) | ✅ Siapa saja | ✅ Guru/Murid di sekolahnya | ✅ Murid | ✅ Hanya Guru/Admin (diverifikasi role) |
| **Lihat Percakapan** | ✅ Semua partisipasi | ✅ Semua partisipasi | ✅ Semua partisipasi | ✅ Hanya dgn Guru/Admin (filter server-side) |
| **Edit/Hapus Broadcast** | ✅ Semua | ✅ Milik sendiri | ✅ Milik sendiri | ❌ |
| **Setujui Hapus Akun** | ✅ Semua | ✅ Di sekolahnya | ❌ | ❌ |

**Aturan Kunci:**
- **Tidak ada percakapan atau pengumuman antar-murid (inter-student)** — backend memverifikasi role setiap penerima
- Murid yang mengirim pesan 1-on-1 (`target_role: "guru"` atau `"admin_sekolah"`) akan diverifikasi: setiap `recipient_id` harus memiliki role guru/admin_sekolah/super_admin
- Conversations API menyaring (skip) percakapan antar-murid untuk user role `murid` di server-side
- Broadcast (`target_role`) tidak bisa `"murid"` jika sender `murid` — di-restrict di `api_send_broadcast`

## UU PDP & PSE Kominfo Compliance Checklist

| Aspek | Status | Lokasi |
|-------|--------|--------|
| **Kebijakan Privasi** (Pasal 5, 14, 19) | ✅ 13 pasal lengkap (Eksplisit: NO birth date, NO sensitive data) | `/privacy`, `templates/compliance/privacy.html` |
| **Syarat & Ketentuan** | ✅ 11 pasal lengkap (Eksplisit: NO birth date) + RBAC matrix table | `/terms`, `templates/compliance/terms.html` |
| **Persetujuan Eksplisit** (Pasal 6) | ✅ Consent checkbox di registrasi | `templates/auth/register.html`, `routes/auth.py` |
| **Tujuan Pemrosesan** (Pasal 7) | ✅ Didokumentasikan di halaman privasi | `privacy.html §3` |
| **Penarikan Persetujuan** (Pasal 11) | ✅ Dijelaskan mekanisme penarikan + konsekuensi | `privacy.html §6` |
| **Hak Subjek Data** (Pasal 8-15) | ✅ Panel hak di settings + privacy page | `student/settings.html`, `privacy.html §5` |
| **Keputusan Otomatis/Profiling** (Pasal 21) | ✅ Diungkap (anti-cheat, auto-correct, weighted scoring) | `privacy.html §7` |
| **Data Sensitif** (Pasal 26) | ✅ Eksplisit: tidak mengumpulkan data sensitif (agama, biometrik, kesehatan, politik) | `privacy.html §2` |
| **Hak Akses & Portabilitas** | ✅ Ekspor data JSON via API | `api.py: /api/account/export-data` |
| **Hak Penghapusan** | ✅ Deletion request flow dengan admin approval, tenggang 90 hari | `api.py: /api/account/delete-request` |
| **Retensi Data** (Pasal 16) | ✅ Jadwal auto-purge + periode ditampilkan ke user | `data_retention_service.py`, `student/settings.html` |
| **Keamanan Data** (Pasal 17-18) | ✅ CSP, X-Frame-Options, X-Content-Type-Options, HTTPS, bcrypt, RBAC, backup, anomaly detection | `__init__.py` after_request handler |
| **Kewajiban Pengendali Data** (Pasal 20-24) | ✅ DPO configurable via super admin, respon 7 hari | `super_admin/privacy_settings.html` |
| **Transfer Lintas Batas** (Pasal 27) | ✅ Diungkapkan (Supabase Singapura) | `privacy.html §8` |
| **Pelanggaran Data** (Pasal 30-32) | ✅ Kebijakan notifikasi 14 hari + lapor ke PSE Kominfo | `privacy.html §10` |
| **Penyelesaian Sengketa** | ✅ Musyawarah → Mediasi 30 hari → Pengadilan RI | `terms.html §9` |
| **LocalStorage Consent** | ✅ Banner persetujuan pada kunjungan pertama | `base.html`, cookie banner |
| **DPO Contact** | ✅ Dapat dikonfigurasi via super admin, tampil di privacy + terms + footer | `system_settings key: dpo_contact` |
| **PSE Registration Number** | ✅ Dapat dikonfigurasi, ditampilkan di footer + terms | `system_settings key: pse_reg_number` |
| **RBAC Pesan** | ✅ 3-layer inter-student restriction + full matrix di terms | `api.py`, `api_conversations`, `api_send_broadcast`, `terms.html §5` |
| **Cookie/LocalStorage Banner** | ✅ Muncul sekali, disimpan di localStorage | `base.html` |
| **Email Kontak** | ✅ scangrade9@gmail.com, 7 hari kerja respon | `privacy.html §13`, `terms.html §11` |

## Progress
### Done (Latest)
- **UU PDP compliance pages**: `/privacy` (13 sections), `/terms` (11 sections with RBAC matrix) with `content_noauth` block for unauthenticated access — EYD-corrected, PSE Kominfo + UU PDP compliant
- **Privacy policy expanded**: Added Penarikan Persetujuan (§6), Keputusan Otomatis/Profiling (§7), Data Sensitif disclosure (§2 explicit Pasal 26 UU PDP — NO birth date, NO sensitive data)
- **Terms updated with RBAC matrix**: Full communication RBAC table in §5 matching AGENTS.md + dispute resolution (§9: musyawarah → mediasi 30 hari → pengadilan)
- **Teks diperbaiki EYD**: Seluruh dokumen compliance menggunakan bahasa Indonesia baku sesuai EYD
- **Contact email updated** to `scangrade9@gmail.com` with 7-day response policy
- **Security headers added**: CSP (`frame-ancestors 'self'`), `X-Content-Type-Options: nosniff`, `X-Frame-Options: SAMEORIGIN`
- **DPO/PSE configuration**: Super admin page at `/super-admin/privacy-settings` with save API
- **Footer**: Dynamic DPO/PSE display from system_settings via `/api/public/privacy-info`
- **Cookie/localStorage consent banner**: Fixed bottom bar on first visit
- **Login page**: Privacy/Terms links added
- **Student settings**: Rights panel (8 UU PDP rights), retention data card, simplified PDP consent checkbox
- **Parental consent API**: `POST /student/settings/pdp-update` with age validation (simplified to only pdp_agreed)
- **Consent at registration**: Required checkbox with `consent_at` timestamp
- **Inter-student restriction**: 3-layer (send-time role verification, list-time filter, FE tab restriction)
- **Data export + deletion**: Full API endpoints with RBAC verification
- **Auto-purge scheduler**: 24h data retention cleanup

### In Progress
- Many exams (Sejarah, Agama, etc.) have `None` answer keys — teacher must set correct answers via UI before scores appear
- All 3 pending migrations (002, 003, 011, 013) executed in Supabase SQL Editor — schema now matches code expectations

### Done (Batch 2026-07-16 — Major Bug Hunt)
- **Fixed `student_auto_save` no-op** (api.py): was accepting data and returning success without saving — now properly persists to submissions table
- **Fixed `NameError: ValidationError`** (exam.py): added missing import, upload PDF kosong no longer crashes
- **Fixed `NameError: app.logger`** (api.py): changed to `current_app.logger`
- **Fixed NISN login** (auth.py): added fallback to `auth.admin.list_users()` when `profiles.nisn` column query fails
- **Fixed Midtrans webhook security** (webhook.py): added HMAC-SHA512 signature verification; Fonnte token check
- **Fixed `/debug/exam` exposure** (__init__.py): added `@login_required` + school access check
- **Fixed CSRF bypass** (csrf.py): no longer "allow for now" when session has no token — now properly rejects
- **Fixed answer key exposure** (exam.py + student.py): stripped from API responses and HTML templates for non-teachers
- **Fixed essay weight = 0 in submit_exam** (student.py): added proper 70/30 MCQ/essay split matching `_recalculate_scores`
- **Fixed double submit race condition** (student.py): checks for existing submission before insert
- **Fixed blank page in answer sheet PDF** (answer_sheet_generator.py): removed extra `c.showPage()` after loop
- **Fixed MCQ comparison in exports** (export_service.py): handles multi-value `["A","B"]` and `"bonus"` keys
- **Fixed analytics scores** (analytics_service.py): uses `final_score` instead of `score`
- **Fixed scan_save grading** (api.py): handles dict-format MCQ answers + multi-value/bonus keys; added school access check; added status check (don't overwrite graded/published)
- **Fixed create/update/delete exam** (exam.py): added field whitelist, ownership check, teacher_id enforcement
- **Fixed publish routes** (teacher.py+publish.py): added `@require_school_access`; `/publish/` was a stub, now actually publishes
- **Fixed admin_sekolah logic** (teacher.py): removed `not exam_ids AND` condition — now always shows school exams
- **Fixed sync-draft race condition** (api.py): checks status before update — never overwrites submitted/graded/published; proper error logging instead of `except: pass`
- **Fixed memory leaks** (api.py+__init__.py): `_sync_last`/`_sync_locks` now clean up stale entries every 10 min; `_demo_cache` replaced with `g._demo_settings` (request-scoped)
- **Fixed timer reset on F5** (take_exam.html): `started_at` persisted server-side; `timeLeft` calculated from elapsed time
- **Fixed submit button loading state** (take_exam.html): added `submitting` prop, spinner, double-click prevention
- **Added CSP headers** (__init__.py): `Content-Security-Policy` added to all responses
- **Fixed OMR deskew memory explosion** (omr_service.py): replaced `np.column_stack(np.where(...))` with Canny edge + Hough lines
- **Fixed `get_whatsapp_number` no caching** (__init__.py): now cached per-request via `g._whatsapp_number`
- **Created migration 013** (supabase/migrations/013_fix_missing_columns.sql): adds `profiles.school_id`, `submissions.started_at`, `schools` table, unique constraint `(student_id, exam_id)`

### Blocked
- Exam scoring shows 0 for students when answer keys are `None` (teacher has not set correct answers)


## Key Decisions
- **Two separate Supabase clients** to avoid service-key client corruption from auth operations
- **Auto-create profile** in register route, not DB trigger
- **Weighted scoring**: MCQ% + Essay% (total=100), distributed equally within each group — fallback to 70/30 when `question_weights` is empty
- **Essay types simplified** to single "Esai" toggle — canvas drawing + text boxes combined on PDF
- **Anti-cheat graduated penalty**: 1st=warning(0), 2nd=-base, 3rd=-2×base, 4th+=-3×base per violation; cap at 100
- **Tool SVGs** always `pointer-events: none` — drag/rotate from toolbar control bar only
- **PDF generation**: `xhtml2pdf` over WeasyPrint (no GTK/Pango on Windows), over pdfkit (needs wkhtmltopdf)
- **Server-side compositing for PDF**: Pillow merges all layers into flat `<img>` — xhtml2pdf doesn't support `position: absolute`
- **NISN**: fill from LEFT if < 10 digits (most significant first, trailing cells empty) — standard OMR convention
- **MCQ answer format**: Use dict `{answer, pages}` only when canvas/text data exists; keep string for backward compatibility
- **Inter-student restriction**: Three-layer defense — (1) `api_send_broadcast` memverifikasi role setiap recipient, (2) `api_conversations` server-side skip conv antar-murid untuk role murid, (3) FE hanya menampilkan guru di "Pesan Guru" tab
- **Tailwind CSS rebuild**: `npm run css:build` after every template change; `npm run css:watch` for dev auto-rebuild
- **CSP/security headers**: Added to `_register_performance_headers` after_request for all responses
- **Content blocks**: Privacy/terms templates define BOTH `block content` (authenticated) and `block content_noauth` (public)
- **No birth date collected**: Privacy policy explicitly states NO birth date collected or stored. Terms also state no birth date required for registration. Student PDP settings simplified to consent checkbox only.
- **Privacy policy expanded**: 13 sections covering UU PDP + PSE Kominfo — added Penarikan Persetujuan (§6), Keputusan Otomatis/Profiling (§7), Data Sensitif (§2 explicit Pasal 26 disclosure).
- **Terms updated with RBAC matrix**: Full communication RBAC table in §5 matching AGENTS.md matrix, plus dispute resolution (§9: musyawarah → mediasi 30 hari → pengadilan).
- **Terms route now passes dpo_contact**: Both `/privacy` and `/terms` fetch DPO from system_settings.
- **Title optional**: Auto-generated from first 50 chars of message. Users no longer need to think of a title — just type and send.
- **Contacts-based sidebar**: Sidebar shows ALL people the user can message (RBAC-filtered, via `/api/broadcast/contacts`), not just existing conversations. Click any contact → if conversation exists, open it; if not, start new chat directly. No "Pesan Baru" popup needed. `pendingRecipient` drives the new-chat flow.
- **Always-visible input bar**: Shows when `activeConv || pendingRecipient`; no compose form replaces the chat view.
- **WhatsApp exact match**: Sent bubble green `#dcf8c6` with `text-[#111b21]` (not blue/primary). Timestamp + double-checks inside bubble at bottom-right (not outside). Check mark colors match WhatsApp: gray `#8696a0` → blue `#53bdeb` for read (last msg).
- **Conversation before notification**: `api_send_broadcast` creates/updates conversation first, then inserts notification WITH `conversation_id` — no separate UPDATE needed, no race condition.
- **Graceful fallback for missing columns**: All migration-021-specific features (unsend, hide, soft-delete) try the new columns first; if they don't exist, fall back gracefully (e.g., unsend always sets `message="Pesan telah hapus"` even if `is_deleted` column is missing).
- **Messages area guarded by `x-if="activeConv"`**: Prevents Alpine errors when `pendingRecipient` is set but `activeConv` is null.

## Anti-Cheat System — Panduan per Role

### Guru (Pembuat Ujian)
| Fitur | Deskripsi | Letak |
|-------|-----------|-------|
| **Blokir Screenshot** | Mencegah PrintScreen via `navigator.clipboard.writeText('')` | Form ujian → Pengaturan Anti-Cheat |
| **Blokir Copy-Paste** | Mencegah copy/paste/cut via `e.preventDefault()` | Form ujian → Pengaturan Anti-Cheat |
| **Blokir Klik Kanan** | Mencegah context menu via `e.preventDefault()` | Form ujian → Pengaturan Anti-Cheat |
| **Wajib Fullscreen** | Mendeteksi keluar layar penuh sebagai pelanggaran (`fullscreen_exit`) | Form ujian → Pengaturan Anti-Cheat |
| **Watermark Nama** | Menampilkan nama siswa sebagai watermark di seluruh halaman ujian | Form ujian → Pengaturan Anti-Cheat |
| **Penalti per Pelanggaran** | Base penalti (default 5 poin) — 1st=warning, 2nd=-base, 3rd=-2×base, 4th+=-3×base | Form ujian → Pengaturan Anti-Cheat |
| **Maks Pelanggaran** | Jumlah pelanggaran sebelum auto-submit (default 5) | Form ujian → Pengaturan Anti-Cheat |

**Catatan Penting:**
- `block_screenshot` sudah diperbaiki — sekarang gate-nya ke `block_screenshot`, bukan `block_copy_paste`
- Semua pelanggaran tercatat di tabel `violation_logs` + dihitung server-side di `submit_exam()`
- Timer ujian divalidasi server-side via `student_sync_draft()` — jika ada mismatch >300 detik antar device, client di-reject
- Jika siswa mematikan JavaScript, server tetap hitung penalti dari violation_logs yang sudah tercatat

### Super Admin / Admin Sekolah
| Fitur | Deskripsi | Letak |
|-------|-----------|-------|
| **Lihat Flag Kecurangan** | Submission dengan `_flags` berisi `suspicious_speed` atau `device_mismatch` akan terlihat di detail submission | Detail submission → `answers._flags` |
| **CSP Header** | Content-Security-Policy diperkuat — membatasi script hanya dari `'self'` + `cdn.jsdelivr.net` | `__init__.py` after_request |
| **Speed Analysis** | Jika >5 MCQ dijawab dalam <1.5 detik/soal, submission di-flag suspicious | Server-side di `submit_exam()` |
| **Device Mismatch** | Jika IP atau User-Agent berubah antara first sync dan submit, submission di-flag | Server-side di `submit_exam()` |
| **Timer Reconciliation** | Server memvalidasi `started_at` — jika selisih >300 detik antar device, sync di-reject (409) | `api.py` → `student_sync_draft()` |

### Murid (Peserta Ujian)
| Aturan | Konsekuensi |
|--------|-------------|
| Pindah tab / buka aplikasi lain (visibilitychange) | 1st = **PERINGATAN**, 2nd = **-base poin**, 3rd = **-2×base**, 4th+ = **-3×base** |
| Keluar layar penuh (`fullscreen_exit`) | Langsung dicatat sebagai pelanggaran (warning-style) |
| Copy/paste/cut | Diblokir — `e.preventDefault()` |
| Klik kanan | Diblokir — `e.preventDefault()` |
| PrintScreen | Diblokir (jika guru mengaktifkan `block_screenshot`) |
| JavaScript dimatikan | Server tetap punya catatan violation_logs + hitung penalti saat submit |
| Buka 2 tab | Timer reconciliation — server reject jika mismatch >300 detik |
| Kerjakan dari device lain | Device mismatch terdeteksi saat submit (IP/UA berbeda) |
| Menjawab terlalu cepat | Speed analysis — jika <1.5 detik/soal untuk >5 MCQ, submission di-flag |
| Mencapai maks pelanggaran | **Ujian otomatis dikumpulkan (auto-submit)** |

## Next Steps
1. **Deploy to VPS**: `cd /opt/scangrade && git pull origin main && sudo systemctl restart scangrade`
2. **Run migration 021** in Supabase SQL Editor (`supabase/migrations/021_conversation_crud.sql`) — unblocks unsend/hide/soft-delete CRUD
3. **Verify all 4 RBAC**: contacts list, direct chat, edit, unsend, sembunyikan, hapus, badge read state
4. **Configure DPO/PSE**: Login as super_admin → `/super-admin/privacy-settings` → fill DPO contact, PSE reg number, data controller info
5. **Register as PSE Kominfo** (operational, not code)
6. **Appoint DPO** (operational, not code) and set contact in system_settings

## Key Files (Notifications)
- `app/routes/api.py`: All conversation/broadcast CRUD + title auto-generation + conv-before-notif insert + graceful migration-021 fallbacks + `last_msg_preview`/`unread_count` + `/broadcast/contacts` endpoint.
- `supabase/migrations/021_conversation_crud.sql`: Unsend, soft-delete, message_hides support (must run in SQL Editor).
- `app/templates/teacher/notifications.html`: 3 tabs + contacts sidebar + CRUD + broadcast form.
- `app/templates/student/notifications.html`: 2 tabs + contacts sidebar.
- `app/templates/admin_sekolah/notifications.html`: 5 tabs + contacts sidebar + CRUD + broadcast + deletion requests.
- `app/templates/super_admin/notifications.html`: 5 tabs + contacts sidebar + CRUD + broadcast + privacy settings.

## Critical Context
- Alpine.js v3.14.8 — `el.__x` does NOT exist. Must use `QUESTION_INSTANCES` global map pattern. NOTE: admin templates use `Alpine.raw(root).__x.$data` for import forms.
- `xhtml2pdf` does NOT support: `display: flex`, `position: absolute/relative`, `border-radius` (limited), `gap` — use tables for layout, floats for positioning
- PDF page URLs are local paths like `/static/uploads/exams/<uuid>/page_001.png` — load via `os.path.join(app.static_folder, ...)` not HTTP
- Student answer JSON (MCQ with canvas): `{"0": {"answer": "A", "pages": {"0": {"canvas": "data:image/png;...", "textBoxes": [...]}}}}` — old format `{"0": "A"}` still supported
- Answer key JSON: `{"0": "A", "1": ["A","B"], "2": "bonus", "3": "essay"}` — `None` keys cause score 0
- `question_weights` + anti-cheat columns now exist in DB (migrations 003 + 011 executed)
- `submissions.is_hidden` column exists (migration 003 executed)
- `profiles` now has `nisn`, `nis`, `class_id`, `school_id`, `tz_offset` (migrations 002 + 013 executed)
- `schools` table now exists (migration 013 executed)
- Violation `sendBeacon` Blob: `new Blob([JSON.stringify([{...}])], {type: 'application/json'})` — must have correct bracket nesting
- Grade detail form: pressing Enter in score/comment inputs submits the HTML `<form>` and reloads page — `@keydown.enter.prevent=""` added to both inputs
- NISN: jika < 10 digit, isi dari kiri (most significant digit pertama), trailing cells kosong
- `final_score` dihitung sebagai `max(0, score - penalty)` saat submit; untuk submission lama di-backfill via script
- Jawaban guru untuk komentar: mulai dari huruf besar setelah titik dan spasi — `calcFinal()` panggil 50ms setelah input berubah
- `_saveCurrentPage()` dan `getAnswersLight()`/`getAnswersWithCanvas()` sudah diupdate untuk MCQ — canvas di `ec-canvas-{i}` di-init melalui `initEcCanvas(i)`
- Essay section's SVG overlay drag/rotate handles: use `startToolRotate(i,'ruler',$event)` — 3-arg form (was `startToolRotate('ruler',$event)` broken)

## Key Files (Compliance)
- `app/routes/public.py`: `/privacy` and `/terms` routes with DPO fetch
- `app/templates/compliance/privacy.html`: Full Kebijakan Privasi — 13 sections (both `block content` and `block content_noauth`)
- `app/templates/compliance/terms.html`: Full Syarat & Ketentuan — 11 sections with RBAC matrix (both `block content` and `block content_noauth`)
- `app/templates/super_admin/privacy_settings.html`: DPO/PSE/controller configuration
- `app/routes/super_admin.py`: `/privacy-settings` route + save API
- `app/routes/student.py`: PDP settings endpoint (simplified — only pdp_agreed)
- `app/templates/student/settings.html`: Rights panel, retention data card, simplified PDP consent checkbox
- `app/__init__.py`: CSP + security headers in after_request
- `app/routes/api.py`: `/api/public/privacy-info`, broadcast RBAC, conversation filtering
- `app/templates/auth/login.html`: Privacy/Terms links
- `app/templates/auth/register.html`: Consent checkbox
- `app/templates/base.html`: Cookie banner, DPO/PSE in footer
