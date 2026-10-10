# Anti-Cheat System

## Yang Dimonitor

| Event | Deteksi | Aksi |
|-------|---------|------|
| **Pindah tab** | `visibilitychange` + timer 1.5s | Layar soal dikaburkan + penalti bertahap |
| **Keluar layar penuh** | `fullscreenchange` + sampling tiap 2 detik | Overlay memblokir + tangga penalti yang sama |
| **Beralih ke jendela lain** (`focus_lost`) | `window` blur + timer 1.5s, hanya saat halaman **tidak** hidden | Layar soal dikaburkan + tangga penalti yang sama |
| **Rotasi layar** | `matchMedia("(orientation: portrait)")` + `orientationchange` | Dicatat (`orientation_shift`), **tidak** dihukum — lihat [Rotasi layar](#rotasi-layar-bukan-pelanggaran) |
| **Klik kanan** | `contextmenu` | Diblokir |
| **Copy/Paste** | `copy`, `cut`, `paste` event | Diblokir |

Ketiga peristiwa pertama menaiki **satu tangga** (`PENALIZED_VIOLATION_TYPES`): dari sudut pandang sekolah ketiganya perbuatan yang sama — ujian tidak lagi menjadi hal yang dilihat. Satu absence tetap satu charge:

- dokumen **hidden** ditangani jalur `visibilitychange` saja (jalur blur jendela berhenti saat `document.hidden`);
- blur jendela tidak dihitung bila overlay layar penuh sudah menagih absence yang sama (`fullscreenBlocked`);
- hanya **tab yang sedang dilihat** (`_isFocusTab`) yang mengaburkan dan menagih.

Sebelum ini jalur blur jendela hanya menulis satu baris `console.log`: browser yang di-restore-down dan ditinggalkan ke jendela lain tidak terdeteksi sama sekali (`document.hidden` bernilai false).

## Kesempatan Kembali (Second Chance)

Absence tidak lagi langsung dihitung sebagai pelanggaran. Layar soal dikaburkan **segera** — proteksi tidak pernah menunggu — lalu panel menghitung mundur:

| Angka | Nilai | Konstanta |
|-------|-------|-----------|
| Hitungan mundur | **10 detik** | `AWAY_GRACE_SECONDS` |
| Kesempatan per ujian | **2×** | `AWAY_GRACE_CHANCES` |

Kembali sebelum hitungan habis: **tidak ada catatan, tidak ada penalti**, satu kesempatan terpakai. Hitungan habis: kejadian dicatat seperti biasa, lengkap dengan `metadata.away_seconds` (lamanya siswa benar-benar pergi, dihitung sejak event, bukan sejak debounce 1.5 detik).

Kenapa terbatas: kesempatan tanpa batas berarti jendela bebas berulang — lihat jawaban soal 7 selama sembilan detik, kembali, lanjut soal 8 — dan daya cegah adalah alasan fitur ini ada. Setelah kedua kesempatan habis, setiap absence langsung dicatat seperti sebelum fitur ini ada, dan panel tidak lagi menampilkan hitungan mundur (jam yang tidak bisa menyelamatkan siswa hanya akan jadi kebohongan berangka).

Yang menjaga satu absence tetap satu charge:

- hitungan mundur yang sedang berjalan **adalah** absence itu — tiga detektor tidak bisa membuka tiga hitungan untuk satu perbuatan;
- `awayCharged` mencegah hitungan yang sama ditagih dua kali (termasuk oleh tick berikutnya);
- `endAway()` mereset penanda begitu siswa kembali, sehingga absence berikutnya adalah kejadian baru;
- saat hitungan habis, halaman **membaca ulang** apakah siswa sudah kembali (`document.hasFocus()`), bukan mengandalkan event fokus yang mungkin tidak terkirim oleh browser.

Batas yang disengaja: **keluar dari layar penuh tetap dicatat seketika**. Panel itu tidak senyap — ia memblokir soal dan menyebut tombol yang harus ditekan — dan panggilan telepon datang sebagai dokumen *hidden*, yang ditangani jalur `visibilitychange` (bukan sampler fullscreen), sehingga tidak mungkin tertagih dua kali.

Angka 10 dan 2 hanya hidup di `app/services/anti_cheat_service.py`. Halaman ujian (`graceSeconds`/`graceChances`), layar ketentuan yang disetujui murid, panduan `/guide/skor` (kedua tab) dan tutorial murid semuanya membacanya — halaman yang menjanjikan angka berbeda dari yang dihitungnya adalah bentuk defect kelas ini.

## Graduated Penalty

| Pelanggaran ke- | Penalti |
|----------------|---------|
| 1 | Peringatan (warning) |
| 2 | -N poin (default: -5) |
| 3 | -2N poin (default: -10) |
| 4+ | -3N poin (default: -15) per pelanggaran |

`N = penalty_per_violation` (dapat diatur per ujian, default 5)

Jika `max_violations` tercapai dan `auto_submit_on_max = true`:
- Ujian otomatis dikumpulkan
- Semua jawaban tersimpan

## Konfigurasi Per Ujian

Guru dapat mengatur saat membuat/mengedit ujian:

| Setting | Default | Deskripsi |
|---------|---------|-----------|
| Anti-Cheat Aktif | ✅ | Master switch |
| Penalti per Pelanggaran | 5 | Poin dikurangi |
| Maks Pelanggaran | 5 | Sebelum auto-submit |
| Auto Submit | ✅ | Kumpulkan otomatis |
| Wajib Layar Penuh | ✅ | F11 required |
| Blokir Copy-Paste | ✅ | Clipboard diblokir |
| Blokir Klik Kanan | ✅ | Context menu diblokir |
| Watermark Nama | ✅ | Nama siswa di overlay |
| Acak Soal | ❌ | Fisher-Yates shuffle |
| Acak Opsi | ❌ | Opsi diacak per siswa |

### Master switch: "Anti-Cheat Aktif" mati berarti tidak ada catatan

Flag `exams.anti_cheat_enabled` (default `true`) adalah saklar milik sekolah. Saat
sebuah ujian menyetelnya `false`, **tidak satu pun baris** boleh masuk ke
`violation_logs` untuk ujian itu — bukan sekadar "tidak dihukum", tetapi tidak
dicatat sama sekali:

- `POST /api/violation/log` membaca baris ujian **lebih dulu**, lalu menolak menulis
  (menjawab `{"logged": false, "reason": "anti_cheat_disabled"}`) bila flag itu
  `false`. Urutan inilah perbaikannya: dulu barisnya ditulis sebelum ujian dibaca,
  sehingga ujian yang dimatikan tetap memunculkan pelanggaran di laporan guru dan
  tetap menaikkan `submissions.violations`.
- Keputusannya milik server, bukan halaman: POST yang dibuat tangan pun ditolak sama
  seperti POST halaman ujian. Halaman juga dibuat inert (`handleViolation` dan
  `_maybeAutoSubmit` berhenti lebih dahulu) supaya tidak ada spanduk pelanggaran —
  atau auto-submit — yang tidak disetujui oleh catatannya sendiri.
- Dibaca sebagai `is False`, sama seperti `calculate_graduated_penalty`: baris yang
  gagal dibaca bukan sekolah yang meminta senyap, jadi anti-cheat tidak pernah mati
  diam-diam hanya karena barisnya hilang.

Penalti dan penguncian (`resume_code`) sudah sejak awal menghormati flag ini; catatan
adalah bagian yang belum. Dijaga oleh `tests/unit/test_anti_cheat_disabled.py`.

## Watermark

Jika diaktifkan, nama siswa ditampilkan sebagai watermark transparan (6x4 grid, rotasi -25°) di seluruh halaman ujian. Mencegah foto layar/share jawaban.

## Logging

Semua pelanggaran dicatat ke tabel `violation_logs`:
- `exam_id`, `user_id`, `violation_type`, `metadata` (berisi `violation_count` dan `trigger`: `visibilitychange` atau `window_blur`)
- Penalti disimpan di `submissions.penalty`
- Dashboard guru menampilkan total penalti per siswa

## Laporan Guru: kapan siswa meninggalkan ujian

Penalti adalah angka; pertanyaan yang muncul setelahnya adalah **kapan**. Dua permukaan menjawabnya, keduanya membaca `violation_logs` lewat `app/services/anti_cheat_service.py` (`events_for_exam`, `events_for_student`, `leaving_summary`) supaya tidak bisa berbeda cerita:

| Permukaan | Yang ditampilkan |
|-----------|------------------|
| Daftar hasil per ujian (`teacher/results.html` + `_results_table.html`) | Chip di header berisi jumlah **siswa** yang pernah meninggalkan ujian, dan di tiap baris: jumlah kejadian + jam kejadian terakhir (WIB, lewat filter `tz`) |
| Halaman koreksi (`teacher/grade_detail.html`) | Satu baris per kejadian: jam, nama kejadian, dan apakah dihitung ke penalti atau hanya dicatat. Bila tidak ada catatan, halaman menyatakannya |

Jam yang ditampilkan adalah `created_at` dari server, bukan waktu yang dihitung di browser — satu-satunya kolom yang tidak bisa dipindahkan siswa. Halaman koreksi memakai bahasa Indonesia apa adanya karena halaman itu mengunci bahasanya (`content_lang = 'id'`), dan gate `deploy/i18n_coverage.py` menolak pasangan `t()` di halaman terkunci.

## Diverifikasi setiap rilis

Dua panel ini hidup di satu halaman (`student/take_exam.html`) dan di tempat lain
mana pun, jadi rilis yang menjatuhkannya tidak terlihat oleh pemeriksaan halaman
biasa: `/student/exams` tetap menjawab 200 sementara pengawasannya hilang. Karena
itu Gate 4 di auto-deploy membuka **ujian demo** sebagai murid dan membaca panel-panel
itu dari halaman yang disajikan:

| Yang diperiksa | Kenapa itu yang diperiksa |
|---|---|
| halaman ujian yang benar (`data-exam-id`) | halaman error juga menjawab 200 |
| `antiCheat.enabled` **dan** `fullscreen_required` | dengan salah satunya mati, kedua panel tidak akan pernah muncul walau markup-nya utuh |
| keberadaan kedua panel **beserta kalimatnya** | panel tanpa teks adalah layar buram tanpa penjelasan |
| angka hitung mundur dari server | angka yang ditulis ulang di JS bisa berbeda dari `AWAY_GRACE_SECONDS` |
| `fullscreenchange` dan `visibilitychange` | tanpa keduanya, tidak ada yang menyetel flag panel |

Ujian yang dibuka adalah **fixture** (`deploy/demo_exam_fixture.py`), bukan ujian
apa pun yang kebetulan ada: ujian sungguhan boleh punya anti-cheat mati, dan gagal
rilis karena setelan guru bukanlah hal yang diperiksa. Fixture itu disegarkan
sebelum pemeriksaan pada setiap rilis (`python manage.py demo-exam`) sehingga selalu
dapat dikerjakan: ditugaskan ke semua kelas sekolah demo, tanpa jendela waktu,
anti-cheat dan fullscreen menyala, dan percobaan lama di-`retracted` supaya demo
yang sudah mengumpulkan tidak menyembunyikannya. Yang bisa dilihat lewat HTTP
hanyalah dokumen yang dikirim — render-nya sendiri diverifikasi lewat preview.

## Rotasi layar bukan pelanggaran

Laporan dari laboratorium tablet: murid yang memutar layar ikut turun tangga
penalti yang sama dengan murid yang benar-benar meninggalkan ujian. Sebabnya
mekanis, bukan misterius — sejumlah browser tablet **melepas status fullscreen
sebagai efek samping rotasi**, dan tidak ada apa pun di deteksi dulu yang tahu
bahwa orientasi baru saja berubah. Jadi `checkFullscreen()` melihat "tidak
fullscreen" dan menagih `fullscreen_exit` untuk sebuah putaran.

Sekarang halaman memantau orientasi sendiri (`orientationchange` **dan**
`matchMedia`), lalu berlaku aturan berikut:

| Kejadian | Hasil |
|----------|-------|
| Fullscreen lepas **di dalam** jendela rotasi | Overlay tetap naik saat itu juga, **nol** penalti, satu baris `orientation_shift` dicatat |
| Fullscreen kembali sendiri sebelum jendela tutup | Tidak terjadi apa-apa |
| Tetap di luar fullscreen setelah jendela tutup, di perangkat yang fullscreen-nya bertahan saat rotasi | Ditagih `fullscreen_exit` seperti biasa — absen yang melewati penjelasannya |
| Tetap di luar fullscreen setelah jendela tutup, di perangkat yang fullscreen-nya **dilepas** rotasi (iPadOS Safari) | Dicatat, **tidak** ditagih: menagihnya sama dengan menghukum murid karena perangkatnya |

Tiga sifat yang menjaga perbaikan ini agar tidak menjadi celah baru:

* **Overlay selalu lebih dulu.** Jendela rotasi hanya memaafkan *penalti*, tidak
  pernah *soal*: kertas tetap terkunci begitu absen terlihat, sama seperti di luar
  jendela. Kalau cabang rotasi ini pernah pindah ke atas `fullscreenBlocked = true`,
  murid bisa menggoyang tablet untuk membaca soal dari jendela biasa.
* **Jendelanya pendek dan berbatas** (`ROTATION_GRACE_SECONDS`, dari service dan
  diteruskan route — bukan angka yang ditulis di halaman). Ia harus menutup animasi
  rotasi perangkat dan `fullscreenchange` browser; selebihnya menjadi jendela bebas.
* **Debounce hanya menghitung baris yang ditagih.** Sebelum ini, satu baris
  informasi membuat `validate_violation_log` menjawab `rate_limited` untuk keluar
  sungguhan tepat setelahnya — jadi perbaikan rotasi sempat membuka bypass itu
  sendiri. Baris `orientation_shift` juga **tidak** lewat `handleViolation`:
  tidak menambah hitungan lokal, tidak memunculkan banner, tidak memicu
  auto-submit. Server yang memutuskan apa yang dihitung, dan `orientation_shift`
  ada di luar `PENALIZED_VIOLATION_TYPES` sehingga tampil di laporan guru tanpa
  menjadi satu anak tangga.

Dijaga oleh `tests/unit/test_rotation_vs_exit.py`, yang menjalankan metode asli
yang diiris dari template di bawah node: rotasi murni tidak menagih apa pun,
tetapi absen yang melewati jendela tetap ditagih di platform yang seharusnya.

## False Positive Handling

- Peringatan pertama hanya teguran (tanpa penalti)
- Guru bisa membatalkan penalti dengan override score manual
- Siswa bisa mengajukan retraction (penarikan pengumpulan)

## Sinyal Lingkungan Virtual (Laptop/PC virtual, remote desktop) — KONTEKS, BUKAN PELANGGARAN

### Apa yang dikumpulkan

Sekali per attempt, saat murid mulai mengerjakan, halaman ujian mengirim **empat
pengukuran mentah** ke server. Tidak ada satu pun yang dikirim sebagai vonis:

| Jenis sinyal | Nilai mentah dari klien |
|---|---|
| `webgl_renderer` | string renderer WebGL apa adanya |
| `timer_precision` | langkah resolusi `performance.now()` dalam milidetik |
| `pixel_ratio` | `window.devicePixelRatio` |
| `hardware_vs_performance` | jumlah core + frame per detik yang terukur |

**Penilaian hanya di server** (`app/services/environment_signals.py`). Klien tidak
punya cara memberi skor; kalau klien mengirim `score` atau `level`, nilai itu
**tidak dibaca sama sekali**.

### Kenapa ini TIDAK PERNAH memicu penalti

Ciri-ciri di atas adalah ciri yang **juga** dimiliki perangkat yang sepenuhnya
wajar, dan itu bukan catatan kaki — ini alasan utama desainnya:

- Laboratorium komputer sekolah **memang sering berupa VM**; itu cara sekolah
  dengan tiga puluh workstation mengelolanya.
- `llvmpipe` / `OffScreen` adalah nasib laptop dengan driver grafis rusak atau
  browser yang dibatasi kebijakan.
- Timer yang kasar bisa berarti browser itu sendiri sedang melindungi privasi,
  atau ada ekstensi privasi yang aktif.
- `devicePixelRatio` yang tidak lazim bisa berarti murid memperbesar halaman.

Karena itu, **tidak ada satu pun jalur** di sini yang memanggil tangga penalti,
penguncian (`locked_pending_resume`), atau pengurangan skor. Jaminan itu ditegakkan
secara struktural, bukan dijanjikan di dokumen ini:

- modulnya **tidak meng-import** `anti_cheat_service`, `resume_code`,
  `grading_service`, maupun `attempt_status`;
- tidak ada trigger, kolom, atau policy di `environment_signal` yang melakukan apa
  pun saat insert;
- **tidak ada tingkat "tinggi"**. Yang bisa dikatakan paling kuat hanyalah
  `sedang`, dan halaman menyebutnya "perlu ditinjau", bukan "curang".
- **Ambang dan bobotnya tinggal di server dan tidak pernah dikirim ke halaman.**
  Halaman menerima *tingkat* + kunci alasan, bukan angkanya, sehingga halaman tidak
  bisa membocorkan ambang yang tidak pernah ia terima.

Dijaga oleh `tests/unit/test_environment_signals.py`, yang membaca daftar modul
terlarang itu dari sumbernya sendiri — sehingga release berikutnya yang menyambung
sinyal ini ke tangga penalti akan gagal sebelum sampai ke sekolah.

### Cara membacanya saat meninjau

Setiap alasan selalu tampil **berdampingan dengan penjelasan wajar alternatifnya**,
dan kalimat pembukanya menyatakan bahwa ciri ini lazim di perangkat lab maupun
perangkat lama. Yang dicari guru bukan satu murid yang punya ciri itu, melainkan
**pola** — misalnya beberapa murid sekamar yang sama-sama melaporkan renderer
perangkat lunak pada jam yang sama.

### Batas yang masih ada (jujur)

- Sinyal ini **tidak** menyimpan riwayat perangkat sebelumnya, jadi "tidak ada
  sinyal" berarti "tidak terpantau", bukan "tidak memakai VM".
- Pengumpul ini berjalan sekali di awal attempt; VM yang dinyalakan di tengah ujian
  tidak terlihat.
- Teknik anti-deteksi (mis. spoofing renderer) tetap bisa mengelabui sinyal ini —
  ini konteks, bukan kontrol.
