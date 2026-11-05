# 07 — Matriks penerimaan

Setiap baris harus memiliki SHA, langkah, expected/actual, alat/versi, platform,
bukti dan PASS/FAIL/SKIPPED/CANNOT_MEASURE. Baris belum dijalankan bukan PASS.

| ID | Skenario minimum | Hasil yang wajib dibuktikan |
| --- | --- | --- |
| E01 | Global OFF / ujian OFF pada load | Tidak meminta izin/membuka kamera/mic atau room |
| E02 | Guru ON sesudah murid membuka soal | Panel muncul tanpa reload/navigation, izin eksplisit |
| E03 | Start/join/reconnect dalam halaman soal | Timer, autosave, jawaban dan submit tidak berubah |
| E04 | Guru OFF saat track aktif | Capture dan publish berhenti, join ditolak, cleanup tahan restart |
| E05 | Dua murid dan pengawas | Murid tidak menerima kamera/audio murid lain, termasuk hacked client |
| E06 | Mic ask/approve/close | Server grants sesuai state; close juga mute track yang sudah aktif |
| E07 | Admin takeover/assignment expiry | Pengawas baru valid; pengawas lama benar-benar kehilangan akses |
| E08 | Nonowner teacher / cross school | GET/POST/token/export/takeover ditolak server |
| E09 | Permission, scroll, rotation, panel | Tidak menimbulkan false penalty atau melonggarkan exit detection |
| E10 | Media gagal/provider down | Tidak mengubah nilai atau mengirim ujian otomatis |
| M01 | Meeting tanpa exam | Room terpisah, 40 total, entitlement/concurrency enforced |
| M02 | Lobby camera/mic OFF/ON | Preferensi sesuai Jumpa, capture hanya sesudah tindakan pengguna |
| M03 | Class/individual/public invite | Target tepat; revoke/expiry/kick/lock berlaku pada token dan media |
| M04 | Host/cohost moderation | Mute/kick/share/chat sesuai grants, bukan tombol saja |
| M05 | Reconnect / double tabs | Identitas/presensi tidak ganda; tidak melebihi participant cap |
| M06 | Duration/expiry/room full | Batas server + SFU dan peringatan UI, kegagalan jelas |
| A01 | Signed webhook valid/invalid/replay | Event valid saja; dedupe; invalid tidak menyentuh presensi |
| A02 | Leave hilang/out-of-order | Reconcile; union interval; estimated/unknown terlihat |
| A03 | Multi-device satu user | Detik overlap tidak double, satu summary |
| A04 | Attendance correction | Alasan, actor, versi dan raw event utuh |
| X01 | PDF 40 peserta / nama panjang | Multi-page rapi, A4, logo, WIB, totals konsisten |
| X02 | XLSX hostile formula/name | Nilai teks aman, filter/freeze, sheet lengkap, totals sama PDF |
| X03 | Expired/download cross-school | Ditolak, private cache/storage, audit aktor |
| R01 | Local Program record | Video semua scene + audio host/peserta sesuai izin; playback terbukti |
| R02 | Recording permission/rejoin | Notice terlihat semua, consent policy, audit, tidak upload video |
| R03 | Crash/disk full/lock/pause/track change | Error terlihat, partial jujur, finalisasi dan fallback teruji |
| R04 | Recording di ujian/hacked client | API/token/clean-feed menolak, tidak sekadar tombol disembunyikan |
| S01 | Green screen/background/light | Preview = publish = recording; reset membersihkan track |
| S02 | Low-end / effect failure | Drop quality atau effects OFF, room tetap dapat dipakai |
| U01 | docs/03 seluruh viewport/theme/lang | Tidak overflow; 44px, keyboard, focus, kontras dan 200% zoom |
| U02 | Android/iPad/iPhone fisik | Core media terbukti, opsi unsupported diberi fallback |
| P01 | Paket/quota race/takeover | Atomic, kontrak lama utuh, tidak charge/reserve ulang transfer |
| P02 | Load memadai untuk target pilot | Ukur CPU/RAM/request/SFU traffic, bukan klaim 500 video gratis |
| D01 | Deployment/rollback | All workers SHA tepat, tidak blind/mixed, health dan alur nyata lulus |

Tes negatif sebelum perbaikan untuk bug yang ditangani. Uji unit, route/RBAC, DOM,
PostgreSQL nyata terisolasi, SFU integration, signed webhooks, real WebRTC multi-browser,
perangkat fisik dan ekspor render/playback adalah lapisan berbeda; jangan substitusi
satu sama lain. Windows suite dan seluruh gate wajib repository tetap dijalankan.
Tidak mewajibkan semua tes berat di VPS; gunakan CI atau mesin staging terpisah.
