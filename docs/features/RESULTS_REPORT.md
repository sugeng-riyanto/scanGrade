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

Ada **empat** pintu masuk, dan keempatnya diuji:

| pintu | dari mana | bentuknya |
|---|---|---|
| nama di tabel **Peringkat Murid** | laporan resmi | tautan |
| nama di kartu **Pernyataan Capaian** | laporan resmi | tautan |
| **Indeks murid kelas** | halaman analisis butir (`/teacher/analysis/<exam_id>`) | daftar tiap murid, urut peringkat |
| **Laporan** di baris antrean koreksi dan di kepala panel | halaman koreksi (`/teacher/grading/<exam_id>`) | satu klik per kertas, tab baru |
| **Laporan** di baris daftar hasil | daftar nilai (`/teacher/results?exam_id=…`), kedua bagian: tabel desktop dan kartu HP | satu klik per kertas, tab baru |

Halaman ini menjawab pertanyaan yang berbeda dari
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

### Pintu masuknya: halaman yang guru benar-benar buka

Laporan resmi adalah halaman yang **diarsipkan**; halaman yang dibuka tiap hari adalah
analisis butir dan antrean koreksi, dan sampai sekarang keduanya tidak punya jalan ke
halaman seorang murid sama sekali. Dua pintu itu ditambahkan, dan keduanya memakai
daftar yang sama:

| permukaan | apa yang ditambahkan | aturannya |
|---|---|---|
| `/teacher/analysis/<exam_id>` | kartu **Indeks murid kelas**, tepat di bawah pemilih kerangka: satu baris per murid — peringkat, nama, nilai — masing-masing menuju halaman murid itu | urutannya `exam_report.ranking` yang sama dengan laporan resmi, jadi dua daftar tidak bisa mengurutkan kelas dengan dua cara |
| `/teacher/grading/<exam_id>` | tautan **Laporan** di tiap baris antrean **dan** di kepala panel kertas yang sedang dikoreksi | `@click.stop` di baris antrean, karena barisnya sendiri memilih murid — tanpa itu, membuka laporan juga menggeser antrean |
| `/teacher/results?exam_id=…` | tautan **Laporan** di baris tabel desktop **dan** di kartu HP pada partial `_results_table.html` | satu guard untuk dua hal: `exam_id and s.student_id` — tanpa ujian tidak ada laporan yang bisa dialamatkan, dan tanpa profil rutenya 404 |

Tiga hal yang gagal tanpa terlihat, dan karenanya diuji:

1. **Tidak ada pintu untuk kertas tanpa profil.** Rute murid menjawab 404 untuk kertas
yang barisnya tidak menyebut profil, jadi tautannya tidak dirender (`x-show="s.student_id"`,
`x-show="current.student_id"`) — tetapi muridnya **tetap terdaftar** di indeks (sebagai
chip tanpa tautan, dengan alasan tertulis): daftar yang diam-diam menghilangkan kertas yang
tidak bisa ditautkan akan bertentangan dengan total di halaman yang sama.
2. **Antrean koreksi hanya boleh membaca field yang dikirim API.** `student_id` kini ikut
dalam payload `api_grading_queue`; uji `test_the_row_only_reads_fields_the_payload_sends`
membandingkan setiap `s.<field>` di template dengan dict yang dibangun API, sehingga field
berikutnya yang dipakai markup harus ikut dikirim.
3. **Tautan itu alamat yang benar-benar dilayani app.** Ujinya membangun URL dari
`url_map` Flask sendiri (termasuk prefix blueprint), jadi mengganti nama rute tanpa
mengubah template gagal di suite, bukan di kelas.
4. **Tidak ada pintu di tampilan semua ujian.** Tanpa `exam_id` pilihannya tidak
mengarah ke laporan mana pun, dan `href="/teacher/analysis//report/student/…"` adalah
tautan yang kelihatan utuh tetapi 404 — jadi guard-nya menyebut dua syarat sekaligus,
`exam_id and s.student_id`.

Baris daftar hasil juga menaruh pintunya **di sebelah `Detail`**, bukan menggantikannya:
`Detail` adalah halaman *satu kertas* (markah dan umpan balik kertas itu), dan yang baru
adalah halaman *murid* — dua dokumen berbeda tentang anak yang sama, jadi dua pintu
alih-alih satu tombol yang artinya bergantung pada tempat menekannya. Di baris ini label
memakai pasangan `t('Laporan','Report')` seperti lencana terlambat di partial yang sama,
sehingga kalimatnya ikut toggle pembaca.

Di salinan **tautan publik** tidak ada indeks sama sekali — nama murid adalah yang
disembunyikan laporan itu — dan penjagaannya dua lapis: templat tidak merendernya saat
`public_view`, dan rute `/r/<token>` memang tidak pernah menerima daftarnya. Diverifikasi
live: indeks muncul di halaman analisis sungguhan, tiap tautannya menjawab 200, payload
antrean mengirim `student_id` untuk setiap baris, dan tautan di antrean juga 200.

### Indeks laporan: satu menu untuk kedua dokumen

Halaman: **`/teacher/reports`** · templat: `app/templates/teacher/reports.html` ·
layanan: `analysis_scope.learners_in_scope()` (baris baru) + `analysis_scope.report()` yang
sudah ada lewat `_scope_report()` · uji: `tests/unit/test_reports_hub.py`

Empat pintu di atas menjangkau murid dari setiap daftar yang dibuka guru, tetapi tetap
tidak ada satu pun di menu: laporan itu **per ujian**, jadi menu tidak bisa menyebut satu
dokumen — yang bisa disebut menu adalah **indeks** yang membuat pilihannya murah. Satu
entri sidebar per peran (guru / admin sekolah / super admin), di bawah bagian *Laporan*
yang sama dengan analitik, menuju satu halaman berisi dua bagian:

| bagian | isi | pintunya |
|---|---|---|
| **Laporan Kelas (Global)** | satu baris per ujian di cakupan: judul, mapel, guru & sekolah (untuk admin/super admin), peserta, jumlah soal, soal bermasalah, rata-rata, median, lulus % | `/teacher/analysis/<exam_id>/report` + analisis butir + PDF/CSV (masing-masing membawa bahasa pembaca) |
| **Laporan Murid (Individu)** | satu baris per **murid per ujian** di cakupan: nama, judul ujian, tanggal, nilai, status penilaian | `/teacher/analysis/<exam_id>/report/student/<student_id>` |

Empat keputusan yang membuat indeks seperti ini jujur:

1. **Perannya adalah peran yang punya lingkup, dan itu satu daftar.** Guard-nya
`role_required("guru", "admin_sekolah", "super_admin")` dan ada uji yang menyamakannya
dengan `set(analysis_scope.SCOPE_LABELS)`. Peran yang diterima halaman tetapi tidak
dikenal lingkup akan merender laporan **kosong tanpa error apa pun** — dan sidebarnya
akan menawarkannya dengan senang hati. Murid bukan hanya ditolak: ada uji yang memastikan
entri itu **tidak ada** di menunya.
2. **Bagian kelas membaca `_scope_report()` yang sama dengan halaman statistik** (dan PDF-nya),
bukan perhitungan kedua. Ada uji yang menolak `analysis_scope.report(` di badan rute ini,
sehingga angka di samping sebuah pintu tidak bisa berbeda dengan dokumen di baliknya.
3. **Setiap pintu adalah alamat yang dilayani app** — dibangun di uji dari `url_map` Flask,
bukan dibaca dari teks templat — dan baris murid hanya ditulis untuk kertas yang menyebut
profil. Kertas tanpa profil **tetap terdaftar** (ia dikerjakan, dan total di atasnya
menghitungnya) dan memakai chip *tanpa akun*, bukan tautan yang 404.
4. **Daftar yang berhenti diam-diam terlihat seperti sekolah dengan anak sebanyak itu.**
Karena itu ada batas `MAX_LEARNERS = 400` yang **dicetak** saat tercapai, dan filternya
berjalan di browser memakai kunci yang sudah di-lowercase sekali di rute. Filter yang
kembali ke server berarti meminta kotak satu-core membaca ulang empat puluh ujian supaya
pembaca bisa mengetik nama yang sudah ada di halaman itu.

Dua hal teknis yang perlu diketahui: `learners_in_scope` **tidak** boleh mengandalkan
`order()` query — kertas dibaca per potongan `CHUNK` dan `order()` di dalam `.in_` yang
berpotong mengurutkan tiap potongan terhadap dirinya sendiri, jadi kertas terbaru hanya
terbaru di dalam 25-nya sendiri; pengurutannya di Python, dan ada uji yang gagal kalau
`order()` muncul kembali. Potongan yang gagal dibaca **di-log dan dilewati** (kertas yang
berhasil dibaca tetap terdaftar), bukan mengosongkan seluruh daftar.

Satu kelemahan yang masih nyata dan tidak ditutup di sini: bagian kelas melewati
`_submissions()` di dalam `analysis_scope.report()`, yang belum menangani kegagalan
sementara Supabase — jadi gangguan jaringan sesaat tetap menjadi 500 di halaman ini,
sama seperti di halaman statistik. Yang baru menanganinya hanya pembacaan baris murid.

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

### Berkas per murid: PDF, XLSX, CSV buatan server

Halaman murid dulu hanya bisa dicetak lewat dialog print browser, dan itu bukan berkas:
yang tercetak adalah **layar** apa adanya (chrome, sidebar, tombol), tidak bisa dibuat sama
sekali dari ponsel, dan tiga puluh laporan anak mendarat di satu folder dengan satu nama.
Sekarang ketiganya dibangun di server dari payload yang halaman itu sendiri render
(`exam_report.learner()` → `app/services/learner_report.py`), sehingga angka di kertas yang
diterima orang tua tidak bisa berbeda dari angka di layarnya.

| berkas | untuk apa |
|---|---|
| **PDF** (A4 potrait) | lembar yang diarsipkan sekolah dan dibaca keluarga — sampul, posisi, enam sebutan soal, tabel tingkat dengan dua batang, tiap soal, kekuatan/kelemahan/langkah, pernyataan capaian, metode |
| **XLSX** | lembar kerja: angka sebagai angka, bagan Excel yang menempel pada selnya (capaian murid vs kelas, sumbu 0–100) |
| **CSV** | dibaca dan disaring; satu berkas dengan bagian-bagiannya, bukan empat berkas terpisah |

Empat hal yang membuat berkas seperti ini berbohong, dan bagaimana masing-masing ditahan:

* **Kunci jawaban hanya ada di salinan guru.** `public=True` — argumen *builder*, bukan
  sembunyian di markup — yang membuangnya dari CSV (kolomnya tidak ditulis), dari XLSX
  (kolomnya tidak ada) dan dari PDF (kolomnya tidak ditambahkan). Rute yang lupa tidak bisa
  menerbitkannya, karena rute tidak memutuskan isi berkas; dan payload yang di-resolve
  tautan sudah dibangun `with_key=False` sebagai sabuk kedua.
* **Banner salinan berbagi tidak dikarang.** Banner laporan *kelas* berkata "nama murid dan
  kunci jawaban tidak disertakan" — kalimat yang **salah** pada berkas yang dinamai dengan
  nama anak itu dan mencetak namanya. Karena itu ada label sendiri (`learner_shared`): yang
ditahan dari salinan ini adalah kuncinya saja.
* **Dinamai menurut anaknya.** `laporan-<nama>-<ujian>-<kode>.<ext>`, dilipat ke ASCII dan
  hanya menyisakan `[A-Za-z0-9-_]` — nama dengan tanda kutip, garis miring atau baris baru
  adalah header `Content-Disposition` yang pecah, jadi yang tersisa hanyalah yang dibaca
  header apa adanya (`Ñoño` → `Nono` adalah nama berkas yang lebih baik daripada
  `laporan.pdf`).
* **Pintunya adalah alamat yang aplikasinya layani.** Halaman menulis tiga tautan dari
  `download_base` yang **rutenya sendiri** berikan — rute guru `/teacher/analysis/<ujian>/
  report/student/<murid>`, tautan berbagi `/r/<token>` — sehingga tidak mungkin menunjuk ke
  salinan lingkup yang lain, dan rute yang lupa memberikannya tidak menulis pintu sama sekali
  (alamat setengah jadi terlihat utuh lalu 404). Tautan berbagi menjawab berkas yang sama:
  `/r/<token>/download.<ext>` membaca baris token dan, bila token itu menunjuk seorang murid,
  mengirimkan berkas murid itu dengan `public=True`.

Gerbang: `tests/unit/test_learner_files.py` (29 uji — tiga dokumen, redaksi di ketiga
format, banner, nama berkas yang bermusuhan, dua pintu, posisi whitelist ekstensi sebelum
pembacaan apa pun) dan `.freebuff/mutate_learner_files.py` (**13/13 cacat yang disuntikkan
tertangkap**). Bukti langsung pada data nyata: `.freebuff/probe_learner_files_live.py` —
login sebagai admin sekolah demo, ketiga berkas 200 dengan magic byte dan nama lampiran
yang benar, lalu tautan murid dibuat, berkasnya diambil **tanpa sesi**, kolom kunci tidak ada
dan nama guru tidak ikut, dan tautannya dicabut kembali (404).

### Satu dokumen untuk sekolah: lampiran + zip

Sekolah menyimpan satu dokumen per ujian, bukan satu dokumen **ditambah** tiga puluh
lembar stapler. Jadi laporan kelas kini punya dua pintu tambahan:

| pintu | apa yang keluar |
|---|---|
| `download.pdf?appendix=1` | laporan kelas **yang sama**, dengan lampiran: satu halaman potret per murid, diawali indeks nama/band/peringkat/nilai |
| `learners.zip` | satu berkas `laporan-<murid>-*.pdf` per anak — berkas yang sama persis dengan yang disajikan pintu masing-masing murid |

Empat hal yang membuat keduanya jujur:

* **Halaman kelasnya tidak bergeser.** Lampiran dilukis lewat *page template* kedua,
  dan bingkai lanskapnya memakai padding bawaan `Frame` (6 pt) yang sama dengan
  `SimpleDocTemplate` — sehingga dokumen ber-lampiran menata ulang laporan kelas
  **persis** seperti dokumen yang sudah beredar. Menolkan padding itu melebarkan
  bingkai 12 pt dan memindahkan titik potong tabel butir; ujinya membandingkan teks
  tiap halaman laporan lama dengan halaman yang sama di dokumen ber-lampiran.
* **Halaman lampiran *adalah* berkas anak itu.** Keduanya memakai `learner_story()`
  yang sama — ujinya mencocokkan halaman lampiran dengan halaman `learner_pdf()` milik
  murid tersebut, karena dua builder adalah cara halaman yang diarsipkan dan halaman
yang diserahkan mulai berbeda.
* **Indeksnya mengikuti urutan dokumennya sendiri** (`analysis.people`, urutan tabel
  murid di laporan itu), bukan urutan peringkat — jadi labelnya bisa diklaim dan diuji.
* **Salinan berbagi tidak pernah membawa lampiran.** Penolakannya di *builder*: meminta
  lampiran pada laporan publik dibuang dan dicatat di log, bukan dipercayakan pada rute
  yang hari ini kebetulan tidak memintanya. Halaman per anak di dalam tautan publik adalah
  kegagalan terburuk fitur ini, dan zipnya pun dibangun `public=True` bila diminta begitu.

Keduanya berhenti di `learner_report.MAX_FILES` (120) dan **mengatakannya**: lampiran
mencetak "hanya {n} dari {total} murid", zipnya menaruh `catatan.txt`. Daftar yang berhenti
diam-diam terlihat seperti kelas dengan jumlah murid segitu. Nama berkas lampiran diberi
akhiran `-lampiran.pdf` supaya tidak menimpa laporan kelas di folder unduhan yang sama.

Gerbang: `tests/unit/test_report_appendix.py` (21 uji) dan `.freebuff/mutate_appendix.py`
(**14/14 cacat tertangkap** — termasuk padding bingkai, urutan indeks, penolakan publik,
penomoran nama yang bertabrakan, dan hilangnya satu pintu dari halaman). Bukti langsung:
`.freebuff/probe_appendix_live.py` pada Supabase demo — laporan biasa 2 halaman lanskap,
`?appendix=1` 5 halaman (2 lanskap + 3 potret) bernama `...-lampiran.pdf`, dan zip berisi
berkas potret per murid yang menyebut nama anaknya.

## Batas yang diketahui (belum dikerjakan)

- **Belum ada DOCX.** `python-docx` belum menjadi dependensi. PDF, XLSX, dan CSV sudah ada.
- **Perbandingan antar kelas belum ada** di halaman ini: satu ujian adalah satu kelas.
  Garis tren membandingkan ujian **sebelumnya** dengan mata pelajaran & guru yang sama.
- **Dasbor admin sekolah & super admin** (papan peringkat sekolah, top-10 kelas,
  pertumbuhan, peta wilayah) dan tabel materialisasi
  `analytics_summary` / `analytics_question` / `analytics_student` belum dibangun;
  analisis masih dihitung per permintaan.
