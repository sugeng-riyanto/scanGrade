# Monitoring & Observability

## Sentry Error Tracking

Sentry captures all unhandled exceptions and 10% of transactions.

**Setup**:
1. Create project at [sentry.io](https://sentry.io)
2. Set `SENTRY_DSN` in `.env`
3. Set `SENTRY_ENVIRONMENT=production`

**Viewing Errors**:
- Go to Sentry dashboard → Issues
- Filter by environment, time range, etc.
- Each error includes: traceback, user context, exam context, school context

## Health Check

```bash
curl http://localhost:8000/health
```

Response:
```json
{"status":"ok","supabase":"connected","cache_size":42,"uptime_ms":123456}
```

## Logging

All logs are structured JSON:
```json
{"timestamp":"2026-06-08T12:00:00","level":"INFO","message":"GET /health 200","logger":"app"}
```

View via:
- Systemd: `journalctl -u scangrade -f`
- Flask output: `flask_out.txt` (dev mode)

## Performance Monitoring

- `X-Response-Time-ms` header on every response
- Cache for Supabase queries (`_lru_cache` in `__init__.py`)
- Rate limiting to prevent abuse

## Alerting

Configure Sentry alert rules:
- Error rate > 10/min → Email
- New error type → Slack/Email
- 500 error rate > 1% → Critical alert
