# ScanGrade Mobile — paket persiapan

Pemilik: Sugeng Riyanto, M.Sc. | Disiapkan: 30 Oktober 2026
Status: rencana awal dan instruksi agent. Belum ada aplikasi Flutter, koneksi backend, pengujian perangkat, atau APK/IPA yang dibuat.

Target: aplikasi Android dan iOS menggunakan Flutter, dikerjakan melalui Android Studio dengan bantuan AI.

## Urutan penggunaan

1. Ikuti SETUP_WINDOWS.md.
2. Dari folder mobile pada clone repo ini, buat proyek Flutter sesuai panduan. Dokumen tetap berada di folder mobile.
3. Baca PRODUCT.md untuk ruang lingkup.
4. Jalankan agent pada clone repo. Berikan isi PROMPT_START.md; baca AGENTS.md utama lalu mobile/AGENTS.md. Target implementasi yang direncanakan adalah subfolder scangrade_mobile di dalam mobile.
5. Ikuti TASKS.md. Catat bukti pekerjaan di STATUS.md.

## Isi paket

| File | Fungsi |
|---|---|
| SETUP_WINDOWS.md | Instalasi dan menjalankan proyek Android |
| PRODUCT.md | Spesifikasi, arsitektur, dan batas tahap awal |
| AGENTS.md | Aturan pengerjaan untuk AI coding agent |
| PROMPT_START.md | Prompt yang langsung dapat diberikan ke AI |
| TASKS.md | Tahapan dan kriteria selesai |
| STATUS.md | Catatan status dan bukti verifikasi |

## Memilih AI

- ChatGPT melalui chat: kirim file proyek atau repo dan error; hasil perubahan harus diterapkan pada salinan proyek yang dipakai Android Studio. Chat tidak otomatis memiliki akses ke laptop.
- Agent coding lokal: jalankan di folder proyek yang sama dengan Android Studio agar perubahan terlihat langsung di IDE. Mulai dengan satu agent penulis.
- Qwen Code: coding agent terminal; ikuti instalasi dan autentikasi resmi di https://github.com/QwenLM/qwen-code. Ketersediaan, biaya, dan kuota bergantung penyedia serta akun.
- Freebuff: identitas produk dan cara instalasinya belum dikonfirmasi. Gunakan prompt yang sama hanya setelah memastikan alatnya dapat membaca dan mengubah folder proyek.
- File AGENTS.md tidak otomatis dibaca semua alat; selalu minta agent membacanya di awal sesi.

Paket ini menambahkan dokumentasi mobile di repo ScanGrade. Agent lokal dan akun AI belum dipasang atau dihubungkan. Kode aplikasi dan konfigurasi produksi tidak berubah.

## Dokumentasi resmi

- Flutter: https://docs.flutter.dev/install
- Android Studio: https://developer.android.com/studio
- Flutter CLI: https://docs.flutter.dev/reference/flutter-cli
- iOS: https://docs.flutter.dev/platform-integration/ios/setup
- Qwen Code: https://github.com/QwenLM/qwen-code

## Struktur dalam repo

Dokumen persiapan berada di mobile. Folder aplikasi berikut masih planned, belum dibuat:

```text
mobile/
  AGENTS.md
  README.md
  PRODUCT.md
  PROMPT_START.md
  SETUP_WINDOWS.md
  TASKS.md
  STATUS.md
  scangrade_mobile/   # planned: pubspec.yaml, lib/, test/, android/, ios/
```

AGENTS.md utama berlaku untuk seluruh repo; aturan mobile menambah batas khusus pekerjaan mobile. Dokumentasi persiapan ini dapat dibaca tanpa memasang Flutter.
