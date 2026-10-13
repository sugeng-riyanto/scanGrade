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

---

## 4. Peran pejabat di sistem pesan — **sudah diputuskan dan dibangun** (2026-10-13)

Kepala sekolah dan wakilnya adalah **staf yang membaca sekolah**, dan pesan bukan
data sekolah. Karena itu mereka boleh berkomunikasi: mereka bisa memulai percakapan
dengan staf yang mereka awasi, dengan sesama pejabat, dan dengan admin sekolah;
sekolah bisa menulis kepada mereka; dan mereka bisa mengirim pengumuman ke sekolah.
Yang **tidak** berubah: mereka tetap tidak punya satu pun wewenang menulis data
sekolah (bagian 1), dan halaman komunikasi mereka hanya GET — pesannya sendiri
ditulis lewat `/api/percakapan` dan `/api/pengumuman`, yang di-scope oleh peran
pemanggilnya.

Tabelnya adalah `_ALLOWED_RECIPIENT_ROLES` di `app/routes/api.py` — satu tabel untuk
percakapan **dan** pengumuman, dan itu sengaja: seorang penerima adalah penerima.
Tiga hal yang harus dibaca bersama tabel itu:

| | Daftar `_get_allowed_recipient_roles` |
| --- | --- |
| `super_admin` | guru, murid, admin sekolah, kepala sekolah, wakil, super admin |
| `admin_sekolah` | super admin, guru, murid, kepala sekolah, wakil |
| `principal` / `vice_principal` | super admin, admin sekolah, guru, murid, kepala sekolah, wakil |
| `guru` | admin sekolah, guru, murid, kepala sekolah, wakil |
| `murid` | guru, admin sekolah |

1. **Daftar itu satu arah; percakapan dua arah.** `api_create_percakapan` menuntut
   entri balikannya juga, jadi pasangan yang tidak timbal balik adalah nama di daftar
   kontak yang menjawab **403** saat diklik. `_conversation_roles` menyaringnya dan
   itulah yang dipakai daftar kontak — sebab itu `super_admin` tidak melihat setiap
   guru lagi: guru memang tidak pernah bisa membalas, jadi yang hilang hanya nama
   yang tidak bisa diklik.
2. **Murid tidak berubah.** Daftar `murid` tetap dua kanal, jadi daftar kontak
   seorang murid tidak melebar; sementara pejabat **boleh** mengirim pengumuman *ke*
   murid. Mengumumkan ke sebuah peran dan memegang percakapan 1-on-1 dengan peran itu
   adalah dua perbuatan berbeda, dan itulah sebabnya "pejabat bisa mengumumkan ke
   murid" tidak berarti "murid bisa chat dengan kepala sekolah".
3. **Meng-address peran sendiri berarti *grup*** ("semua guru"), kecuali
   `super_admin` yang satu akun dan satu-satunya peran yang tidak ditawarkan ke
   dirinya sendiri.

Bukti: `tests/unit/test_official_messaging.py` (tabel menutup keenam peran, pasangan
timbal balik, murid tidak berubah, rutenya GET dan hanya ada di bawah prefiks
masing-masing pejabat, chrome menautkan keduanya dengan path literal, dan dropdown di
`shared/comms.html` sama dengan tabel server) dan `tests/unit/test_role_matrix_docs.py`
(matriks di `AGENTS.md` mencetak daftar yang sama).
