# Nilai Berbobot (Weighted Grade Components)

> A final mark is a **policy**, not one exam. This document explains how a school
> says what its marks mean, how a paper is filed under a component, and how the
> Final Mark is computed — including what happens when a school has configured
> nothing at all.

## The problem it answers

Until this feature, a pupil's "Nilai Akhir" for a subject was the **simple mean**
of every score they had in it — a ten-minute quiz weighed exactly as much as the
final exam. Schools that actually grade on weights (Coursework 30%, Mid-term 30%,
Final 40%) could not express that, so the number shown was not the number the
school meant.

## Two ideas, kept apart

| Table | What it holds | Who manages it |
|---|---|---|
| `grade_component_type` | The **components** a school uses — its own names: "Tugas Kelas", "Proyek", "Ulangan Harian", "UTS", "UAS", … | `admin_sekolah` |
| `grade_weight_config` | The **weight** of each component for one subject in one academic year | `admin_sekolah` |

Components are a per-school list; weights are per **subject per year**. A school
that wants one policy everywhere sets the **default distribution** once (migration
058, stored as `grade_component_type.default_weight`) and every subject follows it
until the admin types a custom row for that subject. Precedence, spelled out:

```
custom subject/year config   →   school default   →   simple mean
```

A default is a policy only when it sums to 100; a half-filled default is ignored
and the subject falls back to the simple mean.

**Why per subject per year?** Because a weight is the thing a mark *already
reported* was decided by. Moving it after the fact would silently change a
historical result, so the year is part of the key (the same discipline the KKM
uses, migration 051).

## How the Final Mark is computed

Implemented once in `app/services/grade_weighting.py` — `compute()` — and read by
every surface, so the teacher's table, the XLSX export, the PDF **and the pupil's
own dashboard** cannot disagree. The teacher/exports read it through
`subject_finals()` (one subject, every pupil); the pupil's page reads it through
`finals_for_student()` (one pupil, every subject), which is the same arithmetic and
the same effective-policy read, batched because the page asks the transpose of the
roster — two reads for ten subjects instead of twenty.

```
weights = config_for(school, subject, year)          # {} when none is configured
if not weights:
    mode   = "simple"
    final  = mean(every score)                       # the pre-feature behaviour
else:
    mode   = "weighted"
    for each configured component:
        average = mean(the pupil's scores in that component, 0 if none)
        total  += average * weight / 100
    final = round(total, 1)
```

### The policy on a missing component: **zero**, not renormalised

When a component is configured with a weight but the pupil has **no score** in it,
that component counts as **0**. The remaining weights are **not** scaled back up.

Renormalising would quietly raise a pupil's mark for work they never did, and hide
the real gap from the teacher. Zero is the conservative reading; the page tells the
teacher which component is empty so they can fix the cause instead.

### The pupil sees released papers only

The teacher's roster counts **every graded paper** — grading happens before
release. The pupil's own dashboard counts **released papers only**, because a mark
is not official to the pupil until the teacher releases it, which is the rule every
other number on that page already follows. `finals_for_student(..., released_only=True)`
is that difference, stated as a parameter so it is never a silent one: once a paper
is released, the pupil's number and the teacher's number are the same number.

Weighting answers `0.0` for a subject the pupil has no scored paper in. That is
right for a roster, but a hard `0` on the pupil's card would read as a real mark, so
the batched read also returns `scored` (the count of scored rows) and the card shows
"no mark yet" while `scored == 0` — a real zero is still a real zero.

### Papers with no component are ignored, and reported

A scored paper whose component is unset (or set to a component the subject does
**not** weight) contributes nothing to a weighted total. `compute()` counts these
in `untagged`, and the roster shows a small warning badge so the teacher can see
that a paper is not being counted and categorise it.

## The fallback contract

A school that has configured no weights for a subject gets the **simple mean**
back — exactly what the page did before this feature existed. Nothing errors and
no mark is blank. The roster prints a small honest label for that case:

> *"Sekolah belum mengatur bobot nilai untuk mapel ini — Nilai Akhir memakai
> rata-rata sederhana."*

## Filing a paper under a component

In the exam builder (**Komponen Nilai**), a teacher files each paper under one of
the components the school configured for its subject. The field is **optional**:

- a school with no weights configured never sees the field, and papers stay
  uncategorised (they simply fall out of the weighted sum);
- a posted component id is checked against the **school's own** list — a stale or
  foreign id is dropped to `None`, never written (`_resolve_grade_component`).

## The admin screen

`/admin-sekolah/grade-weights` (sidebar → **Nilai Berbobot**):

1. **Komponen Nilai** — add / rename / (de)activate components. Deactivating, not
   deleting, keeps the marks a component already decided intact.
2. **Bobot Default Sekolah** — the school-wide distribution, above the matrix. A
   subject with no custom row follows it, so one policy covers the whole school.
3. **Matriks Bobot per Mapel** — one row per subject, pre-filled from the default
   and labelled *default* until it is saved with its own numbers. Each row's total
   is shown live; **Save stays disabled until the row sums to exactly 100%**. The
   *back to default* button clears the custom row so the subject follows again.
4. **Pratinjau Nilai Akhir** — the preview card, which computes the Final Mark a
   sample set of scores carries under the distribution *currently selected* — the
   school default or a subject's own row, as typed and **before anything is
   saved**. Its arithmetic is the page's own `sgPreviewFinal`, kept equal to
   `grade_weighting.compute` by a guard, so the previewed number is the number the
   roster will compute, including the "missing component is zero" policy. Seeding
   it from a real pupil is described below.

A closed academic year is read-only.

### Seeding the preview from a real pupil

An invented sample answers "what does 80/90 carry?". The admin's better question
is what a *named* learner's record carries out under the distribution being typed,
so the preview can be seeded from one pupil:

1. type at least two characters of a name into **Ambil dari murid** — the search
   returns this school's **active** pupils only, never another school's and never
   an alumni row;
2. click a result, and the sample fields are filled with **that pupil's own
   component means** for the subject selected in the preview, read through this
   school's exams for that subject and the running year;
3. the badge names the pupil while the sample is exactly their record. Editing any
   field drops the badge — an edited sample is no longer that pupil's marks;
4. changing the preview's subject re-reads the same pupil for the new subject
   rather than leaving another subject's marks under this subject's weights.

A component the pupil has **no** mark in is left blank rather than filled with 0,
so the preview shows the same gap the roster will: the module's *missing component
is zero* policy, applied where the admin can see it. Marks are read from every
graded paper, released or not, because this is the teacher's roster arithmetic —
the pupil's own dashboard is the view that shows released papers only.

The school default has no subject to read from, so seeding asks for a subject
first instead of guessing one. The doors are `admin_sekolah` only, take the school
from the session rather than from the URL, verify the pupil and the subject belong
to it, and are **reads** — a preview never saves.

### Comparing one pupil across their subjects

A distribution is changed for a **whole school**, not for one subject at a time:
the default covers every subject that never saved its own row. One mark on its own
therefore cannot tell an admin whether a change is fair — so seeding a pupil also
opens a comparison of that learner across the subjects they sit, in the same card:

| Column | What it is |
|---|---|
| **Mapel** | the subjects this pupil has a **scored** paper in, in name order |
| **Tersimpan** | what each subject reports today, from the server's own arithmetic |
| **Dengan bobot ini** | the page's rule re-run over that learner's component means under **the weights currently in the matrix**, saved or not |
| **Selisih** | the difference, coloured by direction, or *tetap* when nothing moves |

This is what makes a change visibly between subjects: retyping the **school
default** moves every subject that follows it and leaves a subject with its own
row untouched, while editing one subject's row moves only that row.

The comparison is a **read** (`/admin-sekolah/grade-weights/pupil-subjects`,
`admin_sekolah` only, scoped to the session's school, refused with 404 for a pupil
that is not this school's) and it is loaded **once per pupil** — it does not depend
on the selected subject, so changing the preview's subject re-reads the sample but
not the table.

Three rules keep it honest:

- **Every row comes from the roster's arithmetic.** The saved figure is
  `grade_weighting.finals_for_student(..., released_only=False)` — the same call the
  teacher's table makes — so the comparison cannot disagree with it. It therefore
  **counts marked-but-unreleased papers** (the pupil's own dashboard does not); the
  two reads differ for that one stated reason and no other.
- **A subject with no mark is absent, not 0.** A row of empty cells would read as a
  zero, so only subjects the pupil has a scored paper in appear; a pupil with no
  marks anywhere gets a sentence instead of a table.
- **The typed column refuses to invent a number.** It is shown only when the
  weights in the matrix form a distribution. With no weights at all the mark falls
  back to the mean over the learner's **papers**, and the page holds their
  per-component means — a mean of those means is a different number, so the cell
  shows `—` rather than a plausible-looking wrong figure. A paper filed under no
  component is flagged on its row (*tanpa kategori*), because it cannot reach a
  weighted mark and would otherwise quietly shrink the list of what counted.

### Judging a change against the class, not one learner

The comparison above answers what a change does to **one** pupil. A mark is changed
for a class, though — the default binds every subject that never saved its own row —
so the same card carries **Dampak ke kelas**: a button that asks the server which
pupils would move, and by how much, before anything is saved.

| Column | What it is |
|---|---|
| **Murid** | the pupil, with their class, and a *sudah terbit* chip when a mark they can already open is one of the ones moving |
| **Mapel** | the affected subject |
| **Tersimpan** | the mark the roster reports today |
| **Dengan bobot ini** | the same rule under the weights now in the matrix, saved or not |
| **Selisih** | the movement, coloured by direction |

Which subjects count as **affected** is derived server-side, never named by the
page: a **default** change covers every active subject that follows the default (a
subject with its own row is not touched by it, so it is not weighed), and a subject
change covers that one subject. The headline counts are for the whole cohort while
the table shows the largest movements — the display cap never shrinks the count.

Three rules decide whether the numbers can be believed:

- **only pupils who have a mark** in an affected subject are counted — a policy
  cannot move a mark that does not exist;
- **a movement below the threshold is not a movement** (both marks are rounded to
  one decimal, so the floor only keeps float noise out);
- **a released mark is flagged**, because a change to a number the pupil can already
  open is the one that needs a decision, while a change to an unreleased draft is
  ordinary work in progress.

It is a deliberate button rather than a live panel: it weighs every pupil's graded
papers in the affected subjects, which is the one heavy read on this page, and an
answer computed before the next keystroke is labelled *stale* instead of being left
to read as current. That read pages past PostgREST's own 1000-row window and **says
so** when it hits its row cap rather than presenting a truncated cohort as a
complete one. Like the preview's other readbacks it is a **GET**: it computes and
stores nothing.

## Where the number appears

- **`/teacher/exams/new`** — the **Komponen Nilai** picker offers every active
  category for the chosen subject (a subject on the school default included), so a
  paper can be filed under any component — not only the first one.
- **`/teacher/students`** — the teacher's grade table: **Kelas, Nama, NISN, Nilai
  Akhir**, with a per-component breakdown on the info button, KKM colouring, search
  and sort, and **XLSX/PDF exports** that carry one column per component.
- **`/student/dashboard`** — each subject on the "Mata Pelajaran Kelas Anda" card
  shows the **same weighted Final Mark** the teacher's table reports for it (a
  "Berbobot"/"Weighted" chip when the school has configured weights, "Rata-rata
  sederhana"/"Simple mean" when it has not), over the **released** papers of the
  running year. It follows the page's score switch like every other mark.
- Scoping: a guru sees only the pupils in the classes they teach **for the selected
  subject** (from their own `teacher_assignments`); the school admin sees the whole
  roster. A requested subject the teacher does not teach falls back to their
  default rather than leaking another subject's roster.

## Roles

| Action | Who |
|---|---|
| Manage components & weights | `admin_sekolah` (scoped to its own school) |
| Preview a distribution, seed it from a pupil's marks, compare that pupil across their subjects, and see which pupils the change would move | `admin_sekolah` (its own school's subjects and pupils only) |
| Tag a paper with a component | the paper's teacher / school admin |
| Read the grade table & exports | the subject's teacher / school admin |

A teacher can never change what a subject mark means — that is the school admin's
write, on a route guarded by `@admin_sekolah_required` and
`@require_school_access("subjects", "subject_id")`.

## Related

- `docs/DATABASE.md` — table columns and the unique constraints.
- `docs/RBAC.md` — who may configure and who may read.
- `app/services/grade_weighting.py` — the single source of the arithmetic.
