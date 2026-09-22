# Laporan Hasil Ujian (the filed report)

Halaman: **`/teacher/analysis/<exam_id>/report`** · layanan: `app/services/exam_report.py` ·
templat: `app/templates/teacher/analysis_report.html` · uji: `tests/unit/test_exam_report.py`

## Kenapa halaman kedua

| halaman | pertanyaan yang dijawab | dibaca oleh |
|---|---|---|
| `/teacher/analysis/<exam_id>` | "apa yang dilakukan **soal** ini?" — kesulitan, daya beda, misfit, pengecoh | guru, sambil melihat daftar butir |
| `/teacher/analysis/<exam_id>/report` | "apa hasil **kelas** ini, untuk diarsipkan?" — sampul, ringkasan, distribusi, peringkat, pernyataan capaian | sekolah, kepala bidang, wali murid |

Keduanya membaca objek `item_analysis` yang sama. Tidak ada angka di laporan yang dihitung
dengan cara kedua; yang diturunkan di sini hanya angka yang belum punya rumah (median,
modus, kuartil, peringkat persentil, band, kurva lonceng).

## Apa yang **tidak** diklaim halaman ini

Tata letaknya menyerupai laporan asesmen yang diterima sekolah dari lembaga ujian eksternal,
dan **bukan** laporan lembaga mana pun. Halaman ini tidak menyebut nama lembaga atau
program apa pun — yang dipinjam adalah *bentuk* laporan semacam itu, bukan standarnya, dan
menyebut satu nama terbaca sebagai klaim kesetaraan dengan lembaga tersebut. Dua kalimat
wajib ada di halaman (dan diuji):

1. **Band capaian adalah milik aplikasi ini**, pada skala 0–100 milik aplikasi ini. Laporan
   lembaga eksternal memakai skala dan ambang batasnya sendiri, yang tidak dapat
   direproduksi dari nilai mentah sebuah kelas. Keduanya tidak dapat dipertukarkan.
2. **Garis lulus adalah KKM sekolah**, dilaporkan terpisah. Ujian tanpa KKM menyatakan
   standarnya belum diisi — bukan menganggap semua murid lulus.

Aturan penamaannya dijaga mesin: `tests/unit/test_exam_report.py` menyisir **seluruh**
templat dan gagal bila ada yang menyebut nama lembaga atau program ujian (Cambridge,
Checkpoint, IGCSE, GCSE, Edexcel, AQA, IB, A/AS Level, Ujian Nasional, UNBK, ANBK) — `A/AS
Level` dicocokkan peka huruf besar-kecil karena tanpa huruf kapital ia kalimat biasa
("pilih level"), dan `OCR` tidak ada di daftar karena di aplikasi ini artinya *optical
character recognition*.

## Band

| band | huruf | rentang | arti |
|---|---|---|---|
| `outstanding` | A | 85–100 | menguasai seluruh materi, siap ke tahap berikutnya |
| `high` | B | 75–84.99 | menguasai sebagian besar materi |
| `good` | C | 65–74.99 | menguasai materi inti, perlu latihan pada topik yang teridentifikasi |
| `developing` | D | 55–64.99 | sebagian materi; beberapa konsep dasar perlu diulang |
| `foundational` | E | 40–54.99 | dasar saja; perlu pendampingan menyeluruh |
| `critical` | F | 0–39.99 | belum menguasai materi dasar; perlu program perbaikan |

Batas bawah inklusif, batas atas eksklusif kecuali band terakhir. `exams.passing_score`
(KKM) **tidak** mengubah band mana pun.

## Angka dan rumusnya

- **rata-rata**, **median**, **modus** — modus mengembalikan **semua** nilai yang paling
  sering; nilai yang tidak pernah berulang tidak punya modus (bukan "modus = nilai
  pertama").
- **variansi & simpangan baku** — rumus **sampel** (`n-1`), sama dengan `item_analysis`
  dan `analysis_scope`. Populasi akan melaporkan sebaran yang lebih kecil daripada
  halaman lain di aplikasi ini.
- **kuartil & persentil** — interpolasi linear antar peringkat; Q1 halaman ini **adalah**
  p25-nya, karena satu fungsi menghasilkan keduanya.
- **histogram** — tepi tetap 0–100 (lebar 10) supaya sebaran dua semester bisa
  dibandingkan; batang terakhir menampung nilai 100.
- **kurva lonceng** — normal dengan rata-rata & simpangan baku kelas, **diskalakan** ke
  tinggi batang tertinggi supaya satu sumbu; kelas tanpa sebaran tidak digambar kurvanya.
- **peringkat** — peringkat kompetisi (1, 1, 3); persentil = `(di bawah + 0.5 × sama) / n`,
  konvensi titik tengah supaya peringkat teratas bukan 100 dan terbawah bukan 0.
- **topik** — pengelompokan berdasarkan level kisi-kisi (C1–C6 dari
  `analysis_frameworks.LEVELS`); soal tanpa level adalah baris **"Tanpa level"**, bukan LOTS.

## Kartu temuan (dulu diminta sebagai "AI Insight Card")

Setiap temuan adalah aritmetika atas statistik ujian ini, dengan ambang yang disebut di
temuannya sendiri:

| temuan | muncul bila | ambang |
|---|---|---|
| keandalan | selalu, bila alpha/KR-20 dapat dihitung | ≥ 0.70 "cukup andal"; di bawahnya peringkat tidak layak dipakai memutuskan apa pun tentang seorang murid |
| butir tersulit | soal dengan P terendah | P < 30% |
| butir menyesatkan | ada soal dengan D negatif | D < 0 |
| opsi tidak dipakai | ada pengecoh tanpa satu pun pemilih | `count == 0` (opsi kunci tidak pernah dilaporkan) |
| program perbaikan | ada murid di band `critical` | band F |
| di bawah KKM | `Mastery.configured` dan ada yang belum tuntas | KKM sekolah |
| KKM belum diisi | `passing_score` kosong | — |

Temuan **tidak pernah** muncul tanpa `evidence` (angkanya). Halaman menyatakan bahwa ini
bukan teks dari model bahasa.

## Ekspor

Laporan memakai unduhan yang sudah ada dan tidak menggandakannya:
`/teacher/analysis/<exam_id>/download.csv | .xlsx | .pdf`, masing-masing membawa bahasa
pembaca. Tombol Cetak memakai `window.print()` dengan gaya cetak aplikasi.

## Halaman murid: satu murid, satu halaman

Halaman: **`/teacher/analysis/<exam_id>/report/student/<student_id>`** · layanan:
`exam_report.learner()` · templat: `app/templates/teacher/analysis_student.html` ·
uji: `tests/unit/test_student_report.py`

Dua pintu masuk dari laporan kelas, dan keduanya diuji: nama di tabel **Peringkat Murid**,
dan nama di kartu **Pernyataan Capaian**. Halaman ini menjawab pertanyaan yang berbeda dari
tabel kelas — bukan "bagaimana kelas ini", tapi "bagaimana **anak ini**, dan apa yang
harus dikerjakan berikutnya" — jadi halaman ini punya isinya sendiri:

| bagian | isi |
|---|---|
| sampul | nilai, band, peringkat + persentil, rata-rata kelas, KKM sekolah |
| Posisi dan Total | markah terkumpul / markah yang dapat dinilai, ukuran (logit), jumlah soal benar penuh, dan hitungan tiap status |
| Capaian per Level Kisi-kisi | capaian murid **di sebelah** capaian kelas, per level |
| Analisis Tiap Soal | jawaban murid, status, markah, kunci (hanya halaman guru), dan capaian kelas per soal |
| Kekuatan / Kelemahan / Langkah berikutnya | kalimat yang setiap klaimnya membawa angkanya |
| Pernyataan Capaian | paragraf yang sama dengan laporan kelas, tidak disalin ke tempat kedua |

### Empat aturan yang membentuknya

1. **Murid dialamatkan dengan `id`, tidak pernah dengan nama.** Dua murid dalam satu kelas
   bisa bernama sama, dan halaman yang mengambil kecocokan pertama akan dengan yakin
   menceritakan **anak yang salah** — kegagalan yang lebih buruk daripada 404. Kertas yang
   barisnya tidak menyebut profil tidak mendapat halaman sama sekali.
2. **Semua markah adalah `shares` milik `item_analysis`** — angka yang sama dari mana
   statistik butir dihitung. Halaman ini tidak menilai ulang kertasnya; yang ditambahkannya
   hanya *alasan* sebuah soal dibayar sekian, dan rincian level milik murid ini.
3. **Penyebutnya milik murid, bukan milik kelas.** Soal tanpa kunci dan esai yang belum
   dikoreksi guru **dikeluarkan** dari capaian level — bukan dihitung nol — sementara soal
   yang tidak dijawab tetap dihitung nol untuk murid ini. Itu kebalikan dari tabel kelas,
   yang rata-ratanya dihitung atas kertas yang menjawab; halaman menyatakannya.
4. **Soal yang sulit bagi seluruh kelas bukan kesalahan anak.** Kelemahan dipisah dua:
   soal yang **kelas sudah bisa** dan murid ini lewatkan (milik murid), dan soal yang tidak
   satu pun bisa (materi ajar ulang untuk kelas, dan halaman mengatakannya begitu).

### Tautan per murid

Kontrol berbagi **di halaman murid itu sendiri**, dan tautannya adalah tautan murid itu:

* satu kolom nullable, bukan tabel kedua — `analysis_share_links.student_id`
  (`supabase/migrations/033_analysis_student_share.sql`): `NULL` adalah laporan ujian,
  sebuah id profil adalah laporan satu murid. Token, kedaluwarsa, pencabutan dan penghitung
  buka tetap satu mekanisme;
* **setiap pembacaan disaring menurut lingkup**, sehingga tautan seorang murid tidak pernah
  dikembalikan sebagai "tautan ujian" — kekeliruan yang kolom ini ada untuk mencegahnya;
* menekan tombol murid tidak pernah memindahkan tautan kelas, dan **Stop sharing** di bawah
  nama seorang anak tidak pernah mematikan laporan kelas;* salinan yang dibagikan **tidak memuat kunci jawaban sama sekali** (bukan disembunyikan di markup: tidak ada di payload), tidak memuat nama murid lain, dan tidak memuat kontrol berbagi;
* salinan itu **dirender, bukan sekadar dibangun**: `base.html` memilih chrome dari sesi, dan seorang pengunjung anonim dilayani oleh blok `content_noauth` — bukan `content`. Halaman yang hanya mendefinisikan `content` karenanya menampilkan kerangka aplikasi **tanpa isi** kepada orang yang membuka tautannya, sementara seluruh uji atas payload-nya lulus. `analysis_student.html` menjawab keduanya (`content` → `body(public_view)`, `content_noauth` → `body(true)`, sebab pengunjung tanpa sesi memang selalu salinan publik), dan ujinya merender halaman itu dua kali — sebagai guru dan tanpa login — bukan membaca bloknya.

**Migrasi 033 harus dijalankan lebih dulu.** Selama kolom `student_id` belum ada,
`analysis_share` membaca baris dengan `select("*")` dan menyaring lingkup di Python:
menyebut `student_id` dalam proyeksi adalah `42703`, dan `except` setiap pemanggil
mengubahnya menjadi "tidak ada tautan" — yaitu tautan kelas yang berhenti bisa dibuat,
berhenti bisa dicabut, dan halaman yang berkata *"Tidak ada tautan aktif"* sementara
tautannya masih membuka laporan. Tanpa kolom itu, tautan kelas tetap bekerja persis
seperti sebelum 033, dan tautan per murid menjawab `None` alih-alih menulis baris tanpa
lingkup (tautan yang membuka laporan kelas di bawah nama seorang anak lebih buruk daripada
tidak ada tautan). Perintahnya:

```
python deploy/apply_migration.py supabase/migrations/033_analysis_student_share.sql --commit
```

## Batas yang diketahui (belum dikerjakan)

- **Belum ada DOCX.** `python-docx` belum menjadi dependensi. PDF, XLSX, dan CSV sudah ada.
- **Perbandingan antar kelas belum ada** di halaman ini: satu ujian adalah satu kelas.
  Garis tren membandingkan ujian **sebelumnya** dengan mata pelajaran & guru yang sama.
- **Dasbor admin sekolah & super admin** (papan peringkat sekolah, top-10 kelas,
  pertumbuhan, peta wilayah) dan tabel materialisasi
  `analytics_summary` / `analytics_question` / `analytics_student` belum dibangun;
  analisis masih dihitung per permintaan.
