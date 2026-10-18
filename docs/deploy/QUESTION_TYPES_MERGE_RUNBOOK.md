# Runbook — reconciling `feat/new-question-types` onto main

Two branches had each built a piece of the same feature set, and this page is the
record of putting them together: **which side won each file and why**, the checks that
were run, and the order they have to run in. The state below is the one that shipped;
everything under "redo" is the recipe for the next question-type branch, or for this
one if a tree has to be rebuilt.

The *why* of the feature is in
[docs/features/new-question-types.md](../features/new-question-types.md); the *why* of
the gates is in [docs/AUTO_DEPLOY.md](../AUTO_DEPLOY.md). This page does not restate
either.

## The state this page describes

| thing | value |
|---|---|
| merge commit | `675b56f` — *merge: the numeric, matrix and stimulus question types onto main's AI marking* |
| parents | `a2876cc` (main) · `bca30d3` (`feat/new-question-types`) |
| merge base | `e656a8e` |
| pushed | `a2876cc..675b56f  HEAD -> main` |
| database | migration `069_exam_stimuli.sql` applied; `exams.question_stimuli` is in the live schema (§6) |
| `deploy/theme_gate.sh` | exit **0**, run twice (standalone, then from the pre-commit hook over the staged tree) |
| `tests/unit` | **10 523 passed, 5 skipped**, one timing flake that passes alone (§9) |
| generated | `tailwind.css` 123 928 bytes · i18n floors re-recorded (§7) |
| uncommitted at the time of writing | this page, and the pointer to it appended to `docs/features/new-question-types.md` — §10 says why `git add -u` misses them |

Every command below is written `python …`. On the Windows checkout that is
`./.venv/Scripts/python.exe` — a bare `python` on `PATH` is not this app's interpreter
and has no `pytest`, and the first `pytest` run here failed with exactly that.

## 1. The rule that decides every conflict

Three sentences. Apply them in this order and most conflicts resolve themselves.

1. **The AI marking path is main's.** It is newer, it is the one the routes serve, and
   the branch's copy was an earlier attempt at the same feature. Anything that exists
   only to talk to a provider — the client, the prompt builder, the settings table, the
   suggestion workers, the polling endpoints — is main's, and the branch's duplicate is
   **deleted**, not merged.
2. **The question types are the branch's.** `numeric_short_answer`, `matrix`, the PGK
   complex-MC kinds and `exam_stimuli` exist only there. Where main has no equivalent,
   the branch wins outright.
3. **Where the two changed different halves of one file, neither wins: both stay.** A
   marking page that lost main's AI panel or the branch's answer panels is broken in a
   way that renders, which is why §4's table names the half each file keeps.

A file that is *identical to main* in the merge result is a decision, not an accident —
and a file that is identical to main **while the branch had changed it** is the shape of
a silently dropped feature. §2's audit is what finds those.

## 2. Step 0 — where you are

```bash
git fetch origin
git log --oneline -1 origin/main            # 675b56f
git rev-parse --short HEAD^1 HEAD^2          # a2876cc  bca30d3
git status --short                           # empty, at the merge: the only entries now
                                             # are the two documents named in the state table
```

To redo the merge from scratch:

```bash
git switch -c merge/question-types-onto-main origin/main
git merge --no-ff feat/new-question-types
```

Then resolve with §4, and **audit before you commit**:

```bash
BASE=e656a8e        # the recorded merge base — see the warning under this block
for f in $(git diff --name-only "$BASE" bca30d3); do
  if git diff --quiet a2876cc -- "$f"; then
    if [ -e "$f" ]; then echo "SAME-AS-MAIN  $f"; else echo "DROPPED      $f"; fi
  fi
done      # `if`, not `&&`: the loop must not exit 1 just because its last file differed
```

**Do not compute the base with `git merge-base origin/main feat/new-question-types`.**
Once the merge is on `main`, that returns the *branch tip* — `main` is a descendant of
it — so the loop walks zero files and prints nothing, which reads exactly like a pass.
This was measured here: computed, the audit printed 0 lines; with `e656a8e`, 9.

The right-hand side is omitted on purpose: `git diff a2876cc -- "$f"` compares the
recorded main commit with the **working tree**, so the same command works mid-merge
(uncommitted) and after the commit (`675b56f`, clean tree). `--cached` would only answer
before the commit, and `origin/main..HEAD` would only answer after it.

Everything it prints must be a file §4 says is main's, dropped, generated, or a doc. Any
other line is a branch feature the resolution threw away. Today it prints nine, and the
two labels are the point:

```
SAME-AS-MAIN  app/services/ai_grading.py
DROPPED       app/services/ai_prompt.py
SAME-AS-MAIN  app/services/ai_service.py
DROPPED       app/static/js/ai-suggestion.js
SAME-AS-MAIN  docs/COMPLIANCE.md
DROPPED       docs/features/ai-essay-grading.md
DROPPED       supabase/migrations/068_ai_grading_suggestion.sql
DROPPED       tests/unit/test_ai_grading_guard.py
DROPPED       tests/unit/test_ai_prompt_crud.py
```

`git diff --quiet A B -- path` answers "identical" for a path **absent on both sides**,
so without the second test this audit reports a deliberately deleted file as main's copy.
That is the whole reason the `[ -e "$f" ]` is there, and the reason §4 has a `dropped`
group at all: six of these nine are the branch's own copies of something main already had
under another name, and only a reader can confirm each drop was on purpose.

The five AI modules the branch never touched do not appear at all, because the loop only
walks files the branch changed.

This is exactly how the three marking templates were caught: `grading_queue.html`,
`grade_detail.html` and `grade_question.html` came out identical to main, and the
branch's own suites went red on `pointsGain is not defined` /
`answerText is not on the Alpine object any more`.

## 3. Step 1 — a real three-way merge, per file

`git merge-file` is the tool, because the index no longer holds the merge stages once a
path has been resolved:

```bash
BASE=e656a8e                        # recorded, for the reason in §2
F=app/templates/teacher/grading_queue.html
git show "$BASE:$F"      > .git/m.base    # scratch inside .git, not /tmp:
git show a2876cc:"$F"    > .git/m.ours    # Windows Git Bash's /tmp is not
git show bca30d3:"$F"    > .git/m.theirs  # visible to the venv's Python
git merge-file -p .git/m.ours .git/m.base .git/m.theirs > .git/m.merged
grep -c '^<<<<<<<' .git/m.merged    # conflicts left to choose, usually fewer than five
```

Resolve each conflict by the rule in §1, then **read the whole file for leftovers the
markers do not cover**: a merged Alpine object can end up with two autosave paths, a
script tag can point at a file main deleted, a function can lose its closing brace
where a conflict ended mid-body. What was actually done for each file is §4, and the
specific leftovers are §5.

## 4. The per-file table

`kept` is what the commit carries. The side is verified, not remembered, and it is
checked against the **merge commit**, not a working tree that later commits edit: a file
marked `main` is byte-identical to `a2876cc` in `675b56f`, one marked `branch` is
byte-identical to `bca30d3`, and `both` differs from both parents — which is also the
cheap way to audit this table:

```bash
BASE=$(git merge-base a2876cc bca30d3)          # e656a8e
for f in $(git diff --name-only "$BASE" bca30d3); do
  if   git diff --quiet a2876cc 675b56f -- "$f"; then echo "main    $f"   # same as main
  elif git diff --quiet bca30d3 675b56f -- "$f"; then echo "branch  $f"   # same as branch
  else                                                 echo "both    $f"; fi
done | sort -u | cut -d' ' -f1 | sort | uniq -c
# 9 main · 17 branch · 22 both   out of the 48 paths the branch changed
```

Nine of the branch's 48 paths came out as main's, and that nine **is** §2's audit — six
dropped on purpose (§4's `dropped` group), three main's own copies. A branch-changed file
that came out identical to main is the shape of a dropped feature; the six are why.
The 22 `both` paths are the rows §4 spells out half by half, and the 17 identical to the
branch are the question types, their suites, and the release-claim suite that came with
the branch's `deploy_status` work. `tests/unit/test_merge_runbook.py` re-runs the loop
above against these refs and fails if a row's label stops matching, or if a classified
path has no row — which is how that seventeenth row was found to be missing.

### main wins — the AI marking path

| file | kept | note |
|---|---|---|
| `app/services/ai_grading.py` | main | the branch's copy is an earlier version of the same module |
| `app/services/ai_grading_prompt.py` | main | replaces the branch's `ai_prompt.py`; the fence and the rubric section live here |
| `app/services/ai_grading_settings.py` | main | |
| `app/services/ai_grading_suggestion.py` | main | |
| `app/services/ai_grading_tasks.py` | main | |
| `app/services/ai_service.py` | main | |
| `app/services/grading_assist.py` | main | unchanged by the merge; the branch's `ai_prompt` callers were repointed here |
| `supabase/migrations/068_ai_essay_grading.sql` | main | the branch's own migration for the same table was numbered 068 too, so it could not land beside this one — it is dropped, below |
| `docs/features/AI_GRADING.md` | main | the branch's `ai-essay-grading.md` described the same feature and is dropped |
| `docs/COMPLIANCE.md` | main | |
| `tests/unit/test_ai_essay_grading.py` | — | main's own; pins `data-ai-panel`, `requestAiSuggestion`, `AI_ROWS` |

### branch wins — the question types

| file | kept | note |
|---|---|---|
| `app/services/question_types.py` | branch | numeric, matrix, PGK complex-MC, stimuli, `validate_answer_map` |
| `app/services/mark_scheme.py` | branch | default buckets and points for the new kinds |
| `app/services/grading_service.py` | branch | |
| `app/templates/teacher/answer_keys.html` | branch | the numeric/matrix key editors |
| `supabase/migrations/069_exam_stimuli.sql` | branch | new; already applied (§6) |
| `docs/features/new-question-types.md` | branch | new. The one exception to "byte-identical": a pointer to this runbook was appended afterwards, so the file no longer matches `bca30d3` — the feature is unchanged, the paragraph is new. |
| `tests/unit/test_new_question_types.py`, `test_mark_agreement.py`, `test_grading_queue_canvas.py`, `test_essay_score_autosave.py`, `test_essay_answer_reaches_teacher.py`, `test_unlock_code_visibility.py`, `test_csrf_dynamic_forms.py` | branch | the branch's own contracts for the above |
| `tests/unit/test_answer_key_source.py`, `test_core.py`, `test_objective_types.py` | branch | extended for the new kinds |
| `tests/unit/test_release_claim.py` | branch | the branch's release-claim suite — `served == released` as a question of its own, and the two states where it comes apart from `served == held`; it arrived with the branch's `deploy_status` work, which is why it is here and not among the question types |

### both — one file, two halves

| file | kept | what each half is |
|---|---|---|
| `app/routes/teacher.py` | both | main's `/ai-settings` + suggestion apply/ignore; the branch's answer-key, queue, preview and question-type routes. The branch's four prompt-CRUD routes were **deleted** with `ai_prompt.py`. |
| `app/routes/api.py` | both | main's AI endpoints untouched; the branch's `validate_answer_map` call on `/api/student/sync-draft` (drops a matrix row or checkbox index the question does not have) |
| `app/routes/student.py` | both | main's window/away work + the branch's stimulus and control payload |
| `app/templates/teacher/grading_queue.html` | both | main's `_pollAi`/`_pollBulk` and "Laporan" link; the branch's two answer panels, `pointsGain`, page pager, review badge |
| `app/templates/teacher/grade_detail.html` | both | main's AI panel (`AI_ROWS`, `requestAiSuggestion`, `applyAiSuggestion`, ignore) + away/late cards; the branch's `/preview` `calcFinal`, `_scoresSignature`, percentage→points box, full-answer block, review queue |
| `app/templates/teacher/grade_question.html` | both | main's polling AI; the branch's save-and-advance presets, points hint, autosave |
| `app/templates/student/take_exam.html` | both | main's away grace, fullscreen and rotation work; the branch's numeric/matrix/PGK controls, stimulus panel, shuffled groups |
| `app/templates/teacher/exam_form.html` | both | main's anti-cheat, window and duration card; the branch's question manager, stimuli editor, key serialisation |
| `app/templates/teacher/ai_settings.html` | both | main's page; **six** cloned/subject forms gained a `_csrf_token` field of their own (§5) |
| `app/templates/base.html` | both | main's chrome; the branch's **submit-time** CSRF stamper (a load-time sweep cannot see an `x-for` clone) |
| `app/services/deploy_status_service.py` | both | main's newer readings; `release_not_served` added to `REASON_KEYS` (§5) |
| `app/templates/super_admin/deploy_status.html` | both | main's cards + the branch's release-claim card |
| `docs/AUTO_DEPLOY.md` | both | main's page + the branch's *Held and released are two different questions* section (`last_release_state`, the five comparisons). This row used to read `main`; it is not — the doc is where the branch's release-claim work is described, so identical-to-main here was the one mislabel in the table. |
| `app/templates/guide/skor.html`, `app/utils/breadcrumbs.py`, `app/static/css/theme.css` | both | branch copy/table and breadcrumb additions over main's structure |
| `app/templates/tutorial_guru.html`, `tutorial_murid.html` | both | **repaired**, see §5 — the branch's new kinds had neither an icon nor an answer instruction |
| `tests/unit/test_deploy_alerts.py` | both | main's fixture + the branch's `last_release` keys |
| `tests/unit/test_student_exam_kind_rendering.py` | both | contract widened, see §5 |
| `tests/unit/test_subscription_quota.py` | both | route names corrected to where the routes actually live, see §5 |
| `tests/unit/test_year_lock.py` | both | one exemption added, see §5 |
| `tests/unit/test_answer_key_marks.py` | both | the branch's suite; its "the reference lands where the AI reads it" case now reads main's `ai_grading.py` (the variable is `rubric`, not `rubrics`) and still refuses a column that is read but not selected |
| `tests/unit/test_seb_refusal_ranking.py` | both | clock made relative, see §5 — **not** a merge defect |
| `AGENTS.md` | both | main's newer paragraphs + the branch's four appended ones (question types, release claim, CSRF-at-submit, the mark's single arithmetic) |

### dropped — branch-only, superseded by main

| file | why |
|---|---|
| `app/services/ai_prompt.py` | duplicate prompt builder; `ai_grading_prompt.py` is the one the routes call |
| `app/static/js/ai-suggestion.js` | the `SGAi` client; main replaced the synchronous endpoints with `POST /api/ai/grade-essay` + polling, and the file is deleted on main |
| `tests/unit/test_ai_grading_guard.py`, `tests/unit/test_ai_prompt_crud.py` | they test the two files above |
| `docs/features/ai-essay-grading.md` | the branch's write-up of the same feature; main's `docs/features/AI_GRADING.md` is the page that describes what shipped |
| `supabase/migrations/068_ai_grading_suggestion.sql` | the branch's own `ai_grading_suggestion` migration; main had already taken `068` (`068_ai_essay_grading.sql`), so it cannot be applied beside it. Main's file is a **superset**: it creates the same `ai_grading_suggestion` table plus `ai_grading_prompt`. Dropping the branch's is what keeps the numbering honest — §6 lists the only column this merge actually added. |
| `tests/unit/test_port_change_harness.py` | **main had already removed it** before this merge, and the branch never changed it, so the merge honours the deletion. If a `deploy/port_change.py` run reports `stale` with nothing failing, this file is why. |

### generated — never resolved by hand

| file | how |
|---|---|
| `app/static/css/tailwind.css` | `npm run css:build` after the last template edit |
| `deploy/i18n_baseline.json` | `deploy/i18n_coverage.py --write-baseline`, deliberately (§7) |

## 5. What the merge exposed, and how each was repaired

Each row is a check that failed **after** the merge and the cause it pointed at. They
are worth reading before the next one, because none of them was the conflict itself.

| symptom | cause | fix |
|---|---|---|
| `TypeError: Object of type Undefined is not JSON serializable` rendering `exam_form.html` (12 errors in `test_exam_edit_actions`, plus `test_duration_rule`, `test_exam_builder_controls`) | the branch added `exam.question_stimuli` to a `tojson` filter; a test fixture's exam dict has no such key, and Jinja's `Undefined` will not serialise | `((exam.question_stimuli if exam else {}) or {})` is what gets `tojson`-ed, in `exam_form.html`, with the same guard in `take_exam.html` |
| queue cards printed `Bobot: 0`; `test_the_card_carries_the_points_the_mark_is_computed_with` | `parts = exam_scoring.exam_parts(exam)` was computed at the top of `api_grading_queue` (`app/routes/teacher.py`) and then never read: the per-question rows carried no `weight` and no `canvas_pages` | both fields added to each row of `per_question` in that route — `"weight": parts.weights.get(ei, 0)`, and the count the existing `_drawn_pages` already had |
| `pointsGain is not defined`, `answerText is not on the Alpine object any more` | the first resolution took main's whole template for the three marking pages | §3, resolved per file |
| `the copy is Indonesian because the page pins it`: a `t('` pair on `grading_queue.html` | main had added pairs to a page that declares `content_lang = 'id'` — the English half can never render there | pairs replaced with the Indonesian string; **this is why the i18n floors were re-recorded**, see §7 |
| `every cloned key form carries the field` | `/teacher/ai-settings` renders its key rows from an Alpine `x-for`; the token was stamped once at load, so a clone had none and Delete answered `CSRF token invalid` | `_csrf_token` written into all six POST forms, on top of the branch's submit-time stamper in `base.html` |
| `every type has an icon` / `the student tutorial explains every type` | `numeric_short_answer` and `matrix` were added to `PICKER_TYPES` with no entry in either tutorial's `_type_icons`, and no `_type_how` line | `fa-hashtag` and `fa-table-cells-large` in both maps, two `_type_how` entries in `tutorial_murid.html` |
| `the template invents keys: ['release_not_served']`, `a key with no sentence ships as a blank line` | main's `REASON_KEYS` (the verdict vocabulary the deploy page must have a sentence for) did not know the branch's release-claim verdict | `release_not_served` added to the set |
| `a test that was sent is confirmed on the page` → `KeyError: 'last_release'` | the merged test kept the branch's assertion but main's fixture | `last_release` / `last_release_file` restored in `report_of()` |
| `assert '/preview' in calcFinal` | main's page still summed the paper in the browser | the branch's `_scoresSignature` + `/preview` `calcFinal` kept, main's browser-side copy dropped |
| `ai_suggestion_apply` not gated | main's two decision routes (`/apply`, `/ignore`) carried no entitlement check | `@require_subscription("ai_grading")` on both, and `test_subscription_quota` now names the routes where they live (`api.py` for grade-essay/bulk, `teacher.py` for apply/ignore) |
| `write route is locked or excused with a reason`: `/grade/<id>/preview` | the branch added a POST route that writes nothing | `EXEMPT` entry in `test_year_lock.py` with the reason (it reports what a save *would* score) |
| `public_options hands the pupil a structure a pairing can ride in` | the test's rule ("every value is a list of strings") predated the matrix row `{text}` and the `select`/`mode` scalars the pupil controls need | rule restated as **leaf-flat** (no nested mapping except `text`, no list of containers) — the intent, "no pairing and no answer field", unchanged |
| `TestThePapersWorthPreparingFor::test_the_scheduled_paper_comes_before_the_one_already_open` (`tests/unit/test_seb_refusal_ranking.py`) | **not a merge defect**: the fixture's window literals were `2026-10-16`, and the wall clock had passed them — so the "before" paper was no longer before anything | the file's `_NOW` is `datetime.now(timezone.utc)` now and its two hard-coded starts are `LONG_AGO` (l. 553-555); the literals the file still carries are future windows, which is why they are allowed to stay |

## 6. Database — already done

The only schema change in this reconciliation is the stimulus column, and **it is
already applied to the live project** (`roshkbzgfzpfedowozfo`). Recorded so a later
session does not apply it twice or assume it is pending:

```bash
python deploy/schema_contract.py --require-applied   # what the gate runs
echo "exit: $?"                                      # 0
# schema contract: OK  -  every table and column this repository declares is in the
#                        live schema (89 object(s) served)
```

Exit **1** means a declared object is missing from the live schema, and prints the
`apply_migration.py --commit` line to fix it; exit **2** means it could not ask (no
`SUPABASE_URL` / service key) — a property of the box, not of the release. The live
schema is read through the API on purpose: that is what the app queries, and it needs
no `DIRECT_URL`.

What was applied, and why the order matters:

* `supabase/migrations/069_exam_stimuli.sql` → `ALTER TABLE public.exams ADD COLUMN IF
  NOT EXISTS question_stimuli JSONB NOT NULL DEFAULT '{}'`. Idempotent; safe to re-run.
* The code tolerates the column being absent (the save routes `pop` it before the
  write), so a box that has not applied it still saves exams — without stimuli. The
  correct order is still **DDL first, release second**, because the read side has no
  such tolerance.

Verify by hand if the gate cannot run:

```sql
select column_name, data_type, column_default
  from information_schema.columns
 where table_schema = 'public' and table_name = 'exams'
   and column_name = 'question_stimuli';
```

No row-level repair was part of this merge: no `submissions`, `exams` or
`violation_logs` data was rewritten, so there is no backfill to re-run and nothing on a
fresh project beyond `069` and the migrations before it.

## 7. Regenerate the two artefacts

Both are committed, and both are derived — never hand-edited:

```bash
npm run css:build                                   # tailwind.css, 123 928 bytes
python deploy/i18n_coverage.py --write-baseline     # deploy/i18n_baseline.json
python deploy/i18n_coverage.py                      # gate: exit 0
```

Expected after the write: `145 template(s), 4836 bilingual unit(s), 1419 leftover(s):
77.3% overall`. The write is a deliberate act, taken here for one reason: `grading_queue.html`
is pinned to Indonesian and lost the dead `t()` pairs main had added to it, so its
*numerator* fell. `--write-baseline` is never run by the deploy, and a release that
merely measures above its floor does not need it.

Order matters: **templates first, then CSS** (`css:build`), then the baseline, then the
gate. A CSS build taken before the last template edit is exactly what
`deploy/css_freshness.py` refuses.

## 8. The gate

```bash
set -o pipefail
bash deploy/theme_gate.sh > /tmp/theme_gate.log 2>&1
echo "gate exit: $?"        # 0
```

It runs the theme literals, the compiled-class-name sweep, the cached-stylesheet check,
`content_lang`, the i18n floors, the schema contract (both directions), the render
fixtures, the school-authority sweep, and its own subset of tests — the pre-commit hook
runs the same script, so a commit cannot carry a template the gate has not seen.

Two lines of noise, expected and **not** a failure:

```
deploy/theme_gate.sh: line 478: skip_arm:start: command not found
deploy/theme_gate.sh: line 507: skip_arm:end: command not found
```

Those are bare markers in the script (they are meant to be comments, or to be stripped
by whoever removes that arm for a mutation run); both sides of the merge have them, and
the gate still exits 0.

## 9. The suite

```bash
python -m pytest -q -p no:randomly tests/unit -n 4 > /tmp/suite.log 2>&1
echo "suite exit: $?"       # expected 0; see below
```

Expected: **10 523 passed, 5 skipped**, ~12–13 minutes on 8 cores. `pytest-xdist` is
needed for `-n` and is **not** in `requirements.txt` — install it into the venv for the
run, and do not add a committed dependency for it.

The one failure this run produced is a **timing flake** and is proved so like this:

```bash
python -m pytest -q tests/unit/test_cache_sweep.py            # 24 passed
python -m pytest -q \
  "tests/unit/test_cache_sweep.py::TestTheLiveSittingGuard::test_it_bounds_the_question_by_time_so_it_can_never_block_forever"
```

If it fails **only** under `-n 4`, it measured a wall-clock bound while four workers
were saturating the CPU; if it fails alone too, the bound is a real regression and is
not this merge's.

When a run fails, fix at the cause and re-run the file — not the whole suite. Files that
already passed are not re-run: nothing in this merge changes them, and a 12-minute run
per edit is how a suite stops being run at all.

## 10. Commit and push

The message convention here is a subject that says what the merge *is*, then the
decisions and the numbers — see `675b56f`:

```bash
git add -u
git commit          # the pre-commit hook re-runs deploy/theme_gate.sh over the index
git push origin HEAD:main
```

`git add -u` stages tracked modifications only, and this page — plus the pointer to it in
`docs/features/new-question-types.md` — was written *after* the merge, so it is untracked
and `-u` will silently leave it behind. Name it:

```bash
git add docs/deploy/QUESTION_TYPES_MERGE_RUNBOOK.md docs/features/new-question-types.md
```

Pushing `HEAD:main` rather than switching to `main` is deliberate: other checkouts of
this repository hold `main` open, and the merge branch already tracks `origin/main`.

## 11. What this page does not claim

* The suite is not proven green by a single run: one timing bound is load-sensitive,
  and that is recorded above rather than retried until it passes.
* `pytest-xdist` is a local convenience, not a project dependency.
* Two test contracts were changed on purpose (leaf-flat `public_options`, the relative
  clock in `test_seb_refusal_ranking.py`). Both are explained in §5; neither was
  relaxed to make a defect pass.
* `tests/unit/test_port_change_harness.py` is gone because main had already removed it.
  This page records that; it does not restore it.

Every number in the state table is some command's output — the parents are §2, the gate
is §8, the artefact sizes are §7, the database is §6, the suite counts are §9. Re-run
them rather than trusting this page: if one disagrees, the page is stale and the tree is
the fact, not the other way round. All but the suite counts were re-read while writing
the per-file table, which is how `docs/AUTO_DEPLOY.md` stopped being listed as main's.
