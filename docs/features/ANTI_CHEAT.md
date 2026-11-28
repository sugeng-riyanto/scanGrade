# Anti-Cheat System

Layar penuh opsional di semua perangkat. Keluar dari layar penuh, rotasi, dan perubahan ukuran jendela tidak dihitung sebagai pelanggaran. Perpindahan fokus dan visibilitas tetap dipantau.

## Yang Dimonitor

| Event | Deteksi | Aksi |
|-------|---------|------|
| **Pindah tab, aplikasi, atau minimize** | `visibilitychange` + timer 1.5s | Layar soal dikaburkan + penalti bertahap |
| **Beralih ke jendela lain** (`focus_lost`) | `window` blur + timer 1.5s, hanya saat halaman **tidak** hidden | Layar soal dikaburkan + tangga penalti yang sama |
| **Rotasi layar** | `matchMedia("(orientation: portrait)")` + `orientationchange` | Dicatat (`orientation_shift`), **tidak** dihukum — lihat [Rotasi layar](#rotasi-layar-bukan-pelanggaran) |
| **Klik kanan** | `contextmenu` | Diblokir |
| **Copy/Paste** | `copy`, `cut`, `paste` event | Diblokir |

Pindah tab dan meninggalkan fokus jendela mengikuti satu tangga penalti (`PENALIZED_VIOLATION_TYPES`). Satu kejadian dihitung sekali:

- dokumen **hidden** ditangani jalur `visibilitychange` saja (jalur blur jendela berhenti saat `document.hidden`);
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

- hitungan mundur yang sedang berjalan **adalah** absence itu — dua detektor tidak bisa membuka dua hitungan untuk satu perbuatan;
- `awayCharged` mencegah hitungan yang sama ditagih dua kali (termasuk oleh tick berikutnya);
- `endAway()` mereset penanda begitu siswa kembali, sehingga absence berikutnya adalah kejadian baru;
- saat hitungan habis, halaman **membaca ulang** apakah siswa sudah kembali (`document.hasFocus()`), bukan mengandalkan event fokus yang mungkin tidak terkirim oleh browser.


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

Gate 4 membuka ujian demo sebagai murid dan memeriksa halaman ujian, konfigurasi anti-cheat, panel absen, hitung mundur dari server, serta pemantau visibilitas dan fokus. Gate juga memeriksa bahwa halaman tidak meminta atau mewajibkan fullscreen.

## Rotasi layar bukan pelanggaran

Perubahan orientasi perangkat dicatat sebagai `orientation_shift` di linimasa sesi. Perubahan ukuran viewport saja tidak menghasilkan peristiwa rotasi. Catatan historis `fullscreen_exit` tetap tersedia di laporan. Perhitungan penalti dari log hanya memakai `tab_switch` dan `focus_lost`.

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
