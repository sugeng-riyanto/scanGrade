# Klarifikasi Peran & "Kepala Sekolah" — rujukan tetap

**Tanggal klarifikasi: 2026-10-13.**
Dokumen ini menutup satu kontradiksi yang sudah **dua kali** memblokir pekerjaan
(fitur `document-verification` dan `final-score-penalty-transparency`). Baca ini
sebelum menyentuh apa pun yang menyebut "peran" atau "Kepala Sekolah".

---

## 1. Daftar peran yang BENAR sekarang: **6 peran**

Diverifikasi langsung ke kode dan skema, bukan README atau laporan lama:

```
super_admin · admin_sekolah · principal · vice_principal · guru · murid
```

| Sumber | Isi |
| --- | --- |
| `app/utils/auth.py` → `DASHBOARD_FOR_ROLE` | tepat 6 kunci (di atas); `ALL_ROLES` diturunkan darinya |
| `supabase/migrations/038_school_officials_roles.sql` | membuka `profiles_role_check` untuk 6 nilai (sebelumnya 4) |
| `deploy/schema_contract.py` (gerbang deploy) | melaporkan **"6 role(s) in the CHECK constraint"** di skema live |

**README lama menyebut 4 peran — itu sudah tidak benar** dan sejak commit
`49b801a` sudah diperbarui. Laporan sesi lain ("principal/vice_principal sudah
fungsional penuh") **benar**. Tidak ada lagi kontradiksi: **6 peran**.

Sifat peran pengawas (tetap, jangan dilebarkan tanpa keputusan produk eksplisit):
`principal`/`vice_principal` adalah **baca-saja berlingkup sekolah** untuk pengawasan
(`docs/RBAC.md`; `exam_access.OFFICIAL_READ_ROLES`; komentar `profiles_role_check`
"laporan dan pengawasan, tanpa wewenang mengubah data"). Mereka **boleh memiliki
kertas ujian sendiri** bila ditugaskan mengajar (`can_manage_exam`), tetapi **tidak
pernah** boleh mengubah kertas kolega. "Tidak ada satu pun route tulis di
`/principal/*`" adalah janji yang diuji `tests/unit/test_analytics_scope.py`.

---

## 2. "Kepala Sekolah" adalah **DUA hal berbeda**, dan itu sumber kebingungannya

Ada satu frasa, dua entitas. Keduanya sah, keduanya dipakai untuk hal berbeda:

### (a) `principal` — **peran login** (akun sendiri)
Kepala sekolah yang *masuk aplikasi*. Akunnya dibuat sekolah lewat
`/admin-sekolah/officials`, punya dashboard `/principal/dashboard`, dan hanya-baca
terhadap data sekolah. Berguna untuk **pengawasan** (grafik, laporan, matriks pengawas).

### (b) `schools.principal_name` — **field teks** (tanpa akun)
Nama kepala sekolah yang **tercetak di dokumen** (rapor, laporan hasil ujian). Kolom
`principal_name TEXT` dan `principal_nip TEXT` ada sejak migrasi `002`/`007`; diisi
`admin_sekolah` lewat Pengaturan Sekolah.

| Bukti | Lokasi |
| --- | --- |
| kolom skema | `supabase/migrations/002_school_classes_nisn.sql:25`, `007_multi_school_role_hierarchy.sql:22-23` |
| form admin mengisinya | `app/templates/admin_sekolah/profile.html:112`, `app/templates/admin/school.html:37` |
| route menyimpannya | `app/routes/admin_sekolah.py:437`, `app/routes/admin.py:185` |

### Kesimpulan definitif untuk **tanda tangan dokumen**: pakai **(b)**
Baris "Mengetahui, Kepala Sekolah" pada dokumen cetak **membaca
`schools.principal_name`**, bukan akun peran `principal`. Alasannya: dokumen adalah
milik **sekolah**, bukan milik seseorang yang kebetulan punya akun; dan sebagian
sekolah tidak pernah membuat akun `principal` sama sekali, tetapi namanya tetap harus
tercetak.

**Kaidah untuk sesi berikutnya:** kalau yang Anda butuh adalah **nama di atas kertas**,
itu `schools.principal_name` (opsi b). Kalau yang Anda butuh adalah **orang yang masuk
aplikasi untuk mengawasi**, itu peran `principal` (opsi a). Jangan pernah menyimpulkan
salah satu menghapus yang lain.

---

## 3. Bug historis yang lahir dari kebingungan ini (dan perbaikannya)

Laporan cetak (`teacher/print_exam_report.html`, `print/report_card.html`) menulis
baris Kepala Sekolah sebagai **garis titik-titik yang di-*hardcode*** —
`(............................................)` — jadi nama kepala sekolah **tidak
pernah** muncul seberapa pun lengkapnya data sekolah. Diperparah: query sekolah di
`report_card_service` (`_fetch_school`/`_fetch_schools`) **tidak memilih**
`principal_name` sama sekali, sehingga bahkan bila template membacanya, nilainya kosong.

Perbaikan (commit yang menyertakan dokumen ini): kedua kolom ikut dibaca, dan kedua
template menampilkan `principal_name` (atau placeholder jujur bila belum diisi).
