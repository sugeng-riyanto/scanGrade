# ScanGrade — Supabase staging cepat dan aman

## Tujuan
Jalankan tes isolasi data Sekolah A dan Sekolah B pada **proyek Supabase Cloud terpisah**. Jangan gunakan data siswa asli, dump produksi, atau kunci service-role untuk permintaan RLS.

## 1. Buat proyek
1. Di dashboard Supabase, buat **New project** bernama `scangrade-staging`, region terdekat dengan VPS, dan password database yang kuat.
2. Simpan password database di password manager, bukan di Git, chat, atau file `.env` yang di-commit.
3. Ambil Project URL dan anon/publishable key dari pengaturan API. Workflow ini mengharapkan anon key yang diterima PostgREST sebagai `apikey`.
4. Di GitHub repository → Settings → Environments, buat environment **staging**. Tambahkan protection/approval bila tersedia.
5. Jangan mengaktifkan akses publik atau deployment aplikasi staging sebelum schema dan RLS diaudit.

## 2. Siapkan skema (hanya staging)
Repository ini memiliki riwayat migrasi dengan **nomor duplikat** (misalnya 001, 002, 012, 013), dan `001_initial_schema.sql` hanya placeholder. **Jangan** menganggap menjalankan seluruh file berdasarkan urutan alfabet pasti aman. Gunakan jalur provisioning/schema yang sudah diverifikasi proyek, periksa dependensi dan idempotensi setiap migrasi, lalu jalankan pemeriksaan `deploy/schema_contract.py` terhadap staging. Bila belum ada jalur yang terbukti, hentikan provisioning sebelum menulis data.

Pastikan kebijakan RLS yang berlaku mencakup perubahan least-privilege pada `supabase/migrations/028_rbac_least_privilege.sql` dan migrasi setelahnya. Jangan menyalin schema produksi secara membabi buta.

## 3. Data fixture fiktif
- Buat Sekolah A dan Sekolah B, masing-masing dengan akun guru **di Supabase Auth staging** dan profil/membership yang konsisten.
- Buat setidaknya satu ujian milik Sekolah B dan satu submission yang mengacu ke ujian itu.
- Simpan UUID ujian dan submission Sekolah B. Pastikan record memang ada menggunakan koneksi admin staging (hanya untuk *setup*, bukan tes).
- Dapatkan **access token guru A** dari login Supabase Auth staging. Token harus valid saat tes dijalankan; token yang kedaluwarsa menyebabkan HTTP 401 dan tes gagal.
- Sebelum menilai keamanan, verifikasi akun B dapat membaca record miliknya sendiri (positive control). Jika tidak, hasil kosong untuk guru A bisa merupakan false positive akibat fixture rusak atau kebijakan menutup semua akses.

## 4. Konfigurasi GitHub Environment `staging`
Environment **Secrets**:
- `SCANGRADE_RLS_URL` — URL Supabase staging
- `SCANGRADE_RLS_ANON_KEY` — anon key staging
- `SCANGRADE_RLS_TEACHER_A_JWT` — access token guru A staging yang masih valid

Environment **Variables**:
- `SCANGRADE_RLS_SCHOOL_B_EXAM_ID` — UUID ujian B
- `SCANGRADE_RLS_SCHOOL_B_SUBMISSION_ID` — UUID submission B

Jangan isi nilai produksi. Jangan gunakan `SUPABASE_SERVICE_KEY` untuk tes RLS. Rahasiakan token login dan jangan masukkan ke issue/PR/log.

## 5. Jalankan
GitHub → Actions → **ScanGrade staging RLS** → Run workflow → pilih branch audit yang diuji.

Tes di `tests/test_rls_security.py::TestSupabaseRLSPolicies` melakukan GET ke PostgREST dengan token guru A dan menolak bila record Sekolah B terlihat. Kedua tes harus **passed**, bukan skipped. Workflow sengaja gagal bila secret/variable belum lengkap.

## 6. Gate sebelum VPS
- Full CI dan Browser Gate hijau pada **commit yang sama**.
- Tes RLS staging hijau, dengan positive control diverifikasi.
- Tidak ada perubahan migrasi produksi tanpa backup, review, dan rollback plan.
- Deploy hanya setelah ada approval; cek health, autentikasi, dan isolasi sekolah sesudah deploy.

**Catatan batasan:** pengujian RLS saat ini baru dua negative controls. Belum cukup untuk menyatakan semua tabel atau operasi aman; tambah positive controls dan INSERT/UPDATE/DELETE pada staging sebelum menjadikannya sertifikasi isolasi menyeluruh.
