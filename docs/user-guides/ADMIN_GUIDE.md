# Panduan Admin Sekolah

## 1. Login

Buka `/auth/login` dan masuk dengan akun admin sekolah (demo: `admin_smp@scan-grade.app` / `demo123`).

## 2. Dashboard

Dashboard menampilkan:
- Total guru, siswa, kelas
- Ujian aktif
- Status langganan
- Grafik distribusi nilai

## 3. Manajemen Kelas

1. Buka menu **Kelas**
2. **Tambah**: isi Nama Kelas + Tingkat (7/8/9/10/11/12)
3. **Edit**: ubah nama kelas
4. **Hapus**: hanya jika tidak ada siswa di kelas tersebut

## 4. Manajemen Mata Pelajaran

1. Buka menu **Mata Pelajaran**
2. Tambah: Nama + Kode (opsional, contoh: MTK, IPA, BIN)
3. Mata pelajaran digunakan saat guru membuat ujian

## 5. Manajemen Guru

1. Buka menu **Guru**
2. **Tambah Manual**: isi NIP, Nama, Mapel, Email, No HP
3. **Import XLSX**: download template → isi data → upload
4. **Edit**: ubah data guru
5. **Reset Password**: generate password baru
6. **Hapus**: hapus guru dan akunnya

Format Import XLSX: `NPSN, Tahun Ajaran, NIP, Email, Nama, Mapel1, Mapel2, Mapel3, Password`

## 6. Manajemen Siswa

1. Buka menu **Siswa**
2. **Tambah Manual**: isi NISN, Nama, Kelas, Email
3. **Import XLSX**: download template → upload
4. **Import CSV** (baru!): upload file CSV via `/students/import`
   - Format: `nama, nisn, email, kelas, password`
   - 500 siswa dalam <1 menit
5. **Edit**: ubah data siswa
6. **Reset Password**: generate password baru
7. **Hapus**: hapus siswa dan akunnya

## 7. Tahun Ajaran

1. Buka menu **Tahun Ajaran**
2. Tambah: Nama (contoh: 2025/2026), Tanggal Mulai, Tanggal Selesai
3. Aktifkan tahun ajaran berjalan

## 8. Langganan & Tagihan

1. Buka menu **Langganan**
2. Lihat status trial / aktif / expired
3. **Beli Paket**: pilih paket → bayar via Midtrans
4. **Tukar Kode Aktivasi**: masukkan kode SG-XXXX-XXXX-XXXX
5. Lihat histori transaksi & invoice

## 9. Profil Sekolah

1. Buka **Pengaturan** → **Profil Sekolah**
2. Edit: Nama, NPSN, Alamat, Telepon, Email, Logo
3. Atur domain email untuk akun otomatis

## Troubleshooting

| Masalah | Solusi |
|---------|--------|
| Tidak bisa tambah guru/siswa | Cek kuota langganan |
| Import gagal | Pastikan format file sesuai template |
| Lupa password admin | Hubungi Super Admin untuk reset |
| Pembayaran gagal | Cek setting Midtrans di Super Admin |
