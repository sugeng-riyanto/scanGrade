# LMS Drive Spec — Semua File via Embed (bukan upload ke VPS)

> Agent: server tidak menyimpan blob. Hanya `drive_file_id`.

## 1. Setup sekali per sekolah (manual, oleh admin)

1. Google Cloud Console -> New Project `scangrade-[npsn]` -> Enable Drive API.
2. OAuth consent External -> scope `drive.file` + `drive.readonly`.
3. OAuth client Web: origin `https://scangrade.web.id`,
   redirect `https://scangrade.web.id/api/drive/callback`.
4. Simpan `GOOGLE_CLIENT_ID/SECRET` sebagai env server saja.
5. Buat Shared Drive `ScanGrade - [NPSN]`, share ke service account sebagai Manager.

## 2. Per user (guru)

- Tombol "Hubungkan Drive" -> OAuth -> simpan `user_drive_tokens
  (user_id, refresh_token_enc, scope)` terenkripsi.
- Picker: Google Picker API -> kembalikan `fileId + mimeType` -> POST ke server.
- Server validasi `drive.files.get` + simpan ke `content_items`.

## 3. Publish / unpublish

- Publish: `permissions.create(fileId, {role:'reader', type:'group', emailAddress: kelas@...})`
  Tahap awal boleh per-email murid (batch 100), lalu migrasi ke grup.
- Unpublish: `permissions.delete`.
- Embed builder server-side (satu fungsi `drive_embed_url(file_id, mime)`), jangan di JS.

## 4. ScanMeet recording upload (milik masing-masing user)

- Rekaman lokal `MediaRecorder` -> Blob -> tombol "Simpan ke PC" (download)
  + "Upload ke Drive saya" -> POST blob ke `/api/meet/recordings/upload`
  (resumable, retry offline) -> server upload pakai `refresh_token` user itu.
- File masuk Drive user, bukan Drive sekolah. Revoke di Pengaturan menghapus token.

## 5. Acceptance

- [ ] File tanpa permission tampil pesan guru, bukan iframe kosong.
- [ ] Unpublish benar-benar mencabut (probe sebagai murid = 404).
- [ ] Tidak ada client secret / refresh token di template, log, atau repo.
- [ ] Test: `tests/unit/test_lms_drive.py` (URL builder + permission toggle mock).
