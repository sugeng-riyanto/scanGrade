# AI essay grading — a suggestion, never a mark

ScanGrade can ask a language model for a suggested score on an essay question. It
never lets that model decide a mark. This document is the contract between those two
sentences, and every paragraph of it is enforced by a test in
`tests/unit/test_ai_grading_guard.py`.

## The shape of one suggestion

```
teacher presses "Koreksi AI"
        │
        ├─ POST /api/ai/suggest-essay ──► row in ai_grading_suggestion  status=queued
        │                                   (202 returned in milliseconds)
        ▼
   Celery worker  ai.grade_suggestion
        │  reads submission.answers + exams.question_weights from the database
        │  builds the prompt in app/services/ai_prompt.py
        │  calls the configured provider
        │  validates the reply against the question's own points
        ▼
   status=ready (or failed, with a sentence a teacher can act on)
        │
        ▼
GET /api/ai/suggestion/<id>     the marking page polls until ready
        │
        ├─ "Terapkan" ──► POST …/apply   status=applied, the number goes back to the form
        └─ "Abaikan"  ──► POST …/reject  status=rejected, kept as evidence
                                   │
                                   ▼
                    the teacher's own save writes the mark
```

**The last line is the whole design.** `apply` writes a decision and returns a
number; it does not touch `submissions`. The teacher's form holds that number, they
may change it, and the save route they already use is what records it. A teacher who
accepts "8" and types "7" gets 7, because the suggestion row is not a source of truth
for a mark.

## The fences

### 1. The answer is data, and it is fenced as data

`build_grading_prompt` puts the pupil's text between `<JAWABAN_MURID>` and
`</JAWABAN_MURID>`, and the closing marker is written by the server. `plain_text`
folds `<` and `>` into guillemets *before* the fence is assembled, so a pupil who
types the closing tag in an essay has written prose, not ended a block.

A fixed system turn says, in as many words, that everything inside that block is
material to be marked and that any instruction inside it is to be ignored. The system
turn is a constant — no teacher field, no question text, no pupil text is
interpolated into it.

### 2. The teacher's prompt is a rubric, not a system prompt

The teacher's own grading prompt (from `/teacher/ai-settings`) is fenced behind a
*different* marker and placed in the **user** turn, under a label that says what it
may do: adjust how an answer is judged. It cannot restate the output contract,
because the contract is not in the turn it can reach.

The prompt is per **teacher**, not per subject. The settings page has said "you can
write a different one for each subject" since it was written, and no column stores a
subject — that sentence is aspirational, and this document says so rather than
describing a feature the schema does not have.

### 3. The reply is validated before it is stored

`parse_suggestion` requires a JSON object with a numeric score. Then:

| what the model did | what happens | what is recorded |
|---|---|---|
| `score: 999` on a 10-point question | clamped to 10 | `score_above_max:999>10` |
| `score: -5` | clamped to 0 | `score_below_zero:-5` |
| `confidence: 95` | read as a fraction, capped at 1.0 | `confidence_out_of_range:95` |
| no readable score at all | the suggestion **fails** | a sentence, and a retry |
| `{"nilai": 8}` under an Indonesian rubric | accepted | `score_field_alias` |

A refusal is a retry; a zero is a mark on a child's paper. Those are different
outcomes and the parser does not conflate them.

### 4. The provider call is not in the request

A teacher's request returns `202` with an id. The model answers in a Celery worker
(`app.services.ai_grading.grade_suggestion_task`). On a 1-vCPU box, holding a
gunicorn worker open for an external API's latency is how one slow provider becomes
a school-wide outage.

If the worker answers nothing within 90 seconds, the page says so. If the provider
refuses the key, runs out of quota, or times out, the row records one of three
sentences (`_provider_message`) rather than a generic failure.

## Points come from the question, not from a constant

`question_points` reads `exams.question_weights["<index>"]`, skipping the reserved
`_scheme` key. The old bulk path hardcoded `max_score = 100 # default, could be from
question_weights`, so even a clamp would have clamped to the wrong ceiling. The
marks used are the marks the question is actually worth.

## What leaves this app

The payload sent to a provider contains: the question text, the answer rubric, the
teacher's criteria, and the pupil's answer text. It does **not** contain a name, an
NISN, a class, a school, or an id of any kind — the suggestion row is linked to the
work by `submission_id`, which is read on this side.

The row stores `answer_chars` (how much of the answer was sent) rather than a second
copy of the text, and the answer is clipped at 6000 characters with the clipping
reported as `answer_truncated`, because a mark given to nine tenths of an essay needs
to say so.

**A school's policy needs to cover this.** See `docs/COMPLIANCE.md`.

## Subscription

Both the request and the decision routes carry `@require_subscription("ai_grading")`.
`trial` and `basic` do not include it; `pro` and `enterprise` do. A school without
the feature gets `403 {"error": "FEATURE_LIMIT", "message": …}` naming the upgrade,
and nothing is queued — so nothing is billed.

The polling route is deliberately *not* gated: a school whose plan lapses mid-marking
should be able to read a suggestion it has already paid for.

## For teachers

- **Write criteria, not commands.** "Award full marks for naming three causes and
  explaining each" works; "give 8 out of 10" does not, and the system turn will
  ignore it.
- **The AI's number is a starting point.** Press *Terapkan* to put it in the field,
  then change it if you disagree — your save is what counts.
- **A confidence of 60% means the model is unsure.** That is the moment to read the
  answer yourself, not to accept it faster.
- **Nothing is written to a pupil's result until you save.** If you close the page
  without saving, nothing happened.
