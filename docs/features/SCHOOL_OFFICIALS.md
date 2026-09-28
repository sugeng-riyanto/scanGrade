# Kepala Sekolah & Wakil Kepala Sekolah (School Officials)

Dua peran pengawas: `principal` (Kepala Sekolah) dan `vice_principal` (Wakil Kepala
Sekolah). Keduanya **membaca** sekolahnya, tidak mengelolanya. Dokumen ini adalah
kontraknya: apa yang boleh mereka buka, apa yang sengaja ditolak, dan di mana
aturan itu ditegakkan.

## 1. Kenapa peran terpisah, bukan menumpang `admin_sekolah`

`admin_sekolah` adalah pemegang akun dan data sekolah: ia mengimpor guru, menaikkan
kelas, mengurus langganan. Kepala sekolah tidak melakukan satu pun dari itu; ia
mengawasi hasilnya. Menaruhnya di `admin_sekolah` berarti memberi wewenang tulis
kepada pembaca laporan demi kepraktisan — dan wewenang tulis yang diberikan "hanya
supaya bisa login" adalah wewenang tulis yang dipakai.

`principal` juga bukan `super_admin`: ia melihat **satu** sekolah, bukan platform.
Dan `vice_principal` bukan salinan `principal` yang disederhanakan — hari ini
keduanya melihat halaman yang sama, dan yang membedakan hanya siapa yang membaca
(judul halaman menyebut peran itu). Kalau nanti ada wewenang yang didelegasikan,
perbedaan itu tumbuh dari sini, bukan dari peran ketiga yang dibuat belakangan.

## 2. Matriks CRUD per tabel

| Tabel / Modul | `principal` | `vice_principal` | ditegakkan oleh |
|---|---|---|---|
| `schools` | SELECT own (`school_id`) | SELECT own (`school_id`) | route + `require_school_access` |
| `profiles` | SELECT own_school | SELECT own_school | route + query `.eq("school_id", …)` |
| `classes` | SELECT own_school | SELECT own_school | route |
| `subjects` | SELECT own_school | SELECT own_school | route |
| `teachers` | SELECT own_school (agregat: cacah) | SELECT own_school | route |
| `students` | SELECT own_school (agregat: cacah) | SELECT own_school | route |
| `exams` | SELECT own_school (baca saja) | SELECT own_school (baca saja, **tidak** buat soal) | tidak ada route tulis |
| `submissions` | SELECT own_school | SELECT own_school | laporan sekolah (`/teacher/reports`, `/teacher/analytics`) |
| laporan (analysis/report) | SELECT + EXPORT own_school | SELECT + EXPORT own_school | cakupan peran yang sudah ada |
| `announcements` | ❌ (hanya baca) | ❌ | — |
| `account_deletion_requests` | ❌ | ❌ (menunggu lapis kedua) | — |
| `school_registration_requests` | ❌ | ❌ | tetap wewenang `super_admin` |
| `audit_logs` | ❌ | ❌ | hanya `super_admin` |
| akun pejabat (`/admin-sekolah/officials/*`) | ❌ | ❌ | hanya `admin_sekolah` |

Aturan yang tidak boleh dilanggar: **setiap query difilter ketat pada
`school_id`/NPSN milik user yang login**. `school_id` tidak pernah datang dari
client — selalu dari `g.session`/`profiles` di server. Endpoint laporan dan
perbandingan lintas tahun pun tidak boleh menembus sekolah lain.

## 3. Login

Kedua peran masuk lewat pintu yang sama dengan guru dan murid:

```
POST /auth/login_user   (email + password)
  → Supabase sign_in_with_password
  → cek profiles.status (pending → /auth/activate)
  → cek role ∈ USER_ROLES = (guru, murid, principal, vice_principal)
  → redirect ke /principal/dashboard atau /vice-principal/dashboard
```

`super_admin` dan `admin_sekolah` yang salah masuk ke pintu ini ditolak dengan pesan
"wrong door" dan diarahkan ke `/auth/login`. Halaman `/demo` menautkan
`/auth/login-user?role=principal` / `?role=vice_principal`.

## 4. Akun dibuat oleh sekolah

Tidak ada self-register. `admin_sekolah` membuka **Akun Pejabat Sekolah**
(`/admin-sekolah/officials`) dan membuat, mengganti nama/HP, mereset password, atau
menghapus akunnya — persis alur guru dan murid.

Yang sengaja **tidak bisa diubah**: perannya. Itu satu-satunya yang membedakan kedua
akun, dan salah klik yang mengubah wakil menjadi kepala sekolah akan diam-diam
memindahkan siapa yang boleh masuk sebagai siapa. Untuk mengubah peran, hapus dan
buat ulang.

Urutan penulisan akun mengikuti konvensi repo: `auth.users` dulu (karena
`profiles.id` mereferensikannya), lalu `profiles`. Kalau tulisan kedua gagal, akun
auth yang sudah jadi **dihapus** — akun yang bisa login tanpa peran tidak bisa
ditolong siapa pun. Guard `tests/unit/test_account_creation_orphans.py` menuntut
setiap `create_user` punya undo di handler yang menangani kegagalan setelahnya.

## 5. Demo & visibilitas oleh super admin

`/demo` menampilkan satu kartu per peran, dan dua kartu baru untuk pejabat sekolah.
Emailnya adalah akun yang benar-benar di-seed `manage.py seed-demo`:

| Kartu | Peran | Contoh email (SMP) |
|---|---|---|
| Kepala Sekolah | `principal` | `principal_smp@scan-grade.app` |
| Wakil Kepala Sekolah | `vice_principal` | `vice_principal_smp@scan-grade.app` |

Password semua akun demo: `demo123`.

Super admin dapat menyalakan/mematikan kedua kartu dan mengatur urutannya di
`/super-admin/demo-settings`, lewat satu blob `school_settings.demo_settings` yang
dibaca oleh `/demo` **dan** landing page. Kunci yang dipakai: `demo_principal`,
`demo_vice_principal` (`ROLE_ITEMS` di `app/services/demo_settings.py`). Sebuah kunci
yang tidak ada di `demo_items(...)` tidak akan digambar — halaman tidak bisa
"lupa" menyembunyikan sesuatu yang dimatikan.

## 6. Halaman yang dibaca kedua peran

Tiga halaman, semuanya GET, semuanya berlingkup `g.user_school_id`:

| Halaman | Isi |
|---|---|
| `/<peran>/dashboard` | Cacah guru/murid/kelas/mapel/ujian, daftar ujian terbaru |
| `/<peran>/analytics` | Statistik seluruh ujian sekolah (`analysis_scope`), CSV/PDF/cetak |
| `/<peran>/progress` | Kalender bulan, tren 8 minggu, tren 6 bulan, tabel per guru |

`<peran>` adalah `principal` atau `vice-principal`. Halaman analitik adalah
**template guru yang sama** (`teacher/analytics.html`), dirender dengan
`analysis_base` menunjuk ke pintu pembacanya — satu laporan, satu implementasi,
tetapi setiap tombol (form tanggal, CSV, PDF, cetak) tetap di dalam blueprint
pejabat. Cakupan datanya dari `analysis_scope` dengan peran `principal` /
`vice_principal`, dan predikatnya `exam_access.can_read_exam` — predikat baca,
bukan `can_manage_exam`: kedua peran ini **tidak boleh** menyentuh kertas siapa pun.

Halaman progres menjawab pertanyaan yang laporan per ujian tidak jawab: *bulan ini
seperti apa?* Sumbernya `app/services/official_insight.py`, dan tiga aturannya:

1. **Jamnya jam sekolah.** Setiap ember dihitung dari stempel waktu yang dikonversi
   dengan offset sesi (`g.tz_offset`). Sesi yang dikumpulkan 17:30 UTC adalah **hari
   berikutnya** di WIB — hari yang dicetak kalender sekolah.
2. **Ember kosong tetap digambar.** Kalender menggambar setiap hari di bulan itu,
tabel mingguan menggambar setiap minggu di jendela, tabel bulanan setiap bulan.
   Periode kosong harus terbaca sebagai periode kosong, bukan sebagai halaman gagal.
3. **Tidak ada penilaian.** Halaman ini menghitung dan menampilkan: tidak ada skor,
   tidak ada peringkat murid, dan pengurutan tabel guru adalah *besarnya antrean
   koreksi*, bukan urutan kecurigaan. Rata-rata hanya dari kertas yang sudah bernilai;
   kertas yang menunggu koreksi bukan nol.

Rencana berikutnya (belum ada): CRUD jadwal & pengawas ujian oleh `vice_principal`,
beserta tugas yang muncul di dashboard guru. Lihat bagian 7.

## 7. Yang belum ada (jujur)

- **Jadwal ujian, pengawas, dan tugas untuk guru.** Belum ada tabel maupun route.
  Rancangan yang disepakati arahnya: `vice_principal` membuat jadwal per ujian/kelas
  lalu menugaskan pengawas; guru melihat tugasnya di dashboard-nya. Ini menambah
  wewenang tulis **pertama** bagi pejabat sekolah, jadi ia harus berupa tabel baru
  dengan policy sendiri — bukan pembukaan route tulis yang sudah ada.
- **Policy RLS sendiri untuk kedua peran.** Hari ini akses ditegakkan decorator route
  (backend memakai service-role key yang menembus RLS). Migrasi policy-nya menyusul
  sebagai migrasi tersendiri, agar satu rilis tidak sekaligus mengubah role `CHECK`
  dan sekumpulan policy. Lihat `docs/SECURITY_RLS_MATRIX.md`.
- **Laporan komprehensif khusus pengawas** (perbandingan antar semester/tahun/angkatan,
  visualisasi 3D, ekspor PDF/XLSX/gambar yang dibangun async) belum dibangun; yang ada
  hari ini adalah halaman pengawasan (`/principal/dashboard`) dan laporan sekolah yang
  sudah dimiliki admin/guru.
- **Dual approval penghapusan akun oleh `vice_principal`** belum diaktifkan;
  `account_deletion_requests` masih sepenuhnya milik `admin_sekolah`.
