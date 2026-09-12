# Automated deployment (pull-based)

Pushing to `main` reaches production without anyone opening a console.

The VPS polls `origin/main` every two minutes. When it finds a new commit it
pulls, verifies the code, and reloads the app gracefully. If the new release does
not come up, it puts the previous commit back and restarts that.

Nothing on GitHub needs a credential, and the server needs no inbound SSH: the
box pulls, so the trust boundary stays where it was.

## Install (once, as root on the VPS)

```bash
bash /opt/scangrade/deploy/install-auto-deploy.sh
```

It fast-forwards the checkout first, then installs `/usr/local/bin/scangrade-deploy`
from the repo copy, installs the units, enables the timer, and does one test run
so you find out immediately whether it works instead of in two minutes.

**The very first time**, the checkout does not contain the installer yet, so run
the copy that was handed over instead — it pulls the same commit before it
installs anything:

```bash
bash /tmp/sgdeploy2/install-auto-deploy.sh
```

Safe to re-run afterwards: it re-reads the automation from the checkout, so it is
also how you upgrade the logic after pulling a newer commit.

## Operating it

| I want to… | Command |
|---|---|
| watch deploys as they happen | `journalctl -u scangrade-deploy.service -f` |
| see when the next check is | `systemctl list-timers scangrade-deploy.timer` |
| deploy right now, without waiting | `systemctl start scangrade-deploy.service` |
| **freeze deploys** (e.g. exam week) | `touch /etc/scangrade-deploy.pause` |
| resume | `rm /etc/scangrade-deploy.pause` |
| stop deploying automatically | `systemctl disable --now scangrade-deploy.timer` |
| see the last release that stuck | `cat /var/lib/scangrade-deploy/last-deploy` |

The freeze file is the one to reach for during exams: the timer keeps ticking,
but the script exits immediately, so nothing restarts while students are working.

## What it refuses to do

The script takes **no arguments**. It is the only thing root runs unattended, so
it is not a general-purpose command runner — the branch, the repo and the paths
are fixed in the script itself.

It also stops instead of guessing when:

- the checkout has local changes (it will not clobber hand edits);
- the update is not a fast-forward (history was rewritten);
- `requirements.txt` changed and `pip install` failed;
- the code does not compile;
- the app cannot construct with all of its blueprints and routes;
- the app does not answer `200` on `127.0.0.1:8000` afterwards.

Each of those is a distinct non-zero exit, visible in the journal. On the last
two it rolls the checkout back first, so the site keeps serving the release that
was working.

## Why a reload and not a restart

`scangrade.service` carries `ExecReload=/bin/kill -s HUP $MAINPID`. SIGHUP makes
gunicorn retire its workers gracefully: in-flight requests get up to
`graceful_timeout` (30 s) to finish. A hard `restart` would cut off whatever a
student was in the middle of, which is the opposite of what an automatic deploy
should risk.

If `ExecReload` is ever removed from the unit, `systemctl reload` fails and the
script falls back to `restart` — still correct, just blunt. `tests/unit/test_auto_deploy.py`
pins that line so the fallback cannot become the silent default.

## What a deploy still does not do

**The Tailwind stylesheet is a committed build artifact.** The VPS never runs
`npm`. If a change touches templates, run `npm run css:build` locally and commit
`app/static/css/tailwind.css` with it, or the new classes will be missing in
production.

Migrations are still manual: SQL in `supabase/migrations/` is applied by hand in
the Supabase SQL Editor. Deploying code that expects a column before the column
exists is the failure mode to avoid.

## Rolling back by hand

```bash
cd /opt/scangrade
runuser -u scangrade -- git reset --hard <commit>
systemctl reload scangrade
```

Then fix `origin/main` — otherwise the next tick will try the same bad release
again. Freeze first if you need time: `touch /etc/scangrade-deploy.pause`.
