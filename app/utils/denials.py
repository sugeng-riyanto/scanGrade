"""What a person is told when something is outside their account.

One place, because the tone is the whole point and it drifts a sentence at a time.
The rule:

* say what the **thing** is — out of scope for this account — never what the reader
  did. `"Akses ditolak"` and `"Tidak punya akses"` were both: they read as an
  accusation, and left somebody who had merely followed a colleague's link with
  nothing to do;
* where an action helps, name it (ask the school admin, try again shortly).

These are Indonesian, and the machine `error` code beside them (`FORBIDDEN`,
`UNAUTHORIZED`) is deliberately untouched: the code is the contract, the prose is
for people, and a client that matches on the code keeps working.

`errors/403.html` carries the same tone in its own words — it is a page a visitor
reads, and it is pinned to Indonesian (its copy has no English half), so its text
lives in the template rather than here. `tests/unit/test_denials.py` holds all of it
to the rule.
"""

# ── a record that is not this account's ──────────────────────────────────────

OUT_OF_SCOPE = "Halaman atau data ini di luar akses akun Anda."
NO_EXAM_ACCESS = ("Ujian ini tidak tersedia untuk akun Anda. Bila seharusnya Anda "
                  "bisa membukanya, silakan hubungi admin sekolah.")
NO_SUBMISSION_ACCESS = ("Lembar jawaban ini tidak tersedia untuk akun Anda. Bila "
                        "seharusnya Anda bisa membukanya, silakan hubungi admin sekolah.")
NO_SUCH_EXAM = "Ujian yang Anda cari tidak ditemukan."

# ── a document asked for a scope it does not cover ───────────────────────────
#
# Both of these are answers to *what the page asked for*, and each one is the
# start of a sentence a reader can act on: which class, or that there is nothing
# to hand back yet. Answering either with an empty document instead would print a
# sheet with no names on it, which reads as "nobody sat this".
CLASS_NOT_ASSIGNED = ("Kelas itu tidak termasuk kelas yang mengikuti ujian ini. "
                      "Pilih kelas yang terdaftar pada ujian, atau cetak tanpa "
                      "menyaring kelas.")
NO_PAPERS_TO_PRINT = ("Belum ada lembar jawaban yang bisa dicetak untuk ujian ini. "
                      "Kartu hasil dibuat dari lembar yang sudah masuk.")

# ── an account that is not attached to a school ──────────────────────────────

NO_SCHOOL = "Akun Anda belum terhubung ke sekolah mana pun. Silakan hubungi admin sekolah."
NOT_YOUR_SCHOOL = "Data ini bukan milik sekolah Anda."

# ── a check that could not be made, which is not the reader's fault ──────────

CANNOT_VERIFY_EXAM = ("Kami belum dapat memverifikasi akses ke ujian ini. Silakan "
                      "coba beberapa saat lagi — bila tetap tidak bisa, hubungi "
                      "admin sekolah.")
