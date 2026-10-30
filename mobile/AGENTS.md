# Aturan untuk coding agent ScanGrade Mobile

## Tujuan dan kewenangan

Kerjakan aplikasi Flutter secara bertahap sesuai PRODUCT.md dan TASKS.md. Selesaikan implementasi lokal dan pemeriksaan yang tersedia tanpa berhenti hanya pada rencana. Mulai dari demo menggunakan data sintetis. Instruksi langsung pemilik menentukan ruang lingkup pekerjaan.

Baca AGENTS.md di akar repo serta instruksi repo lain yang berlaku. Kemudian baca README.md, PRODUCT.md, TASKS.md, dan STATUS.md di folder mobile sebelum mengubah kode. Periksa git status; jangan menimpa perubahan pengguna. Jangan mengasumsikan file ini dibaca otomatis oleh alat.

Dokumen ini berlaku untuk subtree mobile dan melengkapi instruksi akar repo. Source Flutter yang direncanakan berada di subfolder scangrade_mobile; jangan membuat proyek Flutter di akar backend Flask. Jalankan perintah Flutter dari folder aplikasi setelah tersedia.

Ikuti kebijakan bahasa repo: Inggris default, toggle Indonesia, pilihan pengguna disimpan. Untuk draft ujian nyata, selaraskan pemulihan lokal dan sinkronisasi dengan kontrak backend; state dalam memori hanya cukup untuk demo.

## Cara bekerja

1. Catat folder kerja, status Git, dan versi Flutter/Dart; jangan membaca atau mencetak kredensial.
2. Pilih tugas berikut yang dependensinya terpenuhi. Ubah seperlunya; jangan refactor bagian lain tanpa kebutuhan.
3. Gunakan satu agent penulis per working tree. Hindari dua alat mengedit file yang sama bersamaan.
4. Perbaiki kegagalan yang terkait perubahan. Tulis tes perilaku penting, bukan tes yang sekadar menyalin implementasi.
5. Perbarui STATUS.md dengan perubahan, perintah, hasil, dan blocker aktual. Jangan menandai PASS bila belum dijalankan.
6. Laporkan ringkas dalam Bahasa Indonesia: hasil, cara mencoba, pemeriksaan, dan hambatan nyata.

## Batas perubahan

Persiapan awal hanya lokal/demo. Jangan mengubah database, konfigurasi produksi, migrasi, atau sistem ujian yang sedang berjalan. Integrasi backend dimulai dengan inspeksi sumber secara read-only jika akses tersedia. Jangan membuat akun, repo, commit/push, deploy, atau rilis ke store kecuali diminta dalam sesi. Tidak perlu meminta konfirmasi untuk pekerjaan lokal yang sudah masuk ruang lingkup.

Jangan menyertakan password, token, private key, data siswa asli, atau signing material dalam file, prompt, test fixture, screenshot, dan log. Jangan menjadikan API key server sebagai konfigurasi Flutter; isi aplikasi bisa dibaca pengguna perangkat.

## Pemeriksaan yang relevan

```text
dart format --output=none --set-exit-if-changed lib test
flutter analyze
flutter test
flutter build apk --debug
```

Jalankan pada struktur yang ada; formatter boleh dijalankan lebih dahulu untuk memperbaiki format. Periksa demo pada HP/emulator jika tersedia. Bila toolchain/perangkat tidak tersedia, catat NOT RUN dan alasan; lanjutkan pekerjaan independen yang memungkinkan. Jangan mengklaim pengujian iOS berdasarkan build Android.

## Perilaku ujian

Demo selalu terlihat sebagai demo. Waktu dan izin ujian nyata berasal dari server. Tidak ada klaim sukses terkirim tanpa tanda terima server. Uji retry, respons hilang, token habis, dan akses lintas siswa ketika integrasi tersedia. Tidak ada klaim anti-cheat/SEB/mobile ScanMeet siap tanpa bukti perangkat dan server.
