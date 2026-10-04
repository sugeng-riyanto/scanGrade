# ScanGrade — Role-Based Access Control (RBAC) & Interaction Flows

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
| `super_admin` | Mengelola SEMUA sekolah + pengguna + data lintas sekolah | Via Supabase Console | `/super-admin/dashboard` | `/auth/login` |
| `admin_sekolah` | Mengelola 1 sekolah (guru, siswa, kelas, mapel) | Register mandiri (perlu approval) | `/admin/dashboard` | `/auth/login` |
| `principal` | Mengawasi sekolahnya sendiri (baca saja); boleh mengajar bila ditugaskan | Dibuat admin_sekolah | `/principal/dashboard` | `/auth/login_user` |
| `vice_principal` | Mengawasi sekolahnya sendiri (baca saja); boleh mengajar bila ditugaskan | Dibuat admin_sekolah | `/vice-principal/dashboard` | `/auth/login_user` |
| `guru` | Membuat ujian, mengoreksi, melihat hasil | Di-import oleh admin_sekolah | `/teacher/dashboard` | `/auth/login_user` |
| `murid` | Mengerjakan ujian, melihat nilai | Di-import oleh admin_sekolah | `/student/dashboard` | `/auth/login_user` |

---

## 2. Authentication Flow

### 2.1. Login Admin (super_admin & admin_sekolah)
```
Browser → /auth/login → POST (email + password)
  → Supabase Auth sign_in_with_password()
  → Cek profile.status (pending → redirect /auth/activate)
  → Cek role (super_admin/admin_sekolah saja)
  → Set cookies: access_token (24h) + refresh_token (7d)
  → Redirect ke /admin/dashboard
```

### 2.2. Login Guru/Murid/Kepala Sekolah/Wakil Kepala Sekolah
```
Browser → /auth/login_user → POST (email + password)
  → Supabase Auth sign_in_with_password()
  → Cek profile.status (pending → redirect /auth/activate)
  → Cek role (guru/murid/principal/vice_principal — `USER_ROLES`)
  → Set cookies + redirect ke dashboard masing-masing
```
Empat peran masuk lewat pintu yang sama. Satu akun dengan peran di luar keempatnya
(super_admin, admin_sekolah) ditolak dengan pesan bahwa ia salah pintu — mereka memakai
`/auth/login`.

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
| `teacher_assignments` | ALL | ALL own_school | INSERT own + SELECT own | ❌ |
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
                    ┌───────────────────┐
                    │   Landing (/auth)  │
                    └────────┬──────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
       /auth/login     /auth/login_user  /auth/register
       (super_admin,   (guru, murid)     (admin_sekolah)
        admin_sekolah)      │                │
              │              │                ▼
              │              ▼          Pending approval
              │         /teacher/*       by super_admin
              │         /student/*            │
              │                                ▼
              │                           /auth/activate
              │                           (activation code)
              │                                │
              │                                ▼
              │                           /admin-sekolah/*
              │                                │
              │                           Import guru/siswa
              │                                │
              │                           ┌────┴────┐
              │                           ▼         ▼
              │                       /teacher/*   /student/*
              │                       (guru)       (murid)
              │
              ▼
     /super-admin/dashboard
     /super-admin/schools
     /super-admin/users
     /super-admin/exams
     /super-admin/logs
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
