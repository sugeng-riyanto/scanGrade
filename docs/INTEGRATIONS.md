# Third-Party Integrations

## Supabase

| Purpose | Client | Key Used | Bypasses RLS? |
|---------|--------|----------|---------------|
| Database (read/write) | `get_supabase()` | `SUPABASE_SERVICE_KEY` | Yes |
| Authentication (JWT) | `get_auth_client()` | `SUPABASE_ANON_KEY` | No |

**Setup**: Dashboard.supabase.com → Project → Settings → API

## Midtrans (Payment Gateway)

| Setting | Description |
|---------|-------------|
| `MIDTRANS_SERVER_KEY` | Server key from Midtrans dashboard |
| Merchant ID | Set in super admin settings page |
| Mode | Sandbox / Production |

**Flow**: Admin selects plan → Snap API generates token → Frontend `snap.embed()` → Payment → Webhook → Activation code

## Sentry (Error Tracking)

- **DSN**: From sentry.io project settings
- **Integrations**: FlaskIntegration
- **Capture**: 100% errors, 10% performance traces
- **Context**: Tags `app` + `version`, user context, exam/school context

## AI Providers (Essay Grading)

| Provider | URL | API Key Location |
|----------|-----|------------------|
| Google Gemini | `generativelanguage.googleapis.com` | Teacher AI Settings |
| OpenAI | `api.openai.com` | Teacher AI Settings |
| DeepSeek | `api.deepseek.com` | Teacher AI Settings |
| Groq | `api.groq.com` | Teacher AI Settings |
| Custom | User-specified | Teacher AI Settings |

Keys are stored per-teacher in `teacher_ai_keys` table (plaintext — encryption is a future enhancement).
