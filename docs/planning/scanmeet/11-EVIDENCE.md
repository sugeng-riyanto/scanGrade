# 11 — Format bukti dan laporan

## Template per fase

```text
Fase / pemilik / waktu UTC dan WIB:
Repository / base SHA / head SHA:
Scope dan requirement ID:
Perilaku sebelum → sesudah:
Berkas/migrasi yang berubah:
Perintah / environment / exit code / pass fail skip:
Browser/perangkat nyata dan versi:
Bukti negatif (cross-school, OFF, expired, unauthorized):
Bukti media/attendance/export/playback:
CI URL dan head yang diuji:
Migrations: disiapkan / staging diuji / produksi diterapkan (pisahkan):
Known limitations / cannot measure / blocker:
Rollback target dan bukti restore:
Status: IMPLEMENTED / LOCAL_CHECKS_PASS / CI_PASS / STAGING_PASS / BLOCKED:
Langkah berikutnya:
```

Artefak redacted: log teks, report JSON, screenshots tampilan tanpa data pribadi,
sample PDF/XLSX sintetis, checksum rekaman sintetis dan hasil playback. Media murid,
token/signature/webhook secrets, password dan nilai ujian nyata tidak boleh masuk Git.

Runner menghasilkan receipts fase dan check berisi SHA awal/akhir, exit code, waktu,
log path dan hash. Exit 0 provider = AGENT_EXIT_ZERO_UNVERIFIED; bukan kualitas kode.
Check sukses dengan perubahan tree selama check = STALE; bukti tidak dipakai.
Receipt lokal bukan tanda tangan independen, dapat diubah oleh user/agen yang punya
akses. Reviewer/CI harus mengonfirmasi hasil untuk SHA dan tree yang sama.

Laporan pengguna maksimal beberapa paragraf: selesai apa, bukti apa, belum diuji apa,
masalah apa, langkah berikutnya. Tidak ada “semua aman” tanpa scope atau status live
palsu. Jangan memaksa pengguna mengetik 20 perintah VPS untuk menutupi pekerjaan yang
bisa diselesaikan di repository/CI.
