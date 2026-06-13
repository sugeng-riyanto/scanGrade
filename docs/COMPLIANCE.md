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
