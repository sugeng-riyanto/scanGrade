# Tahapan pekerjaan

Semua kotak masih kosong: dokumen ini bukan bukti pekerjaan selesai.

## Fase 0 — lingkungan

- [ ] Periksa kompatibilitas Windows/perangkat dan instal Android Studio, Git, Flutter, plugin Flutter/Dart.
- [ ] Catat versi dan hasil flutter doctor untuk target Android.
- [ ] Buat proyek dan jalankan aplikasi dasar pada HP atau emulator.

Selesai jika aplikasi dasar tampil di perangkat dan bukti perangkat/toolchain dicatat.

## Fase 1 — prototipe demo

- [ ] Pisahkan data demo dan logika tampilan; selalu tampilkan label DATA CONTOH.
- [ ] Bahasa Inggris default, toggle Indonesia, dan penyimpanan pilihan bahasa.
- [ ] Login demo siswa/guru dan logout yang membersihkan state sesi.
- [ ] Daftar/detail ujian dan instruksi.
- [ ] Soal pilihan ganda, navigasi, dan jawaban bertahan saat pindah soal dalam sesi.
- [ ] Konfirmasi pengumpulan, cegah klik ganda, tanda terima dan hasil demo.
- [ ] Dashboard guru serta status loading/kosong/error/retry.
- [ ] Tes perilaku sesi, navigasi, serta submit; analyze dan debug APK.
- [ ] Pemeriksaan pada HP: keyboard, ukuran teks, orientasi, tombol kembali.

Selesai jika alur siswa/guru dapat dicoba, pemeriksaan aktual dilaporkan, dan tidak ada klaim data nyata.

## Fase 2 — penemuan API dan staging

- [ ] Catat commit backend yang diperiksa dan tulis API_CONTRACT.md berdasarkan kode/bukti.
- [ ] Konfirmasi staging terpisah, akun sintetis dan metode autentikasi.
- [ ] Implementasikan adapter sesuai kontrak, batas izin server dan penanganan sesi habis.
- [ ] Verifikasi waktu server, akses soal, autosave, resume, retry dan submit idempoten.
- [ ] Uji akses silang dua siswa dan guru, token kedaluwarsa, koneksi putus, respons submit hilang.
- [ ] Verifikasi kebijakan publikasi hasil.

Dependensi: akses kode/backend dan lingkungan staging yang sah. Temuan API tidak otomatis mengizinkan modifikasi produksi.

## Fase 3 — pilot Android

- [ ] Uji perangkat nyata dan skenario jaringan buruk, latar belakang, serta aplikasi ditutup.
- [ ] Tentukan kebijakan sisa jawaban lokal, privasi, retensi dan logging.
- [ ] Konfirmasi application ID resmi, versi, signing, serta penyimpanan kunci di luar repo.
- [ ] Siapkan release Android dan bukti penerimaan untuk ditinjau pemilik.

## Fase 4 — iOS

- [ ] Sediakan Mac atau runner macOS dengan Xcode.
- [ ] Konfigurasikan identitas aplikasi, signing dan kemampuan platform.
- [ ] Jalankan pemeriksaan dan uji iPhone dengan skenario yang setara Android.
- [ ] Siapkan distribusi pengujian sesuai akun Apple yang tersedia.

## Fase lanjutan — ScanMeet dan mode ujian

- [ ] Evaluasi izin kamera/mikrofon, lifecycle dan kompatibilitas SDK pada kedua OS.
- [ ] Buktikan isolasi siswa, persetujuan mikrofon, dan penutupan sesi.
- [ ] Tentukan batas penguncian perangkat per OS dan kebijakan perangkat sekolah.

Belum termasuk pekerjaan implementasi awal.
