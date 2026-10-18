# CRUD egress budget

Supabase Usage showed 1.36 GB of PostgREST egress on 6 October 2026
(99.1% of that day's transfer). The dashboard covered all projects; it does
not establish which query or project caused that transfer.

## Changes

- Student draft INSERT and UPDATE use `returning="minimal"`. The route consumes
  no returned row; the database need not echo the answer JSON and PNG canvases.
- Teacher exam updates, including autosaves and legacy-schema fallback, use
  minimal responses. Exam creation still returns a representation because its
  generated ID is required by the create-then-update flow.
- Teacher exam cards read explicit metadata, class assignments, question types
  and answer keys. They retain warning behavior without fetching question
  canvases, question text or stimuli. Teacher and school-admin filters are retained.
- The existing guarded exam-delete flow uses minimal responses for all five
  deletions, avoiding a download of every deleted submission's answers.
- Light draft merges preserve existing canvases even when the incoming question
  omits `pages`; previously a newly created page map was never attached.

No global write default is changed: writers that inspect returned rows must keep
those rows. No new cross-request cache or migration is introduced. Answer reads
needed for merging and revision checks remain in place.

## Verification and rollout

`tests/unit/test_crud_egress.py` drives the actual PostgREST query builders through
an HTTP mock transport, including empty HTTP 204 write responses, preservation of
a 100 KB synthetic canvas, tenant filters and card warnings. It makes no calls to
a live project. Existing route, autosave, deadline, ownership and retry tests are
also required.

After CI passes and the change is released, compare daily PostgREST egress for
ScanGrade alone at similar activity levels. Record request counts and typical
answer sizes; do not claim a percentage reduction from synthetic tests. These
changes reduce future transfer and cannot erase usage already recorded.

Further work requires measurement: pagination must preserve search/filter behavior,
large answer projections may need schema changes, and shared caching must retain
school/user boundaries and invalidate after writes.
