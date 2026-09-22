# Anti-Cheat System

## Yang Dimonitor

| Event | Deteksi | Aksi |
|-------|---------|------|
| **Pindah tab** | `visibilitychange` + timer 1.5s | Layar soal dikaburkan + penalti bertahap |
| **Keluar layar penuh** | `fullscreenchange` + sampling tiap 2 detik | Overlay memblokir + tangga penalti yang sama |
| **Beralih ke jendela lain** (`focus_lost`) | `window` blur + timer 1.5s, hanya saat halaman **tidak** hidden | Layar soal dikaburkan + tangga penalti yang sama |
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

## False Positive Handling

- Peringatan pertama hanya teguran (tanpa penalti)
- Guru bisa membatalkan penalti dengan override score manual
- Siswa bisa mengajukan retraction (penarikan pengumpulan)
