# 08 — Rilis dan pemulihan

## Status yang boleh dilaporkan

SPECIFIED → IMPLEMENTED → LOCAL_CHECKS_PASS → CI_PASS → STAGING_PASS → RELEASE_READY → LIVE_VERIFIED.
BLOCKED/FAIL/SKIPPED/CANNOT_MEASURE tetap terlihat. Runner hanya merekam proses lokal,
tidak memberi status CI_PASS/STAGING_PASS/RELEASE_READY otomatis.

Satu head SHA untuk bukti. Perubahan setelah pengujian membatalkan bukti yang relevan;
ulang pemeriksaan affected scope. Jangan menyamakan nama branch atau hijau PR lama
sebagai bukti commit baru. Review belum terjawab dan media test belum jalan bukan hijau.

## Sebelum rilis

- Main terbaru, PR fokus, required CI lulus, review dan bukti acceptance lengkap.
- Backup yang mencakup data dan fungsi terdampak, bukan sekadar file kosong. Catat
  cakupan dan uji restore pada proyek terisolasi. Jangan menyebut backup parsial full DB.
- Migrasi diuji, ledger versi jelas, schema compatible dengan kode lama untuk rollback.
- Tidak ada ujian aktif pada maintenance yang membutuhkan gangguan. Global flags tetap
  OFF sampai konfigurasi/provider/pilot/pengawas siap. Trial tidak otomatis untuk semua sekolah.
- Verifikasi secrets tersedia tanpa mencetak nilainya. Provider account milik ScanGrade,
  tidak menyalin LiveKit/API secrets Bahasa Cerdas. Tidak upgrade billing provider.
- Siapkan SHA last-known-good yang pernah terbukti melayani aplikasi dan skema cocok.
- Ikuti otorisasi pemilik yang berlaku; paket ini tidak menganggap izin menulis dokumen
  sama dengan izin deploy. Jangan minta ulang jika sudah ada otorisasi eksplisit yang sesuai.

## Mekanisme produksi

Repository ScanGrade memakai main fast-forward dan runner/gate produksi yang sudah ada.
Agen harus membaca installer/systemd/deploy aktual sebelum memberi perintah operator.
Jangan `git reset --hard`, `git checkout oldsha`, menonaktifkan gate, menghapus quarantine
atau menyentuh release marker tanpa memahami runner. Jangan menukar `.env` staging
ke produksi. Skrip paket tidak melakukan restart service, merge, push atau DDL.

## Verifikasi sesudah deploy

Health: status ok, Supabase/Redis sesuai konfigurasi, commit sesuai release dan semua
worker terdaftar uniform tanpa blind/mixed. Uji akses staf/murid yang sah pada pilot,
OFF invariant, media isolation, presensi dan ekspor yang dirilis. Aset served cocok
commit; hard reload bukan pengganti asset versioning. Amati error, latency dan cleanup.

## Jika gagal: rollback stabil yang dapat dibuktikan

1. Catat SHA served/checkout, gejala, worker state, migration ledger dan last good.
2. Lindungi ujian: hentikan rollout/aktivasi ScanMeet baru; jika OFF memutus media,
   beri tahu operator karena ini perubahan yang disengaja, bukan menyentuh jawaban.
3. Pilih target **last-known-good yang terverifikasi**, bukan main terbaru atau tag
   stable yang belum diverifikasi. Pastikan masih cocok dengan schema saat ini.
4. Gunakan prosedur rollback resmi repository yang telah diperiksa. Bila belum ada,
   buat dan uji dahulu di staging; jangan mengarang satu perintah pemulihan.
5. Jangan DROP tabel/kolom atau downgrade DB otomatis. Pulihkan aplikasi dulu jika
   migrasi additive compatible; pemulihan data perlu prosedur terpisah dan backup proof.
6. Verifikasi seluruh worker/health/alur pengguna, media permissions dan data ujian.
7. Rekam incident dan bukti pulih. Perbaikan berikutnya kembali lewat PR dan gate.

Tidak ada jaminan rollback 1 perintah pada paket ini karena jalur rollback dan service
VPS harus diverifikasi pada versi deployment yang sedang digunakan.
