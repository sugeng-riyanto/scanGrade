# Anti-Cheat System

## Yang Dimonitor

| Event | Deteksi | Aksi |
|-------|---------|------|
| **Pindah tab** | `visibilitychange` + timer 1.5s | Penalti bertahap |
| **Keluar layar penuh** | `fullscreenchange` | Peringatan |
| **Klik kanan** | `contextmenu` | Diblokir |
| **Copy/Paste** | `copy`, `cut`, `paste` event | Diblokir |

## Graduated Penalty

| Pelanggaran ke- | Penalti |
|----------------|---------|
| 1 | Peringatan (warning) |
| 2 | -N poin (default: -5) |
| 3 | -2N poin (default: -10) |
| 4+ | -3N poin (default: -15) per pelanggaran |

`N = penalty_per_violation` (dapat diatur per ujian, default 5)

Jika `max_violations` tercapai dan `auto_submit_on_max = true`:
- Ujian otomatis dikumpulkan
- Semua jawaban tersimpan

## Konfigurasi Per Ujian

Guru dapat mengatur saat membuat/mengedit ujian:

| Setting | Default | Deskripsi |
|---------|---------|-----------|
| Anti-Cheat Aktif | ✅ | Master switch |
| Penalti per Pelanggaran | 5 | Poin dikurangi |
| Maks Pelanggaran | 5 | Sebelum auto-submit |
| Auto Submit | ✅ | Kumpulkan otomatis |
| Wajib Layar Penuh | ✅ | F11 required |
| Blokir Copy-Paste | ✅ | Clipboard diblokir |
| Blokir Klik Kanan | ✅ | Context menu diblokir |
| Watermark Nama | ✅ | Nama siswa di overlay |
| Acak Soal | ❌ | Fisher-Yates shuffle |
| Acak Opsi | ❌ | Opsi diacak per siswa |

## Watermark

Jika diaktifkan, nama siswa ditampilkan sebagai watermark transparan (6x4 grid, rotasi -25°) di seluruh halaman ujian. Mencegah foto layar/share jawaban.

## Logging

Semua pelanggaran dicatat ke tabel `violation_logs`:
- `exam_id`, `user_id`, `violation_type`, `metadata`
- Penalti disimpan di `submissions.penalty`
- Dashboard guru menampilkan total penalti per siswa

## False Positive Handling

- Peringatan pertama hanya teguran (tanpa penalti)
- Guru bisa membatalkan penalti dengan override score manual
- Siswa bisa mengajukan retraction (penarikan pengumpulan)
