# AI Essay Grading

## Cara Kerja

1. Guru mengirim jawaban esai siswa ke AI
2. Sistem mengirim prompt ke LLM (Large Language Model)
3. AI mengembalikan skor (0-max_score) + feedback
4. Guru review dan bisa override skor

## Provider Support

| Provider | API Key Required | Model |
|----------|-----------------|-------|
| **Gemini** (default) | ✅ | gemini-pro / gemini-1.5-pro / gemini-2.0-flash |
| **OpenAI** | ✅ | gpt-4o / gpt-4o-mini / gpt-4 / gpt-3.5-turbo |
| **DeepSeek** | ✅ | deepseek-chat |
| **Groq** | ✅ | Llama 3 / Mixtral |
| **Custom** | ✅ | Any OpenAI-compatible API (masukkan base URL + model name) |

## Setup API Key

1. Buka **Pengaturan AI** di dashboard guru
2. Klik **Tambah Key**
3. Pilih provider (Gemini recommended — gratis)
4. Masukkan API key
5. Klik **Test** untuk verifikasi koneksi
6. Aktifkan key (centang aktif)

## Prompt Templates

ScanGrade memiliki 11+ template prompt per mata pelajaran:

| Template | Untuk |
|----------|-------|
| **Default** | Semua mapel |
| **IPA** | Fisika, Kimia, Biologi |
| **Matematika** | Perhitungan, pembuktian |
| **Bahasa** | Bahasa Indonesia, Bahasa Inggris |
| **IPS** | Sejarah, Geografi, Ekonomi |
| **ICT** | Informatika, Komputer |
| **Agama** | Pendidikan Agama |
| **PJOK** | Olahraga |
| **Ketat** | Nilai sulit |
| **Ringan** | Nilai mudah |

### Variable Prompt

Gunakan variable dalam template:
- `{question}` — teks soal
- `{answer}` — jawaban siswa
- `{max_score}` — bobot maksimal
- `{rubric}` — pedoman penskoran

## Keterbatasan

- AI bisa memberikan skor tidak konsisten
- AI tidak bisa menilai jawaban berbasis gambar
- Hasil AI perlu review guru
- Perhatikan kuota API (berbayar setelah batas gratis)

## Manual Override

1. Di halaman detail siswa, lihat skor AI
2. Jika tidak sesuai, ubah skor manual
3. Klik **Simpan**
4. Skor final akan menggunakan nilai manual
