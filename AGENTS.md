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
- Default timezone UTC+7 (WIB), configurable per-user and per-school
- UI language: Indonesian (default) with English toggle (`localStorage.sg_lang`)
- Color theme: `primary` (blue) defined in Tailwind config — `brand-*` was previously undefined (invisible buttons/text), now aliased to `primary` palette
- No direct DB DDL access — migrations must be run manually in Supabase SQL Editor
- `profiles` table columns: `['id', 'full_name', 'phone', 'role', 'created_at', 'updated_at']` — **no `school_id`, `class_id`, `nisn`, `nis`, `email` columns exist** (unless migration 017 added `school_id`)
- `schools` table **does NOT exist** in `public` schema
- MCQ answer keys: single value (`"A"`), multiple (`["A","B"]`), or `"bonus"` (all correct)
- Weighted scoring: Teacher sets MCQ% + Essay% (total must = 100), distributed equally per question within each group
- `question_weights` JSONB column does NOT exist in DB — code uses `.get()` + `setdefault` with try/except fallback
- PDF generation uses `xhtml2pdf` (pure Python) — WeasyPrint requires GTK/Pango not available on Windows
- Anti-cheat: graduated penalty (1st=warning, 2nd=-base, 3rd=-2×base, 4th+=-3×base), auto-submit on max violations
- Router / security: 404 catch-all `/tools` for admin functions; CSP `frame-ancestors 'self'` in response headers

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
- None

### Latest (2025-06-23)
- **Conversation reply simplified**: Removed `title` input from reply form (student & teacher) — only `message` textarea shown. Reply API no longer updates conversation title (keeps original title). `title` field removed from API validation (optional). True two-way reply works until creator clicks "Selesaikan".
- **Badge alert mechanism fixed**: `api_conversations()` checks `notification_recipients.read_at` for `has_unread`. Added `POST /api/broadcast/mark-read-all` endpoint. Both notification pages call `mark-read-all` on tab switch and dispatch `sg:unread` custom event for instant badge refresh.

### Blocked
- Migration 018 (soft-delete + deletion_requests) must run in Supabase SQL Editor
- Migration for PDP columns (`birth_date`, `pdp_agreed`, `parent_pdp_agreed`, `parent_name`, `parent_contact`, `consent_at`) must run in Supabase SQL Editor:
  ```sql
  ALTER TABLE profiles ADD COLUMN IF NOT EXISTS birth_date DATE;
  ALTER TABLE profiles ADD COLUMN IF NOT EXISTS pdp_agreed BOOLEAN DEFAULT FALSE;
  ALTER TABLE profiles ADD COLUMN IF NOT EXISTS parent_pdp_agreed BOOLEAN DEFAULT FALSE;
  ALTER TABLE profiles ADD COLUMN IF NOT EXISTS parent_name TEXT;
  ALTER TABLE profiles ADD COLUMN IF NOT EXISTS parent_contact TEXT;
  ALTER TABLE profiles ADD COLUMN IF NOT EXISTS consent_at TIMESTAMPTZ;
  ```

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
- **CSP/security headers**: Added to `_register_performance_headers` after_request for all responses
- **Content blocks**: Privacy/terms templates define BOTH `block content` (authenticated) and `block content_noauth` (public)
- **No birth date collected**: Privacy policy explicitly states NO birth date collected or stored. Terms also state no birth date required for registration. Student PDP settings simplified to consent checkbox only.
- **Privacy policy expanded**: 13 sections covering UU PDP + PSE Kominfo — added Penarikan Persetujuan (§6), Keputusan Otomatis/Profiling (§7), Data Sensitif (§2 explicit Pasal 26 disclosure).
- **Terms updated with RBAC matrix**: Full communication RBAC table in §5 matching AGENTS.md matrix, plus dispute resolution (§9: musyawarah → mediasi 30 hari → pengadilan).
- **Terms route now passes dpo_contact**: Both `/privacy` and `/terms` fetch DPO from system_settings.

## Next Steps
1. **Run migration 018** in Supabase SQL Editor (`supabase/migrations/018_data_retention.sql`)
2. **Run PDP columns migration** (see SQL above) in Supabase SQL Editor
3. **Deploy**: `git pull origin main && systemctl restart scangrade`
4. **Configure DPO/PSE**: Login as super_admin → `/super-admin/privacy-settings` → fill DPO contact, PSE reg number, data controller info
5. **Register as PSE Kominfo** (operational, not code)
6. **Appoint DPO** (operational, not code) and set contact in system_settings

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
