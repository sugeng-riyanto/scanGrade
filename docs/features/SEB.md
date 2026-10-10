# Safe Exam Browser (SEB)

Untuk ujian berisiko tinggi di **laptop Windows/Mac atau iPad**. SEB mengunci
perangkat selama ujian: tidak ada tab lain, tidak ada aplikasi lain, tidak ada jalan
keluar tanpa kata sandi pengawas.

---

## ⚠️ BACA INI DULU: keterbatasan platform

**SEB TIDAK TERSEDIA untuk Android dan ChromeOS/Chromebook.** Hanya Windows, macOS,
dan iOS/iPadOS. Ini keterbatasan resmi SEB, bukan pilihan ScanGrade.

Konsekuensinya keras dan harus diucapkan sebelum siapa pun menyalakan sakelarnya:

- Mayoritas murid ScanGrade mengerjakan ujian dari HP, dan kemungkinan besar
  Android. **Menyalakan "Wajibkan SEB" pada ujian yang dikerjakan dari HP akan
  memblokir total mayoritas murid.**
- Sakelarnya karena itu **default MATI**, dan menyalakannya butuh centang
  pernyataan terpisah yang **diperiksa server**, bukan sekadar tombol yang
  dinonaktifkan di HTML.
- Panel menampilkan **angka konkret** sebelum sakelar dinyalakan: berapa murid
  peserta ujian itu yang terakhir masuk dari Android, dari riwayat yang benar-benar
  dicatat aplikasi. Angka nol berarti "tidak terpantau", bukan "tidak ada yang
  memakai HP" — panel mengatakan itu apa adanya.

---

## KOREKSI PENTING: Config Key, BUKAN Browser Exam Key

Dokumen ini ditulis sebagian supaya asumsi yang salah tidak diulang.

Versi pertama desain ini berasumsi server bisa membuat **Browser Exam Key (BEK)**.
**Itu tidak mungkin secara teknis.** BEK dihitung dari pengaturan config **dan tanda
tangan biner aplikasi SEB itu sendiri**, yang berbeda per versi/platform. Hanya
klien SEB yang bisa menghasilkannya; satu-satunya jalan bagi server adalah
mengumpulkan kunci terdaftar untuk setiap platform.

**Config Key (CK)** hanya dihitung dari pengaturan config, dan dokumentasi resmi SEB
menyatakan dua hal yang membuatnya tepat untuk kasus ini:

> "The Config Key is also same in each platform version of SEB, so SEB for Windows
> and SEB for iOS will generate the same key as SEB for macOS."
>
> "The most important advantage of the Config Key is, that it can be calculated in
> an exam system, if that system automatically generates SEB settings for an exam
> (server-side)."

Sumber: <https://safeexambrowser.org/developer/seb-config-key.html>

**Konsekuensi yang menguntungkan:** satu nilai kunci per ujian, berlaku untuk
**semua** platform dan versi SEB. Tidak ada logika "kunci per platform", tidak ada
tabel kunci terdaftar, dan **tidak ada kolom BEK di skema** — ketiadaan itu yang
menegakkannya. Kolom yang tidak ada tidak bisa dipakai.

Algoritma CK diimplementasikan di `app/services/seb_config_key.py`, mengikuti tujuh
aturan resmi apa adanya. Satu di antaranya adalah jebakan yang membuat seluruh
validasi gagal bila terbalik: hash permintaan adalah
**`SHA256(URL absolut + Config Key)` — URL dulu**.

### Float dilarang, dan itu keputusan yang diukur

SEB issue #1495 (terbuka, belum ditriase) menunjukkan Windows dan macOS
menghasilkan **byte berbeda untuk `<real>` yang sama**, sehingga satu nilai pecahan
membuat Config Key berbeda per platform — yang langsung membatalkan jaminan
"satu kunci untuk semua platform". Karena itu
`seb_config_key.config_key()` **menolak** pengaturan yang memuat float
(`ConfigKeyError`). Setiap pengaturan yang dibutuhkan ujian pengawasan adalah bool,
int, string, atau list, jadi divergensinya bisa dihindari, bukan sekadar dikurangi.

### Pengaturan enum sengaja TIDAK diisi

Konfigurasi yang digenerate hanya memuat boolean dan string yang artinya tidak
ambigu, plus satu integer yang satuannya jelas (`taskBarHeight`, piksel).
`sebConfigPurpose`, `browserViewMode`, kode `action` pada URL filter, dan
`newBrowserWindowByLinkPolicy` **dibiarkan kosong** karena penomoranannya tidak
dinyatakan di dokumentasi yang dipakai modul ini, dan salah enum bukan kesalahan
kosmetik: kode URL-filter yang terbalik memblokir **ujian itu sendiri**, dan
`sebConfigPurpose` yang salah mengarsipkan config sebagai pengaturan klien alih-alih
memulai ujian.

Membiarkannya kosong **aman secara algoritmik**, dan itu sifat resmi algoritmanya:
klien hanya meng-hash key/value yang **benar-benar ada di berkas yang dibukanya**
("The SEB client only uses setting key/values to calculate the checksum, which are
actually contained in an opened config file"). Jadi **menghilangkan** sebuah key
selalu konsisten; **menebak nilainya salah** yang merusak semuanya. Enum bisa
ditambahkan nanti oleh release yang sudah membuktikannya terhadap klien sungguhan.

---

## Kunci masuk vs kunci keluar

Bahasa ini dipakai di seluruh UI dan dokumen, supaya tidak ada yang perlu tahu
istilah teknis:

### 🔑 Kunci masuk — **Config Key (CK)**

- Dihitung **otomatis oleh server** dari pengaturan ujian. Tidak ada yang mengetik
  apa pun, dan hanya sistem yang tahu.
- Fungsinya: memastikan yang membuka soal adalah **SEB asli dengan konfigurasi yang
  benar**, bukan browser biasa.
- **Satu nilai untuk semua platform SEB.**
- Boleh ada di berkas `.seb` yang diunduh murid — ini bukan rahasia dari murid.

### 🔒 Kunci keluar — **Quit Password** dan **Admin Password**

- Digenerate **otomatis** oleh server; guru tidak pernah mengetiknya.
- **TIDAK PERNAH terlihat atau diberitahukan ke murid dalam bentuk apa pun.** Tidak
  ada endpoint, halaman, atau berkas yang bisa memberi murid kata sandi ini.
- Yang masuk ke berkas `.seb` **hanya sidiknya (hash)**, bukan kata sandinya. Murid
  boleh membuka berkas itu, dan tetap tidak bisa keluar dari ujian.
- Salinan yang bisa dibaca **disimpan terenkripsi** (`exam_seb_credential.*_enc`)
  supaya guru/admin/pengawas yang berwenang bisa membacakannya saat darurat.

Bentuk hash-nya penting dan tidak ditebak: `hashedQuitPassword` adalah **Base16
SHA-256 huruf kecil, tanpa awalan `SHA256:`** — persis seperti contoh resmi di
halaman Config Key (`"hashedQuitPassword":"8577da2e…"`). Beberapa tulisan pihak
ketiga menampilkan awalan itu; dengan awalan tersebut, SEB akan menolak kata sandi
yang benar, tanpa pesan kesalahan yang menjelaskan kenapa.

---

## Bentuk berkas `.seb`

Dari halaman *Developer Documentation — File Format* resmi, dan diimplementasikan di
`app/services/seb_service.py`:

```
berkas .seb = gzip( "plnd"  +  gzip( XML plist pengaturan ) )
```

Empat byte pertama menyatakan jenis isi (`plnd` = tidak terenkripsi). SEB membaca
prefix itu untuk tahu bagaimana membaca isinya, jadi berkas tanpa prefix bukan
berkas config sama sekali.

**Berkas ini tidak dienkripsi dengan kata sandi, dan itu keputusan yang disengaja.**
SEB sendiri menganjurkan enkripsi supaya murid tidak bisa mengubah pengaturan, tetapi
memasukkan kata sandi ke berkas berarti murid **harus diberi** kata sandi itu untuk
membuka ujiannya — dan murid yang memegang kata sandi itu bukan murid yang dijauhkan
dari config. Penggantinya lebih kuat: **setiap perubahan apa pun pada berkas mengubah
Config Key**, sehingga berkas yang diutak-atik ditolak di pintu. Itu penjagaan
kriptografis, bukan kata sandi yang dibagikan.

---

## Dari pengaturan ujian ke berkas `.seb`

Berkas itu **digenerate dari pengaturan ujian yang bersangkutan**, bukan dari satu
templat tetap. Satu tabel di `app/services/seb_service.py` (`EXAM_OWNS`) adalah
satu-satunya tempat pemetaannya ditulis, dan inilah isinya:

| Sakelar di form ujian | Kunci di dalam berkas `.seb` |
|---|---|
| `block_right_click` | `enableRightMouse` |
| `block_screenshot` | `enablePrintScreen`, `allowScreenSharing`, `allowDisplayMirroring`, `allowVideoCapture`, `allowAirPlay` |

**Arahnya dibalik tepat satu kali.** Kolom di baris ujian berarti *blokir*, kunci SEB
berarti *izinkan*: `block_screenshot = true` ditulis sebagai `enablePrintScreen =
false`. Nama dua kunci di antaranya terbalik dari yang diharapkan pembaca sekilas,
jadi aturannya ditulis di satu tempat, bukan disimpulkan dari nama.

Sakelar yang **tidak** dipetakan, dan alasannya:

- **`block_copy_paste`, `watermark_name`, `allow_calculator`, `fullscreen_required`**
  ditegakkan oleh halaman ujian sendiri (`take_exam.html`). Tabel di atas sengaja
  hanya memuat *jalan keluar dari browser* — inti SEB adalah mengunci perangkat,
  bukan mengulang kebijakan halaman di dalam berkas yang bisa dibaca murid. Untuk
  `fullscreen_required` ada satu alasan tambahan: SEB mengekspresikannya lewat enum
  `browserViewMode`, dan enum sengaja tidak diisi (lihat bagian di atas).
- **`anti_cheat_enabled`** mengatur penegakan di sisi halaman, bukan kunci browser.
  Menyalakan SEB berarti meminta perangkat terkunci; mematikan anti-cheat halaman
  tidak mengubah itu.
- **Kolom yang tidak terbaca tetap berarti BLOKIR.** Baris yang tidak memuat salah
  satu kolom di atas (pilih kolom yang lupa, baris lama) menghasilkan kunci yang
  tetap melarang, karena PostgREST melaporkan kolom yang tidak diminta sebagai
  *tidak ada* dan bukan sebagai error. Arah ini disengaja: "tidak terbaca" tidak
  boleh menjadi "guru mengizinkan".

### Satu Config Key untuk semua platform

Generatornya **tidak punya parameter platform**, dan itu dijaga dua arah:

- tanda tangannya hanya `(exam, start_url, quit_hash, admin_hash)` — sebuah argumen
  `platform` adalah cara paling wajar nilai per-platform masuk;
- tidak ada Browser Exam Key maupun `examKeySalt` (kunci khusus klien Apple) yang
  ditulis. BEK dihitung dari tanda tangan biner SEB, jadi ia memang mustahil
  dihitung server; yang dipakai adalah Config Key, yang menurut dokumentasi resmi
  "same in each platform version of SEB".

### Tidak ada nilai pecahan

Setiap nilai yang dipetakan adalah `bool` **karena konstruksinya** (`not bool(...)`),
dan peta akhirnya tetap diperiksa `seb_config_key.check_types()`, sehingga bilangan
pecahan ditolak **di titik pembuatannya** dengan menyebut nama pengaturannya — bukan
nanti saat berkasnya menolak dibuka. Alasannya ada di bagian *Float dilarang* di atas
(Windows dan macOS menuliskan `<real>` dengan byte berbeda, jadi satu float berarti
dua Config Key dari satu berkas).

### Kalau pengaturan ujian berubah setelah berkas diterbitkan

Konsekuensi yang tidak bisa dihindari dari "berkas mengikuti pengaturan ujian":
baris ujian bisa diubah **setelah** berkas dibagikan, dan kunci yang tersimpan saat
itu menggambarkan konfigurasi yang tidak lagi dihasilkan baris tersebut. Tiga hal
berlaku, dan ketiganya penting untuk sekolah:

1. **Unduhan berikutnya DITOLAK** (`409 stale_config`) — bukan berkas rusak, tetapi
   berkas yang tidak akan membuka ujian, jadi menyerahkannya berarti murid pulang
   dengan berkas yang tidak bisa mulai.
2. **Berkas yang sudah beredar tetap bisa dibuka** sampai diterbitkan ulang, karena
   kunci yang tersimpan masih kunci milik berkas itu. Jawaban yang benar untuk
   sekolah adalah **"terbitkan ulang lalu bagikan ulang"**, bukan "semua murid
   terkunci" — dan panel mengatakan kalimat itu apa adanya, di sebelah tombol
   terbitkan ulang.
3. **Panel memberi tahu sebelum ada yang mencoba mengunduh.** Penolakan yang hanya
   muncul saat seseorang menekan tombol adalah penolakan yang tidak terlihat; karena
   itu panel punya kartu peringatan sendiri, dan `tests/unit/test_seb_generator.py`
   merendernya dalam dua bahasa.

### Dua kerusakan yang ditemukan saat bagian ini dibangun

1. **Panel tidak pernah bisa dibuka, di mana pun.** Halaman itu menautkan tombol
   "Kembali" ke `url_for('teacher.exams')`, padahal nama endpoint-nya
   `my_exams`. Jinja tidak memeriksa nama endpoint sampai halamannya dirender, jadi
   ini bukan tautan rusak: **seluruh halaman gagal dirender** (`BuildError` → 500)
   untuk setiap guru. Penjaganya sekarang umum — setiap endpoint yang disebut
   keempat halaman SEB diuji ada di `app.view_functions`
   (`tests/unit/test_seb_pages.py`).
2. **Halaman itu juga tidak bisa dicapai, bukan hanya rusak.** Tidak ada satu pun
   tautan di aplikasi yang menuju panel, dan `seb.panel` menolak (403) selama SEB
   masih MATI — padahal tombol untuk menyalakannya, kotak centang pernyataannya, dan
   angka peringatan Android semuanya ada **di halaman itu**. Artinya fitur ini tidak
   punya jalan masuk selain mengetik URL yang menjawab 403. Sekarang: panel boleh
   dibuka pemilik ujian dalam keadaan SEB mati (hanya pemilik, tanpa kartu kata
   sandi), dan form ujian menautkannya **di kartu Anti-Cheat**, tepat di sebelah dua
   sakelar yang dibaca berkasnya.

---

## Matriks akses (RBAC)

Satu aturan, empat peran. Dijaga oleh `tests/unit/test_seb_access.py`, dan setiap
akses yang **diberikan** tercatat di `seb_access_log` **dan** di `audit_logs`.

| Peran | Lihat kata sandi | Unduh berkas `.seb` | Batas |
|---|---|---|---|
| **Guru pemilik ujian** | ✅ kapan saja selama ujian aktif | ✅ | — |
| **Admin sekolah** (sekolah sama) | ✅ kapan saja | ✅ | Bukan guru pemilik → dicatat **"atas nama guru X"** |
| **Kepala sekolah / Wakil kepala sekolah** | ✅ (setara admin sekolah) | ✅ | Sama seperti di atas |
| **Guru yang bertugas sebagai pengawas** | ✅ **hanya selama jendela tugasnya** | ✅ | Di luar jendela → **DITOLAK** |
| **Murid peserta ujian** | ❌ **tidak pernah, di endpoint mana pun** | ✅ (hanya hash + CK) | Hanya berkasnya sendiri |
| **Peran lain (termasuk `super_admin`)** | ❌ | ❌ | Dicatat sebagai `role_not_covered` |

Catatan penting:

- **Kepala sekolah & wakil kepala sekolah mendapat akses setara admin sekolah**
  karena Fase 0 menemukan keduanya **sudah** punya dashboard akademik sekolah
  sendiri (`/principal/dashboard`, `/principal/analytics`, unduh CSV/PDF) — syarat
  yang ditetapkan sebelum akses diberikan. Predikatnya memakai `can_read_exam`
  yang sudah ada, bukan daftar baru.
- **`super_admin` sengaja ditolak.** `can_manage_exam` meloloskannya, tetapi
  administrator platform tidak punya kebutuhan operasional untuk membaca kata sandi
  ujian satu sekolah. Penolakan ini dicatat sebagai data, supaya kebutuhan itu — bila
  benar-benar ada — muncul sebagai fakta, bukan sebagai tebakan di sini.
- **Jendela tugas pengawas** berasal dari jam ujian itu sendiri
  (`exam_window.deadline`), ditambah 30 menit sebelum dan 60 menit sesudah. Di luar
  rentang itu aksesnya **tertutup sendiri**, tanpa cron job.
- Tombol **tampilkan/sembunyikan** ada supaya kata sandi tidak tampil saat halaman
  dibuka — mencegah bocor lewat *screen-share* atau rekaman tanpa ada yang sengaja
  membagikannya.

---

## Di mana pemeriksaannya benar-benar terjadi

Klien membuktikan dirinya dengan header `X-SafeExamBrowser-ConfigKeyHash` =
`SHA256(URL absolut + Config Key)`. Server menghitung ulang **kedua** bagiannya dan
membandingkan dengan `hmac.compare_digest` (konstan-waktu, bukan per-byte).

Pemeriksaan itu ada di **pintu ujian** — `/student/exams/<exam_id>`, di
`app/routes/student.py`, setelah `exam_sitting_allowed` dan sebelum halaman
dirender — dan **bukan** di JavaScript halaman. Alasannya satu kalimat: penolakan
yang diputuskan halaman adalah penolakan yang bisa diminta halaman untuk dilewati.

Dua fakta dari dokumentasi resmi yang mengikat config kita, dicatat di sini supaya
tidak perlu diturunkan ulang: header itu **tidak dikirim secara otomatis** — ia
menyala bersama setelan *"Use Browser & Config Keys (send in HTTP header)"*, yaitu
`sendBrowserExamKey = true`, dan `settings_for()` menulisnya. Dan Config Key baru
didukung sejak **SEB 2.1.4 (macOS) / 3.0 (Windows) / 2.1.14 (iOS)**; **tidak ada build
Android atau ChromeOS**, jadi tidak ada yang bisa dijanjikan di sana.

Tiga keputusan yang menyertai, masing-masing karena bentuk salahnya lebih buruk:

1. **URL yang di-hash adalah `seb_service.start_url(exam_id)`** — fungsi yang sama
yang menulis `startURL` ke dalam berkas, bukan `request.url`. `request.url`
membawa query string apa pun yang dipakai murid untuk tiba; men-hash-nya berarti
menolak klien jujur yang tautannya diteruskan dengan `?src=wa`. Satu definisi URL,
dijaga `tests/unit/test_seb_door.py` (hanya satu modul yang boleh membangunnya).
2. **Yang ditolak diarahkan, bukan diberi halaman kosong.** Pembaca yang paling
mungkin adalah murid yang sudah memegang berkasnya dan membuka tautan di browser
biasa; ia diberi pesan apa yang harus dibuka dan tautan `/panduan/seb`.
3. **Ujian tanpa `require_seb` sama sekali tidak tersentuh** — jalur regresi yang
   semua uji lain bersandar padanya, dan sekarang dibuktikan lewat **rute itu
   sendiri**, bukan lewat predikatnya: badan `take_exam` dijalankan terhadap
   pengganti PostgREST (`tests/unit/test_seb_door.py` bagian 4), lalu diperiksa apa
   yang diterima murid. Dua kemungkinan bentuk salahnya mahal dan keduanya ditolak
   sebagai uji: pintu yang menolak **semua** orang (semua pembacaan kode di atas
   tetap lolos), dan penolakan yang tetap **membuka sitting** — murid yang ditolak
   lalu memasang SEB dan kembali akan kehabisan jatah percobaan untuk ujian yang
   tidak pernah ia kerjakan. Karena itu gerbang ini duduk **sebelum**
   `open_sitting`, dan satu uji menuntut tidak ada baris `submissions` yang
   tertinggal setelah penolakan.


### Apakah pintu itu benar-benar menahan? Diukur setiap rilis, bukan diklaim

Semua di atas adalah **pembacaan**: pembangkit sepakat dengan dirinya sendiri,
berkasnya terunduh, panelnya tergambar, dan `tests/unit/test_seb_door.py`
menjalankan pintunya terhadap klien palsu. Tidak satu pun bisa menjawab pertanyaan
yang sebenarnya ditanyakan sekolah — *apakah setelan `require_seb` menghentikan
browser biasa pada aplikasi yang sedang melayani sekarang, di database produksi* —
dan kegagalan yang tidak bisa mereka lihat justru bentuk terburuknya: setelannya
tertulis ON, berkasnya terunduh, dan murid membuka kertasnya di Chrome dengan
menempel tautannya.

Karena itu ada `deploy/seb_door_gate.py`, dijalankan deploy **sebelum rilis
dinyatakan sehat**, setelah reload. Ia membuat satu kertas buangan di database
produksi (klon dari fixture demo yang deploy tulis ulang tepat sebelum smoke test),
lalu membuka kertas itu sebagai murid demo **sebelas kali plus satu browser
headless**, di **dua paruh satu pintu** — paruh header dan paruh JavaScript — dan
menghapus barisnya di `finally` — sisa baris dilaporkan sebagai `seb door: LEFTOVER`,
bukan dilewatkan:

| Langkah | Permintaan | Yang disingkirkannya |
|---|---|---|
| **C0** | kertas dengan `require_seb` **mati** | jendela, kelas, sekolah, jatah percobaan: tanpa kontrol ini, "penolakan" tidak membuktikan apa pun tentang SEB; judul kertasnya harus kembali, bukan hanya HTTP 200 |
| **C1** | kertas yang sama dengan **hidup**, tanpa header | penegakan: jawabannya harus 302 ke `seb-claim`, bukan kertasnya |
| **C1b** | halaman `seb-claim` itu sendiri | jalan yang dipakai klien yang tidak bisa mengirim header (SEB di iOS berjalan di WKWebView); pintu yang mengarahkan murid ke 500 sudah menolaknya dengan memutar |
| **C2** | header yang di-hash atas Config Key kertas itu sendiri | pintu menerima klien jujur: 200 **dan** kertasnya ada di badan respons |
| **C3** | header yang di-hash atas kunci **lain** | pemalsuannya: tanpa ini, C1 dan C2 sama-sama bisa dijelaskan oleh "header apa pun diterima" — kegagalan yang justru jadi alasan pemeriksaan ini ada |
| **C4a** | klaim JavaScript yang di-hash atas alamat **kertas** | pengikatannya terbalik: klien men-hash URL halaman tempat skripnya berjalan, yaitu halaman handshake — itulah alasan C1 mengirim browser ke *sana*. Menerima alamat kertas berarti pengikatan itu tidak diperiksa sama sekali |
| **C4b** | klaim JavaScript atas **kunci lain** | pemalsuan untuk jalur ini: tanpa ini, "ada nilai yang dikirim" dan "nilai yang benar dikirim" tidak bisa dibedakan |
| **C4c** | klaim JavaScript yang dulu jujur **sebelum kunci diterbitkan ulang** | berkas basi yang masih dipegang murid: kunci tersimpan diubah di antara menghitung nilai dan mengirimkannya, jadi menerimanya berarti pintu menghormati nilai yang *pernah* benar, bukan kunci yang tersimpan sekarang |
| **C4d** | kertas itu lagi, masih tanpa header | ketiga penolakan itu tidak meninggalkan pintu terbuka — klaimnya hidup di sesi, jadi "harus ditolak" berarti kertasnya tetap tertutup, bukan sekadar POST-nya menjawab 403 |
| **C5** | klaim JavaScript yang jujur | penerimaannya: 200 dengan `ok: true`. Handshake yang menolak nilai kertasnya sendiri mengunci setiap iPad dan setiap klien macOS modern, dan tidak ada apa pun lain di kotak itu yang akan mengatakannya |
| **C5b** | kertas itu **tanpa header sama sekali** | intinya: klien yang membuktikan diri lewat API milik klien itu membuka kertasnya. Hanya lewat jalan ini klien WKWebView bisa mengerjakan kertas bergerbang |
| **C6** | halaman handshake itu sendiri, di browser headless sungguhan yang **tidak punya API `SafeExamBrowser`** | **paruh halaman dari C4–C5.** C4 menyusun POST-nya sendiri, jadi halaman yang skripnya tidak pernah mengirim apa pun tidak terlihat oleh semua langkah di atas — padahal halaman itulah seluruh populasi yang dilayani jalur penolakan ini. Keadaan halaman harus berakhir `refused` dengan panel penolakan yang benar-benar tertata, skripnya harus mengirim penolakan **sekali per pemuatan halaman** (alasan `no_client`, membawa token CSRF yang dibaca rutenya), rutenya harus menerimanya, database hidup harus memegang **sebanyak baris berpenanda browser dari jalan ini sebanyak laporan yang dikirim halamannya** — tiap baris berpenanda itu milik murid ini, supaya hitungan baris tidak bisa dibaca sebagai bukti halaman ini — dan kertasnya harus mengembalikan browser yang sama ke handshake |

Ketiga penolakan JavaScript berjalan **sebelum** klaim jujur, karena klaimnya hidup
di sesi yang ditandatangani: kalau dikirim lebih dulu, ia akan membuka kertasnya untuk
semua permintaan sesudahnya dan C4d tidak membuktikan apa pun. Yang basi dibuat basi
oleh gate itu sendiri — nilainya dihitung atas kunci yang tersimpan, lalu kuncinya
diterbitkan ulang — sehingga kasus itu perubahan nilai tersimpan yang nyata, bukan
permintaan yang memang tidak mungkin cocok sejak awal.

C6 adalah satu-satunya langkah yang bukan permintaan yang disusun berkas ini sendiri.
Diarahkan ke halaman handshake tanpa API browser sama sekali — murid yang menempel
tautannya di Chrome, dan populasi klien yang justru jadi alasan penolakan `no_client`
ada — sebuah browser headless ditanya apa yang dilakukan **skrip halaman itu sendiri**:
panel mana yang ditatanya, keadaan apa yang jadi akhirnya, dan apa yang dikirimnya ke
jaringan. Itu pengamatan yang berbeda dari C4 tepat di tempat yang paling penting,
karena POST di C4 ditulis oleh gate-nya sendiri dan akan tetap lulus pada halaman yang
`ask()`-nya tidak pernah berjalan. Empat fakta dibaca sebagai satu: panel dan
keadaannya (pandangan murid itu sendiri), jumlah laporan (satu per kunjungan, tidak
lebih), alasannya dan header CSRF-nya (kontrak rutenya), jawaban rutenya, lalu baris
yang dipegang database hidup — **tepat satu, milik murid ini, membawa penanda
`User-Agent` yang hanya dipasang jalan ini**, sehingga baris yang ditinggalkan browser
orang lain tidak bisa dibaca sebagai halaman ini yang melaporkannya. Ia ditutup dengan
mengirim browser yang sama kembali ke kertasnya, yang harus mengembalikannya ke
handshake lagi: laporan yang membuka apa pun adalah temuan.

Kedua paruh satu kunjungan browser berada di jaringan pada saat yang sedikit berbeda —
halamannya melapor dari handler yang sama yang menetapkan keadaannya — jadi
pembacaannya di-*poll* dengan batas waktu, bukan ditidurkan, dan pemuatan **pertama**
adalah yang dibaca keadaan halamannya.

**Barisnya dipilah lewat penanda dan dibandingkan dengan jumlah laporan — dan versi
pertama pemeriksaan ini salah di database hidup, dua kali.** Ia meminta *satu* baris
untuk kertas itu, dan tidak satu pun paruhnya bertahan saat dijalankan sungguhan: tiga
klaim yang ditolak di atas meninggalkan barisnya sendiri (rute klaim memang mencatat
ketidakcocokan) untuk murid yang sama, dan browsernya memuat halaman handshake **dua
kali** — kertasnya menolaknya lalu mengembalikannya, dan pemuatan baru adalah
kunjungan baru, jadi satu kunjungan yang tertolak melapor dua kali. "Tepat satu"
karena itu tidak mungkin tercapai; yang dibaca sekarang adalah `reports_total` (kedua
pemuatan) dibandingkan dengan baris yang membawa penanda `User-Agent` jalan ini —
yang menangkap tulisan yang hilang maupun yang ganda. Jalan ini mengatakannya sendiri:
`5 total, 2 this browser's … for 2 report(s)`.

Kotak tanpa browser untuk dijalankan, browser yang ternyata melaporkan ada API SEB
(jadi pembacaannya tentang klien yang lain), atau database yang lebih tua daripada
rekaman penolakan (migrasi 064) adalah **exit 2, bukan lulus**: paruh header dan
protokol klaimnya terukur sedangkan skrip halamannya tidak, dan "ditegakkan" adalah
klaim tentang keduanya. Browser dicari persis seperti dua gate DOM mencarinya —
`SG_CHROME` lebih dulu, lalu nama-nama biasa — sehingga satu kunci conf mempersenjatai
ketiganya.

Paruh JavaScript ini juga dibuktikan di tingkat aplikasi, bukan hanya di kotak:
`tests/unit/test_seb_js_api.py` bagian 8 menjalankan **skrip halaman yang dikirim ke
klien** di bawah `node`, mengambil persis permintaan POST yang dihasilkannya,
menyuapkannya ke rute `seb-claim` yang sebenarnya, lalu menyerahkan sesi yang
dijawab rute itu kepada pintu ujian yang sebenarnya. Nilai yang dilaporkan klien
jujur dihitung **dari aturan dokumentasinya dengan tangan**
(`hashlib.sha256(URL halaman + Config Key)`), bukan dengan fungsi server sendiri,
sehingga rantai itu tidak bisa lulus hanya karena kedua sisinya salah dengan cara
yang sama.

| Hasil | Tindakan |
|---|---|
| pintu menahan semuanya (exit 0) | satu baris di jurnal |
| browser biasa membuka kertas bergerbang, klien jujur ditolak di salah satu paruh, nilai kunci lain diterima, klaim yang harus ditolak tidak ditolak — atau ditolak tetapi tetap membuka kertasnya — atau jalan `seb-claim` tidak ada (exit 3), `SEB_ENFORCE=true` | rilis di-rollback, temuan dikutip |
| hal yang sama (exit 3), belum di-arm | rilis tetap jalan, tetapi dikatakan |
| tidak ada kredensial, tidak ada fixture untuk diklon, aplikasi tak terjangkau, **rilis yang mendahului fitur ini**, tidak ada browser untuk dijalankan, atau database yang lebih tua daripada rekaman penolakannya (exit 2) | rilis tetap jalan, dan dikatakan lantang bahwa pintunya **tidak terukur** |

Exit 2 bukan putusan, dan bedanya disengaja: rilis yang mendahului fitur ini memang
membuka kertas bergerbang tetapi tidak punya rute `seb-claim` sama sekali — itu
*duluan fitur*, bukan *berhenti menegakkan* — jadi menolaknya akan me-rollback rilis
yang tidak pernah mengklaim fitur ini. Gate ini dipersenjatai `SEB_ENFORCE=true` di
`/etc/scangrade-smoke.conf`; installer baru meng-arm-nya **setelah** satu pengukuran
nyata di kotak itu lulus (lihat `docs/AUTO_DEPLOY.md`).

**Gate ini menemukan satu cacat nyata sebelum sempat dijalankan.** `EXAM_COLUMNS` di
`app/routes/seb.py` memilih `manual_unlock` — nama *fungsi* (`resume_code.manual_unlock`),
bukan kolom, dan tidak ada migrasi yang pernah membuatnya. Di database produksi itu
membuat **setiap rute SEB** menjawab PostgREST 42703, dan tidak ada uji yang bisa
melihatnya karena ujinya memakai klien palsu. `deploy/schema_contract.py` pun tidak
pernah membacanya: select-nya ditulis sebagai konstanta modul, sementara pembacanya
hanya mengerti literal. Kontraknya kini membaca select yang ditulis sebagai nama
(dan rangkaian nama), kolomnya dicabut, dan `tests/unit/test_seb_door_gate.py`
menahan kedua-duanya: instansinya, dan pembacanya.

Berkas `.seb` yang gagal cocok dengan kunci tersimpan **ditolak, bukan disajikan**:
`/teacher/exams/<id>/seb/file` menghitung kunci dari pengaturan yang akan dikirim dan
membandingkannya dengan yang tersimpan — berkas yang tidak bisa membuka ujian tidak
boleh sampai ke tangan murid, dan itu jawaban yang jujur daripada file yang salah.

---

## Penolakan di pintu dicatat — dan tidak pernah menjadi penalti

Keluhan yang membuat bagian ini ada: guru menyalakan SEB, membagikan berkas, lalu
separuh ruangan tidak bisa masuk — dan **tidak ada satu pun layar yang bisa
menjawab "berapa murid yang ditolak, dan kapan"**. Yang ada hanya satu baris di log
server (`"SEB refused: exam %s without a matching Config Key or claim"`), dan baris
itu hanya menyebut ujiannya.

**Di mana penolakan benar-benar diputuskan.** Bukan di pintu ujian, dan itu pilihan
yang diukur: SEB macOS/iOS berjalan di WKWebView yang tidak bisa mengirim header
Config Key, jadi di iPad **setiap kunjungan yang jujur** diarahkan ke halaman
handshake lalu diterima. Menulis baris di pintu akan mencatat murid itu sebagai
ditolak, di setiap ujian gated, selamanya — angka yang salah tepat ke arah yang
menyalahkan platform yang fitur ini dukung. Pintu ujian adalah **pengarah**.

Handshake-lah yang memutuskan, dan ia punya tiga akhir:

| akhir handshake | penyebab | dicatat oleh |
|---|---|---|
| nilai cocok | kunci benar | — (kertas terbuka) |
| nilai tidak cocok | berkas `.seb` lama / kunci salah | `POST …/seb-claim` → `config_key_mismatch` |
| tidak ada yang bisa dijawab | browser biasa (`no_client`), atau SEB terbuka tetapi kunci belum siap (`no_key`) | `POST …/seb-claim/refused`, dikirim halaman itu sendiri |

Satu kunjungan yang terkunci = satu baris. **Halaman itu satu-satunya saksi** untuk
`no_client` — klien tanpa API SEB tidak pernah mengirim klaim apa pun, jadi tanpa
laporan dari halaman, cara paling umum sebuah kelas terkunci justru satu-satunya
penolakan tanpa catatan. Halaman melaporkan, server mencatat; jalur kunci yang tidak
cocok **tidak** dilaporkan dua kali (route klaim sudah mencatatnya), karena dua baris
untuk satu kunjungan melebihkan jumlah murid yang ditolak.

**Dua angka, dua pertanyaan.** Lima percobaan oleh satu murid adalah tautan yang
salah tempel; satu percobaan oleh lima murid adalah berkas yang tidak sampai ke siapa
pun. Karena itu tabel menyimpan `student_id`: panel menampilkan *jumlah murid berbeda*
dan *jumlah percobaan*, keduanya dari baris yang sama (`seb_door_log.refusal_summary`),
sehingga angka di kartu tidak bisa berbeda dari tabel di bawahnya.

**Bukan penalti — dan itu ditegakkan kode, bukan janji.** Murid yang ditolak belum
mengerjakan apa pun; paling sering ia hanya membuka tautan dari Chrome, atau berkas
`.seb`-nya sudah lama karena guru mengubah pengaturan setelah membagikannya.
`supabase/migrations/064_seb_door_refusals.sql` menulis ke tabelnya sendiri, dan:

* modulnya **tidak pernah menyentuh `violation_logs`** (dijaga uji sumber);
* tidak satu pun nama alasan menjadi anggota
  `anti_cheat_service.PENALIZED_VIOLATION_TYPES` (dijaga uji);
* tidak ada pembaca di sini yang dipakai skor, tangga penalti, atau penguncian;
* kartu panel dan lencana halaman hasil **mengatakan sendiri** bahwa penolakan hanya
dicatat.

**Dua permukaan, keduanya per ujian:**

* **panel SEB** (`/teacher/exams/<id>/seb`) — jumlah murid, jumlah percobaan, waktu
  penolakan terakhir, dan tabel Waktu / Nama / Alasan. Kartunya muncul juga ketika
  **belum ada** yang ditolak (selama SEB menyala), karena "belum ada yang ditolak"
  adalah konfirmasi yang dicari guru tepat setelah membagikan berkas — dan tempat
  satu-satunya fakta itu bisa muncul. Alasan tanpa kalimat adalah kegagalan uji,
  bukan sel kosong: satu tabel (Indonesia, Inggris) memegang semua kalimatnya.
* **lencana di halaman hasil** (`stats.refused_pupils`) — angka di samping hitungan
  peserta, lencana terlambat dan lencana meninggalkan ujian. Ia **tidak** menempel
  pada baris murid, karena murid yang ditolak di pintu tidak punya baris `submissions`
  sama sekali.

**Batasnya, apa adanya:** yang tercatat adalah klien yang benar-benar **memuat
halaman handshake**. Klien yang ditolak lalu menutup jendelanya sebelum halaman itu
jalan tidak meninggalkan baris, dan modulnya tidak menebak. Yang juga tidak dicatat:
jalur simpan (murid iPad dan browser biasa tidak bisa dibedakan lewat header di sana
— lihat bagian keterbatasan di atas), dan laporan dari halaman adalah laporan
best-effort: kegagalannya dicatat di log, tidak pernah membuat murid kehilangan
halamannya.

Dijaga `tests/unit/test_seb_door_refusals.py` (52 tes): bentuk barisnya, pembacaan
yang gagal menjadi daftar kosong (bukan 500), dua angka ringkasannya, setiap alasan
punya pasangan kalimat, kedua rute dijalankan melawan PostgREST tiruan (kunci tidak
cocok → satu baris; kunci cocok → tidak ada baris; laporan halaman → satu baris;
alasan tak dikenal → 400; kertas tidak gated → 404; murid bukan peserta → 403),
kedua permukaan **dirender**, dan kontrak non-punitifnya. Tiga di antaranya menutup
cara perubahan ini bisa rusak tanpa terlihat: kolom yang dibaca harus ada di migrasi
(yang dibaca sebagai *absen*, bukan error), `student_id` harus mendeklarasikan kunci
asing supaya PostgREST bisa menyematkan nama, dan tidak boleh ada label yang memuat
tanda kutip — karena pasangan itu diikat sebagai `t('{{ id }}','{{ en }}')`, dan satu
apostrof akan menutup string Alpine itu dan merusak panel bagi setiap pembaca.

---

## Panduan per peran (bahasa sederhana, siap dipakai sebagai teks bantuan UI)

**Untuk GURU** — "Begitu kamu aktifkan Wajibkan SEB, sistem otomatis membuat kata
sandi rahasia yang mengunci tombol keluar SEB. Kamu bisa melihatnya di halaman
ujianmu — simpan untuk kondisi darurat saja. Murid tidak pernah melihat kata sandi
ini."

**Untuk ADMIN SEKOLAH** — "Kamu bisa melihat kata sandi yang sama seperti guru
pemilik ujian — supaya kalau guru berhalangan, kamu tetap bisa membantu murid yang
terkunci."

**Untuk PENGAWAS** — "Selama kamu bertugas mengawasi, kamu bisa melihat kata sandi
keluar SEB untuk ujian di ruanganmu. Setelah tugasmu selesai, akses ini otomatis
tertutup."**Untuk MURID** — "Kamu cukup buka berkas yang diberikan guru/pengawas, ujian akan
terbuka otomatis dan terkunci. Kalau ada masalah, panggil pengawas — merekalah yang
punya kata sandinya, bukan kamu."

Kalau berkasnya belum sampai (atau berkas lama sudah tidak cocok karena guru
mengubah pengaturan), halaman **Buka dengan SEB** menawarkan **Unduh berkas .seb
saya** — berkas ujian itu sendiri, dari rute `/student/exams/<id>/seb-file` yang
sudah dijaga matriks RBAC di atas (`murid` + `download_file`, dicatat di
`seb_access_log`). Sebelum tautan itu ada, izin itu nyata tetapi tidak punya satu
pun jalan masuk dari layar: rutenya hanya bisa dicapai dengan mengetik URL-nya.

Halaman publik `/panduan/seb` memuat versi lengkap untuk murid dan orang tua,
**tanpa login**, dan tautan unduh resmi ke `safeexambrowser.org` (ScanGrade
**tidak pernah** menghosting pemasang SEB sendiri).

---

## Keterbatasan yang masih ada (jujur)

1. **Config Key SUDAH diverifikasi terhadap satu klien nyata — untuk satu config, di
   satu platform.** Pada 9 Oktober 2026, Windows SEB **3.10.2.920 (x64)** membuka
   berkas uji yang dibuat `seb_service` dan mengirim
   `X-SafeExamBrowser-ConfigKeyHash` yang **sama** dengan
   `SHA256(startURL + Config Key)` yang dihitung server. Kunci klien = kunci kita,
   terbukti, bukan sekadar konforman. Angka mentahnya, tanggal, dan daftar yang
   **belum** diukur ada di **[`SEB_PHASE11.md`](SEB_PHASE11.md)** bagian **Hasil**.

   Yang **masih** belum terbukti, dan tidak boleh dibaca lebih jauh dari itu:
   **berkas ujian dari baris database** (putaran pengujian itu tidak punya `.env`,
   jadi tidak ada ujian `require_seb` yang bisa dibuka, tidak ada kunci tersimpan
   untuk dibandingkan, dan pintu tidak diuji menolak browser biasa) dan **build
   macOS/iOS**. Kunci adalah hash atas isi berkas, dan isi berkas ujian berbeda dari
   berkas uji umum (ada `startURL` ujian + dua hash kata sandi) — tapi semuanya
   bool/int/string dan melewati fungsi yang sama. Itu argumen, bukan pengukuran: yang
   mengukurnya adalah Langkah 3-4.

   Uji-silang yang ada tetap perlu dan tetap dijalankan: serialisasi SEB-JSON diuji
   byte-for-byte terhadap contoh kerja **resmi** yang diterbitkan halaman Config Key
   SEB, dan berkas yang digenerate di-round-trip (tulis → baca → hitung ulang)
   sehingga tipe dan nilai yang hilang akan ketahuan.

   Langkahnya sudah tertulis lengkap di **[`SEB_PHASE11.md`](SEB_PHASE11.md)**, dan
   bagian yang bisa dijalankan mesin ada di **`deploy/seb_phase11.py`** (kode keluar
   0 cocok · 1 tidak cocok · 2 tidak bisa diukur — `2` sengaja **bukan** lulus) dan
   **`deploy/seb_phase11_probe.py`** (menyajikan config uji lalu mencatat header yang
   benar-benar dikirim klien, dinilai per-URL). Alat pertama membandingkan nilai yang
   dilaporkan **klien** dengan yang dihitung server; kunci berkas itu sendiri
   **tidak** dijadikan bukti, karena berkas ditulis oleh server, jadi
   membandingkannya dengan dirinya sendiri tidak membuktikan apa pun. Yang membawa
   bukti: SEB-JSON dari log klien (beserta **offset byte pertama** yang berbeda bila
   tidak cocok), Config Key yang ditampilkan SEB, kunci yang tersimpan di baris
   ujian, dan header yang benar-benar dikirim klien.
2. **iPad/macOS 3.0+ tidak mengirim header CK.** WKWebView tidak mendukung header
   CK sama sekali; jalur resminya adalah SEB JavaScript API. Belum diimplementasikan
   di sini, jadi jangan menjanjikan perlindungan header untuk iPad sampai itu
   dikerjakan. Rinciannya (fungsi `updateKeys()`, variabel
   `SafeExamBrowser.security.configKey`, dan setelan `browserWindowWebView`) ada di
   [`SEB_PHASE11.md`](SEB_PHASE11.md) Langkah 6 — dan **iPad harus dicatat sebagai
   belum terlindungi** di laporan sampai fase itu selesai.
3. **Config Key bukan rahasia dari murid yang memegang berkasnya.** Kunci itu
   dihitung dari pengaturan yang ada di berkas, jadi murid yang membacanya bisa
   menghitung kunci yang sama dan memalsukan header dari browser biasa. SEB adalah
   **penghalang kuat dan kunci nyata di sisi klien**, bukan bukti kejujuran. UI
   mengatakan ini apa adanya.
4. **Pemasang SEB tidak dihosting** dan tidak akan — tautan selalu ke situs resmi,
   supaya tidak ada mirror usang dari alat keamanan.
5. **Jalur sinkronisasi dan kirim jawaban membaca header CK — dan tetap tidak
   pernah menolak.** Putarannya begini, dan urutannya sengaja: pertanyaannya lebih
   dulu **diukur**, baru kodenya dipasang.

   **Yang terukur (9 Oktober 2026, Windows SEB 3.10.2.920 x64).** Pertanyaan "apakah
   header ikut pada XHR, bukan hanya pada navigasi" sekarang punya jawaban mesin,
   bukan kutipan dokumentasi: `fetch()` dari halaman probe tiba dengan
   `X-SafeExamBrowser-ConfigKeyHash` yang **cocok** — `SHA256(URL XHR itu sendiri +
   Config Key)` — dinilai per-URL, dan `deploy/seb_phase11_probe.py --report` keluar
   **0**. Nilai mentahnya, tanggal, dan berkasnya ada di
   [`SEB_PHASE11.md`](SEB_PHASE11.md) bagian **Hasil → pertanyaan B**.

   **Yang dilakukan aplikasi karena itu.** `/api/student/sync-draft` dan route submit
   sekarang **membaca** header itu sebelum keputusan apa pun diambil dan mencatat
   hasilnya lewat satu fungsi, `seb_service.observe_save_key()` — satu baris log
   `SEB header on XHR: match|mismatch|absent`. Itu yang menutup lubang di paragraf
   berikut: sesi yang dipindahkan ke browser biasa **tidak lagi tidak terlihat**.

   **Yang tetap TIDAK dilakukan, dan alasannya bukan kehati-hatian.** Kedua jalur itu
   tidak pernah menolak karena header. Satu platform terukur bukan semua platform —
   dan yang menentukan bukan itu: SEB untuk macOS/iOS berjalan di WKWebView, yang
   **tidak bisa** menempelkan header CK pada permintaan apa pun. Menolak di jalur
   yang menyimpan jawaban berarti membuang kertas yang sudah selesai, untuk **setiap**
   murid di platform itu, di tengah ujian, dengan jawabannya sudah ada di baris
   `submissions`. Pintu yang berhak menolak tetap pintu halaman: di sana klien yang
   tidak bisa membuktikan diri diarahkan ke halaman handshake JavaScript, bukan
   ditolak. Jadi yang dibeli putaran ini adalah **bukti**, bukan penolakan kedua.

   Konsekuensi yang masih terbuka, apa adanya: sesi gated yang pindah ke browser
   biasa sekarang **tercatat** (log `absent`/`mismatch`) tetapi belum *dihentikan*;
   dan di jalur simpan, murid iPad tidak bisa dibedakan dari browser biasa lewat
   header — klaim JavaScript hanya diperiksa di pintu. Menutupnya butuh keputusan
   produk (menahan kertas di tengah jalan berisiko lebih besar daripada risikonya),
   bukan sekadar satu pemeriksaan lagi. Yang dijaga oleh
   `tests/unit/test_seb_save_path.py` (24 tes): kedua jalur **memanggil** pengamat itu
   sebelum keputusan pertama, tidak ada satu pun dari keduanya yang boleh memanggil
   fungsi penolak (`seb_service.verify`), URL yang di-hash adalah URL permintaan itu
   sendiri (bukan `startURL` ujian), kolom `require_seb`/`seb_config_key` ikut pada
   bacaan yang sudah ada, dan kertas gated **tetap tersimpan dan tetap terkumpul**
   tanpa header — dua route itu **dijalankan** melawan PostgREST tiruan, bukan dibaca.
   Dijalankan lewat 14 mutasi (`.freebuff/mutate_seb_save_path.py`), **14/14
   tertangkap** — termasuk "menolak kalau header tidak ada" pada kedua jalur, yang
   memang perilaku yang tesnya ada untuk mencegah.
6. **URL yang di-hash terikat pada host yang dipakai saat berkas dibuat.** Berkas
   membawa `startURL` dari `request.url_root` saat guru mengunduhnya. Sekolah yang
   membuka aplikasi lewat nama host berbeda (alias domain, `www.` vs tanpa, port
   berbeda) akan ditolak walau SEB-nya benar. Satu sekolah = satu alamat, dan itu
   perlu dikatakan di panduan operasional sekolah.
7. ~~**Migrasi `062_seb_and_environment_signals.sql` belum diterapkan.**~~
   **Sudah diterapkan ke produksi** (proyek `roshkbzgfzpfedowozfo`) lewat
   `deploy/apply_migration.py --commit`: delta 65 objek, seluruhnya penambahan,
   rollback dry-run bersih dan re-run no-op, ditambah titik pemulihan sebelum
   penerapan. Diverifikasi ulang langsung dari skema hidup (bukan dari laporan
   alat): ketiga tabel ada dengan seluruh kolomnya, RLS aktif, tiga policy dan
   dua belas indeks terpasang, dan `exams.require_seb` benar-benar
   `boolean NOT NULL DEFAULT false` — sehingga **tidak ada ujian lama yang berubah
   perilakunya**. Kolom-kolom itu juga sudah terbaca lewat PostgREST (HTTP 200),
   jadi aplikasinya bisa langsung memakainya.

   Satu ketidaksamaan yang ditemukan saat pemeriksaan: `AGENTS.md` mencatat ref
   proyek sebagai `roshkbkbzgfedowozfo` (19 karakter), sedangkan `DIRECT_URL` dan
   `SUPABASE_URL` **sama-sama** menunjuk `roshkbzgfzpfedowozfo` (20 karakter, satu-
   satunya bentuk ref Supabase yang sah). Catatan di `AGENTS.md` itu salah ketik;
   yang menentukan target adalah kedua URL tersebut, dan keduanya cocok.
