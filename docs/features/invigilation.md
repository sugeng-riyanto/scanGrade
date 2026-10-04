# Invigilators: the schedule and the matrix

ScanGrade answers two different questions about who watches an exam, and they are
two different pages:

* the **schedule** (`/admin-sekolah/invigilation`, also `/vice-principal/invigilation`)
  says *when a class sits a paper*, and attaches one or more teachers to that sitting.
  This is what a teacher's *My duty* page reads back.
* the **matrix** (`/admin-sekolah/invigilation/matrix`) says *who stands in this room,
  in this slot, on this day*. It is a grid — rows are teachers, columns are rooms, and
  a cell is a tick for one teacher in one room — and it is the page a school uses when
  a room holds candidates from several classes at once.

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

Pick a date and a session, and the grid shows that session: rows are this school's
teachers, columns are its rooms. **Click a cell to put that teacher in that room;
click it again to take them out.** Every click saves by itself — there is no *Save*
button and no page reload. A room's header chip shows `taken/2` and a teacher's row
chip shows the same, so you can see at a glance who is short.

**Filling a whole run at once.** Holding **Shift** and clicking a second cell in the
same row applies the first click's action — add or clear — to every cell between the
two, in a single save. The first click is the *anchor*: it decides whether the run
fills or empties, so the range never invents an operation of its own. The run goes
through the same service a single click uses, so the ceilings still hold: a run that
would seat a teacher in a third room lands the rooms it can and names the one it
refused, and a refused cell is left exactly as it was. Ranges are one row only — a
teacher across their own run of rooms; there is no cross-teacher drag.

Two ceilings hold, and both are enforced by the **database**, not just the page:

* a room holds **at most two** invigilators per slot per day;
* a teacher stands in **at most two** rooms per slot per day.

And two minimums are shown, not enforced: the summary above the grid counts the rooms
with **no** invigilator yet and the teachers with **no** room yet, and both are the
amber chip until they are zero. They are stated rather than refused because a hard
"you cannot remove the last one" would make moving a teacher between rooms
impossible — the grid tells you the gap and leaves the judgement to you.

The service checks the ceilings first so you read a sentence (*"this room already has
two invigilators in this period"*, *"this teacher already covers two rooms in this
period"*) instead of a constraint error, and the trigger installed by migration 061 is
what actually holds when two people click at the same moment. The same teacher may of
course take a *later* slot — the ceilings are per slot, not per day.

A ticked cell carries a tick and a **uploaded** marker (from the Excel import) is kept
on the duty row; the load strip under the grid counts each teacher's duties for the
day, which is how you tell whether the work is spread evenly.

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
* the room already holds its **two** teachers that slot, or the file itself asks for a
  third;
* the teacher already stands in **two** rooms that slot, or the file itself asks for a
  third;
* a row **repeats** the very same teacher *and* room the file already claimed (that is
  a duplicate, not a second seat).

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
* **Schema.** Migration `059_invigilation_matrix.sql` is additive and idempotent and
  creates the three tables; migration `061_invigilation_seat_caps.sql` relaxes the two
  single-occupancy constraints to a ceiling of two — it drops `…_room_slot_key` and
  `…_teacher_slot_key`, keeps a `UNIQUE` on the exact cell
  (`school_id, exam_date, period_id, room_id, teacher_id`) so the same teacher cannot
  be written twice into one room, and installs a `BEFORE INSERT OR UPDATE` trigger that
  refuses a third row for a room or a teacher. A `UNIQUE` index cannot express "at most
  two"; a trigger can, and it takes a `FOR UPDATE` lock on the session's `exam_period`
  row so two simultaneous clicks are counted in order rather than against a stale
  count.
