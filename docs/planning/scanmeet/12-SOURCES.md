# 12 — Sumber, tanggal dan asumsi

Tanggal penyusunan 31 Oktober 2026 (WIB).

## Sumber repository

- ScanGrade main saat pengecekan: `40d5e8d52da0ddf050a31af19cdd3fa7c85141cc`.
  https://github.com/sugeng-riyanto/scanGrade/tree/40d5e8d52da0ddf050a31af19cdd3fa7c85141cc
- Bahasa Cerdas main saat pengecekan: `ef875badc42650e9e3c03c2050c51152273f85b2`.
  Acuan Jumpa, baca perilaku dari source bukan menyalin endpoint/secrets produksi.
  https://github.com/dominikus02-source/bahasa-cerdas/tree/ef875badc42650e9e3c03c2050c51152273f85b2
- `components/jumpa/media-prefs.ts`: camera=true, mic=true, auto, audioOnly=false,
  noiseSuppression=true, background=none. Ini preferensi, bukan izin capture otomatis.
- `components/jumpa/SettingsPanel.tsx`: perangkat, kualitas, audio-only, noise,
  background, blur premium dan pilihan speaker bila didukung.
- `components/jumpa/media-policy.ts`: adaptive media, low-end/mobile, quality tiers.
- `lib/jumpa/room-actions.ts`: moderation, cohost, lock/chat/screen dan scoped invites.
- `lib/jumpa/tiers.ts`: tier Jumpa berbeda kapasitas; **jangan** mengganti kapasitas
  ScanMeet 40 dengan 50 hanya karena sumber Jumpa. Recording pada tier Jumpa masih
  ditandai belum tersedia; rekaman lokal ScanMeet adalah kebutuhan baru pengguna.
- `lib/billing/plans.ts`: acuan harga Guru Pro bulanan/tahunan yang telah disepakati.

## Sumber browser primer

- https://www.w3.org/TR/mediastream-recording/ — blob parsial tidak selalu playable;
  gabungan chunk dari rekaman selesai harus valid sesuai spesifikasi.
- https://developer.mozilla.org/en-US/docs/Web/API/MediaRecorder/dataavailable_event
  — timeslice tidak presisi dan perilaku lock/background berbeda.
- https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getDisplayMedia
  — perlu tindakan pengguna; dukungan audio bervariasi.

## Batas akurasi

Dokumen ini spesifikasi dan workflow, bukan hasil tes media baru. Tidak menyatakan
seluruh target sudah diimplementasi, green CI baru, uptime saat ini atau kompatibilitas
semua perangkat. Recheck main/sumber/browser pada awal implementasi. Target usia data,
retensi dan ambang hadir belum diputuskan pemilik; jangan mengarang aturan hukum.
Ukuran 1 GB VPS dari konteks pemilik, bukan benchmark baru. Semua contoh endpoint,
tabel baru dan role assignment harus disesuaikan dengan schema/release aktual.
