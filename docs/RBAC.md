# ScanGrade — Role-Based Access Control (RBAC) & Interaction Flows

> **Daftar peran yang definitif: 6 — `super_admin`, `admin_sekolah`, `principal`,
> `vice_principal`, `guru`, `murid`** (README lama menyebut 4; sudah usang).
> Dan hati-hati: **"Kepala Sekolah" adalah dua hal berbeda** — peran login `principal`
> (pengawasan, baca-saja) versus **field teks `schools.principal_name`** yang tercetak
> di dokumen. Rujukan tetap lengkap beserta buktinya: **`docs/ROLES_CLARIFICATION.md`**.

## 1. Role Hierarchy

```
super_admin        (Super Admin — akses semua sekolah)
    │
    └── admin_sekolah    (Admin Sekolah — 1 sekolah spesifik; pemegang akun & data)
            │
            ├── principal       (Kepala Sekolah — baca saja: pengawasan & laporan)
            │       │
            │       └── vice_principal (Wakil Kepala Sekolah — baca saja, sama)
            ├── guru          (Guru — mengajar)
            │
            └── murid         (Siswa — mengerjakan ujian)
```

Dua peran pengawas (`principal`, `vice_principal`) **mengawasi sekolahnya sendiri, dan
boleh mengajar bila sekolah menugaskannya**. Secara bawaan mereka tidak mengelola apa pun —
mereka membaca angka, ujian dan laporan sekolahnya sendiri. Wewenang membuat dan menghapus
akun tetap di `admin_sekolah`: yang tahu siapa kepala sekolahnya adalah sekolahnya, jadi
sekolah yang membuat akunnya lewat `/admin-sekolah/officials`.

**Yang berubah: kepala sekolah dan wakilnya bisa ditugaskan sebagai guru mapel.** Seorang
pejabat yang mengajar umumnya mengampu satu-dua mapel, kadang hanya di kelas tertentu —
sama seperti guru. Karena itu `/admin-sekolah/teachers` kini menampilkan mereka di daftar
yang sama (dengan lencana perannya), dan matriks **Kelola Penugasan** bisa mengisi
(kelas, mapel) untuk mereka. Batasnya tetap sama seperti guru, bukan lebih lebar:

* penulisan ujian dibatasi `assignments.SCOPED_ROLES` — hanya (kelas, mapel) yang
ditugaskan, dan daftar itu kini memuat `principal`/`vice_principal`;
* `exam_access.can_manage_exam` mengizinkan mereka **hanya atas ujian miliknya sendiri**
  (`teacher_id` = dirinya, di sekolah yang sama) — bukan ujian kolega, bukan se-sekolah;
* `@teacher_or_admin_required` membuka workspace guru bagi mereka supaya penugasan itu
  benar-benar bisa dipakai (buat ujian, koreksi). Tanpa penugasan, workspace-nya kosong
  dan tidak ada yang bisa ditulis; pengawasan tetap hanya-baca.

`/principal/*` dan `/vice-principal/*` tetap tanpa route tulis. Akses mengajar memakai
pintu guru yang sudah ada, dan `tests/unit/test_officials_as_subject_teachers.py` menahan
keempat sifat ini (bisa ditugaskan, dibatasi pasangan, hanya ujian sendiri, gerbang
workspace).

| Role | Tujuan | Dibuat oleh | Dashboard | Login di |
|------|--------|-------------|-----------|----------|
| `super_admin` | Mengelola SEMUA sekolah + pengguna + data lintas sekolah | Via Supabase Console | `/super-admin/dashboard` | `/auth/sign-in` |
| `admin_sekolah` | Mengelola 1 sekolah (guru, siswa, kelas, mapel) | Register mandiri (perlu approval) | `/admin-sekolah/dashboard` | `/auth/sign-in` |
| `principal` | Mengawasi sekolahnya sendiri (baca saja); boleh mengajar bila ditugaskan | Dibuat admin_sekolah | `/principal/dashboard` | `/auth/sign-in` |
| `vice_principal` | Mengawasi sekolahnya sendiri (baca saja); boleh mengajar bila ditugaskan | Dibuat admin_sekolah | `/vice-principal/dashboard` | `/auth/sign-in` |
| `guru` | Membuat ujian, mengoreksi, melihat hasil | Di-import oleh admin_sekolah | `/teacher/dashboard` | `/auth/sign-in` |
| `murid` | Mengerjakan ujian, melihat nilai | Di-import oleh admin_sekolah | `/student/dashboard` | `/auth/sign-in` |

---

## 2. Authentication Flow

### 2.1. Satu halaman login untuk semua peran
```
Browser → /auth/sign-in → POST (identifier + password)
  → Supabase Auth sign_in_with_password()
  → Cek profile.status (pending → redirect /auth/activate)
  → Cocokkan role terhadap SELURUH peran yang punya dashboard (`ALL_ROLES`)
  → Set cookies: access_token (24h) + refresh_token (7d) + session_start
  → Redirect ke dashboard peran tersebut (`dashboard_for`)
```

`identifier` menerima **email, NISN murid, atau NIP pegawai** dalam satu field, dan
server yang menentukan peran dari akun yang cocok — bukan tab yang dipilih pembaca.
Tab pada halaman (`Admin`, `Staf Sekolah`, `Guru`, `Murid`, dari `SIGN_IN_TABS`)
hanya mengubah placeholder dan teks bantuan; nilainya tidak pernah dikirim sebagai
klaim dan tidak pernah mempersempit pencarian. Inilah sebabnya tidak ada lagi
"halaman yang salah": sebuah peran tidak lagi bisa ditolak hanya karena ia mendarat
lewat pintu yang bukan miliknya.

Enam peran, satu halaman, masing-masing punya rumah:

| Peran | Dashboard |
|---|---|
| `super_admin` | `/super-admin/dashboard` |
| `admin_sekolah` | `/admin-sekolah/dashboard` |
| `principal` | `/principal/dashboard` |
| `vice_principal` | `/vice-principal/dashboard` |
| `guru` | `/teacher/dashboard` |
| `murid` | `/student/dashboard` |

### 2.2. Kontrak URL lama dan parameter `?role=`

`/auth/login` dan `/auth/login-user` tetap **dijawab** sebagai alias: GET
meneruskan pembaca ke `/auth/sign-in` sambil membawa `?role=` dan `?next=` apa pun,
dan POST tetap memproses login (menjawab POST dengan redirect akan membuang
kredensial yang baru saja diketik). Keduanya dipertahankan karena sudah
*dipublikasikan*: `/tutorial/guru`, `/tutorial/murid`, `/tutorial/admin-sekolah`,
kartu-kartu di `/demo`, dan kartu login cetak yang beredar di sekolah menaut ke sana.

`?role=<peran>` hanya membuka halaman dengan tab kelompok itu sudah terpilih;
nilainya divalidasi (`sign_in_tab` menjawab `""` untuk yang tidak dikenal), tidak
digemakan, dan tidak pernah sampai ke backend. Redirect internal sekarang menunjuk
langsung ke `/auth/sign-in` (dengan `?role=` bila perannya diketahui) sehingga
pembaca yang sesinya berakhir tidak lagi mendarat di tab admin.

Satu pesan penolakan untuk semua sebab: identifier tidak dikenal, password salah,
dan akun dengan peran yang tidak dikenal menghasilkan kalimat generik yang sama
("Email atau password salah"). Pesan lama — "Halaman ini untuk Admin" dan "Halaman
ini untuk Guru/Murid" — dihapus: keduanya menyebut halaman lain, yang berarti
menyebut peran pembaca.

### 2.3. Register Admin Sekolah
```
Browser → /auth/register → POST (NPSN, sekolah, WA, jabatan, email, password)
  → Buat user di Supabase Auth (role: admin_sekolah, status: pending)
  → Buat school_registration_requests (status: pending)
  → Tampilkan halaman sukses
  → Super admin approve via /admin/registration-requests
  → Email/WA dikirim kode aktivasi
  → Admin sekolah aktivasi via /auth/activate
```

### 2.4. JWT Verification (setiap request)
```
Request → login_required decorator
  → Extract token dari cookie/Bearer header
  → Supabase Auth get_user(token)
  → Fetch profile dari profiles table (role, status, school_id)
  → Set g.user_id, g.user_role, g.user_school_id
  → Cek user_status (pending → redirect)
```

---

## 3. Akses Peran (Route Protection)

| Route | super_admin | admin_sekolah | guru | murid |
|-------|:-----------:|:-------------:|:----:|:-----:|
| **Super Admin (slug: `/super-admin/`)** |
| `/super-admin/dashboard` | ✅ | ❌ | ❌ | ❌ |
| `/super-admin/schools` | ✅ | ❌ | ❌ | ❌ |
| `/super-admin/users` | ✅ | ❌ | ❌ | ❌ |
| `/super-admin/exams` | ✅ | ❌ | ❌ | ❌ |
| `/super-admin/logs` | ✅ | ❌ | ❌ | ❌ |
| **Admin (slug: `/admin/`)** |
| `/admin/registration-requests` | ✅ | ❌ | ❌ | ❌ |
| `/admin/compliance` | ✅ | ❌ | ❌ | ❌ |

**URL lama di bawah `/admin/`** — `/admin/dashboard`, `/admin/users`, `/admin/exams`,
`/admin/comms`, `/admin/compliance/logs`, `/admin/classes`, `/admin/students`,
`/admin/teachers`, `/admin/school` — tidak lagi berupa halaman. Masing-masing menjawab
**308 Permanent Redirect** ke halaman yang sekarang memilikinya (`/admin-sekolah/*`
untuk yang berlingkup sekolah, `/super-admin/*` untuk yang membaca seluruh platform).
Tabel pemetaannya satu dan hanya satu: `app/utils/legacy_urls.py`. Sebelum ini, satu
halaman hidup di dua URL sekaligus sehingga `/tools/device-preview` menampilkan dua
tombol bernama sama di seksi Admin Sekolah.
| **Admin Sekolah (slug: `/admin-sekolah/`)** |
| `/admin-sekolah/*` | ❌ | ✅ | ❌ | ❌ |
| **Teacher (slug: `/teacher/`)** |
| `/teacher/dashboard` | ❌ | ❌ | ✅ | ❌ |
| `/teacher/exams/*` | ❌ | ❌ | ✅ | ❌ |
| `/teacher/grade/*` | ❌ | ❌ | ✅ | ❌ |
| `/teacher/results` | ❌ | ❌ | ✅ | ❌ |
| `/teacher/analytics` | ❌ | ❌ | ✅ | ❌ |
| `/teacher/scan` | ❌ | ❌ | ✅ | ❌ |
| **Student (slug: `/student/`)** |
| `/student/dashboard` | ❌ | ❌ | ❌ | ✅ |
| `/student/exams/*` | ❌ | ❌ | ❌ | ✅ |
| `/student/results` | ❌ | ❌ | ❌ | ✅ |
| **API & Tools** |
| `/api/*` | ✅ | ✅ | ✅ | ✅ |
| `/publish/*` | ❌ | ❌ | ✅ | ❌ |
| `/tools/*` | ✅ | ✅ | ✅ | ❌ |

### Dua peran pengawas (route sendiri, hanya-baca)

| Route | `principal` | `vice_principal` | role lain |
|-------|:-----------:|:----------------:|:---------:|
| `/principal/dashboard` | ✅ | ❌ | ❌ |
| `/vice-principal/dashboard` | ❌ | ✅ | ❌ |
| `/principal/analytics` (`/download.csv`, `/download.pdf`, `/print`) | ✅ | ❌ | ❌ |
| `/vice-principal/analytics` (idem) | ❌ | ✅ | ❌ |
| `/principal/progress` — kalender bulan, tren mingguan & bulanan, per guru | ✅ | ❌ | ❌ |
| `/vice-principal/progress` | ❌ | ✅ | ❌ |
| `/admin-sekolah/officials` (+ `/create`, `/<id>/edit`, `/<id>/delete`, `/<id>/reset-password`) | ❌ | ❌ | hanya `admin_sekolah` |
| `/principal/assessment-periods` — kalender penilaian sekolah | ✅ baca | ❌ | ❌ |
| `/vice-principal/assessment-periods` (+ `/save`, `/<id>/delete`) — kalender, wakil kepala menyusun | ❌ | ✅ | ❌ |
| `/admin-sekolah/assessment-periods` (+ `/save`, `/<id>/delete`) — **kalender yang sama**, disusun admin sekolah | ❌ | ❌ | hanya `admin_sekolah` |
| `/principal/invigilation` — matriks pengawas sekolah | ✅ baca | ❌ | ❌ |
| `/vice-principal/invigilation` (+ `/save`, `/<id>/assign`, `/assignments/<id>/remove`) — matriks pengawas, wakil kepala menyusun | ❌ | ✅ | ❌ |
| `/admin-sekolah/invigilation` (+ `/save`, `/<id>/assign`, `/assignments/<id>/remove`, `/retake-requests/<id>/decide`) — **matriks yang sama**, disusun admin sekolah | ❌ | ❌ | hanya `admin_sekolah` |

Satu view melayani dua alamat; yang berbeda hanya peran pembacanya, dan judul halaman
menyebut peran itu. **Tidak ada satu pun route tulis di `/principal/*`** — sifat
hanya-baca bagi kepala sekolah struktural, bukan janji di dokumen
(`tests/unit/test_official_insight.py` gagal begitu satu route di prefiks kepala
sekolah mendaftarkan metode POST/PUT/PATCH/DELETE). Wewenang tulis yang didelegasikan —
jadwal pengawasan dan kalender penilaian — berada di prefiks `/vice-principal/*`
masing-masing di belakang `@vice_principal_required`.

**Kalender penilaian punya tiga pintu, dua di antaranya menulis.** Jendela ini milik
sekolah, bukan milik jabatan wakil kepala: sekolah kecil yang belum membuat akun wakil
kepala tetap harus bisa menamai tanggal UTS-nya. Karena itu `/admin-sekolah/assessment-periods`
(+ `/save`, `/<id>/delete`, di belakang `@admin_sekolah_required`) merender template
yang sama dengan `can_write=True`. Satu halaman untuk dua penulis berarti tujuan
formulirnya tidak boleh ditulis mati: template menerima `period_save_url` dan
`period_delete_base` dari pemanggilnya. Sekolahnya selalu dari sesi — `assessment_periods`
mensyaratkan `school_id` sebagai argumen wajib dan setiap penolakan terjadi sebelum
menulis, jadi tidak ada `school_id` dari request yang bisa mengalihkan tulis ke
sekolah lain (`tests/unit/test_assessment_periods.py`).

Dua halaman laporan dibagi dengan guru, **bukan disalin**: `/principal/analytics`
dan `/vice-principal/analytics` merender `teacher/analytics.html` dengan
`analysis_base` menunjuk ke pintu pembacanya, sehingga form, CSV, PDF, dan tampilan
cetak semuanya tetap di dalam blueprint pejabat. Cakupan datanya berasal dari
`analysis_scope` dengan peran `principal`/`vice_principal`, yang memakai predikat
baca (`exam_access.can_read_exam`: pemilik, admin sekolah, **atau** pejabat sekolah
yang sama) — bukan predikat tulis. Kedua peran **tidak** diizinkan masuk
`/teacher/reports` dan `/teacher/analytics`: laporan mereka ada di pintunya sendiri,
dan `tests/unit/test_reports_hub.py` menahan daftar itu dalam dua arah.

### Decorators (digunakan di routes)

| Decorator | Roles yang diizinkan |
|-----------|---------------------|
| `@super_admin_required` | `super_admin` |
| `@admin_sekolah_required` | `admin_sekolah` |
| `@guru_required` | `guru` |
| `@murid_required` | `murid` |
| `@admin_required` | `super_admin`, `admin` |
| `@teacher_required` | `guru`, `teacher` |
| `@teacher_or_admin_required` | `guru`, `admin_sekolah`, `admin`, `teacher`, `principal`, `vice_principal` (dua pejabat masuk workspace guru; tulis tetap dibatasi penugasan & kepemilikan ujian — lihat §3) |
| `@school_official_required` | `principal`, `vice_principal` |
| `@principal_required` | `principal` |
| `@vice_principal_required` | `vice_principal` |
| `@login_required` | Semua role yang sudah login |

---

## 4. Database RLS Policies (Supabase)

| Table | super_admin | admin_sekolah | guru | murid |
|-------|:-----------:|:-------------:|:----:|:-----:|
| `schools` | ALL | SELECT own | SELECT own | SELECT own |
| `profiles` | ALL | SELECT own_school + UPDATE own_school | SELECT murid + UPDATE own | SELECT own |
| `exams` | ALL | SELECT own_school | CRUD own + SELECT active | SELECT active+published |
| `submissions` | ALL | SELECT own_school | SELECT own_exam + UPDATE own_exam | SELECT own+published, INSERT own |
| `classes` | ALL | ALL own_school | SELECT own_school | SELECT own_school |
| `subjects` | ALL | ALL own_school | SELECT own_school | SELECT own_school |
| `teachers` | ALL | SELECT own_school | SELECT own | ❌ |
| `students` | ALL | SELECT own_school | SELECT own_school | SELECT own |
| `teacher_assignments` | ALL | ALL own_school (incl. INSERT, migrasi 077) | SELECT own | ❌ |
| `violation_logs` | ❌ | ❌ | SELECT own_exam | ❌ |
| `audit_logs` | ALL | ❌ | ❌ | ❌ |

**`principal` dan `vice_principal` belum punya policy RLS sendiri, dan itu disadari.**
Backend memakai service-role key yang menembus RLS, jadi RLS adalah lapisan pertahanan
kedua, bukan yang menegakkan akses — yang menegakkannya adalah dekorator route plus
`require_school_access`. Migrasi `038_school_officials_roles.sql` karena itu hanya membuka
*nama* perannya di `profiles_role_check`; policy berlingkup sekolah untuk kedua peran
menyusul sebagai migrasi tersendiri (lihat `docs/SECURITY_RLS_MATRIX.md`). Sampai itu
mendarat, setiap route pengawas tetap diuji lintas sekolah di `tests/unit/test_school_officials.py`.

---

## 5. Interaction Flows

### 5.1. Admin School → Import Guru & Siswa
```
admin_sekolah login → /admin-sekolah/dashboard
  ├── /admin-sekolah/teachers → Import Excel / Tambah manual
  │     → supabase.auth.admin.create_user() (role: guru)
  │     → Insert ke profiles + teachers table
  │     → Generate password (random 12 char)
  │
  └── /admin-sekolah/students → Import Excel / Tambah manual
        → supabase.auth.admin.create_user() (role: murid)
        → Insert ke profiles + students table  
        → Generate password
```

### 5.2. Guru → Membuat Ujian
```
guru login → /teacher/dashboard
  → /teacher/exams/new → Exam form
      ├── Isi: judul, mapel, durasi, passing score, deskripsi
      ├── Atur jumlah soal + tipe (MCQ / Essay)
      ├── Upload PDF soal → convert ke page images (PyMuPDF)
      ├── Set answer key + bobot nilai
      ├── Atur anti-cheat (tab switch penalty, watermark, dll)
      ├── Atur randomize questions/options
      ├── Izinkan kalkulator (opsional)
      → Save → status: draft
  → /teacher/exams/{id}/publish-exam → status: active + is_published: true
```

### 5.3. Siswa → Mengerjakan Ujian
```
siswa login → /student/exams
  → Klik ujian → /student/exams/{id}
      → Cek access code (jika diperlukan)
      → Tampilkan agreement modal + aturan
      → START → timer mulai
      
      → Offline-first:
          ├── Jawaban → localStorage (instant)
          ├── Light sync → server setiap 20 detik
          └── Canvas sync → server setiap 60 detik
          
      → Tools:
          ├── Pena / Garis / Hapus / Teks
          ├── Ruler 30cm (transparan, fixed scale)
          ├── Protractor 0-180° (1° accuracy)
          ├── Set Square 10cm
          ├── Compass (circle drawing)
          └── Scientific calculator (jika diaktifkan)
          
      → Anti-cheat:
          ├── Tab switch detection (1.5s delay)
          ├── Graduated penalty (1st=warning, 2nd=-base, 3rd=-2×, 4th+=-3×)
          ├── Auto-submit on max violations
          ├── Block right-click, copy-paste
          └── Watermark nama siswa
          
      → Submit → POST /student/exams/{id}/submit
```

### 5.4. Guru → Mengoreksi Esai
```
guru login → /teacher/grading
  → Lihat submission pending
  → /teacher/grade/{submission_id}
      → Lihat PDF soal + jawaban siswa (canvas overlay)
      → Tools koreksi:
          ├── Pena (warna bisa diatur)
          ├── Eraser
          ├── Text box (multi-font, multi-size)
          ├── Ruler, Protractor, Set Square
          └── Kalkulator ilmiah
      → Beri skor per soal + komentar
      → Simpan (auto-save setiap 15 detik)
      → Publish nilai
```

### 5.5. Export Hasil
```
guru → /teacher/results?exam_id={id}
  ├── Export XLSX: summary + per-question answers
  ├── Export PDF: per-student report + canvas drawings
  └── Cetak LJK (bubble sheet)
```

---

## 6. API Endpoints

| Endpoint | Method | Auth | Deskripsi |
|----------|--------|------|-----------|
| `/api/violation/log` | POST | Cookie | Log tab-switch violation |
| `/api/student/sync-draft` | POST | Cookie | Sync draft jawaban |
| `/api/grade/auto-save/{id}` | POST | Cookie | Auto-save grading |
| `/api/grade/batch` | POST | Guru | Batch grading |
| `/api/scan/process` | POST | Guru | Process OMR scan |
| `/auth/me` | GET | Cookie | Current user info |
| `/auth/set-timezone` | POST | Cookie | Set timezone cookie |

---

### 6.1. Super Admin (`/super-admin/*`) — Akses Khusus

Super Admin memiliki **dashboard terpisah** di `/super-admin/` yang berbeda dari admin sekolah biasa.
Fitur yang hanya ada di Super Admin:

| Fitur | Lokasi | Fungsi |
|-------|--------|--------|
| Dashboard global | `/super-admin/dashboard` | Stats seluruh sistem + registrasi pending |
| Semua sekolah | `/super-admin/schools` | Lihat semua sekolah + jumlah guru/siswa/ujian |
| Semua user | `/super-admin/users` | Filter by role + search lintas sekolah |
| Semua ujian | `/super-admin/exams` | Lintas sekolah, lihat submission count |
| Audit log | `/super-admin/logs` | Semua aktivitas sistem (filter 1-90 hari) |

Super Admin juga masih bisa mengakses `/admin/*` untuk approval registrasi dan compliance.

---

## 7. Security Measures

| Measure | Implementasi |
|---------|-------------|
| **CSRF Protection** | Token via meta tag + auto-inject ke semua form/fetch |
| **Rate Limiting** | In-memory + Redis support (30 auth/mnt, 60 default/mnt) |
| **PDF Validation** | Magic bytes `%PDF`, size limit 50MB, page count check |
| **Password Strength** | Min 6 char, require letter + number (register) |
| **Session Timeout** | access_token: 24h, refresh_token: 7d |
| **Cookie Security** | `httponly=True`, `samesite=Lax`, `Secure` di production |
| **JWT Verification** | On every request via `login_required` decorator |
| **RLS** | Row-level security di semua tabel Supabase |
| **Audit Log** | Semua operasi CRUD tercatat di `audit_logs` |
| **Input Sanitization** | HTML escaping, UUID/email/NISN validation |

---

## 8. Flow Diagram (Text)

```
                    ┌─────────────────────┐
                    │  Landing (/auth)    │
                    └──────────┬──────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                                 ▼
   /auth/sign-in                        /auth/register
   SATU halaman, semua peran            (admin_sekolah)
   (email / NISN / NIP + password)            │
              │                              ▼
              │                        Pending approval
              │                        by super_admin
              │                              │
              │                              ▼
              │                        /auth/activate
              │                        (activation code)
              │                              │
              │                              ▼
              │                        /admin-sekolah/*
              │                              │
              │                        Import guru/siswa
              │                              │
              │                        ┌─────┴─────┐
              │                        ▼           ▼
              │                    /teacher/*   /student/*
              │                    (guru)       (murid)
              │
              └──► dashboard peran itu sendiri (`dashboard_for`):
                   /super-admin/dashboard, /admin-sekolah/dashboard,
                   /principal/dashboard, /vice-principal/dashboard,
                   /teacher/dashboard, /student/dashboard
```

## Penugasan guru–kelas–mapel (banyak-ke-banyak)

`/admin-sekolah/teachers` memberi admin sekolah sebuah **matriks** penugasan: baris = kelas
(dikelompokkan per jenjang), kolom = mata pelajaran, sel = pasangan *(kelas, mapel)* yang
diampu guru itu untuk **tahun ajaran aktif**. Pratinjau di baris daftar menunjukkan
"N kelas, M mapel" tanpa perlu membuka modal.

Endpoint (admin sekolah saja):

- `GET  /admin-sekolah/teachers/<teacher_id>/assignments` — kelas + mapel sekolah, pasangan
  aktif untuk tahun berjalan, dan nama tahunnya; guru yang bukan milik sekolah ini → **404**.
- `POST /admin-sekolah/teachers/<teacher_id>/assignments` — seluruh pilihan, bukan delta.
  Server yang menghitung selisihnya (`app/services/teacher_assignments.py`).

Aturan yang ditegakkan server, bukan UI:

- setiap `class_id`/`subject_id` harus milik **sekolah pemanggil**; yang bukan → **403**,
  bukan diabaikan diam-diam (diam-diam terlihat seperti berhasil);
- guru target harus terdaftar di sekolah yang sama (role `guru`/`teacher`);
- pencabutan penugasan **menonaktifkan** baris (`status='inactive'`), tidak menghapusnya —
  pasangan tempat ujian lama dibuat tetap ada di riwayat;
- pasangan yang dicabut tetapi masih punya **ujian berjalan** mengembalikan **409** beserta
  daftar ujiannya; penyimpanan hanya berlanjut dengan `confirm_remove: true`;
- baris lama tanpa `school_year` (pra-migrasi 045) dianggap milik tahun aktif, supaya
  menyimpan ulang tidak diam-diam menonaktifkannya.

## Nilai berbobot (migration 057)

- Hanya **admin_sekolah** yang boleh mengelola komponen nilai, **bobot default
  sekolah** (`POST /admin-sekolah/grade-weights/defaults`), dan matriks bobot
  (`/admin-sekolah/grade-weights*`), dan hanya untuk sekolahnya sendiri
  (`@require_school_access("subjects", "subject_id")` pada simpan bobot per mapel).
  Guru tidak bisa memutuskan apa arti sebuah nilai mapel; ia hanya membacanya
  (efektif: konfigurasi mapelnya, atau default sekolah).
- Guru hanya melihat tabel nilai (`/teacher/students`) dan ekspornya untuk murid
  di kelas yang **ia pegang untuk mapel terpilih**; admin sekolah melihat seluruh
  daftar. Mapel yang diminta tetapi tidak diajar jatuh kembali ke mapel default
  guru itu, bukan membocorkan daftar mapel lain.
- `exams.grade_component_type_id` hanya menerima komponen milik sekolah pemanggil;
  id asing/stale dibuang ke `NULL`, tidak ditulis (`_resolve_grade_component`).

Sumber otorisasi guru tetap `app/services/assignments.py`: guru hanya boleh membuat/mengubah
ujian untuk pasangan yang benar-benar dipegangnya, dan daftar dropdown yang kosong **menutup**
(admin sekolah & super admin tidak dibatasi per pasangan).

## Keanggotaan lintas sekolah (migration 063 + 066)

Satu akun, banyak sekolah. `teacher_school_membership` memisahkan **identitas** (satu baris
`profiles`, satu email) dari **keanggotaan** (satu baris per sekolah). Peran yang boleh
memegang lebih dari satu keanggotaan hanya `guru`, `principal`, dan `vice_principal`;
murid dan admin sekolah tetap satu sekolah lewat `profiles.school_id`, sehingga perilaku
mereka tidak berubah karena fitur ini.

Perannya **sengaja tidak seragam**, dan asimetri itu keputusan produk:

| Tindakan | Peran yang berwenang |
|---|---|
| Mengajukan bergabung (sisi guru) | `guru`, `principal`, `vice_principal` |
| Menyetujui / menolak permintaan | `admin_sekolah`, `principal`, `vice_principal` |
| Menutup keanggotaan | `admin_sekolah`, `principal`, `vice_principal` |
| **Membuka kembali** keanggotaan | **hanya `admin_sekolah`** |

Alasannya: menyetujui permintaan baru dan menutup akses adalah tindakan operasional yang
boleh dibagi tiga peran; membuka kembali akses yang sudah ditutup adalah keputusan
administratif yang dipersempit ke satu peran. Aturan itu ditegakkan **dua lapis** —
dekorator rute (`@admin_sekolah_required` pada pembukaan kembali) dan service
(`school_membership.reopen_membership` menolak **sebelum** menulis apa pun), sehingga
pemanggil masa depan yang lupa dekorator tetap tidak bisa membuka kembali. Halaman
`/admin-sekolah/membership` hanya merender tombol buka-kembali untuk pembaca yang berwenang;
untuk principal/wakilnya ia mengatakan *"Hanya admin sekolah"*.

### Penutupan itu total, bukan bertahap

`closed` adalah **satu-satunya** status penutupan sejak migrasi 066 (nilai `inactive` dari
migrasi 044 dipindahkan ke sana dan tidak ditulis kode mana pun lagi — ditegakkan test).
Setiap nilai selain `active` berarti **tanpa akses**, jadi penutupan tidak butuh jalan baca
kedua. Sekolah yang ditutup:

- dihapus dari `memberships_for`, `member_school_ids`, `is_active_member`, dan resolusi
  sekolah aktif;
- hilang dari sesi **pada request berikutnya**, bukan setelah cache sesi kedaluwarsa:
  sekolah aktif diresolusi ulang tiap request dari pembacaan keanggotaan yang segar
  (`school_membership.resolve_for_request`), sedangkan cache sesi hanya memegang profil;
- tidak bisa dimasuki kembali lewat permintaan baru maupun lewat persetujuan — jalannya
  hanya `reopen_membership`, satu-satunya penulis pembukaan kembali (peran yang boleh
  memanggilnya diperiksa di dalam layanan, sebelum tulisan apa pun).

Dua arah kegagalan dibedakan dengan sadar: **pembacaan yang gagal tidak menyempitkan apa
pun** (sekolah dari `profiles` tetap dipakai, supaya gangguan database tidak mengunci guru
keluar), sedangkan **penutupan yang diketahui menyempitkan** (baris yang ada dan tidak
`active` mencabut sekolahnya). Baris yang tidak ada sama sekali bukan penutupan — akun yang
dibuat setelah migrasi 044 memang tidak punya baris keanggotaan.

### Sekolah tujuan: aturan yang terlihat di halaman

- Sekolah yang belum punya kelas terdaftar **tetap muncul** di hasil pencarian, dengan
  jenjang yang dikatakan *"Jenjang belum terdata"* — bukan disembunyikan. Sekolah tanpa
  kelas justru tujuan baru yang paling mungkin, dan sekolah yang tidak muncul tidak bisa
  diajukan sama sekali. Pembacaan kelas yang gagal hanya menghilangkan kolom jenjangnya.
- Sekolah tempat guru itu sudah menjadi anggota tampil sebagai *flag*, bukan disaring keluar.
- Kebijakan privasi: sekolah tujuan menerima **nama, email, dan status akun** saja. Tidak ada
  kolom sekolah asal di skema, karena kolom yang tidak ada tidak bisa ikut ter-SELECT.
- Persetujuan dicatat sebagai bukti: `membership_consent_log` menyimpan nomor versi dokumen
  dan SHA-256 teks yang ditampilkan. Halaman mencetak teks itu dari service yang sama yang
  menghitung hash-nya, jadi bukti yang tersimpan benar-benar tentang teks yang dibaca guru;
  versi/hash yang tidak cocok ditolak (`consent_required`) sebelum baris apa pun ditulis.

### Sekolah tujuan: siapa yang dianggap guru di sana

Persetujuan membuat seseorang **anggota**, dan itu dulu berhenti di situ: barisnya ada di
`teacher_school_membership`, sementara `/admin-sekolah/teachers` membaca tabel `teachers`
saja dan `teacher_assignments.teacher_in_school` membandingkan `profiles.school_id` — kolom
yang justru tidak lagi jadi satu-satunya jawaban sejak migrasi 044. Akibatnya laporan
dari produksi: guru disetujui, lalu tidak muncul di daftar guru sekolah itu, tidak bisa
diberi kelas (404 dari matriks penugasan), dan tidak bisa direset dari daftar itu.

Satu predikat sekarang menjawab "apakah orang ini guru di sini": `teacher_in_school` —
baris `profiles` yang menyebut sekolah ini, **atau** keanggotaan `active` di sekolah ini.
Dibaca segar tiap kali (penutupan harus terlihat pada request berikutnya) dan **gagal
tertutup**: pembacaan yang gagal menjawab *tidak*. Guru anggota muncul di daftar dengan
badge yang menyebut keanggotaan — dan tidak pernah menyebut sekolah lain — plus tombol
matriks penugasan (pekerjaannya di sini adalah kelas dan mapel) dan tombol **Keluarkan**
yang menutup keanggotaan (`school_membership.close`, lengkap dengan siapa dan kapan)
tanpa menghapus akun, profil, atau keanggotaan lain. Anggota yang sudah ditutup hilang
dari daftar pada pembacaan berikutnya, dan daftar itu tidak merender formulir edit untuk
mereka: formulir itu menulis baris `teachers` milik sekolah ini, bukan keanggotaan.

Batas yang disengaja antara **keanggotaan** dan **akun**:

| Pintu | Pertanyaannya | Guru anggota |
|---|---|---|
| Daftar guru, matriks penugasan | "apakah ia mengajar di sini?" | boleh |
| Edit profil & `employee_id` | "apakah baris `teachers` ini milik sekolah ini?" | tidak (404) — catatan kepegawaiannya milik sekolah asalnya |
| Hapus akun | "apakah akun ini milik sekolah ini?" | tidak (404) |
| Reset password | "apakah kredensial ini milik sekolah ini?" | **tidak** (404) — password baru dari sekolah mana pun tempat ia mengajar akan mengunci dia dari semua sekolah itu |
| Pilih akun massal (select-all) | "boleh dihapus/direset borongan?" | tidak pernah ikut terpilih |

Sekolah aktifnya dipilih guru sendiri lewat `POST /teacher/membership/switch`
(`role_required` tiga peran lintas sekolah). Rute itu menolak sekolah yang bukan
keanggotaan **active** milik pemanggil — formulir basi tidak bisa masuk kembali ke
keanggotaan yang sudah ditutup — dan memperlakukan `home` sebagai *hapus pilihan*, bukan
sebagai id sekolah yang bisa dikarang klien. Setelah dipilih, resolusi per-request di
`app/utils/auth.py` membuat kelas, murid, bobot nilai, KKM dan ujian mengikuti NPSN itu;
penutupan keanggotaan mencabutnya pada request berikutnya, bukan setelah cache kedaluwarsa.

### Satu daftar staf untuk setiap pemilih, dan tulisannya menerima apa yang ditawarkan

Dua permukaan yang lahir sebelum migration 044 membaca tabel `teachers` saja, dan itu
cukup untuk "staf sekolah ini" sampai satu sekolah bisa **menerima** guru dari NPSN lain.
Guru anggota tidak punya baris kepegawaian di sekolah tujuan — dan memang sengaja, karena
baris kedua tentang orang yang sama adalah jawaban kedua — sehingga daftar yang dibaca dari
`teachers` saja melaporkan bahwa sekolah itu tidak punya guru tersebut. Yang terkena bukan
daftar guru (sudah diperbaiki lewat `teacher_in_school`), melainkan pengawas ujian:
`invigilation.form_options` dan `invigilation_matrix.teachers_of` mengisi dropdown, grid,
auto-fill, dan importer Excel dari daftar itu, sehingga sekolah yang baru menerima seorang
guru tidak bisa memberinya tugas mengawas — dan `assign_invigilator`, `assign`, `toggle`,
`apply` **menolak** id yang sama dengan `teacher_not_in_school`, jadi POST yang dibuat
tangan pun tidak bisa menembusnya.

Sekarang satu pembaca menjawab pertanyaan itu untuk semua:
**`teacher_assignments.staff_directory`** menggabungkan staf sendiri (dari `teachers`)
dengan anggota `active` sekolah itu (dari `school_membership.active_members`, lewat service
yang memiliki tabel keanggotaan), diurutkan menurut nama dan tidak pernah menduplikasi satu
orang. **`teacher_assignments.is_staff`** adalah sisi tulis dari daftar yang sama
(`any(t["id"] == wanted …)`), dan itu yang dipakai `invigilation` maupun
`invigilation_matrix` — jadi apa yang ditawarkan formulir adalah apa yang diterima penulisan,
satu jawaban, bukan dua. Sifatnya **melebarkan saja**: setiap id yang dulu diterima baris
`teachers` masih diterima, ditambah guru anggota; orang asing tetap ditolak, dan pembacaan
keanggotaan yang gagal tetap mengembalikan staf sendiri (aturan service itu) alih-alih
melempar di dalam dropdown. Pembacaan itu memo per-request (`req_cache.memo`) sehingga satu
halaman tidak membaca daftar yang sama berkali-kali; karena memo, ini **daftar tampilan dan
bukan keputusan akses** — keputusan akses tetap `is_active_member`/`teacher_in_school` yang
dibaca segar.

Penjaga: `tests/unit/test_member_on_the_staff_list.py` (12 uji — anggota ada di daftar, orang
yang sama tidak muncul dua kali, orang asing tetap tidak, `form_options` dan `teachers_of`
sama-sama menawarkannya, penugasan mengawas untuk anggota diterima, orang asing tetap ditolak,
dan pemeriksaan sumber bahwa tidak ada jalur kembali ke `_row(…, "teachers", …)`), dan
`tests/unit/test_member_teacher_works_in_the_active_npsn.py` (6 uji — permukaan guru
menjalankan alat yang sama: kelas, murid, bobot nilai dan KKM diambil dari NPSN aktif, dengan
kontrol negatif bahwa sekolah rumah benar-benar berisi data lain sehingga asersi tidak lulus
karena fixture kosong).
## Pintu tulis ujian: sekolah mana yang menyimpan sebuah ujian (guru anggota dua NPSN)

`exams.school_id` adalah satu-satunya kolom yang menentukan **sekolah mana yang menimbang**
sebuah ujian, **murid siapa** yang boleh mengerjakannya, **nilai siapa** yang muncul di
bawahnya, dan **laporan siapa** yang menghitungnya. Sisi **baca** sudah benar di cabang ini
(`school_membership.resolve_for_request`, dipanggil `app/utils/auth.py::_resolved_school`).
Yang belum ada adalah sisi **tulis**: setiap pintu yang menulis kolom itu menjawab dengan
caranya sendiri.

- Dua arah, dua fungsi, dan perbedaannya disengaja (`app/services/school_membership.py`):
  - **membaca** — `resolve_for_request`: pilihan sekolah di sesi (disimpan di server, per
    token, tidak pernah di cookie) dihormati **hanya bila masih ada baris `active`**;
    pilihan yang barisnya hilang dibuang; dan **tabel yang tidak terbaca mengembalikan
    sekolah rumah** — gangguan basis data tidak boleh mengambil sekolah seseorang di
    tengah halaman.
  - **menulis** — `filing_school`: sekolah yang **tidak dapat dipastikan justru ditolak**,
    karena gangguan basis data yang diam-diam menjadi sekolah rumah adalah tepat kebocoran
    yang aturan ini ada untuk mencegah. Dipanggil lewat
    `app.utils.auth::filing_school_for_request` (satu pembaca `g`, seperti
    `check_subscription_write`).
- Satu tabel yang **tidak ada sama sekali** (migrasi 044 belum dijalankan) bukan gangguan:
  itu `[]`, dan di sana `profiles.school_id` adalah seluruh kebenarannya — kalau tidak,
  setiap simpan ujian ditolak di kotak yang tidak pernah punya fitur ini.
- `g.user_home_school_id` = `profiles.school_id`; `g.user_school_id` = sekolah yang
  **diresolusi** untuk request ini. Keduanya disimpan karena aturannya harus membedakan
  keduanya (sekolah rumah adalah siapa guru itu di atas kertas; sekolah aktif adalah tempat
  ia bekerja sekarang).
- **Nama sekolah di dalam body bukan otoritas.** Body yang menyebut sekolah lain ditolak
  (400), bukan dituruti dan bukan pula diam-diam ditimpa.
- Pintu tulis: `POST /exam/` dan `PUT /exam/<id>` tidak lagi menerima `school_id` sebagai
  kolom yang bisa ditulis; builder create (`/teacher/exams/new`) memakai jawaban otoritas dan
  menolak simpan bila tidak terverifikasi; builder edit tidak pernah **menyebut ulang**
  sekolah (baris mempertahankan miliknya; hanya baris tanpa sekolah sama sekali yang
  diperbaiki, dan perbaikan itu pun lewat otoritas yang sama); `duplicate_exam` mewarisi
  sekolah makalah sumber; `generate_remedial` menyematkan set remedial ke sekolah makalah
  sumber, bukan ke sekolah pemanggil.
- Pembacaan `GET /exam/` ikut dipersempit ke sekolah request (dan kunci jawaban hanya ikut
  bila `can_read_exam` mengizinkan), sebab sisi baca yang tetap lintas sekolah akan
  menyerahkan persis apa yang sisi tulis menolak untuk dibuat.
- Refresh token menyelesaikan sekolah **lagi** (kedua cabang pembacaan profil) dan
  memindahkan pilihan sekolah ke token baru **sebelum** resolusi pertama — tanpa itu, refresh
  tiap jam mengembalikan guru lintas sekolah ke sekolah rumahnya, yaitu sekolah tempat
  makalah berikutnya akan disimpan.

Batas yang jujur: **pemilih sekolah tidak ada di cabang ini** — tidak ada pemanggil
`set_active_school()` selain alur bergabung dan pengujian, jadi sekolah aktif = sekolah rumah
kecuali ada yang memanggilnya (dan panggilan itu hanya dihormati bila baris `active` masih
memberikannya). Yang dijamin: setiap pintu penulis ujian bertanya ke satu otoritas itu, dan
body request tidak bisa menjawabnya.

Penjaga: `tests/unit/test_exam_school_authority.py` (37 uji, termasuk **delapan uji yang
menjalankan pintunya** — empat menjalankan `POST /exam/` dan empat menjalankan simpan milik
builder (`/teacher/exams/new` dan `/teacher/exams/<id>`) — lalu membaca baris yang
benar-benar diserahkan ke basis data, bukan hanya memastikan rutenya bertanya;
`.freebuff/mutate_exam_school.py` **29/29 cacat tertangkap**).

Di pintu create builder, jawaban otoritas **sama** dengan sekolah hasil resolusi sesi
(`filing_school` selalu menjawab dengan sekolah yang ditanyakan bila ia menjawab), jadi yang
melindungi sebuah simpan adalah **penolakannya** — pintu berhenti sebelum baris dibentuk.
Karena itu empat ujinya yang menentukan adalah posting tangan yang menyebut sekolah rumah dan
keanggotaan yang tidak terbaca, keduanya diperiksa **tidak menulis apa pun**; kolomnya sendiri
dipin di pintu JSON, tempat nilainya memang bisa berbeda dari yang disebut body.

## Peta kerja guru anggota: satu baris, satu NPSN — dan kelas yang ditulis harus milik sekolah yang bertindak

Kelima bagian di atas membuat penerimaan seorang guru dari NPSN lain **terlihat** dan
**dapat dipilih**. Yang belum dipin adalah apa yang baris itu **berikan**, dan di mana
baris itu **boleh ditulis**:

- **Penulisnya matriks sekolah tujuan.** `teacher_assignments.save` menulis satu baris
  `(teacher_id, class_id, subject_id, school_id, school_year, status)` setelah
  `validate_targets` menyetujuinya: guru harus `teacher_in_school` (baris `profiles`
  sekolah ini **atau** keanggotaan `active`), dan setiap kelas/mapel harus
  `owned_class_ids`/`owned_subject_ids` sekolah itu — kalau tidak, **403 dan tidak
  ada baris ditulis** (`db.writes == []`), bukan penurunan diam-diam. Mapel yang tidak
  diaktifkan untuk kelasnya (migrasi 049) ditolak dengan cara yang sama.
- **Yang diberikan hanya berlaku di NPSN itu.** `current_pairs`, `assigned_class_ids`,
  `school_pairs_detail` dan `students_in_classes` semuanya bertanya dengan
  **sekolah + guru** dalam satu kueri. Jadi kelas yang anggota ajar di sekolah kerja
  adalah miliknya **di sana saja**: `assigned_class_ids(db, WORK, u)` berisi `c-work`
  sementara `assigned_class_ids(db, HOME, u)` kosong, dan `students_in_classes(db, HOME,
  ["c-work"])` tidak mengembalikan siapa pun. `POST /teacher/assignments` menulis dengan
  `g.user_school_id`, jadi peta yang dibuat admin satu sekolah tidak pernah muncul di
  sekolah lain.
- **Pintu papan nama murid menolak sebelum membaca.** `/teacher/api/exam-roster` menjawab
  **403** untuk kelas yang tidak ditugaskan ke pemanggil — dan penolakan itu terjadi
  *sebelum* satu pun baris `profiles` dibaca, karena daftar murid adalah hal yang paling
  tidak ingin diberikan oleh penolakan yang terlambat.
- **Bobot nilai mengikuti NPSN makalahnya.** `_resolve_grade_component` menerima id
  komponen yang dimiliki sekolah yang bertindak, dan **menjatuhkan** id sekolah lain ke
  `None` (makalah tanpa kategori, yang keluar dari penjumlahan berbobot). Diuji dua arah:
  id milik sekolah sendiri diterima, id sekolah lain ditolak — di kedua sekolah — sehingga
  sebuah makalah hanya pernah masuk ke penjumlahan bobot satu NPSN.
- **Kelas seorang murid adalah milik sekolah yang menuliskannya.** `profiles.class_id` dan
  `students.class_id` datang dari body/kolom CSV, dan dulu tidak ada pintu yang
  menanyakan apakah kelas itu milik sekolah yang bertindak — padahal
  `school_integrity` sudah lama melaporkan baris seperti itu sebagai
  `class_pupil_mismatch` (kelas sekolah lain menahan murid sekolah lain), lengkap dengan
  pintu karantina/pemulihan untuk membersihkannya. Aktor yang *punya alasan* memegang id
  kelas sekolah lain adalah guru lintas NPSN itu sendiri: `/students/import` adalah
  halaman guru. Aturannya kini satu kalimat di satu fungsi —
  `student_import.owned_class_id` (`(class_id, error)`), yaitu pertanyaan yang sama
  yang ditanyakan pada baris `classes` (`id` **dan** `school_id` dalam satu kueri):
  - `create_student_account` (penulis tunggal setiap akun murid) menolaknya sebagai
    `ValidationError` **sebelum** akun dibuat — jadi pintu yang ditambahkan nanti pun
    mewarisi aturannya;
  - `/students/import/csv` (guru) dan `POST /api/students/import` menjawab **422** satu
    kali untuk seluruh berkas;
  - `/admin-sekolah/students/create` dan `/admin-sekolah/students/<id>/edit` menolaknya
    dengan pesan dan tidak menulis apa pun.
  - Kelas **kosong** tetap berarti "belum ditempatkan" (bukan penolakan), sedangkan
    pembacaan `classes` yang **gagal** menolak: "tidak bisa bertanya" bukan "milik kita",
    dan yang dijaga adalah baris yang akan jadi dasar seluruh daftar kelas sekolah.

Penjaga: `tests/unit/test_member_teacher_crud_in_the_active_npsn.py` (13 uji —
peta ditulis dengan `school_id` sekolah yang bertanya, kelas/mapel sekolah lain ditolak
tanpa baris, satu baris memberi kerja di satu NPSN dan tidak di NPSN lain, pintu papan
nama menolak sebelum membaca murid, dan komponen bobot sekolah lain dijatuhkan — semuanya
dijalankan terhadap database yang berisi **dua** sekolah dengan baris berbeda, jadi
"membaca NPSN aktif" tidak bisa lulus karena fixture kosong) dan
`tests/unit/test_pupil_class_belongs_to_the_school.py` (17 uji — aturan itu sendiri
termasuk pembacaan yang gagal dan kelas kosong, penulis tunggal yang tidak mencetak akun,
dua pintu impor, dua pintu admin, dan dua ejaan rumah (`_class_in_school` di
`admin_sekolah` untuk form kenaikan kelas vs `owned_class_id` untuk murid) yang harus
menjawab empat pertanyaan yang sama dengan jawaban yang sama).
`.freebuff/mutate_pupil_class_scope.py` **9/9 cacat tertangkap** (filter sekolah dihapus,
hasil kosong dibaca sebagai milik sendiri, pembacaan gagal dibaca sebagai izin, kelas
kosong dibaca sebagai kelas asing, dan masing-masing dari empat pintu berhenti bertanya).

---

## Q&A — setelan, wewenang, dan hal teknis

Bagian ini menjawab pertanyaan yang benar-benar muncul dari sekolah dan dari guru, bukan
yang mudah ditulis. Setiap jawaban menyebut **pintu** (route/halaman) atau **berkas** yang
menegakkannya, supaya pembaca bisa memeriksa sendiri alih-alih percaya pada dokumen. Kalau
sebuah jawaban di sini berbeda dengan perilaku aplikasi, aplikasinya yang benar dan bagian
ini yang harus diperbaiki.

### S. Setelan

**S1. Siapa yang boleh mengubah setelan sekolah?**
`admin_sekolah` — dan hanya lewat pintu yang memang ada: `/admin-sekolah/profile` (identitas
sekolah), `/admin-sekolah/school-years` (tahun ajaran, termasuk menutupnya),
`/admin-sekolah/grade-weights` (bobot nilai), `/admin-sekolah/emails/template` (template
email), `/admin-sekolah/subscription` dan `/admin-sekolah/invoices` (langganan).
`principal` dan `vice_principal` **tidak punya satu pun pintu tulis** di sana; mereka membaca.
Guru tidak punya pintu tulis tingkat sekolah sama sekali.

**S2. Kepala sekolah punya akses lebih daripada guru?**
Lebih **luas untuk membaca** (seluruh sekolahnya), bukan lebih tinggi untuk menulis. Bila
ditugaskan sebagai guru mapel, ia menulis sebagai guru: hanya ujian miliknya sendiri
(`exam_access.can_manage_exam`), hanya di kelas/mapel yang ditugaskan. Menghapus atau membuat
akun tetap milik `admin_sekolah` — yang tahu siapa kepala sekolahnya adalah sekolahnya.

**S3. Siapa pemilik kunci API AI, dan apakah terlihat oleh guru lain?**
Guru pemiliknya sendiri. `teacher_ai_keys` difilter `teacher_id` di setiap route
(`/teacher/ai-settings/add-key`, `/<key_id>/toggle`, `/<key_id>/delete`) dan rubrik per mapel
ada di `teacher_ai_settings` dengan filter yang sama. Tidak ada route yang membaca kunci guru
lain, dan admin sekolah tidak bisa melihat maupun memakai kunci guru. Halaman itu menandai
satu kunci `is_active` pada satu waktu: mengaktifkan yang baru menonaktifkan yang lama.

**S4. Apakah nilai AI menggantikan nilai guru?**
Tidak. AI mengembalikan **saran** skor beserta alasannya; yang tersimpan adalah angka yang
ditekan guru. Karena itu fitur ini juga dijaga langganan: route-nya memakai
`require_subscription("ai_grading")`, jadi sekolah tanpa fitur itu mendapat penolakan yang
dijelaskan, bukan tombol yang gagal diam-diam.

**S5. Siapa yang mengatur pengiriman email (SMTP)?**
`super_admin`, di `/super-admin/email-settings`. Kata sandi kotak surat bersifat
**tulis-saja**: tidak pernah dikirim ke template, dan simpanan kosong mempertahankan yang
tersimpan (bukan menghapusnya). Ini pintu yang menggantikan `SMTP_PASSWORD=` kosong di
`.env` yang dulu membuat "kirim kode reset" selalu gagal tanpa jalan keluar selain root.

**S6. Kenapa guru tidak bisa mengubah bobot nilai atau tahun ajaran?**
Karena keduanya berlaku untuk **seluruh sekolah**, bukan untuk satu ujian: bobot dipakai
semua guru di rapor, dan tahun ajaran menutup arsip semua orang. Pintunya ada di
`admin_sekolah`. Yang bisa diatur guru per ujian adalah bobot per tipe soal di dalam ujian
itu sendiri (mark scheme), karena itu milik makalahnya.

**S7. Apa yang terjadi pada ujian di tahun ajaran yang sudah ditutup?**
Penulisan ditolak, termasuk dari layar koreksi, editor kunci jawaban, tombol hitung ulang
dan override — bukan hanya dari wizard penutupan. Penegaknya `app/decorators/year_lock.py`,
yang membaca method request (jadi membaca tetap boleh) dan menyelesaikan tahun dari
**sumber dayanya**, bukan dari sesi: guru bisa masuk di tahun berjalan dan tetap membuka
form milik makalah tahun lalu. Pesannya menyebut tahun tutup sebagai arsip, bukan "akses
ditolak".

**S8. Di mana murid mengatur akunnya?**
`/student/settings` (kata sandi) dan `/student/dashboard`. Murid tidak punya pintu untuk
mengubah data akademik (kelas, NISN) — itu milik sekolah, karena data itulah yang dicetak
di rapor dan dipakai mencocokkan lembar jawaban.

**S9. Bagaimana permintaan data pribadi (UU PDP) dilakukan?**
Guru dan staf memakai `/teacher/settings`: **Ekspor Data Pribadi**
(`/api/account/export-data`) dan **permintaan penghapusan**
(`/api/account/delete-request`, status di `/api/account/deletion-status`). Permintaan itu
diproses lewat `app/services/data_retention_service.py`, dan admin sekolah melihat bahwa
ekspor kredensial termasuk hal yang dibatasi kebijakan retensi. Detail kewajiban dan
jangka waktunya ada di `docs/COMPLIANCE.md`, sedangkan pernyataan yang dibaca publik
(termasuk di mana data disimpan) dirender oleh `app/templates/compliance/privacy.html` dari
konfigurasi, bukan ditulis tangan di halaman itu.

### T. Teknis

**T1. Saya membuka ujian milik guru lain, lalu muncul "Ujian ini tidak tersedia untuk
akun Anda" di halaman lain (mis. AI Grading). Peringatan apa itu?**
Itu **pesan sisa dari penolakan sebelumnya**, bukan pesan tentang halaman yang sedang
dibuka. Penolakan ujian memakai `flash(...)` lalu mengalihkan ke `/teacher/grading`
(`_guard_exam` di `app/routes/teacher.py`, kalimatnya di `app/utils/denials.py`), sedangkan
`teacher/grading.html` tidak merender pesan flash — jadi pesan itu menunggu di sesi dan
baru tampil di halaman berikutnya yang merendernya (`ai_settings`, `exams`, `results`,
`subjects`, `invigilation`, `grading_queue`). Artinya: **tidak ada akses yang bocor dan
tidak ada fitur AI yang rusak**; yang terjadi adalah satu tautan ke makalah yang bukan
milik akun itu. Muat ulang halaman sekali dan pesan itu hilang.

**T2. Kenapa guru tidak bisa membuka ujian koleganya, padahal satu sekolah?**
Karena "satu sekolah" bukan izin. `app/utils/exam_access.py` menanyakan satu pertanyaan:
pemilik makalah (`teacher_id` = pemanggil) **atau** admin sekolahnya. Admin sekolah boleh
mengelola semua ujian sekolahnya; guru boleh mengelola miliknya sendiri. Route yang hanya
**melaporkan** (cetak, ekspor, pratinjau, analisis) memakai `ACCESS_READ`; route yang
mengubah memakai `ACCESS_MANAGE`.

**T3. Guru mengajar di dua sekolah (dua NPSN). Kenapa menyimpan ditolak?**
Karena kertas itu milik sekolah yang lain dari yang sedang aktif di sesi, dan bobot, murid,
serta nilainya mengikuti sekolah kertas itu. Penolakannya menyebut **dua nama sekolah**
("Kertas ini milik X, sedangkan sesi Anda di Y. Pindah ke X dulu…"), jadi jalan keluarnya
ada di kalimatnya. Yang **boleh** dilakukan dari sekolah lain adalah membaca laporan
makalah sendiri (`ACCESS_READ`).

**T4. Berapa lama sesi berlaku?**
`app/utils/auth.py` (`SESSION_TIMEOUTS`), dihitung per peran, dengan dua batas sekaligus —
idle dan absolut:

| Peran | Idle | Absolut |
|-------|------|---------|
| `super_admin` | 15 menit | 4 jam |
| `admin_sekolah` | 30 menit | 8 jam |
| `principal`, `vice_principal` | 45 menit | 10 jam |
| `guru` | 60 menit | 12 jam |
| `murid` | 120 menit | 24 jam |

Sesi yang habis tidak mengalihkan ke pintu yang salah: `login_door_for(role, path)` memilih
halaman login menurut peran, dan menurut URL yang sedang dibuka bila perannya tidak diketahui.

**T5. Kenapa menu saya berbeda dengan menu guru lain?**
Sidebar di `app/templates/base.html` disusun per peran, bukan dengan menyembunyikan tautan
yang sama. Karena itu tautan yang tidak boleh dipakai tidak dirender sama sekali — dan
route-nya tetap dijaga sendiri, jadi menghapus atribut di DevTools tidak membuka pintu.
Akun yang belum terhubung ke sekolah ditolak dengan kalimat `NO_SCHOOL` di pintu yang
membutuhkan sekolah, bukan dengan daftar yang kosong tanpa alasan.

**T6. Penolakan kadang 403 JSON, kadang kembali ke halaman. Mana yang benar?**
Keduanya, dan pemilihnya satu fungsi, `_wants_json()`: browser yang mengirim form mendapat
alih halaman + pesan (bisa dibaca manusia), sedangkan `fetch`/API yang meminta
`application/json` mendapat **403** yang bisa dibaca kode — karena 302 ke halaman HTML akan
terlihat seperti sukses dan menyembunyikan penolakan.

**T7. Siapa yang boleh memakai `/tools` (lembar jawaban OMR, pratinjau perangkat)?**
Staf saja: `super_admin`, `admin_sekolah`, `guru` (`app/routes/tools.py`, `staff_required`).
Murid mendapat **403** yang menyebut perannya, bukan halaman yang kosong atau galat 500 —
dulu `login_required` saja membuat murid bisa membuka alat internal itu. Pratinjau perangkat
tetap memakai **sesi si pembuka**: halaman milik peran lain akan dialihkan, dan halaman itu
mengatakannya terang-terangan alih-alih diam-diam menampilkan halaman lain.

**T8. Form yang dirender belakangan (daftar kunci AI) tidak bisa menyimpan. Kenapa?**
Token CSRF dipasang pada event **submit** (fase capture), bukan sekali saat halaman selesai
dimuat: form yang lahir dari `<template x-for>` setelah `DOMContentLoaded` tidak pernah
mendapat token dengan cara lama. Itulah sebabnya semua form dinamis bisa menyimpan tanpa
satu pun `onsubmit` tambahan, dan halaman yang scripting-nya mati tetap membawa token di
markup-nya.

**T9. Bahasa apa yang dipakai halaman, dan mengapa ada halaman yang tetap Indonesia?**
Defaultnya Inggris; pilihan pembaca disimpan di `localStorage.sg_lang` dan selalu menang.
Halaman yang copy-nya memang belum diterjemahkan memaku dirinya (`content_lang = 'id'`),
dan itu disengaja: toggle tidak bisa menerjemahkan kalimat yang tidak ada. Yang tetap
mengikuti toggle di halaman semacam itu adalah chrome (sidebar, top bar, footer), karena
bagian itulah yang memang punya dua bahasa. Dokumen cetak (`monitor.html`,
`print/report_card.html`, `student/result_detail_pdf.html`, `teacher/print_exam_report.html`)
selalu Indonesia karena targetnya kertas, bukan layar.

**T10. Kenapa jawaban esai tidak hilang walau WiFi putus?**
Halaman ujian menulis draf ke `localStorage` lebih dulu dan menyinkronkan ke server saat
online (`app/static/js/exam-autosave.js`), dengan status di bilah ujian dan di bawah lembar
jawaban. Itu juga alasan `MAX_CONTENT_LENGTH` 50 MB: gambar/canvas satu halaman ikut terkirim
saat jaringan kembali.

**T11. Nilai sudah tampil, lalu berubah setelah "hitung ulang". Kenapa?**
Karena nilai akhir dihitung dari **jawaban tersimpan + skema bobot yang dibekukan**
(`app/services/exam_scoring.py`), bukan dari angka yang dikirim halaman. Tombol hitung ulang
membaca ulang keduanya; karena itu nilai bisa bergerak bila kunci jawaban, bobot, atau
penalti berubah setelah pengumpulan — dan justru itu gunanya: satu penjumlahan, bukan dua
yang berbeda diam-diam.

**T12. Siapa yang boleh mengoreksi esai, dan apakah koreksi bisa diambil alih?**
Guru pemilik ujian (dan admin sekolahnya). Setiap tulisan pada lembar diperiksa ulang ke
ujiannya, jadi skor tidak bisa ditulis ke lembar milik ujian orang lain hanya dengan menebak
id. Yang mengubah nilai akhir tetap satu jalur: `exam_scoring`, yang juga dipakai tombol
hitung ulang.
