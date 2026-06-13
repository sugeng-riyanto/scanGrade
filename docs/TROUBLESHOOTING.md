# Troubleshooting Guide

## Flask Won't Start

**Cause**: Missing Supabase credentials in `.env`
**Solution**: Verify `SUPABASE_URL` and `SUPABASE_SERVICE_KEY` are set correctly.

## Redis Connection Error

**Cause**: `REDIS_URL=redis://localhost:6379/0` in `.env` but Redis not running
**Solution**: Safe to ignore. Flask-Limiter falls back to `memory://`. Remove `REDIS_URL` from `.env` to suppress warning.

## Rate Limited (429)

**Cause**: Too many requests in a short time
**Solution**: Wait for window to expire:
- Auth routes: 1 minute
- Register: 10 minutes
- OMR scan: 1 minute

## 500 Internal Server Error

**Cause**: Unhandled exception
**Solution**:
1. Check Flask console output or `journalctl -u scangrade -f`
2. Check Sentry dashboard
3. Look for traceback in error output

## Login Fails

**Cause**: Various
**Solution**:
1. Verify email/password
2. Check user status in Supabase → Authentication → Users
3. Ensure user is confirmed (email_confirm)

## OMR Scan Fails

**Cause**: Image quality issues
**Solution**:
1. Ensure good lighting, no shadows
2. Full page visible (all 4 corners)
3. Minimum resolution 720p
4. Avoid creased or folded sheets

## AI Grading Fails

**Cause**: API key issue or provider outage
**Solution**:
1. Go to Pengaturan AI → Test Key
2. Check API key is active (green checkmark)
3. Verify provider service status

## Payment Fails

**Cause**: Midtrans configuration
**Solution**:
1. Check Midtrans server key in super admin settings
2. Verify Midtrans account is active
3. Check webhook endpoint is configured

## File Upload Fails

**Cause**: Invalid file or size exceeded
**Solution**:
1. Max file size: 20MB (images), 50MB (PDF)
2. Allowed formats: .jpg, .jpeg, .png, .pdf
3. Re-upload with original file (avoid compression)
