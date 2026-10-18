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

## Third-party AI processing (essay grading)

The AI essay-grading feature sends data outside this application. This section is the
record of what, to where, and on whose instruction — UU PDP art. 20 requires that a
data subject be told before their data travels, and art. 13 requires the purposes be
stated in the privacy notice.

### What is sent

| Sent to the provider | Not sent |
|---|---|
| The pupil's **answer text** for the question being marked | Name, NISN, NIS, e-mail |
| The question text, answer rubric and diagram description | Class, school, NPSN, school id |
| The teacher's own grading criteria for that subject | Any id linking back to a person |

The answer is extracted to plain text first (`app/services/ai_prompt.py`): the
editor's markup is stripped, and the text is clipped at 6000 characters. The request
is linked back to a pupil on this side only, through `submission_id` in
`ai_grading_suggestion`.

### Where it goes, and when

Only when a teacher presses **Koreksi AI**, or asks for a whole exam. The provider is
whichever one that teacher configured in `/teacher/ai-settings` — Gemini, OpenAI,
DeepSeek, Groq, or a custom OpenAI-compatible endpoint — so the recipient is chosen
per school, not by ScanGrade, and a school must name its own processor in its own
record. Requests leave through a Celery worker and are not made in the background:
there is no scheduled job that grades papers nobody asked about.

### Retention on our side

`ai_grading_suggestion` holds the mark proposed, the reasoning, the confidence, the
anomalies the server had to correct, and the **length** of the answer sent — not the
answer itself. The answer remains where it already was, in `submissions.answers`,
under that row's own retention. What the provider retains is governed by the
provider's terms; a school that needs to know should read the processor it chose.

Note for the privacy notice: `compliance/privacy.html` is the page a parent reads.
**It does not currently mention third-party AI processing.** Until it does, a school
using this feature is relying on its own notice rather than on ours, and the app
should not be presented as covering it. That gap is recorded here rather than closed,
because translating and adding a clause to the fourteen-section notice is a decision
for the school's legal review, not a code change.

### The teacher's control

No suggestion becomes a mark without a teacher accepting it (see
`docs/features/ai-essay-grading.md`). That is a data-protection property as much as a
grading one: art. 22's right not to be subject to a decision based solely on
automated processing is satisfied by design — the automated part produces a
recommendation, and a human makes the decision.
