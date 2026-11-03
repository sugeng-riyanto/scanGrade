# Operational Maintenance

## Regular Tasks

| Frequency | Task | Description |
|-----------|------|-------------|
| Daily | Check Sentry | Review new errors |
| Weekly | DB cleanup | Remove old draft submissions |
| Weekly | Log review | Check for anomalies |
| Monthly | Security patches | `pip install --upgrade -r requirements.txt` |
| Monthly | Backup verification | Prove a backup **restores**, not that one ran — see [BACKUP_RESTORE_PROOF](deploy/BACKUP_RESTORE_PROOF.md) |
| Quarterly | Performance review | Check response times, optimize queries |

## Database Cleanup

```sql
-- Remove draft submissions older than 30 days
DELETE FROM submissions 
WHERE status = 'draft' 
AND created_at < NOW() - INTERVAL '30 days';

-- Remove old violation logs
DELETE FROM violation_logs 
WHERE created_at < NOW() - INTERVAL '1 year';
```

## Log Rotation

Systemd journald handles log rotation automatically.
To check disk usage: `journalctl --disk-usage`
To limit: edit `/etc/systemd/journald.conf` → `SystemMaxUse=500M`

## Backup

Supabase provides automated daily backups (14-day retention on free tier).
Download manually via Supabase Dashboard → Database → Backups.

That is the infrastructure's copy. The application also keeps its own logical
snapshot of every writable table, root-only in `/var/backups/scangrade`, taken by
`deploy/db_snapshot.py` (and by the deploy whenever a release ships SQL).

**An archive existing is not a backup that restores.** Before any release that
changes the schema, the procedure to prove one restores — and the two things that
make that proof silent if skipped — is in
[BACKUP_RESTORE_PROOF](deploy/BACKUP_RESTORE_PROOF.md).

## Monitoring Health

```bash
# Every 5 minutes (cron job)
curl -s http://localhost:8000/health | grep '"status":"ok"' || systemctl restart scangrade
```

## Update Dependencies

```bash
source .venv/bin/activate
pip install --upgrade -r requirements.txt
sudo systemctl restart scangrade
```
