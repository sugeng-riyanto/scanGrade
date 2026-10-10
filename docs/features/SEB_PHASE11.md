# Fase 11 — menguji Config Key terhadap Safe Exam Browser sungguhan

> **Status: KEDUA PERTANYAAN SUDAH DIUKUR (9 Oktober 2026, Windows SEB 3.10.2.920 x64).**
> Dua putaran nyata dikerjakan memakai berkas uji umum. **Pertanyaan A terjawab ya**:
> header yang dikirim klien sama dengan `SHA256(startURL + Config Key)` yang dihitung
> server — dua implementasi berbeda menyetujui kunci yang sama, dan
> `deploy/seb_phase11.py` keluar **0**. **Pertanyaan B juga terjawab ya**: `fetch()`
> dari halaman probe tiba membawa header yang cocok untuk URL XHR-nya sendiri, dan
> `deploy/seb_phase11_probe.py --report` keluar **0** — itulah dasar
> `seb_service.observe_save_key()` di jalur sinkronisasi dan kirim jawaban
> (keterbatasan #5 di `SEB.md`). Yang **belum** diukur, dan tidak boleh dihitung
> sebagai terbukti: berkas ujian dari baris database (checkout ini tidak membawa
> `.env`, jadi tidak ada ujian, kunci tersimpan, atau pintu yang bisa diuji), dan
> platform non-Windows. Rincian, nilai mentah, dan batasnya ada di bagian **Hasil** di
> bawah.

Fase 11 menjawab **dua** pertanyaan, dan keduanya harus dijawab terpisah:

| # | Pertanyaan | Kenapa ini satu-satunya cara menjawabnya |
|---|---|---|
| **A** | Apakah Config Key yang **dihitung server** sama dengan yang **dihitung klien SEB** untuk berkas yang sama? | Serialiser yang *hampir* benar menghasilkan kunci yang *tidak pernah* cocok. Test satuan membuktikan aturan 1-7 dijalankan; hanya klien nyata yang membuktikan byte-nya sama. |
| **B** | Apakah SEB mengirim header `X-SafeExamBrowser-ConfigKeyHash` pada **XHR** (sinkronisasi & kirim jawaban), bukan hanya pada navigasi halaman? | Ini yang menahan ditutupnya keterbatasan #5 di `SEB.md`. Dokumentasi resmi mengatakan *"every HTTP request"*, tetapi memaksa header di jalur yang menyimpan jawaban tanpa mengukurnya berisiko memutus ujian di tengah kertas. |

Alatnya ada dua, dan keduanya dipakai:

- [`deploy/seb_phase11.py`](../../deploy/seb_phase11.py) — membandingkan nilai yang
  **dibaca manusia** dari log atau jendela pengaturan SEB. Kode keluarnya **0** (semua
  cocok), **1** (ada yang tidak cocok — inilah temuannya), **2** (tidak bisa diukur;
  **bukan** berarti lulus).
- [`deploy/seb_phase11_probe.py`](../../deploy/seb_phase11_probe.py) — **menanyakan
  langsung ke klien**. Ia menyajikan config uji, membuat berkas `.seb` dengan fungsi
  yang sama dengan aplikasi, lalu mencatat **setiap** header yang datang dan menilainya
  per-URL. Ini yang dipakai untuk Langkah 0, dan ini juga yang mengukur pertanyaan
  **B** tanpa menambah baris log di aplikasi: yang ditanyakan adalah sifat *klien*, dan
  klien bisa ditanya langsung. Artefaknya ditulis ke direktori sementara, tidak pernah
  ke dalam checkout.

---

## Hasil — apa yang benar-benar terukur (9 Oktober 2026)

**Klien.** `SafeExamBrowser.exe` (bukan Config Tool), Windows SEB **3.10.2.920 (x64)**
dari installer resmi. User-Agent yang tercatat:

```
Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 (KHTML, like Gecko)
    Chrome/147.0.7727.118 SEB/3.10.2 (x64)
```

Build itu penting, bukan kebetulan: **3.10.2** adalah versi yang sama dengan yang
dilaporkan di SEB issue #1495 — alasan modul ini menolak `float` alih-alih menebak.

**Berkas yang dibuka.** `ScanGrade-SEB-Uji.seb`, 716 byte, sha256
`21dc4bdbee6c5ea7f8823467094110aee024605cf65cd714362ec566c7a44d07`, dengan
`startURL = http://127.0.0.1:8765/panduan/seb/berhasil` (host lokal: SEB dan server ada
di mesin yang sama), dua hash kata sandi uji, dan `sendBrowserExamKey = true`.

| Yang dibandingkan | Nilai | Hasil |
|---|---|---|
| Config Key dihitung server | `b7782dd33a34b108f4c0bf0f4c8d7dbf252116037e467e86a061e9afb63f1d4e` | — |
| Header yang dikirim klien | `9b65f55eadc68da0be80ea82f305192e14c3d5c62acc8df5fd2a7523d85d39e4` | |
| `SHA256(startURL + kunci kita)` | `9b65f55eadc68da0be80ea82f305192e14c3d5c62acc8df5fd2a7523d85d39e4` | **MATCH** |

Nilai itu menjawab pertanyaan **A**, dan menjawabnya **tanpa** memerlukan log klien
(SEB untuk Windows tidak menulis log berkas pada level bawaan — tidak ada berkas log
yang ditemukan di `%LOCALAPPDATA%`, `%APPDATA%`, `%PROGRAMDATA%`, dan `%TEMP%` setelah
putaran ini). Alasannya cukup: kunci adalah hash atas SEB-JSON berkas, jadi header
`SHA256(URL + kunci)` hanya bisa sama bila kunci klien **sama** dengan kunci kita —
selain itu harus ada tabrakan SHA-256. Alat resminya mengonfirmasi hal yang sama dan
keluar **0**:

```
[MATCH] header the client sent
      sent:     9b65f55eadc68da0be80ea82f305192e14c3d5c62acc8df5fd2a7523d85d39e4
      expected: 9b65f55eadc68da0be80ea82f305192e14c3d5c62acc8df5fd2a7523d85d39e4
MATCH: every comparison asked for agreed with the client.
```

Perbandingan 1-3 (`--seb-key`, `--seb-json`, `--exam`) **tidak** dijalankan: SEB tidak
menyerahkan SEB-JSON-nya pada putaran ini, dan checkout ini tidak punya `.env` sehingga
tidak ada baris ujian untuk dibandingkan. Yang sama pentingnya: berkas **berhasil
dibuka oleh klien**. Serialiser yang salah byte menghasilkan berkas yang ditolak SEB,
bukan header yang cocok.

Dua temuan tambahan dari catatan mentah:

1. **Permintaan selain navigasi juga membawa header.** Satu `GET /favicon.ico` dari
   WebView SEB membawa header yang benar untuk URL-nya sendiri. Ini **indikasi**, bukan
   bukti, untuk pertanyaan B: favicon adalah muatan subresource, bukan XHR.
2. **Jalur header bekerja di Windows.** `MATCH` di atas berarti `require_seb` menahan
   pintu dengan benar di platform ini tanpa JavaScript API sama sekali.

> **Catatan instrumen.** Probe versi pertama menilai *semua* permintaan terhadap hash
> `startURL`, sehingga `GET /favicon.ico` yang **benar** tercetak `MISMATCH` — temuan
> palsu, satu-satunya hal yang tidak boleh dihasilkan sebuah alat. Penilaiannya
> sekarang per-URL dan dihitung ulang dari catatan mentah setiap kali laporan dibaca;
> capture aslinya tidak pernah ditulis ulang. Perbaikannya diverifikasi secara
> perilaku, bukan dengan mata: header favicon yang benar dinilai `match`, header
> `startURL` pada favicon dinilai `mismatch`, dan tanpa header dinilai `no_header`.

### Pertanyaan B — header pada XHR: **YA** (putaran 2, 9 Oktober 2026)

Putaran ini menjawab satu-satunya pertanyaan yang menahan ditutupnya keterbatasan #5:
apakah header ikut pada **XHR**, bukan hanya pada navigasi halaman. Halaman probe
menembakkan satu `fetch()` ke dirinya sendiri, dan yang datang dicatat apa adanya:

| Yang dibaca | Nilai |
|---|---|
| Metode & jalur | `POST /probe-xhr` (XHR dari halaman, bukan navigasi) |
| `has_header` | **true** |
| Header yang dikirim klien | `X-SafeExamBrowser-ConfigKeyHash: 304bdce9fbeef6ebfdc40c1dd4d602fd0269ec4c83d1c08b7042d54a56158014` |
| `SHA256(http://127.0.0.1:8765/probe-xhr + Config Key)` | `304bdce9fbeef6ebfdc40c1dd4d602fd0269ec4c83d1c08b7042d54a56158014` → **MATCH** |
| `X-SafeExamBrowser-RequestHash` | `b412703fc035b9a355ca6ac1e951ad081e646360610cb6161102114dcd871ec6` |
| User-Agent | `… Chrome/147.0.7727.118 SEB/3.10.2 (x64)` |
| Laporan halaman sendiri | `{"jsapi":"absent","configKey":"","userAgent":"… SEB/3.10.2 (x64)"}` |

`--report` menilai tiap permintaan terhadap **URL-nya sendiri** dan keluar **0**:

```
1 request(s); header verdict(s): ['match']
  -> YES: the Config Key header rides on an XHR, so a check on the sync
     or submit path would have a header to read.
```

Tiga hal yang dibaca dari angka mentah ini, dan yang ketiga yang paling berguna:

1. **XHR membawa header.** Dokumentasi resmi SEB mengklaim "setiap permintaan HTTP";
   sekarang klaim itu terukur pada build ini, bukan diasumsikan.
2. **Dua hashnya berbeda karena URL-nya berbeda.** `startURL`
   membawa `9b65f5…`, XHR membawa `304bdc…`. Itu bukti langsung bahwa klien
   meng-hash **alamat yang sedang ia ambil** — jadi jalur simpan harus menghash
   `request.url` (URL XHR itu sendiri), bukan `startURL` ujian. Itulah yang dilakukan
   `seb_service.key_state()`, dan salah di sini akan mencetak `mismatch` untuk setiap
   klien jujur.
3. **`jsapi: absent` di Windows.** Jalur header dan jalur JavaScript API tidak
   bergantung satu sama lain: di Windows yang bekerja adalah header, dan di
   macOS/iOS keduanya berbeda sama sekali (keterbatasan #2).

**Dasar kode yang mengikutinya:** `seb_service.observe_save_key()`, dipanggil dari
`/api/student/sync-draft` dan route submit **sebelum keputusan apa pun** — mencatat,
**tidak pernah menolak**, karena WKWebView (macOS/iOS) tidak bisa mengirim header ini
sama sekali. Dijaga oleh `tests/unit/test_seb_save_path.py`.

### Yang belum terukur, dan apa artinya

| Belum diukur | Kenapa | Yang tetap tidak terbukti |
|---|---|---|
| `--exam` (kunci tersimpan di baris ujian) | checkout ini tidak punya `.env`, jadi tidak ada baris ujian dan tidak ada kunci tersimpan | bahwa berkas yang dipegang murid membawa kunci yang **sama dengan yang dipakai pintu** |
| Langkah 4 (murid duduk di kursinya) | butuh ujian `require_seb` + akun murid, yaitu dua hal di atas | bahwa pintu **memilih**, bukan menolak semua orang |
| ~~Pertanyaan B (XHR)~~ | **sudah diukur pada putaran 2 — jawabannya YA** (lihat bagian di atas); yang belum: XHR ke **route aplikasi sendiri** dari dalam SEB, dan build non-Windows | bahwa jalur simpan punya header di **setiap** platform; jalur simpan karena itu mencatat, tidak menolak |
| macOS / iOS | hanya ada Windows di mesin ini | klaim lintas platform; WKWebView **tidak bisa** mengirim header sama sekali, jadi jalurnya berbeda (Langkah 6) |
| SEB-JSON milik klien (`--seb-json`) | SEB tidak menulis log berkas pada level bawaan | kesamaan **byte demi byte**; yang terbukti adalah kesamaan **kunci**, bukan serialisasinya |

---

## Prasyarat

1. **SEB sungguhan**, satu build per platform yang akan diklaim. Unduh **hanya** dari
   `safeexambrowser.org` (`/panduan/seb` sudah menautkan resminya — jangan pakai mirror).
   - Config Key didukung sejak **SEB 2.1.4 (macOS)**, **SEB 3.0 (Windows)**, **SEB 2.1.14 (iOS)**
     (dokumentasi developer SEB). Pakai build yang lebih baru bila ada.
   - **Tidak ada build Android/ChromeOS** — jangan menjanjikan perlindungan di sana.
2. **Satu ujian sekali pakai** (`require_seb` dinyalakan lalu dimatikan), di kelas
   buangan, dengan **satu akun murid uji**. Jangan pernah melakukan ini pada ujian
   yang sedang berjalan: menyalakan `require_seb` di tengah jendela ujian mengunci
   murid yang sudah mulai.
3. Akses ke checkout yang punya `.env` (untuk `--exam`), dan Python dari venv proyek.
4. **Waktu**: satu putaran penuh (pasang, unduh, catat, buka) ± 30 menit per platform.

---

## Langkah 0 — buktikan dulu bahwa kliennya berfungsi sama sekali

Sebelum menyentuh ujian apa pun, buka **berkas uji umum**: `/panduan/seb/uji.seb`.
Berkas itu membawa config nyata yang menunjuk ke `/panduan/seb/berhasil`, dan halaman
itu **menghitung ulang** Config Key-nya sendiri lalu membandingkan header yang datang.

- Halaman terbuka dan mengatakan **"SEB berhasil terpasang"** → mekanisme header,
  penamaan header, dan serialiser kita **sudah cocok dengan klien ini** untuk config uji.
- Halaman terbuka di browser biasa → itu jawaban yang benar (bukan SEB), bukan kegagalan.
- Halaman terbuka **di dalam SEB** tetapi berbunyi "belum dibuka dari SEB" → serius:
  SEB tidak mengirim header, atau kuncinya berbeda. Hentikan di sini dan catat; langkah
  berikutnya akan sia-sia.

**Yang langkah ini buktikan:** klien mengirim header dan serialiser kita setuju untuk
*config uji*. **Yang belum:** config ujian berbeda (ada `startURL`, dua hash kata sandi),
jadi ini bukan bukti untuk pertanyaan **A** pada berkas ujian — hanya penyaring cepat
untuk membedakan "SEB-nya salah pasang" dari "config ujian kita salah".

---

## Langkah 1 — siapkan ujian `require_seb`

1. Buat ujian sekali pakai (1 soal pilihan ganda cukup), terbitkan, dan tugaskan ke
   kelas buangan.
2. Sebagai **guru pemilik**, buka panel SEB: `/teacher/exams/<exam_id>/seb`, lalu
   **aktifkan** (`/seb/enable`). Panel menerbitkan kata sandi keluar/admin **dan**
   Config Key dalam satu operasi, lalu menyimpan keduanya di baris ujian.
3. Unduh berkasnya:
   - guru/admin/pengawas: `/teacher/exams/<exam_id>/seb/file`
   - murid: `/student/exams/<exam_id>/seb-file`
4. **Catat id ujiannya** — alat di Langkah 3 membutuhkannya.

> Rute unduh menolak berkas yang tidak cocok dengan kunci tersimpan, jadi berkas yang
> berhasil terunduh sudah konsisten dengan baris ujian pada saat itu.

---

## Langkah 2 — ambil nilai yang dilaporkan **SEB sendiri**

Ini bagian yang tidak boleh dilewatkan: nilainya harus datang dari klien, bukan dari
kita.

**Cara paling andal — log SEB (semua platform).** Setel level log ke **Verbose**
(SEB → Security/Exam preferences), buka berkas `.seb` ujian, lalu cari baris:

```
JSON for Config Key: {...}
```

Simpan baris itu apa adanya ke sebuah berkas, mis. `seb-json.txt` (potong hanya
awalannya saja: heading-nya boleh ikut, alat ini menerima keduanya).

**Cara tambahan — kunci yang ditampilkan SEB.** SEB menampilkan Config Key dari config
yang dimuat di **SEB Config Tool** (dan di panel Security SEB untuk macOS). Bila build
Anda tidak menampilkannya, log di atas sudah cukup dan tidak butuh UI.

---

## Langkah 3 — bandingkan dengan alat

```bash
python deploy/seb_phase11.py exam.seb --exam <exam_id> \
    --seb-json @seb-json.txt \
    --seb-key <Config Key yang dilaporkan SEB> \
    --seb-header <nilai header yang dikirim klien> \
    --show-json
```

Empat perbandingan, dan **apa artinya bila masing-masing gagal**:

| Perbandingan | `MATCH` berarti | `MISMATCH` berarti |
|---|---|---|
| `--seb-json` | serialiser kita menghasilkan **byte yang sama** dengan klien → pertanyaan **A** terjawab ya | aturan 1-7 salah di suatu tempat; alat ini mencetak **offset byte pertama yang berbeda** dan potongan teks di sekitarnya. Offset itu yang dilaporkan, bukan "kuncinya beda". |
| `--seb-key` | kunci kita = kunci klien | sama seperti di atas, hanya tanpa lokasi. Pakai `--seb-json` untuk menemukannya. |
| `--exam` | berkas yang dipegang murid membawa kunci yang **sama dengan yang dipakai pintu** | berkas lebih tua dari kunci tersimpan (guru menekan *reissue* setelah berkas diunduh) — unduh ulang berkasnya, jangan ubah kodenya |
| `--seb-header` | SHA256(startURL + Config Key) kita = header yang dikirim klien | Kunci bisa cocok tetapi URL-nya tidak: host berbeda, `www.`, port, atau tautan yang membawa query string saat berkas dibuat |

Bila **semua** `MATCH`, pertanyaan A terjawab untuk config itu, di platform itu.

---

## Langkah 4 — dudukkan murid sungguhan di kursinya

1. Di mesin uji, **buka berkas `.seb`-nya** (bukan tautannya). SEB harus terbuka,
   menuju `startURL`, dan **halaman ujian harus muncul** — bukan dialihkan kembali ke
   daftar ujian.
2. Di mesin yang sama, buka tautan `/student/exams/<exam_id>` di **browser biasa**.
   Yang benar: pesan "Ujian ini hanya bisa dibuka lewat Safe Exam Browser…" dan
   pengalihan ke `/student/exams`.
3. Catat keduanya. Dua percobaan ini adalah bukti bahwa pintunya **memilih**, bukan
   bahwa pintunya menolak semua orang. Bila murid di dalam SEB pun ditolak, server log
   memuat: `SEB refused: exam <id> opened without a matching Config Key` — dan pada titik
   itu Langkah 3 sudah memberi tahu bagian mana yang berbeda.

---

## Langkah 5 — pertanyaan B: apakah header ikut pada XHR?

> **Sudah dijalankan (putaran 2, 9 Oktober 2026).** Jawabannya **YA** — lihat
> **Hasil → pertanyaan B**. Yang dibaca halaman probe adalah XHR ke probe itu
> sendiri, jadi yang **belum** terukur adalah XHR ke **route aplikasi** (sinkronisasi
> dan submit) dari dalam SEB. Karena itu jalur simpan memasang **pencatatan**, bukan
> penolakan: satu baris yang sama yang diukurnya kelak menjadi jawabannya sendiri di
> instance yang memakainya. Langkah di bawah tetap ditulis utuh sebagai prosedur
> manual yang berlaku di semua platform.

Ini eksperimen terpisah, dijalankan **hanya di instance lokal**, jangan di produksi.

Tambah **satu baris pencatatan** (mencatat, tidak pernah menolak) di `/api/student/sync-draft`
dan di route submit — tepat sebelum keputusan apa pun dibuat:

```python
current_app.logger.info(
    "SEB header on XHR: %s",
    bool(request.headers.get("X-SafeExamBrowser-ConfigKeyHash")))
```

Lalu jalankan satu pengerjaan **dari dalam SEB** di instance lokal itu, dengan WiFi
sengaja diputus-nyalakan sekali supaya sinkronisasi benar-benar terjadi. Yang dibaca:
`True` atau `False` di log. Tulis apa adanya.

- `True` → prasyarat keterbatasan #5 sudah ada, dan menutup jalur sinkronisasi/submit
  menjadi pekerjaan biasa (bukan tebakan).
- `False` → **jangan** pasang pemeriksaan di jalur itu. Dokumentasi resmi SEB mengklaim
  header dikirim pada *setiap* permintaan HTTP; temuan `False` berarti klaim itu tidak
  berlaku untuk build/versi kita, dan itu adalah temuan yang harus masuk laporan —
  bukan sesuatu yang diakali dengan menolak sinkronisasi.

Pada Windows SEB 3.x, log aplikasi real-time dan developer tools build itu juga tersedia
(bila diaktifkan), sehingga header permintaan XHR bisa dilihat langsung — pakai bila
tersedia, tetapi baris pencatatan di atas adalah cara yang berlaku di semua platform.

---

## Langkah 6 — iPad/macOS: jalur yang **berbeda**, bukan jalur yang sama

WKWebView (mesin browser SEB di macOS/iOS 3.0+) **tidak bisa** mengirim header CK/BEK —
ini pernyataan resmi SEB, bukan dugaan. Jadi di iPad, Langkah 4 **akan gagal** dan itu
bukan bug kita. Jalur resminya adalah **SEB JavaScript API**:

- `SafeExamBrowser.security.configKey` (dan `browserExamKey`) berisi kunci yang sudah
  di-hash dengan URL halaman.
- Pada SEB 3.0 macOS/iOS, `SafeExamBrowser.security.updateKeys(callback)` harus dipanggil
  lebih dulu; pada versi yang lebih baru variabelnya sudah terisi saat halaman dimuat.
- Config kita memakai `sendBrowserExamKey = true` (yang juga yang mengaktifkan header),
  jadi secara bawaan SEB memakai WebView klasik untuk halaman ujian — cukup untuk
  header, tetapi bukan masa depan.

**Kerjakan sebagai fase terpisah** (`browserWindowWebView = 3` + pembacaan `configKey`
di halaman ujian), dan catat di laporan bahwa sampai itu ada, **iPad belum terlindungi**.

---

## Lembar catatan (salin, isi, lampirkan ke laporan)

```
FASE 11 — SEB sungguhan
Tanggal / petugas        : 9 Oktober 2026 (putaran mesin; bukan petugas sekolah)
Platform & versi SEB     : Windows Safe Exam Browser 3.10.2.920 (x64), dari installer resmi
Ujian                    : — TIDAK ADA. Checkout tanpa .env: tidak ada ujian require_seb
Berkas .seb              : sha256 21dc4bdbee6c5ea7f8823467094110aee024605cf65cd714362ec566c7a44d07
                           (716 byte, config uji umum, startURL host lokal)

Langkah 0 (berkas uji)   : halaman /panduan/seb/berhasil -> "berhasil terpasang"?  YA
A. Kesesuaian Config Key
   --seb-json            : TIDAK DIUKUR   (SEB tidak menulis log berkas pada level bawaan)
   --seb-key             : TIDAK DIUKUR   (hanya lewat header, lihat baris berikut)
   --exam (kunci tersimpan): TIDAK DIUKUR  (tidak ada baris ujian)
   --seb-header          : MATCH   (URL: http://127.0.0.1:8765/panduan/seb/berhasil)
   kode keluar alat      : 0
   yang membuktikan A    : header klien = SHA256(startURL + kunci server)
B. Header pada XHR
   sinkronisasi tercatat : YA — PUTARAN 2. POST /probe-xhr dari halaman di dalam SEB
                           membawa X-SafeExamBrowser-ConfigKeyHash
                           304bdce9fbeef6ebfdc40c1dd4d602fd0269ec4c83d1c08b7042d54a56158014
                           = SHA256(http://127.0.0.1:8765/probe-xhr + Config Key) — MATCH
                           dinilai per-URL; probe --report keluar 0
   jsapi di platform ini  : absent (jalur header yang bekerja di Windows)
Langkah 4 (duduk)        : di dalam SEB halaman terbuka?         YA (halaman uji)
                           di browser biasa ditolak menuju daftar? BELUM (butuh ujian)
iPad/macOS               : header tidak berlaku (Langkah 6) — BELUM diuji, tidak ada perangkat
Yang BELUM terbukti      : (1) berkas ujian dari baris database membawa kunci yang sama
                           dengan yang dipakai pintu; (2) pintu menolak browser biasa pada
                           ujian sungguhan; (3) XHR ke route APLIKASI (sync/submit) dari dalam
                           SEB — probe hanya membuktikan XHR ke dirinya sendiri; (4) build non-Windows;
                           (5) kesamaan byte SEB-JSON klien (yang terbukti: kesamaan kunci)
```

---

## Batas kejujuran: yang **tidak** dibuktikan oleh Fase 11 yang hijau

1. **Satu platform bukan semua platform.** Aturan serialisasi sama di semua OS, tetapi
   SEB pernah mengirim bytes `<real>` yang berbeda antar-platform (SEB issue #1495) —
   itulah sebabnya modul kita **menolak float** alih-alih menebak. Fase 11 yang hijau di
   Windows tetap perlu diulang di macOS bila macOS akan diklaim.
2. **Satu config bukan semua config.** Yang diuji adalah satu berkas dengan satu
   `startURL` dan dua hash kata sandi. Kunci berubah bila isinya berubah; itu sifatnya,
   bukan cacat.
3. **Config Key bukan rahasia dari murid yang memegang berkasnya** (keterbatasan #3 di
   `SEB.md`): kunci dihitung dari isi berkas, jadi murid yang membacanya bisa menghitung
   nilai yang sama dan memalsukan header dari browser biasa. SEB adalah **penghalang kuat
   dan kunci di sisi klien**, bukan bukti kejujuran.
4. **URL terikat pada host saat berkas dibuat** (keterbatasan #6): sekolah dengan alias
   domain atau `www.` yang berbeda akan ditolak walau SEB-nya benar. Jika Fase 11
   menghasilkan `--seb-header` MISMATCH sementara `--seb-key` MATCH, periksa ini lebih dulu.
5. **Bukan anti-cheat.** Fase 11 hanya menjawab "kunci kita cocok dan pintunya menolak
   browser biasa". Perilaku lain (tangga penalti, sinyal lingkungan, absen) punya
   pengujiannya sendiri.

---

## Bersih-bersih setelah selesai

1. Matikan `require_seb` pada ujian uji (`/seb/disable`) — **wajib**, karena barisnya
   menyimpan kata sandi terenkripsi yang tidak perlu terus ada.
2. Hapus ujian sekali pakai itu beserta akun murid ujinya.
3. Simpan berkas `.seb` yang dipakai dan baris log-nya bersama lembar catatan: itulah
   bukti yang membuat laporan bisa diperiksa ulang, dan tanpanya hanya tersisa klaim.
