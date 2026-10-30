# Spesifikasi awal ScanGrade Mobile

Status: rancangan usulan; kontrak backend belum diperiksa. Repo backend rujukan: https://github.com/sugeng-riyanto/scanGrade. Jangan menganggap endpoint, autentikasi, atau skema tersedia sebelum inspeksi kode pada commit yang dicatat.

## Sasaran

Siswa dapat mengakses ujian dengan antarmuka ponsel yang jelas. Guru dapat melihat daftar ujian dan ringkasan peserta. Mulai Android, lalu verifikasi iOS secara terpisah.

## MVP prototipe

- Login demo yang diberi label DATA CONTOH; pilihan akun siswa/guru hanya dalam mode demo.
- Siswa: daftar ujian, detail, instruksi, soal pilihan ganda, navigasi soal, status jawaban tersimpan dalam sesi demo, konfirmasi pengumpulan, tanda terima demo, dan halaman hasil contoh.
- Guru: daftar ujian dan ringkasan status peserta menggunakan data contoh.
- Tampilan loading, kosong, error, dan retry; Bahasa Inggris sebagai default dan toggle Bahasa Indonesia yang menyimpan pilihan pengguna, mengikuti kebijakan repo; tata letak untuk HP dan ukuran teks aksesibel.
- Jangan mengklaim nilai, pengumpulan, atau autentikasi sebagai data nyata.

## Integrasi berikutnya

Adapter API menghubungkan tampilan ke backend yang sudah diverifikasi. Hak akses, jadwal ujian, tenggat, akses soal, penerimaan jawaban, dan hasil ditentukan server. UI tidak menjadi batas keamanan.

Sebelum ujian nyata, tulis API_CONTRACT.md dari bukti repo/staging: metode dan path, autentikasi, request/response, error, role, kepemilikan, versi, waktu server, serta aturan retry. Tandai setiap hal yang belum diketahui. Jangan membuat endpoint produksi berdasarkan tebakan.

Pengumpulan jawaban harus mempunyai tanda terima server; retry tidak boleh menimbulkan submission ganda. Gunakan mekanisme idempotensi yang disepakati backend. Bedakan status lokal, menunggu sinkronisasi, tersimpan server, dan final. Rekonsiliasi setelah koneksi putus dan uji kehilangan respons setelah server menerima submission.

Sasaran backend mencakup koneksi WiFi tidak stabil. MVP demo hanya menyimpan jawaban selama sesi; sebelum pilot nyata, persistensi draft lokal yang aman dan pemulihan setelah proses dihentikan wajib dirancang dan diuji. Ini bukan janji pengerjaan penuh tanpa internet.

## Arsitektur usulan

Flutter/Dart dengan Material, pemisahan tampilan, logika sesi ujian, dan repository data. Mulai dengan SDK bawaan; tambahkan dependensi hanya jika ada kebutuhan yang jelas.

Lokasi usulan: lib/app untuk konfigurasi/navigasi, lib/core untuk error dan transport, lib/features/auth, lib/features/exams, lib/features/teacher, serta lib/data untuk adapter demo/API. Struktur boleh disederhanakan sesuai ukuran implementasi.

Pisahkan lingkungan demo, staging, dan production. URL API bukan rahasia; kredensial server, private key, service-role key, atau signing key tidak boleh masuk kode/aplikasi/log. Token sesi mengikuti kontrak backend dan disimpan menggunakan penyimpanan aman platform setelah integrasi.

## Batas fitur lanjutan

- Prototipe belum menjamin anti-cheat atau penguncian perangkat. Jangan melabeli aplikasi setara SEB. Kebijakan pembatasan perangkat harus diuji per OS dan metode pengelolaan perangkat.
- ScanMeet belum termasuk MVP. Target rancangan berikutnya: kamera wajib, mikrofon dengan persetujuan guru, chat nonaktif, angkat tangan aktif, recording nonaktif, grid. Ini spesifikasi yang perlu diuji; bukan klaim kesiapan mobile.
- Integrasi ScanMeet harus membuktikan isolasi media siswa dan penghentian sesi sebelum pilot.
- Esai, unggah berkas, push notification, dan pengerjaan penuh tanpa internet ditunda.

## Kriteria kesiapan pilot nyata

Kontrak API tervalidasi; izin dan isolasi lintas siswa terbukti di server; timer mengikuti waktu server; pemulihan jaringan dan pengumpulan tidak kehilangan jawaban; sesi habis ditangani; hasil mengikuti kebijakan publikasi server; Android diuji pada perangkat nyata. iOS membutuhkan bukti tersendiri. Rilis produksi mengikuti persetujuan pemilik setelah bukti tersedia.
