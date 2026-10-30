# Prompt awal untuk AI coding agent

Salin teks di bawah ke agent yang memiliki akses folder proyek Flutter. Untuk chat biasa, lampirkan file yang disebutkan dan jelaskan bahwa toolchain ada di laptop; jangan menganggap chat dapat menjalankan perintah di laptop.

---

Anda membantu Sugeng Riyanto membuat ScanGrade Mobile menggunakan Flutter untuk Android dan iOS. Bekerjalah pada clone repo ScanGrade. Target aplikasi yang direncanakan adalah subfolder scangrade_mobile di dalam mobile. Jangan mengubah backend untuk pekerjaan prototipe ini.

Baca AGENTS.md di akar repo, lalu AGENTS.md, README.md, PRODUCT.md, TASKS.md, dan STATUS.md di folder mobile. Periksa instruksi repo yang berlaku dan status Git. Lindungi perubahan pengguna. Jangan menampilkan rahasia.

Kerjakan fase 0 dan 1 sampai menghasilkan prototipe demo yang bisa diuji: login demo siswa/guru; daftar dan detail ujian; alur soal pilihan ganda dengan navigasi, status jawaban dalam sesi, konfirmasi pengumpulan dan tanda terima demo; hasil contoh; dashboard ringkas guru. Semua data sintetis dan diberi label DATA CONTOH. Gunakan tampilan responsif, Bahasa Inggris default, dan toggle Bahasa Indonesia yang menyimpan pilihan.

Jika proyek belum dibuat dan Flutter tersedia, buat proyek Flutter pada subfolder scangrade_mobile di dalam mobile yang belum ada, tanpa menimpa dokumen atau kode. Buka subfolder aplikasi tersebut di Android Studio dan jalankan perintah Flutter dari sana. Jika Flutter belum tersedia, catat hambatannya, siapkan pekerjaan yang dapat dikerjakan, lalu berikan satu langkah instalasi berikut yang konkret. Jangan mengklaim aplikasi sudah dibuild.

Pisahkan repository demo dari adapter API masa depan. Jangan mengarang endpoint atau menyentuh produksi. Buat tes perilaku untuk navigasi soal, pemeliharaan jawaban dalam sesi, dan konfirmasi pengumpulan. Jalankan format, analyze, tests, dan build APK debug jika toolchain tersedia; perbaiki kegagalan terkait perubahan. Uji pada perangkat bila tersedia.

Perbarui TASKS.md dan STATUS.md berdasarkan bukti. Akhiri dengan laporan singkat: yang selesai, cara menjalankan, hasil pemeriksaan, dan blocker. Jangan berhenti pada proposal jika implementasi lokal masih memungkinkan.

---

## Prompt sesi berikutnya

Baca AGENTS.md utama, mobile/AGENTS.md, dan mobile/STATUS.md. Lanjutkan tugas berikut yang dependensinya terpenuhi di TASKS.md dalam ruang lingkup yang sudah diizinkan. Validasi perubahan dan perbarui bukti. Jika fase berikut memerlukan backend yang belum dapat diverifikasi, dokumentasikan kontrak yang belum diketahui dan lanjutkan pekerjaan lokal yang independen.
