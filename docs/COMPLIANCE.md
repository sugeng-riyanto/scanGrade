# Compliance & Data Privacy

## Data Isolation

Schools are isolated by NPSN (unique 8-12 digit school identifier). Each school's data is completely separate:
- Teachers only see their school's students/exams
- Students only see their school's assignments
- Admins only manage their own school

## Audit Logging

All CRUD operations are logged to `audit_logs` table:
- Who (user_id)
- What (action + entity_type)
- When (created_at)
- Changes (old_data + new_data as JSONB)

## Student Data Protection

- Minimal data collected: name, NISN, email
- No sensitive personal data stored
- Passwords are hashed by Supabase Auth
- API keys encrypted at rest (teacher_ai_keys)

## Session Security

- HttpOnly cookies (not accessible via JavaScript)
- SameSite=Lax (CSRF protection)
- Secure flag in production (HTTPS only)
- Session expires on browser close

## Data Retention

| Data | Retention |
|------|-----------|
| Violation logs | 1 year |
| Audit logs | 3 years |
| Submissions | Duration of school subscription |
| Student accounts | Until deleted by admin |
| Teacher accounts | Until deleted by admin |

## Right to Deletion

- Admin can delete students/teachers at any time
- Super admin can delete entire schools
- Deletion removes auth user + profile + all related data

## Backups

Supabase provides automated daily backups (14-day retention on free tier).

## Third-Party AI Processing (AI Essay Grading)

AI essay grading sends content to an external model provider chosen by the
school's teacher (Gemini, OpenAI, DeepSeek, Groq, or a custom OpenAI-compatible
endpoint). This is a **data processor** relationship and it is recorded here
because the answer is the pupil's own work.

**What is sent**

- the question text and marking rubric;
- the teacher's rubric for that subject;
- the pupil's essay answer as plain text (after the toolbar HTML is stripped).

**What is not sent**

- the pupil's name, NISN, class, email, account id, or any other identifier.
  The correction is linked to the pupil inside ScanGrade by `attempt_id`, not at
  the provider.

**When it is sent**

Only when a teacher asks for a suggestion, or when a teacher starts a bulk
request for an exam they manage. Nothing is sent while a pupil is sitting an
exam, and no background job grades a paper nobody asked about.

**What is stored**

`ai_grading_suggestion` holds the suggested score, reason, confidence, the
prompt version used, the provider/model name and the outcome. The request and
reply are also written to `ai_grading_logs` (the existing audit table) with the
answer text in `prompt_sent`, because a teacher must be able to explain why a
suggestion said what it said. That table is service-role only.

**Retention and the privacy policy**

Provider-side retention and training use are governed by the provider's own
terms, not by this repository. The 13-article privacy policy must therefore name
the AI essay-grading feature as a third-party processing purpose, the categories
of data above, and the fact that a teacher can disable it by removing their API
key (or that the school can decline the `ai_grading` tier). Schools that do not
buy a tier with `ai_grading` cannot use the feature at all: the endpoints are
gated by `@require_subscription("ai_grading")`.

**Pupil rights**

Because no pupil identifier is sent, an access or erasure request is handled in
ScanGrade: deleting the submission (or the pupil) removes the linked suggestions
through `ON DELETE CASCADE`, and the provider is never given a key to re-identify
the pupil from the payload alone.
