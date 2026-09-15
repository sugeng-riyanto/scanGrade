# ScanGrade - AI Agent Context

## Goal
- Complete notification system with WhatsApp-like percakapan (threaded 1-on-1 chat), pengumuman (broadcast), and UU PDP data retention compliance, with strict RBAC separating student↔teacher messaging (no inter-student conversations or broadcasts).

## Constraints & Preferences
- Supabase project: `roshkbkbzgfedowozfo` (region: ap-southeast-1)
- Python 3.12.10, Flask 3.1.1, supabase-py 2.12.0, Alpine.js v3.14.8, Tailwind CSS (compiled locally, see below), Chart.js 4.4.7
- Two Supabase clients: `get_supabase()` (service key) and `get_auth_client()` (anon key)
- Must support 500 concurrent students with spotty WiFi (3 floors, uneven coverage)
- Offline-first: localStorage first, sync to server when online
- `MAX_CONTENT_LENGTH = 50MB`
- Canvas data saved as PNG (not JPEG — was causing black overlay bug)
- **Tailwind CSS**: Local compiled CSS (`npm run css:build` after template changes). NOT CDN — users have spotty WiFi.
- **A Tailwind utility name must never be built at render time.** Tailwind reads the template as text, so `from-{{ color }}-600` matches nothing, generates nothing, and the element silently keeps its ancestor's gradient — that is how two of the three "Log in" buttons on `/demo` shipped as white text on a white box. Name each utility literally; `demo.html` uses `{% set EMAIL_COLOR/COPY_BUTTON/LOGIN_COLOR %}` maps. Guarded three ways in `tests/unit/test_tailwind_class_names.py`: no interpolation flush against an identifier inside a class attribute; no utility name **assembled in JavaScript** — `:class="'from-' + color"`, `` :class="`bg-${tone}-500`" `` and `.concat('bg-', tone)` leave the name half-literal, which rule 1 cannot see (the glue is JS) and rule 2 cannot see (the name never appears whole); and every colour utility a class-bearing attribute **or a script** names must exist in the committed `tailwind.css` (the `<script>` and `x-data` regions are where `ai_settings.html`'s provider palette and `schools.html`'s toggle live). All three run in `deploy/theme_gate.sh`, so they gate the pre-commit hook and the VPS auto-deploy, not just the suite
- **Legibility floor + mobile layout**: `app/static/css/theme.css` (linked from `base.html`) maps `text-[6..11px]` up to ≥12px in one block, so 546 call sites did not have to be edited by hand (the only documented below-floor exemption is the OMR answer-sheet mockup in `tools/generate_answer_sheet.html`, where 4-5px *is* the artefact). Every non-print table with ≥4 columns sits in an `overflow-x-auto` container **and** carries a `min-w-[...]` — without the min-width the table squeezes instead of scrolling. Four print documents (`monitor.html`, `print/report_card.html`, `student/result_detail_pdf.html`, `teacher/print_exam_report.html`) are exempt from the table rule because a phone is not their target. Guarded by `tests/unit/test_mobile_layout.py`
- **The cost of a page is its round-trips, so a row is asked for once.** Supabase answers in ~100-165 ms from this box, and the *cold* student dashboard issued **12** calls: `profiles` four times (three of them for a `class_id` the session already carried), `submissions` three times over the same rows (the row carries `status`, so the "answered" and "retracted" sets come from one read), `teacher_assignments` counted twice with an identical filter where the second answer overwrote the first, and `schools` twice for columns no template renders (`base.html` reads `g.get('school_info')`, which nothing ever sets — the dashboards' `school_info` was dead data, and the per-school favicon has never activated). It is now **6**, and the exam list went 4 → 2. `app/utils/req_cache.py` holds the two lifetimes that make that possible: `memo()` for this request only, in `g` so it cannot leak into the next (the dict it replaced was keyed by `id(request)` and never evicted — it grew for the life of the worker and could serve another request's row), and `ttl()` for rows identical for everyone in a school (name, logo, feature flag, classes, subjects, subject count, a class's active whiteboards), which is what turns 500 students into one query per TTL instead of five hundred. `class_id` rides on the cached session, so no student page reads `profiles` for it. Every writer invalidates what it changed, and `tests/unit/test_round_trip_budget.py` fails if an `invalidate_*` helper loses its call site, if a duplicate query reappears in the two hot routes, or if `id(request)` returns as a cache key
- **A stylesheet the browser can cache beats a smaller one it must re-download.** `base.html` carried the theme tokens, the dark-mode remap, the light-mode corrections and the legibility floor as one **36 KB `<style>` block**, so every page carried all of it inside its HTML: an inline block cannot be cached, nginx re-gzips it on each request, and a phone re-downloads it on every navigation. It is `app/static/css/theme.css` now, linked from `base.html`, and the same probe on the same box measures every student page **36 KB lighter** — dashboard 79 → **42 KB**, exams 75 → **39**, results 78 → **42**, settings 93 → **57**, comms 117 → **81** — with render CPU down only where it is measurable (dashboard 8.2 → **5.0 ms** warm; cold is Supabase, not rendering). It does **not** raise the concurrency ceiling by itself, and the measurement says why: during an 80-session run of `loadtest_concurrent.py` the box sat at **us+sy 85% median / 97% p90, `wa` 0** — CPU-bound, so capacity follows CPU per request and worker count, not bytes (same command: 2450 → 2578 requests, p50 513 → **374 ms**, 0 errors, p95 inside run-to-run noise). `asset_v()` versions the URL by content hash, which is what makes nginx's `immutable, max-age=31536000` safe — and `tailwind.css` now uses it too, because until now a committed CSS rebuild had no way to reach a browser that had already loaded the old one. `tests/unit/test_theme_stylesheet.py` fails if the block is inlined into `base.html` again, if the link moves before `tailwind.css` (which loses the dark remap in the cascade, silently), if a Jinja tag appears in the file, or if either stylesheet is linked by a bare path; the two guards that read that CSS were repointed at the file rather than relaxed, and all three run in `deploy/theme_gate.sh`
- **Inter font**: Local TTF files in `/static/vendor/inter/`, NOT Google Fonts CDN
- **Midtrans Snap.js**: vendored at `/static/vendor/midtrans/<build>/veritrans.co.id/snap.js` (build = `production` or `sandbox`), with the CDN only as a load-failure fallback. **The `veritrans.co.id` directory is load-bearing, not decoration**: Snap.js finds its own `<script>` tag by matching that string (or its API host, which carries a scheme and so can never match a same-origin path), then reads `data-client-key` off the tag it found. Rename the directory and the key silently becomes empty — the SDK loads fine, no error is raised, and the payment iframe comes up without a merchant. Guarded by `tests/unit/test_snap_vendored.py`
- The two Snap.js builds differ **only** in the host they hardcode, so they are not interchangeable: the sandbox build sends payments to sandbox (where they silently do not count), the production build takes real card details
- Default timezone UTC+7 (WIB), configurable per-user and per-school
- UI language: **English (default)** with an Indonesian toggle (`localStorage.sg_lang`) — a stored choice always wins. The default is decided in exactly one place, `base.html` (`default_lang|default('en', true)`), read both by `<html lang>` and by the Alpine `lang` scope; no route passes `default_lang` (a guard in `tests/unit/test_language_toggle.py` fails if one does). Strings are bound inline as `t('Indonesia','English')`; plain JS outside Alpine uses `window.sgT()`. The `TRANSLATED` list in `tests/unit/test_language_toggle.py` is the contract of which pages must stay bilingual — the five role dashboards (student/teacher/admin/super_admin/admin_sekolah) plus the working pages, the three role tutorials, `/demo` and the exam paper. Email addresses are excluded from that check: a demo credential like `guru_mtk_smp@scan-grade.app` is data, not copy
- **The i18n sweep reads card bodies, not just text nodes.** A card here is usually an Alpine binding — `<p x-text="t('Total','Total')">`, or a value out of `x-data` that a later `x-text` renders — and the original check read only `>text<`, so an entire untranslated card passed. The sweep now also reads the *string literals* inside text-rendering bindings (`x-text`, `x-html`, `title`, `placeholder`, `aria-label`, `alt`, `value`), inside `x-data`, and inside every `<script>` (alerts, status lines, `.textContent` writes). Three things make that honest rather than noisy: Jinja is **blanked before reading** (a text node containing `{{ }}` never matched the old pattern at all, and a `t()` whose argument holds a Jinja ternary had its pair split in half); an Indonesian literal counts as translated only when its partner is adjacent — the three shapes this app writes are `t('id','en')` / `sgT('id','en')`, `['id','en']`, and `{ id/en }` or `{ badgeId/badgeEn }` keys — and the partner must look English, so `['Simpan','Batal']` is still an offender; and a literal that is a path (`/api/…`) or a role slug (`guru`, `murid`, `admin_sekolah`) is data, the same string in both languages. `label` and `para` were removed from the marker set for the same reason: both are English words used as code here (`k.label`, `para_`, `p.append('label',…)`), and a guard that fails on code gets deleted. Adding card vocabulary (`aksi`, `tersedia`, `tren`, `penalti`, `mulai`, …) is how the five dashboards were found to be untranslated in the first place
- **`t()` re-renders, `sgT()` does not**: an expression containing `t(...)` is re-evaluated when `lang` changes because `t()` reads `this.lang`; a string `sgT()` already assembled is frozen in the language it was built in. That is correct for a message shown once (an alert, a violation banner that just fired), and wrong for a label that stays on screen — `student/take_exam.html`'s sync label, its violation banner and its device bar are rebuilt by `relabelLocale()`, which `init()` runs from a `$watch('lang')` and which also re-invokes the device probes `initStatusBar()` stashes. No label may be *declared* as `name: sgT(...)`; `tests/unit/test_language_toggle.py` fails if one is, or if a stored label is unreachable from the rebuild hook. The exam page needs three language controls (exam bar, fullscreen overlay, agreement modal) because each overlay covers the bar and the navbar's button
- 4 standalone documents do NOT inherit base.html and are hardcoded Indonesian (`lang="id"`): `monitor.html`, `print/report_card.html`, `student/result_detail_pdf.html`, `teacher/print_exam_report.html`. They cannot honour the English default
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
- **The deploy runner is never a copy**: `/usr/local/bin/scangrade-deploy` and `/usr/local/bin/scangrade-db-snapshot` are `deploy/entrypoint.sh` rendered with the checkout path, and they exec `deploy/*.sh` **in the checkout** (chosen by the name they were installed under). `install-auto-deploy.sh` used to `install` the scripts *to* those paths, which is a snapshot: fixes to the deploy logic then sat on GitHub while root kept running the version of the day it was installed — and the installed `scangrade-db-snapshot` was broken outright (the wrapper derives the checkout from its own location, so it looked for `/usr/local/.venv/bin/python` and refused to snapshot). Gate 0 of `deploy/scangrade-deploy.sh` now compares the file it is running with `deploy/scangrade-deploy.sh` in the checkout **before fetching or merging** and exits **14** when they differ (a byte-identical copy passes); it sits after the pause check on purpose. Guards: `tests/unit/test_auto_deploy.py`, which also renders the launcher and runs it — changing the checkout's script changes what the installed path runs, arguments are dropped for the deploy and forwarded for the snapshot. **One-time migration**: a box installed before this change keeps working but still runs its old copy; `sudo bash /opt/scangrade/deploy/install-auto-deploy.sh` once, and it cannot recur

- **Measured capacity of the production VPS** (measured 14 Sep 2026 — do not advertise more without re-measuring): the box is **1 vCPU / 957 MB** (3 gevent workers). Ceiling is **~20 requests/s**, and it is **CPU-bound page rendering** (`vmstat`: user 90–93%, idle 0%, `wa` ≈0 — not network, memory, or Supabase). Concurrency curve, one account per session, identity verified: **50 murid** → p50 82–620 ms, p95 ≤ 1.7 s, 0% error · **100** → p50 1.9 s · **150** → p50 4.6–5.9 s · **500 sessions** → only 174 got past login (nginx per-IP login burst), p50 6.3–7.0 s, 2.3% 429, **zero 5xx**. Server-side `localhost /health` went 0.006 s idle → 5.03 s under load. Harness: `loadtest_concurrent.py --duration N` (endurance mode); guard: `tests/unit/test_landing_claims.py`
- **`locustfile.py` now authenticates for real** (fixed 14 Sep 2026) — previously it posted its login with no CSRF token (every attempt 403), judged success by HTTP status although a *rejected* login also answers 200, and drew from `siswa1..siswa1000_smp` of which six exist; every figure attributed to it was a logged-out visitor following redirects. It now reads the CSRF token off the login page, judges success by **where the response landed** (`/dashboard`), records a login that does not land there as a Locust **failure**, takes a **distinct roster account per virtual user** (`load_roster` shared with `loadtest_concurrent.py`) and **refuses to run** when `--users` exceeds the roster, verifies `/auth/me` identity per session, declares `wait_time = between(1, 3)` (without it Locust's `constant(0)` fires ~180 req/s at one account and the rate limiter's 429s look like a server failure), counts status codes for **every** request via `_RecordingSession`, and writes its JSON artifact **before** printing. Guarded by `tests/unit/test_locust_harness.py`, which drives the real functions in a child process (importing Locust calls `gevent.monkey.patch_all()`)
- **Claims gate** (`deploy/claims_gate.py`, deploy Gate 5, after the reload): re-measures **the rung the landing page itself advertises** — parsed out of `app/templates/landing.html`, not from a constant — and refuses the release when the box no longer behaves the way the page says. Compares the **worst signed-in page endpoint** (never a login burst: that is a different claim, and folding it in made a healthy app fail) against the published p50/p95, with `LATENCY_SLACK=2.0` and `ERROR_SLACK_PCT=1.0`. Two strikes: one divergent probe is contention, two are a stale claim. exit 0 pass · 1 confirmed divergence (rolls back only if `CLAIMS_ENFORCE="true"`) · 2 could not measure (no roster, box already busy, unconfirmed) and **never** a rollback. Needs one account per session (`.freebuff/lt_roster.json`, gitignored → survives a rollback reset). Evidence: `/var/lib/scangrade-deploy/claims/history.jsonl`. Guard: `tests/unit/test_claims_gate.py`
- **Landing-page rows are recomputed from `docs/measurements/`** (14 Sep 2026: 50/100/150 are 60-second endurance runs; the 500\* row is the recorded 10-minute run). Published: **50** → p50 64–962 ms, worst p95 3.7 s, 0% · **100** → 1.4–2.2 s, ≤ 4.2 s, 0% · **150** → 3.7–4.4 s, ≤ 15.2 s, 0% · **500\*** → 5.6–7.0 s, ≤ 10.8 s, 2.31%. A rung may have **several** artifacts (`rung-050.json` and `locust-050.json` are two independent measurements of the same load) and the published bound is the **worst** figure any of them produced — never the better one, because `claims_gate.py` holds the page to 2× of what it advertises and this box has really been observed at p50 1464 ms on the 50-student rung. Every artifact behind a row must also prove it authenticated (`logins_failed == 0`, identities verified), so a harness that cannot log in can never back a number again. The old 50-row (p50 82–620 ms) was replaced because a probe did not reproduce it. **Shape matters**: a 12 s probe measures 1464 ms at the same rung, because 50 simultaneous logins dominate the opening seconds — the published rows are 60 s for that reason, and the gate probes for 30 s. `tests/unit/test_landing_claims.py` recomputes every row from the artifacts, so the page and its evidence cannot drift
- **The Supabase client is one instance per app** (`current_app.extensions["supabase"]`), shared by every request. That is safe under the **gevent** workers production uses (no interleaving inside a worker), but under **thread**-level concurrency it is not: the Flask dev server at 50 concurrent sessions produced **2.1% 500s** (`RemoteProtocolError: Server disconnected` on `profiles`/`submissions` reads, 12–14 per run, reproducible in both probes), while production measured **0 × 5xx** on the identical probe. Do not move to gunicorn `gthread` (or any threaded worker) without making the client thread-local first
- **Measured capacity of the production VPS** (measured 14 Sep 2026 — do not advertise more without re-measuring): the box is **1 vCPU / 957 MB** (3 gevent workers). Ceiling is **~20 requests/s**, and it is **CPU-bound page rendering** (`vmstat`: user 90–93%, idle 0%, `wa` ≈0 — not network, memory, or Supabase). Concurrency curve, one account per session, identity verified: **50 murid** → p50 82–620 ms, p95 ≤ 1.7 s, 0% error · **100** → p50 1.9 s · **150** → p50 4.6–5.9 s · **500 sessions** → only 174 got past login (nginx per-IP login burst), p50 6.3–7.0 s, 2.3% 429, **zero 5xx**. Server-side `localhost /health` went 0.006 s idle → 5.03 s under load. Harness: `loadtest_concurrent.py --duration N` (endurance mode); guard: `tests/unit/test_landing_claims.py`
- **`locustfile.py` now authenticates for real** (fixed 14 Sep 2026) — previously it posted its login with no CSRF token (every attempt 403), judged success by HTTP status although a *rejected* login also answers 200, and drew from `siswa1..siswa1000_smp` of which six exist; every figure attributed to it was a logged-out visitor following redirects. It now reads the CSRF token off the login page, judges success by **where the response landed** (`/dashboard`), records a login that does not land there as a Locust **failure**, takes a **distinct roster account per virtual user** (`load_roster` shared with `loadtest_concurrent.py`) and **refuses to run** when `--users` exceeds the roster, verifies `/auth/me` identity per session, declares `wait_time = between(1, 3)` (without it Locust's `constant(0)` fires ~180 req/s at one account and the rate limiter's 429s look like a server failure), counts status codes for **every** request via `_RecordingSession`, and writes its JSON artifact **before** printing. Guarded by `tests/unit/test_locust_harness.py`, which drives the real functions in a child process (importing Locust calls `gevent.monkey.patch_all()`)
- **Claims gate** (`deploy/claims_gate.py`, deploy Gate 5, after the reload): re-measures **the rung the landing page itself advertises** — parsed out of `app/templates/landing.html`, not from a constant — and refuses the release when the box no longer behaves the way the page says. Compares the **worst signed-in page endpoint** (never a login burst: that is a different claim, and folding it in made a healthy app fail) against the published p50/p95, with `LATENCY_SLACK=2.0` and `ERROR_SLACK_PCT=1.0`. Two strikes: one divergent probe is contention, two are a stale claim. exit 0 pass · 1 confirmed divergence (rolls back only if `CLAIMS_ENFORCE="true"`) · 2 could not measure (no roster, box already busy, unconfirmed) and **never** a rollback. Needs one account per session (`.freebuff/lt_roster.json`, gitignored → survives a rollback reset). Evidence: `/var/lib/scangrade-deploy/claims/history.jsonl`. Guard: `tests/unit/test_claims_gate.py`
- **Landing-page rows are recomputed from `docs/measurements/`** (14 Sep 2026: 50/100/150 are 60-second endurance runs; the 500\* row is the recorded 10-minute run). Published: **50** → p50 64–962 ms, worst p95 3.7 s, 0% · **100** → 1.4–2.2 s, ≤ 4.2 s, 0% · **150** → 3.7–4.4 s, ≤ 15.2 s, 0% · **500\*** → 5.6–7.0 s, ≤ 10.8 s, 2.31%. A rung may have **several** artifacts (`rung-050.json` and `locust-050.json` are two independent measurements of the same load) and the published bound is the **worst** figure any of them produced — never the better one, because `claims_gate.py` holds the page to 2× of what it advertises and this box has really been observed at p50 1464 ms on the 50-student rung. Every artifact behind a row must also prove it authenticated (`logins_failed == 0`, identities verified), so a harness that cannot log in can never back a number again. The old 50-row (p50 82–620 ms) was replaced because a probe did not reproduce it. **Shape matters**: a 12 s probe measures 1464 ms at the same rung, because 50 simultaneous logins dominate the opening seconds — the published rows are 60 s for that reason, and the gate probes for 30 s. `tests/unit/test_landing_claims.py` recomputes every row from the artifacts, so the page and its evidence cannot drift Evidence files have to be ones git carries: `.gitignore` excludes `*.log`, so the 500\* run and the Locust console output are `.txt`, and `tests/unit/test_landing_claims.py` fails when any file in `docs/measurements/` is one no fresh clone would receive.
- **The Supabase client is one instance per app** (`current_app.extensions["supabase"]`), shared by every request. That is safe under the **gevent** workers production uses (no interleaving inside a worker), but under **thread**-level concurrency it is not: the Flask dev server at 50 concurrent sessions produced **2.1% 500s** (`RemoteProtocolError: Server disconnected` on `profiles`/`submissions` reads, 12–14 per run, reproducible in both probes), while production measured **0 × 5xx** on the identical probe. Do not move to gunicorn `gthread` (or any threaded worker) without making the client thread-local first
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
| **Wajib Fullscreen** | Ujian hanya bisa dikerjakan dalam layar penuh. Keluar dari layar penuh (Esc), me-restore jendela, atau minimize → overlay memblokir ujian sampai kembali ke layar penuh, dan tercatat sebagai `fullscreen_exit` + dihitung di tangga penalti | Form ujian → Pengaturan Anti-Cheat |
| **Watermark Nama** | Menampilkan nama siswa sebagai watermark di seluruh halaman ujian | Form ujian → Pengaturan Anti-Cheat |
| **Penalti per Pelanggaran** | Base penalti (default 5 poin) — 1st=warning, 2nd=-base, 3rd=-2×base, 4th+=-3×base | Form ujian → Pengaturan Anti-Cheat |
| **Maks Pelanggaran** | Jumlah pelanggaran sebelum auto-submit (default 5) | Form ujian → Pengaturan Anti-Cheat |

**Catatan Penting:**
- `block_screenshot` sudah diperbaiki — sekarang gate-nya ke `block_screenshot`, bukan `block_copy_paste`
- Semua pelanggaran tercatat di tabel `violation_logs` + dihitung server-side di `submit_exam()`
- Yang **dihitung** ke tangga penalti hanya `tab_switch` dan `fullscreen_exit` (`PENALIZED_VIOLATION_TYPES` di `anti_cheat_service.py`); tipe lain (mis. `blur`) hanya dicatat
- Fullscreen adalah state yang **dilaporkan tepat** oleh browser (`document.fullscreenElement`). "Jendela maximized" tidak bisa dideteksi halaman web (tidak ada API-nya; `outerWidth/outerHeight` berubah oleh zoom), jadi fullscreen yang diwajibkan
- Jika browser tidak punya Fullscreen API (Safari iPhone) atau policy mematikannya (`fullscreenEnabled === false`), pemeriksaan ini **berhenti sendiri** — siswa tidak digagalkan karena hal yang tidak bisa ia perbaiki
- Timer ujian divalidasi server-side via `student_sync_draft()` — jika ada mismatch >300 detik antar device, client di-reject
- Jika siswa mematikan JavaScript, server tetap hitung penalti dari violation_logs yang sudah tercatat

### Super Admin / Admin Sekolah
| Fitur | Deskripsi | Letak |
|-------|-----------|-------|
| **Lihat Flag Kecurangan** | Submission dengan `_flags` berisi `suspicious_speed` atau `device_mismatch` akan terlihat di detail submission | Detail submission → `answers._flags` |
| **CSP Header** | Content-Security-Policy set on every response — currently only `frame-ancestors 'self'`. It does **not** restrict `script-src`/`style-src` (no such directive is emitted), so third-party scripts are not blocked by CSP; the X-Content-Type-Options and X-Frame-Options headers beside it are real | `__init__.py` `add_performance_headers` |
| **Speed Analysis** | Jika >5 MCQ dijawab dalam <1.5 detik/soal, submission di-flag suspicious | Server-side di `submit_exam()` |
| **Device Mismatch** | Jika IP atau User-Agent berubah antara first sync dan submit, submission di-flag | Server-side di `submit_exam()` |
| **Timer Reconciliation** | Server memvalidasi `started_at` — jika selisih >300 detik antar device, sync di-reject (409) | `api.py` → `student_sync_draft()` |

### Murid (Peserta Ujian)
| Aturan | Konsekuensi |
|--------|-------------|
| Pindah tab / buka aplikasi lain (visibilitychange) | 1st = **PERINGATAN**, 2nd = **-base poin**, 3rd = **-2×base**, 4th+ = **-3×base** |
| Keluar layar penuh via Esc (`fullscreen_exit`) | Overlay memblokir ujian + masuk tangga penalti yang sama (1st = PERINGATAN). Waktu ujian tetap berjalan |
| Restore-down / perkecil jendela | Terdeteksi (state fullscreen disampel tiap 2 detik, tidak hanya lewat event) → overlay memblokir |
| Minimize | `visibilitychange` → tangga penalti (dihitung 1× saja, tidak dobel dengan overlay) |
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
