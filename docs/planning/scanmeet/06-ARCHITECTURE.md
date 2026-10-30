# 06 — Arsitektur dan kontrak implementasi

## Audit awal wajib

Main acuan paket: 40d5e8d52da0ddf050a31af19cdd3fa7c85141cc. Ambil main saat mulai.
Kode diketahui: app/routes/live.py; services live_policy, live_exam_toggle,
livekit_gateway, live_cleanup, live_reconcile, scanmeet_packages; templates live/exam,
teacher/exam_form dan student/take_exam; JS scan-live-exam.js; migrations 070–077.
Baca AGENTS.md, gate deploy dan schema_contract yang benar-benar ada.

Konfirmasi dengan inspeksi: global dual flags, per-exam OFF, school entitlement,
creator-only camera subscription, recording prohibition exam, quota trigger dan
cleanup debt. CLASS_POLICY ada, tetapi jangan menganggap route meeting sudah ada.
Dokumen lama menyebut PR/staging lama: jangan menganggap statusnya masih berlaku.

## Batas modul yang diusulkan

- exam integration: state, teacher/admin controls, embedded student media panel;
- meeting rooms: lifecycle, audience, host/co-host dan moderation;
- access policy: school scoping + assignments + invitations/admission;
- attendance: signed events, idempotency, interval union, reconcile dan summary;
- reporting: private PDF/XLSX, filters dan export audit;
- media client: transport, reconnect, permission/track lifecycle;
- studio/recorder: device-local effects, Program renderer, mixer dan local persistence.

UI bukan pagar akses. Token SFU scoped pada room, TTL, role, publish sources,
subscription isolation dan deadline. Tidak ada token room-admin/record server bagi
browser. Camera student exam tetap hanya untuk pengawas aktif yang ditugaskan.

## Kontrak API usulan, belum endpoint tersedia

| Operasi | Kontrak minimum |
| --- | --- |
| Exam status | enabled, availability reason, active session, supervisor, expiry, own access |
| Exam toggle/start/end | CSRF, management assignment, entitlement, year lock, idempotency |
| Supervisor handover | assignment, expected_version, reason, transfer state, audit id |
| Meeting CRUD/start/end | school scope, version, audience, one room budget, deadline |
| Invite/create/revoke/admit | opaque token, scope, expiry, revocation and waiting state |
| Join token | authenticated/admitted identity, limits, policy, private no-store response |
| Recording state | authorized start/stop, participant notice/consent, audit, no media body |
| Attendance/read/export | scoped filters, authorization on export/download, immutable audit |

Reuse route/service names bila tepat; jangan memaksakan semua room menjadi exam dummy.
Meeting tidak boleh memerlukan soal atau membuat ujian palsu. Token umum tidak dapat
digunakan di room ujian. Pisahkan flags dan status UI meeting dari dual kill switch
ujian, dengan master kill switch yang tetap dapat mematikan keseluruhan media.

## Database dan skalabilitas

Forward-only additive migration, RLS dan explicit grants default deny, FK sekolah
konsisten, indeks school/session/status/time, unique event id dan unique live room
sesuai kontrak. Review SECURITY INVOKER/DEFINER/search_path dan hak service role.
Quota reservation concurrency harus atomik mencakup room ujian + meeting; jangan
memakai count lalu insert terpisah tanpa lock. Deadline tak dapat diperpanjang klien.

Presensi write append/idempotent; paginate riwayat; bounded export, background jobs
jika perlu. Tidak polling satu query per murid per detik. Ukur budget request dan
cache scoped dengan invalidation; server 1 GB tidak memproses/transcode video atau
menjalankan puluhan Chrome. Media provider menangani SFU; perangkat memproses efek.

Tidak menerapkan DDL dari paket ini. Agen menyiapkan migrasi, tes DB terisolasi,
backup terukur dan rencana pemulihan; operator menjalankan sesuai kebijakan repo.
