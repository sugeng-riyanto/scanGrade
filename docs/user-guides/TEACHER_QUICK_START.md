# Panduan Guru — Quick Start

ScanGrade membantu Anda membuat ujian digital, mengoreksi otomatis (OMR + AI), dan menganalisis hasil.

## 1. Login

1. Buka `/auth/login-user`
2. Masukkan email dan password (demo: `guru_mtk_smp@scan-grade.app` / `demo123`)
3. Klik **Masuk**

## 2. Membuat Ujian Baru

1. Klik **Buat Ujian** di dashboard
2. Isi: Judul, Mata Pelajaran, Durasi, Jumlah Soal
3. Klik **Simpan Draft** → masuk ke halaman detail ujian

## 3. Menambahkan Soal

- **MCQ**: Klik "Tambah Soal" → pilih MCQ → ketik opsi A/B/C/D/E → centang jawaban benar
- **Esai Teks**: Pilih tipe "Esai Teks" → siswa mengetik jawaban paragraf
- **Esai Canvas**: Pilih "Esai Canvas" → siswa menggambar/menulis di atas PDF

Atur bobot nilai per soal di kolom "Bobot".

## 4. Upload PDF Soal (Opsional)

Jika ujian menggunakan PDF:
1. Klik tab **PDF**
2. Upload file PDF (maks 50MB)
3. Sistem akan konversi setiap halaman jadi gambar

## 5. Atur Anti-Cheat

Centang fitur yang diinginkan:
- Deteksi pindah tab (penalti bertahap)
- Wajib layar penuh
- Blokir copy-paste
- Watermark nama siswa
- Acak soal & opsi

## 6. Publikasikan Ujian

Klik **Publikasikan** → siswa bisa mulai mengerjakan.

## 7. Lihat Hasil

1. Buka menu **Hasil Ujian**
2. Lihat daftar siswa + skor + penalti
3. Klik **Detail** untuk lihat jawaban per soal

## 8. Koreksi dengan AI

1. Di halaman detail siswa, klik **Koreksi AI**
2. AI memberi skor + feedback
3. Guru bisa override skor manual

## 9. Export Nilai

- **XLSX**: Klik Export → unduh Excel dengan kolom Bahasa Indonesia
- **PDF**: Export per siswa lengkap dengan jawaban canvas

## Troubleshooting

| Masalah | Solusi |
|---------|--------|
| Siswa tidak lihat ujian | Pastikan ujian sudah dipublikasi & kelas siswa sesuai |
| AI grading error | Cek API Key di Pengaturan AI → Test Key |
| File PDF terlalu besar | Kompres PDF (max 50MB) |
| Lupa password | Hubungi admin sekolah untuk reset |
