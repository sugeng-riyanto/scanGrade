# Pilihan Ganda Kompleks (PGK) — Cara Kerja

## Apa itu PGK?

Satu soal PGK memuat sebuah **stem** (teks/konteks soal seperti biasa) lalu beberapa
**pernyataan** yang harus dinilai murid satu per satu. Murid tidak memilih satu jawaban
benar; ia memberi satu penilaian untuk tiap pernyataan — misalnya *Benar/Salah*,
*Ya/Tidak*, atau kategori yang ditulis guru sendiri (*Hewan/Tumbuhan/Mikroorganisme*).

Nama tipe yang tersimpan: `complex_multiple_choice` — ditulis di
`app/services/question_types.py` sebagai `COMPLEX_MULTIPLE_CHOICE`, dan sudah masuk
`PICKER_TYPES`, jadi ia muncul sebagai tipe ke-3 di `/teacher/exams/new`.

PGK **objektif**: dinilai otomatis saat submission masuk, tanpa koreksi guru — sama
seperti Pilihan Ganda dan Benar/Salah.

## Bentuk data

Tidak ada tabel baru. PGK memakai bentuk yang sudah lama dipakai `match`, `drag_drop`,
dan `order`: satu soal dengan banyak bagian, disimpan di dalam objek kunci
`answer_key[i]` milik soal itu.

```json
{
  "statements": ["Air mendidih pada 100°C", "Es lebih padat daripada air"],
  "categories": ["Benar", "Salah"],
  "key": [0, 1]
}
```

| Kolom | Arti |
|---|---|
| `statements` | daftar pernyataan, **urutan penting** — kunci dibaca per indeks |
| `categories` | label kategori yang dipakai murid; urutannya juga penting |
| `key` | satu indeks kategori yang benar **per pernyataan** |

`pgk_key()` sengaja membaca tiga bentuk, sebab kunci yang tersimpan dua cara akan
dinilai dua cara: daftar indeks (bentuk builder), **nama kategori** (bentuk yang muncul
kalau kunci diketik ulang di konsol Supabase), dan **boolean** (bentuk form dua-kategori).
Boolean diperiksa **sebelum** integer, sebab di Python `True == 1`. Kunci yang tidak
terbaca mengembalikan `()` — dibaca sebagai "tidak ada kunci di sini", bukan ditebak.
Sejalan dengan itu, `public_options()` mengirim `statements` dan `categories` ke murid
dan **tidak** mengirim `key`.

Mode penskoran per soal disimpan di kolom terpisah, `exams.question_scoring`
(**migrasi `supabase/migrations/037_question_scoring_mode.sql`**), dengan kunci yang sama
seperti `question_types` dan `question_weights` — indeks soal sebagai teks:

```json
{ "3": "akm_standard", "7": "proportional" }
```

Kolomnya `JSONB` dan bukan tabel, sebab ia satu fakta tentang satu soal yang dibaca pada
query yang sama dengan ujiannya. Validasi isinya tidak bisa memakai `CHECK` biasa —
PostgreSQL tidak punya `CHECK` yang bisa menembus isi map `jsonb`, dan subquery tidak
diizinkan di constraint — jadi gerbangnya fungsi `IMMUTABLE question_scoring_valid(jsonb)`
yang dipakai di dalam `CHECK`. Tiga nama yang diterimanya **sama persis** dengan
`question_types.SCORING_MODES`, dan `tests/unit/test_pgk_scoring.py` gagal bila keduanya
menyimpang: mode yang diterima basis data tetapi tidak dikenal penilai adalah aturan
penilaian yang diam-diam tidak melakukan apa pun. Soal yang tidak punya entri di peta itu
dibaca sebagai default (`akm_standard`).

## Aturan penskoran

Tiga mode, dan guru memilihnya **per soal** — aturan yang benar bergantung pada soal yang
ia tulis, bukan pada kertasnya. `DEFAULT_SCORING_MODE = akm_standard`.

### Mode 1 — `akm_standard` (default, rujukan AKM Kemendikbud)

Aturannya bergantung pada **dua sifat soal itu sendiri**, bukan pada jawaban muridnya:

| Sifat soal | Skor mentah |
|---|---|
| 3–5 pernyataan **dan** tepat 2 kategori | **1 / 0** — semua benar, atau nol |
| di luar band itu (≥6 pernyataan, atau >2 kategori) | **2 / 1 / 0** — semua benar / salah 1–2 / salah lebih dari 2 |

Konsekuensi yang paling sering mengejutkan guru, dan memang begitu aturannya:

| Pernyataan | Salah 1 | Salah 2 |
|---|---|---|
| 5 (dua kategori) | **0** — satu salah menghabiskan soal | 0 |
| 6 (dua kategori) | **0,5** — separuh | 0,5 |
| 6 (tiga kategori) | 0,5 | 0,5 |

Itulah sebabnya `pgk_akm_band(key)` mengembalikan `"binary"` atau `"ladder"` **dari kunci
saja**: band adalah sifat soal, sehingga penilai dan simulator membacanya dengan fungsi
yang sama. Default ini yang direkomendasikan karena paling tahan tebakan acak —
peluang menebak benar **semua** pernyataan soal dua-kategori adalah `0.5^n`.

### Mode 2 — `proportional`

`max(0, (jumlah_benar - jumlah_salah) / total)`. Lebih halus, tetapi kurang tahan
tebakan; dipakai untuk latihan formatif, bukan ujian sumatif.

### Mode 3 — `all_or_nothing`

Skor penuh hanya bila **semua** pernyataan benar, nol bila ada satu pun yang salah —
berapa pun jumlah pernyataannya.

### Dari skor mentah ke poin soal

`pgk_score(mode, key, answer)` mengembalikan **share** di `[0, 1]`; poin soal = harga soal
× share. Jadi tangga 2/1/0 pada soal 10 poin otomatis menjadi 10/5/0, dan tangga 1/0
menjadi 10/0. Konversinya diterapkan **sekali**, di fungsi itu — bukan berkali-kali di
tiap pemanggil.

Kunci yang tidak sepanjang pernyataannya, atau jawaban yang bukan satu penilaian per
pernyataan, mendapat `0.0` dan bukan exception: pembaca di modul ini total, dengan alasan
yang sama seperti `match_pairs`. Baris yang dikosongkan murid dibaca sebagai `None`, yang
**tidak benar** tetapi tetap dibedakan dari "salah" supaya `has_answer` bisa membedakan
soal yang tak disentuh dari soal yang dijawab salah.

Harga bawaan soal PGK adalah **3.0** (`mark_scheme.py`), bersama `match`, `drag_drop`, dan
`order` — satu soal PGK meminta beberapa penilaian.

## Simulator di builder

Editor memanggil `POST /api/pgk/simulate` (dijaga `@login_required` lalu
`@require_role(*STAFF_ROLES)`) dan menampilkan empat pola jawaban kanonik —
`all_right`, `one_wrong`, `two_wrong`, `all_wrong` (pola yang jumlah salahnya tak bisa
dibedakan digabung).

Yang penting: **simulator tidak punya aritmetika sendiri**. Tiap barisnya adalah skor dari
`pgk_score` itu sendiri. Simulator yang setuju dengan halamannya alih-alih dengan penilai
lebih buruk daripada tanpa simulator — guru akan memeriksa soal 2/1/0 dan membaca angka
yang tidak pernah diterima muridnya. Kunci yang belum terbaca mensimulasikan **nol baris**
alih-alih mengarang aturan.

## Editor soal (builder)

Di `/teacher/exams/new`, tipe PGK punya editornya sendiri:

- 3–8 pernyataan (`PGK_MIN`/`PGK_MAX` di template). Tombol hapus berhenti di tiga, sebab
  di bawah tiga setiap pernyataan akan mendapat aturan 1/0 yang tidak dijelaskan pedoman —
  batas itu ditegakkan tombolnya, bukan pesan validasi setelah kejadian.
- Kategori dari preset dua-kategori yang **bilingual** (`Benar/Salah`, `Ya/Tidak`,
  `Sesuai/Tidak Sesuai`) atau kategori sendiri dipisah koma (>2 diperbolehkan).
- Satu kunci per pernyataan, dan pernyataan sengaja **tidak** disaring kosong supaya
  panjang `statements` tetap sejajar dengan `key`: pernyataan yang belum diisi membuat
  seluruh kunci tak terbaca (artinya: soal belum selesai), bukan menggeser kunci ke
  pernyataan berikutnya.
- Tabel simulasi yang membaca endpoint di atas (debounce 250 ms).
- Mode skor **tetap "Standar AKM"** — barisnya ditampilkan sebagai keterangan, tanpa
  pemilih. Lihat bagian Batas di bawah.

## Kontrol murid

Halaman ujian menggambar satu baris per pernyataan, dengan satu tombol per kategori.
Ketuk lagi tombol yang sama untuk menghapus penilaian baris itu. Kategori **tidak
diacak**, dan itu berbeda dengan pilihan ganda: kunci di sini adalah *indeks* kategori,
jadi mengacaknya akan memindahkan jawaban benarnya.

## Di mana mode harus sampai ke penilai

Bagian PGK dibaca **lewat mode**, jadi `question_scoring` harus sampai ke `objective_result`
dan ke `earned_points` di setiap titik panggilnya — termasuk rute pindai OMR dan worker
Celery-nya, yang menulis kolom `score` yang sama. Peta mode yang tidak diterima akan
membuat `scoring_mode` menjawab default, yaitu soal `proportional` dinilai seperti AKM
tanpa satu pun tanda. Karena itu ada penjaga sumber
(`test_every_objective_result_call_hands_it_the_mode`) yang menuntut tiap panggilan
menyertakan modenya, dan daftar `SCORING_FILES` di suite yang sama.

Soal lama **tidak bergerak** oleh kehadiran tipe ini: untuk tipe lama `partial_applies()`
tetap setara dengan `part = partial_credit(weights)` yang dulu, dan `objective_result`
menguji tipe **yang tersimpan di kertas**, bukan daftar tipe objektif yang berlaku hari
ini. Itu dibuktikan secara diferensial oleh `.freebuff/audit_pgk_old_marks.py`.

## Batas yang jujur

- **Mode 2 dan 3 belum bisa dipilih dari editor.** Keduanya terdefinisi, teruji, dan
  diterima `CHECK` migrasi 037 (vocabulary tidak bergerak), tetapi sampai perilaku mode 1
  di batas 3–5 vs ≥6 dinyatakan cukup, builder hanya menawarkan Standar AKM. Menambah
  pemilihnya adalah pekerjaan di template, bukan di penilai.
- **Analisis butir per pernyataan belum ada.** Nilai tambah psikometri PGK — tingkat
  kesukaran dan daya beda *per pernyataan*, bukan per soal — belum dikerjakan; analisis
  butir yang ada masih menghitung satu titik data per soal.
- **Kategori tidak diacak**, dan itu disengaja (kunci adalah indeks). Bila suatu saat
  pengacakan kategori diperlukan, kuncinya harus ikut dipetakan ulang, bukan hanya
  tampilannya.
- Kertas hasil **pindai** tidak memuat PGK (satu pernyataan satu penilaian tidak mungkin
  ada di lembar kertas), jadi mode di jalur OMR hanya menjaga invariant "satu aturan di
  semua penulis kolom `score`".

## Di mana mengubahnya

| Ingin mengubah | Berkas |
|---|---|
| aturan skor, definisi band, pola simulasi | `app/services/question_types.py` (`pgk_score`, `pgk_akm_band`, `PGK_SIMULATION_PATTERNS`) |
| daftar mode yang diterima basis data | `supabase/migrations/037_question_scoring_mode.sql` **dan** `SCORING_MODES` — keduanya harus sama |
| harga bawaan soal PGK | `app/services/mark_scheme.py` |
| editor, batas 3–8, preset | `app/templates/teacher/exam_form.html` |
| kontrol menjawab murid | `app/templates/student/take_exam.html` |
| perilaku di batas 3–5 vs ≥6 dan dua-kategori vs >2 | `tests/unit/test_pgk_scoring.py` (`TestModeOneAtTheBoundary`, `TestModeOneWithMoreThanTwoCategories`) |

Setiap perubahan pada aturan skor harus ditulis sebagai **test yang merah lebih dulu**,
dengan aturan AKM ditulis ulang dari pedoman di dalam test itu sendiri
(`akm_rule(n, kategori, salah)`) — sehingga yang diuji adalah pedomannya, bukan
implementasinya.

## Rujukan

- Pedoman penilaian AKM Kemendikbud: aturan 1/0 untuk soal 3–5 pernyataan dua-kategori,
  dan 2/1/0 di luarnya.
- `docs/features/RESULTS_REPORT.md` — bagaimana markah soal ini muncul di laporan hasil.
