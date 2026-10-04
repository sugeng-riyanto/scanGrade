# Invigilators: the schedule and the matrix

ScanGrade answers two different questions about who watches an exam, and they are
two different pages:

* the **schedule** (`/admin-sekolah/invigilation`, also `/vice-principal/invigilation`)
  says *when a class sits a paper*, and attaches one or more teachers to that sitting.
  This is what a teacher's *My duty* page reads back.
* the **matrix** (`/admin-sekolah/invigilation/matrix`) says *who stands in this room,
  in this slot, on this day*. It is a grid — rows are time slots, columns are rooms,
  a cell holds one teacher — and it is the page a school uses when a room holds
  candidates from several classes at once.

Both are written by the same people: **`admin_sekolah`** always, and
**`vice_principal`** on their own prefix. A school that never made a deputy can still
staff an exam, because the admin door owns the whole feature.

## For a school admin

### The two axes

Before a cell can be filled, the grid needs something to hang on:

* **Periods** — the day's time slots (`Sesi 1`, `07:30–09:30`). The sort order is the
  order the rows are drawn, top to bottom.
* **Rooms** — the exam rooms, with an optional capacity so the grid can show whether a
  room is large enough for the candidates assigned to it.

Both are created on the matrix page itself. Names are unique per school (case and
punctuation are ignored, so `Sesi 1` and `sesi1` are the same slot). No developer and
no SQL are involved.

### Filling the grid

Pick a date, and the grid shows that day. An empty cell offers a dropdown of **this
school's teachers who are free in that slot** — a teacher already standing in another
room is not offered, because the write would refuse them. Choose one and press *Set*.

Two rules hold, and both are enforced by the **database**, not just the page:

* a room holds **one** invigilator per slot per day;
* a teacher stands in **one** room per slot per day.

The service checks them first so you read a sentence (*"that room already has an
invigilator in this period"*, *"that teacher is already on duty in another room this
period"*) instead of a constraint error, and the unique indexes are what actually hold
when two people press the button at the same moment. The same teacher may of course
take a *later* slot — the rules are per slot, not per day.

A filled cell shows the teacher's name, an **uploaded** chip if it came from the Excel
import, and an ✕ to empty it. The load strip under the grid counts each teacher's
duties for the day, which is how you tell whether the work is spread evenly.

### Bulk assignment through Excel

Download the template (**Unduh template Excel**), fill it, upload it.

| column | meaning |
|---|---|
| `Tanggal` | the exam date — a real date cell or `YYYY-MM-DD` / `DD/MM/YYYY` text |
| `Periode` | the slot name, exactly as it is written on the matrix page |
| `Nama Ruangan` | the room name, exactly as written |
| `Email Guru` | the teacher's login address |
| `Catatan` | optional |

The workbook has two tabs: **Pengawas** (the header plus one highlighted example row
marked `[CONTOH - hapus baris ini]`) and **Referensi**, which lists this school's own
periods and rooms so you write the names the importer will match. The example row is
skipped, never counted and never reported — forgetting to delete it is not an error.

The upload gives you a **preview before anything is written**: every row, its line
number, and each problem on it. Problems are reported per row and the file is **never
abandoned at the first bad line** — fix all of them in one pass:

* the date cannot be read;
* the period name is not one of this school's;
* the room name is not one of this school's;
* the teacher's address is not a teacher at this school;
* the room is already filled that slot, or a **second row in the same file** asks for
  the same room;
* the teacher is already on duty that slot, or a **second row in the same file**
  repeats them.

Only the rows marked valid are applied, and only after you press the confirm button.
The rows you confirmed are re-checked through the **same** write the grid uses, so a
hand-edited preview cannot commit a line the grid would refuse.

### The file is not kept

The uploaded workbook is read into memory, parsed, and dropped when the request ends.
It is **not** written to disk and **not** put in Storage — no file holding teacher
names and addresses survives the upload. What is kept is the structured duties and one
audit line naming who uploaded and how many rows were valid and invalid.

## For a reviewer

* **Scope is a required argument.** Every read and write in
  `app/services/invigilation_matrix.py` takes `school_id` and puts it in the query, and
  every route reads the school from the session — never from a form field. The backend
  uses the service key and walks through RLS, so these filters *are* the guard.
* **The importer is one pure function.** `parse_workbook` is a function of bytes and
  the school's own period/room/teacher lists; it holds no database handle, so a test
  drives forty rows through it without a connection, and the function the test
  exercises is the one the route calls.
* **The template's signature is checked, not its name.** `is_xlsx` reads the ZIP
  magic (`PK\x03\x04`), so a renamed CSV or an image is refused before parsing.
* **RLS names the caller's role.** The three tables (`exam_period`, `exam_room`,
  `invigilation_duty`) enable RLS and each policy compares against the database's own
  roles (`admin_sekolah`, `vice_principal`, `principal`, and a teacher's own rows on
  `invigilation_duty`); none is open to `PUBLIC`.
* **Schema.** Migration `059_invigilation_matrix.sql` is additive and idempotent, and
  the two conflict rules are the two `UNIQUE` constraints on `invigilation_duty`
  (`…_room_slot_key`, `…_teacher_slot_key`).
