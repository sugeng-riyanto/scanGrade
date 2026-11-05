# 10 — Memasukkan paket ke VPS

Paket bukan sesuatu yang dijalankan di folder `/opt/scangrade` produksi. Ia adalah
instruksi dan tool pengembangan; aplikasi live tetap berjalan independen dari AI.

## Persiapan minimum

- Akun Linux non-root dengan home sendiri, git, Python 3.12+, jaringan repository.
- CLI AI yang sudah dipasang dan login jika memilih adapter otomatis; tidak diperlukan
  untuk membaca Markdown/paste prompt ke ChatGPT Work atau Freebuff.
- Mesin pengembangan/staging terpisah dengan resource cukup. VPS produksi 1 GB tetap
  untuk melayani pengguna, bukan menjalankan browser/efek video/build besar.
- Secrets dev/staging disediakan melalui secret manager/environment yang tepat;
  jangan mengunggah `.env` produksi atau private key ke chat.

Upload ZIP lewat SFTP/alat upload yang biasa digunakan, ekstrak ke home, lalu buka
terminal **sebagai akun pengembangan**. Jika login root, pindah memakai metode admin
VPS yang biasa dipakai; jangan mengubah kepemilikan checkout produksi agar git lolos.

```bash
cd "$HOME/ScanMeet-Agent-Pack"
sha256sum -c SHA256SUMS
bash tools/prepare.sh
```

Installer memverifikasi isi paket sekali lagi dan clone branch baru; tidak menginstall
AI, npm, Python dependencies, browser atau service. Installer tidak overwrite direktori
existing. Tidak ada alias root atau curl|bash yang mengunduh skrip tidak terverifikasi.

## Dependency aplikasi

Baca requirements, lockfile, README/AGENTS di clone terbaru. Siapkan venv sendiri dan
install dependency di mesin pengembangan. Jangan memakai venv `/opt/scangrade/.venv`,
menyalin `.env`, menjalankan migrations atau install di interpreter global. Paket tidak
mengunci ulang dependency repo. Node/npm diperlukan jika membangun CSS atau DOM test.
Berat browser/Windows/full suite dijalankan di CI/mesin test, bukan VPS live.

`checks.example.json` memakai `.venv/bin/python` dalam clone. Jika environment memakai
lokasi berbeda, ganti argv pada salinan config dan tinjau. Jangan berikan config asing
sebagai daftar perintah trusted tanpa dibaca.

## Adapter AI

`adapter.example.json` bukan CLI nyata. Ganti placeholder dengan argv program/wrapper
lokal yang menerima prompt melalui stdin; runner memakai subprocess tanpa shell.
Wrapper harus menyalakan sandbox CLI sesuai versi yang terpasang. Jangan gunakan flag
bypass approvals/sandbox. Login model dan biaya akun dikelola terpisah. Runner membuang
environment secrets aplikasi yang umum dari child; HOME tetap tersedia untuk login
CLI. Ini **bukan sandbox OS**: akun/CLI masih dapat mengakses file sesuai hak Unix.
Karena itu akun pengembangan tidak boleh mempunyai akses secrets produksi atau sudo.

Log disimpan mode private di `.scanmeet-agent/` dan tidak otomatis dibagikan/commit.
Agen dapat menulis rahasia ke output tanpa sengaja: tinjau/redaksi sebelum membagikan.
Abaikan folder log/config lokal di Git. Prompt mengandung path paket lokal; pindahkan
paket utuh ke mesin tempat agen berjalan, bukan hanya agent.py.

## Kunci eksekusi

Satu runner per clone. `.scanmeet-agent/execution.lock` mencegah dua penulis berjalan
bersamaan. Setelah proses crash, periksa PID dan pastikan tidak ada child yang masih
berjalan sebelum menghapus lock usang secara manual. Runner tidak menghapus lock
asing atau menganggap proses lain boleh dihentikan.
