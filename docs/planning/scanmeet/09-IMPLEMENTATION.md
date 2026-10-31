# 09 — Backlog eksekusi dan definisi selesai

## Fase audit

A0: inventaris actual main, migrations, flags, UI route, tests dan deploy runner.
Buat gap matrix: requirement → existing file/behavior → defect → proposed PR → evidence.
Bandingkan sumber Jumpa pada SHA tercatat. Prioritaskan ujian; jangan mulai studio dulu.

## Urutan PR

| Fase | PR yang dibangun | Ketergantungan | Bukti utama |
| --- | --- | --- | --- |
| exam | Kartu kontrol ujian, panel murid satu halaman, OFF lifecycle, pengawas/admin dan takeover | Audit | E01–E10, P01, core U01/U02 |
| meetings | Room standalone, lobby, scope/link/admission, host/cohost dan kebijakan Jumpa | Access policy, entitlement concurrency | M01–M06, U01/U02 |
| attendance | Event/interval/reconcile, assignments export, PDF/XLSX dan audit | Stable session identities | A01–A04, X01–X03 |
| studio | Camera effects, Program/Preview, clean feed dan local recorder | Meeting grants dan participant notice | R01–R04, S01/S02 |
| release | Integration, CI/Windows, real media, capacity, runbook/rollback dan pilot | Semua fase yang masuk rilis | P02, D01 dan scope acceptance |

Pisahkan fase besar menjadi PR lebih kecil bila perlu: assignments/migration, client,
recorder/audio, export. Jangan melakukan satu migrasi serba guna atau satu PR ribuan
baris tanpa review. Fitur baru default OFF sampai layer lengkap; UI tidak boleh
menawarkan tombol palsu atau memberi kesan fitur stub telah selesai.

## Definisi selesai setiap PR

- Perilaku nyata sesuai acceptance, termasuk jalur gagal dan batas akses.
- Tes bermakna, output/exit code, SHA dan keterbatasan tercatat; test fail sebelum fix
  bila regresi. CI, real media dan pengujian fisik yang wajib tidak disubstitusi mock.
- Docs/handoff/migration/release notes konsisten. Tidak ada rahasia/recording/PII di Git.
- Konflik dengan main diselesaikan tanpa menghilangkan perubahan pemilik.
- Pekerjaan berada di branch development; tidak menyalin paket AGENTS ke root repo
  dan tidak mengganti instruksi repository.
- Local checks hanya membuktikan yang benar-benar mereka jalankan. Status akhir
  jujur, blocker memiliki penyebab dan langkah yang dapat dikerjakan.

## Hemat dan cepat

Satu pelaksana + review terpisah per perubahan. Reuse service/test yang sudah ada setelah
audit; targeted tests dulu, full required suite setelah stabil; cache dependency di
mesin pengembangan; jangan polling API/CI tanpa jeda atau mengulang full suite tanpa
perubahan. Batasi dua retry kegagalan yang sama, lalu diagnosis. Tidak mengurangi
keamanan atau menghilangkan bukti demi cepat. Jangan menambah fitur di luar backlog.
