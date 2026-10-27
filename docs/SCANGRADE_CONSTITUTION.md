# Konstitusi ScanGrade / ScanGrade Product Constitution

**Status:** Pedoman keputusan produk, engineering, dan operasional yang wajib menjadi referensi dalam setiap usulan fitur, refactor, migrasi, dan rilis.  
**Prinsip inti:** Kualitas dan kepercayaan sekolah lebih dahulu; pertumbuhan biaya mengikuti kebutuhan nyata dan pendapatan.

## Pasal 1 — Keselamatan ujian tidak boleh ditawar
Autosave, timer, jawaban siswa, penilaian, privasi, isolasi tenant/sekolah, keamanan SEB, dan keberlangsungan ujian adalah fungsi inti. Eksperimen, integrasi video, fitur premium, optimasi biaya, atau percepatan deploy tidak boleh merusaknya. Jika dampak tidak diketahui, default **HOLD** dan pertahankan rilis terakhir yang sehat.

## Pasal 2 — Kualitas dasar setara untuk setiap sekolah
Sekolah gratis, pilot, maupun berbayar memperoleh standar keselamatan, integritas penilaian, stabilitas, aksesibilitas, dan perlindungan data yang sama. Paket komersial boleh membedakan **fitur tambahan** (misalnya ScanMeet), bukan keamanan, kebenaran hasil ujian, atau hak atas data peserta.

## Pasal 3 — Guru dan siswa sebagai ukuran keberhasilan
Desain harus responsif di ponsel dan desktop, mudah dipahami, inklusif, serta tahan konektivitas sekolah yang tidak stabil. Beban kerja guru harus berkurang; siswa dapat fokus mengerjakan ujian. Fitur baru memerlukan alur nyata, pesan kesalahan yang jelas, dan regresi UX yang diuji.

## Pasal 4 — ScanMeet: privasi media dan persetujuan
Integrasi LiveKit dipisahkan dari mekanisme ujian inti. Kamera hanya sesuai kebijakan ujian yang sah; mikrofon berbasis permintaan, chat nonaktif pada mode ujian, angkat tangan aktif, perekaman nonaktif secara default. Setiap sekolah dikendalikan melalui entitlement server-side berdasarkan NPSN/school_id dengan **default OFF**. Menolak token baru tidak cukup: OFF, akhir sesi, dan kedaluwarsa lisensi harus menghentikan media aktif secara terukur, termasuk retry cleanup dan audit. Jangan pernah menganggap uji sintetis sebagai izin mengaktifkan kamera siswa produksi.

## Pasal 5 — Hemat biaya tanpa mengorbankan keselamatan
Gunakan kapasitas VPS dan kuota database secara disiplin: ukur beban, egress, query, cache, media bandwidth, dan biaya per sekolah sebelum menaikkan infrastruktur. Kurangi wildcard query dan transfer data tak perlu, tanpa mengorbankan konsistensi, backup, aksesibilitas, atau perlindungan privasi. Hindari klaim kapasitas tanpa load test. Upgrade VPS/DB/SFU ketika data penggunaan dan anggaran mendukung; rancang agar upgrade tidak menuntut pembangunan ulang.

## Pasal 6 — Rilis harus dapat dibuktikan dan dibatalkan
Rilis produksi memerlukan CI relevan PASS, verifikasi target database dan migrasi, backup yang **terbukti dapat dipulihkan**, pemeriksaan ujian aktif, flag aman, smoke test, dan jalur rollback. Migration readiness tidak sama dengan file SQL hadir atau ledger tercatat; verifikasi schema langsung. Kegagalan atau bukti yang hilang menghasilkan **NO-GO**. Jangan bypass guard, skip tes, mengubah izin backup, atau memaksa deploy demi tenggat.

## Pasal 7 — Data dan hak akses sekolah
Gunakan identitas sekolah dari sesi/rekaman terotorisasi, bukan hanya input klien; pastikan RLS, akses role, audit mutasi sensitif, keamanan kredensial dan kepatuhan perlindungan data. Pembayaran manual tidak boleh dianggap berhasil hanya dari nilai formulir tanpa bukti dan otorisasi yang bisa diaudit. Tidak ada penagihan otomatis tanpa keputusan produk yang eksplisit.

## Pasal 8 — Uji dahulu, klaim kemudian
Setiap keputusan produk harus dibedakan sebagai **rencana**, **implementasi**, **lulus tes**, **terdeploy**, atau **terverifikasi produksi**. Hindari menyebut fitur tersedia hanya karena kode merged. Uji kasus perangkat, koneksi putus, kegagalan layanan eksternal, batas tenant, dan rollback; cantumkan keterbatasan bukti secara jujur.

## Pasal 9 — Pilot sebelum ekspansi
Urutan utama: **stabilitas ujian → pengamanan ScanMeet dan pengalaman pengguna → pilot sekolah terbatas → observabilitas dan pengukuran kapasitas → ekspansi serta upgrade infrastruktur berbasis penggunaan/pendapatan**. Pilot harus melindungi siswa dan tidak mengganggu ujian produksi; gunakan lingkungan dan akun sintetis ketika memungkinkan.

## Pasal 10 — Cara mengambil keputusan baru
Setiap ide, PR, proposal, atau perubahan arsitektur sebaiknya menjawab:
1. Apa dampaknya bagi guru dan siswa?
2. Apa risikonya terhadap integritas ujian, media siswa, privasi, dan tenant?
3. Apa yang tetap berjalan jika komponen baru gagal atau dimatikan?
4. Bagaimana pembuktian QA, migrasi, rollback, serta bukti kesiapan produksi?
5. Berapa biaya terukur sekarang dan pada skala lebih besar?
6. Apa yang didapat seluruh sekolah sebagai kualitas dasar dan apa yang opsional?
7. Kapan status NO-GO harus dipilih?

**Aturan konflik:** Jika usulan bertentangan dengan keselamatan ujian, privasi, kejujuran klaim, atau pemulihan data, keselamatan dan keandalan menang atas kecepatan rilis, pendapatan, atau tampilan fitur.

## Tata kelola
- Konstitusi ini adalah referensi jangka panjang bagi pemilik, Freebuff, AI agent, reviewer, dan kontributor.
- Usulan perubahan konstitusi dilakukan melalui PR tersendiri, dengan alasan, risiko, dampak bagi pengguna, dan persetujuan pemilik repository.
- Dokumen tidak menggantikan kebijakan hukum/keamanan yang lebih ketat maupun prosedur operasional rilis; keduanya tetap berlaku.
- Rujukan operasional rinci berada pada runbook deploy, pengujian, dan dokumentasi ScanMeet di repository.

*Disahkan sebagai prinsip arah pengembangan; pemberlakuan teknisnya mengikuti review dan merge PR konstitusi.*
