# Tipe Soal Baru: Angka/Isian Singkat, Matriks/Grid, Stimulus

Dokumen ini menjelaskan **tiga tipe soal** yang ditambahkan untuk menutup celah standar
asesmen internasional (PISA/IIALS-style) dan AKM Indonesia, beserta tipe keempat yang
ternyata **sudah ada** dan hanya perlu satu mode tambahan.

| # | Tipe | Kode tersimpan | Bentuk kunci | Nilai sebagian |
|---|------|----------------|--------------|----------------|
| 1 | Angka / Isian Singkat | `numeric_short_answer` | `{mode, value, tolerance}` atau `{mode, accepted}` | tidak (benar/tidak) |
| 2 | Matriks / Grid | `matrix` | `{columns, rows:[{text, key}], mode}` | ya — per baris |
| 3 | Stimulus | *(bukan tipe)* `exams.question_stimuli` | `{id: {title, content, questions}}` | — wadah, tanpa poin |
| 4 | Tabel Pernyataan (AKM) — mode *centang semua* | `complex_multiple_choice` + `select: multi` | `{select:'multi', statements, key:[index]}` | ya — per pernyataan |

---

## 0. Keputusan Fase 0: apakah "Statement grid (AKM)" sudah cukup?

**Tidak, dan ia tidak dibangun ulang.** Tipe `complex_multiple_choice` yang sudah ada
**selalu** berarti "setiap pernyataan punya satu kategori benar" — kuncinya adalah daftar
indeks kategori, satu per pernyataan (`app/services/question_types.py`, `pgk_key`). Yang
diminta "pilih semua yang berlaku" adalah bentuk yang berbeda: kuncinya adalah *daftar
pernyataan yang benar*, dan tidak ada kategori untuk ditunjuk.

Keputusan yang diambil: **menambah mode pada tipe yang sama**, bukan tipe baru, dan bukan
duplikat.

* `pgk_select(key)` membaca mode dari **kunci** (`{select: 'multi'}`), bukan dari nama tipe.
  Kunci yang ditulis sebelum field ini ada tetap dibaca sebagai mode satu-kategori — yaitu
  apa yang memang ia maksud. Ini yang membuat kunci lama tidak berubah arti.
* Satu tipe, satu baris skema. Dua tipe yang berbeda hanya oleh satu kontrol adalah cara
  seorang guru berakhir di tipe yang salah.
* Semua pembaca (`pgk_score`, `grade_answer`, `public_options`, `normalise_key`,
  `key_has_answer`, `manual_review_map`) bercabang di satu tempat: `pgk_select`.

**Rumus mode centang-semua** (dokumentasi eksplisit, karena ini keputusan dan bukan detail):

```
skor_soal = poin_soal × (pernyataan yang centangannya tepat / jumlah pernyataan)
```

Satu pernyataan yang dibiarkan kosong dihitung sebagai **tidak dicentang** — itu penilaian
yang bisa diambil murid dengan tidak menyentuhnya. Tetapi **seluruh soal kosong** bukan
jawaban dan bernilai 0 lewat `has_answer`, sehingga mencentang-semua tanpa berpikir tidak
dapat hadiah dari halaman kosong.

---

## 1. Angka / Isian Singkat (`numeric_short_answer`)

### Cara pakai (guru)

1. Tambahkan soal bertipe **Angka / Isian Singkat** di `/teacher/exams/new`.
2. Pilih jenis jawaban:
   * **Angka** — isi jawaban benar, dan toleransi bila jawaban boleh bergeser
     (`0,2` untuk soal yang jawabannya 9,8; kosong berarti harus tepat).
   * **Teks singkat** — daftarkan **semua** ejaan yang diterima (mis. `fotosintesis`,
     `fotosintesa`).
3. Poin soal diatur seperti biasa di tabel Mark Scheme (barisnya `numeric_short_answer`,
   default 1 poin).

### Cara penilaian (persis)

```
mode angka : |jawaban_murid − nilai_benar| ≤ toleransi      → benar
mode teks  : normalisasi(trim, casefold, spasi-tunggal) ada di daftar accepted → benar
```

Normalisasi angka (`question_types.parse_number`) menangani koma desimal Indonesia secara
eksplisit — ini bagian dari pencocokan, bukan tambahan:

* `9,8` ≡ `9.8`;
* bila koma dan titik muncul bersamaan, **yang terakhir** adalah pemisah desimal dan yang
  lain pemisah ribuan, jadi `1.234,5` dan `1,234.5` sama-sama 1234.5;
* titik di akhir dibuang (`12.` = 12);
* spasi, non-breaking space, dan tanda minus unicode dibersihkan;
* `1/2` **bukan** angka — desimal yang ditulis sebagai pecahan tidak ditebak.

### Yang tidak cocok otomatis → **Perlu Ditinjau**, bukan salah

Inilah alasan utama tipe ini ada dalam bentuk ini. Sebuah jawaban yang tidak cocok
**tidak** diberi nol sebagai putusan, melainkan ditandai untuk manusia:

* `manual_review_map()` menulis `{"<index>": {"reason": "format"|"value"|"spelling",
  "answer": "…"}}` ke `submissions.answers["_needs_review"]` saat paper dikumpulkan;
* nilai otomatisnya **nol untuk sekarang** (consistency: satu aturan, `earned_points`),
  tetapi barisnya muncul di halaman koreksi guru;
* guru membetulkan **kunci atau toleransi** di halaman Kunci Jawaban lalu simpan; halaman
  itu memanggil `_recalculate_scores(exam_id)` sehingga seluruh kertas dinilai ulang.

Tiga alasan berbeda karena tiga tindakan berbeda:

| reason | artinya | yang dilakukan guru |
|--------|---------|---------------------|
| `format` | bukan angka yang terbaca (`1/2`, `9,8 cm`) | periksa apakah jawabannya harus diterima; mungkin toleransi/key salah bentuk |
| `value` | angka, di luar toleransi | kemungkinan memang salah — tetapi toleransi 0 pada soal yang seharusnya punya toleransi adalah bug penilaian yang **terlihat sama**, jadi tetap ditinjau |
| `spelling` | teks di luar daftar accepted | tambahkan sinonim/ejaan yang diterima, atau biarkan |

**Jawaban kosong bukan "perlu ditinjau"**: tidak ada yang ditulis, tidak ada yang dilihat.

---

## 2. Matriks / Grid (`matrix`)

### Cara pakai (guru)

1. Tambahkan soal **Matriks / Tabel Pilihan**.
2. Tentukan **opsi bersama** (kolom) — preset `Benar/Salah` dll., atau ketik sendiri
   dipisah koma.
3. Tambahkan **baris** (pernyataan/item), masing-masing dengan kunci sendiri:
   * mode **Satu pilihan** → satu kolom benar per baris (radio di sisi murid);
   * mode **Banyak pilihan** → boleh beberapa kolom benar per baris (checkbox).
4. Baris bisa dinaikkan/diturunkan/dihapus; setiap baris dan kolom CRUD penuh.

### Cara penilaian (persis)

```
skor_soal = poin_soal × (jumlah baris dijawab tepat / jumlah baris)
```

**Baris dijawab tepat** — semuanya-atau-tidak per baris, di kedua mode
(`matrix_row_correct`):

* mode satu pilihan: pilihan tunggal itu sama dengan kunci;
* mode banyak pilihan: **seluruh** kolom benar dipilih **dan** tidak ada kolom salah yang
  dipilih.

Keputusan sadar: **tidak ada nilai sebagian di dalam satu baris**. Baris adalah unit
kredit. Nilai sebagian di dalam baris akan menghasilkan angka yang tidak bisa dijelaskan
ke murid ("kenapa grid ini memberi 0,63?") dan akan membayar penebak pada baris dengan
lebih dari dua opsi. Ini dinyatakan di panel bantuan builder dan di `/guide/skor`.

### Responsif

Tabel lebar adalah masalah, jadi **matriks tidak pernah dirender sebagai tabel**:

* **builder**: tiap baris adalah kartu dengan tombol kolomnya di bawah;
* **murid**: tiap baris adalah kartu dengan tombol opsi di bawah — di HP, tablet, dan
  laptop sama. Kolom media hanya membatasi lebar kolom jawaban (`answer-medium`), bukan
  isinya.

---

## 3. Stimulus (`exams.question_stimuli`)

Satu konteks (bacaan panjang, data, tautan gambar) untuk **beberapa sub-soal** bergaya
PISA/AKM.

### Bentuk data

```json
{"s1": {"title": "Bacaan: Siklus Air", "content": "…", "questions": [3, 4, 5]}}
```

Migrasi `supabase/migrations/069_exam_stimuli.sql` menambahkan satu kolom JSONB. Tidak ada
tabel `exam_stimulus`/`exam_question` baru — sebuah soal di ScanGrade bukan baris, ia
adalah entri di peta JSON (`question_types`, `answer_key`, `question_weights`,
`question_pages`). Menambah tabel untuk stimulus berarti menciptakan struktur kedua yang
harus disinkronkan pada setiap simpan; lihat komentar kepala migrasi untuk alasan
lengkapnya.

### Integrasi Mark Scheme (penting)

Stimulus **tidak punya baris di tabel Mark Scheme**. Yang dihitung adalah tiap sub-soal
menurut tipenya:

| isi stimulus | kontribusi ke tabel skema |
|--------------|---------------------------|
| 2 × `mcq` + 1 × `numeric_short_answer` | bucket `mcq` += 2, bucket `numeric_short_answer` += 1 |
| 1 × `matrix` + 1 × `essay_canvas` | bucket `matrix` += 1, bucket `essay_canvas` += 1 |

Tidak ada rumus baru di `hitung_nilai_akhir()`: sub-soal dinilai persis seperti soal lain
lewat `earned_points`/`objective_result`, lalu dijumlahkan.

### Anti-kolusi

* Urutan **antar stimulus** dan urutan soal tanpa stimulus tetap diacak per murid
  (`randomize_questions`).
* Urutan **di dalam** satu kelompok stimulus dijaga berdampingan: pengacakan bekerja pada
  *kelompok*, bukan per soal (`take_exam.html`, blok `units`). Lima sub-soal yang tersebar
  di seluruh kertas membuat bacaannya terpisah jauh dari soal yang membacanya — itu bukan
  kertas yang lebih sulit, itu kertas yang rusak.
* Opsi jawaban tiap sub-soal tetap diacak sesuai tipe soalnya (pengacakan opsi untuk
  pilihan ganda, dan **tidak** untuk matriks/PGK/TF yang kuncinya berbasis indeks).
* Posisi stimulus **tetap** relatif terhadap sub-soalnya; bacaannya dirender sekali di atas
  soal pertama kelompok itu pada layar murid.

### Editor guru

`Tambah Stimulus` di `/teacher/exams/new` membuat satu kartu konteks (judul + isi). Pada
tiap soal ada daftar `Stimulus` untuk menempelkannya. Stimulus tanpa sub-soal **tidak
disimpan** — konteks tanpa pertanyaan bukan soal.

---

## 4. Integrasi lintas sistem

| Sistem | Bagaimana tipe baru masuk |
|--------|---------------------------|
| Skema poin | `mark_scheme.DEFAULT_MARKS` + `SCHEME_ORDER`: `numeric_short_answer` = 1, `matrix` = 3. Normalisasi ke 100 (`sgNorm100`) dan override poin per soal berlaku sama. |
| Nilai sebagian | `PARTIAL_TYPES` menambahkan `matrix`; `part_factor` bercabang ke `matrix_score` **sebelum** aritmetika pasangan/posisi. `numeric_short_answer` sengaja **bukan** tipe sebagian: satu angka adalah satu keputusan. |
| Penilaian akhir | `earned_points` dan `objective_result` — tidak ada jalur skor bayangan. `exam_scoring` membaca keduanya. |
| Answer keys | Halaman `/teacher/exams/<id>/answer-keys` menampilkan kunci matriks dan **mengedit** kunci angka (mode, nilai, toleransi, daftar ejaan) — tempat guru mendarat setelah sebuah flag `Perlu Ditinjau`. Kunci matriks tetap ditulis di builder, seperti pasangan menjodohkan. |
| Anti-kolusi | Pengacakan soal/opsi tetap; kelompok stimulus dijaga utuh (lihat di atas). |
| Validasi server | `validate_answer_map` membuang baris matriks yang tidak ada, kolom di luar matriks, dan indeks pernyataan di luar grid — **disanitasi, bukan ditolak** (murid tidak boleh kehilangan satu sesi karena satu kunci nyasar), dipanggil di dua penulis: `POST /api/student/sync-draft` dan `POST /student/exams/<id>/submit`. |
| Koreksi guru | `teacher._review_items()` membaca `_needs_review` dan mengirimnya ke halaman koreksi; `grading_queue` menampilkan lencana jumlahnya sebelum kertas dibuka. |
| Nilai yang dimanipulasi | Struktur jawaban divalidasi terhadap struktur **kunci** di server, bukan terhadap apa yang dikirim klien. Klien tidak dapat menambah baris/kolom/pernyataan yang tidak ada. |

---

## 5. Bantuan di dalam aplikasi (dua tempat, sesuai aturan)

1. **Builder** — tiap tipe baru punya panel `<details>` “Cara pakai & cara penilaian” di
   kartu editornya, bilingual (`t('id','en')`), muncul saat tipe itu dipilih.
2. **`/guide/skor`** — bagian **8** (tab Guru) menjelaskan cara membuat dan rumus penilaian
   ketiga tipe, dan bagian **4** (tab Murid) menjelaskan cara menjawabnya, termasuk kenapa
   sebuah jawaban angka bisa masuk antrean “Perlu Ditinjau”.

---

## 6. Rumus, di satu tempat

```
numeric (angka)  : 1 bila |murid − kunci| ≤ toleransi, else 0        (tidak cocok → Perlu Ditinjau)
numeric (teks)   : 1 bila normalisasi(murid) ∈ daftar accepted, else 0
matrix           : poin × (baris tepat / total baris)
pgk select:multi : poin × (pernyataan tepat / total pernyataan)
stimulus         : Σ skor sub-soal menurut tipe masing-masing
```

## 7. Berkas yang terlibat

* `app/services/question_types.py` — tipe, akses kunci, penilaian, bagian, validasi.
* `app/services/mark_scheme.py` — bucket & poin default.
* `app/templates/teacher/exam_form.html` — editor, bantuan, serialisasi kunci.
* `app/templates/student/take_exam.html` — kontrol murid, panel stimulus, pengacakan kelompok.
* `app/templates/teacher/answer_keys.html` — tampilan/edit kunci.
* `app/templates/teacher/grade_detail.html`, `grading_queue.html` — antrean Perlu Ditinjau.
* `app/routes/{teacher,student,api}.py` — simpan, validasi, flag tinjauan.
* `supabase/migrations/069_exam_stimuli.sql` — kolom stimulus (sudah dijalankan di
  database; verifikasinya di
  [docs/deploy/QUESTION_TYPES_MERGE_RUNBOOK.md](../deploy/QUESTION_TYPES_MERGE_RUNBOOK.md) §6).

Cara branch ini disatukan dengan `main` — berkas mana yang diambil dari sisi mana, dan
apa yang diperbaiki setelahnya — ada di
[docs/deploy/QUESTION_TYPES_MERGE_RUNBOOK.md](../deploy/QUESTION_TYPES_MERGE_RUNBOOK.md).
