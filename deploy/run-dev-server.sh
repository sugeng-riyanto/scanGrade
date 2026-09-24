#!/usr/bin/env bash
# ─── ScanGrade — boot the dev server in one command ──────────────────────────
#
#     bash deploy/run-dev-server.sh          # 5000, or the next free port
#     bash deploy/run-dev-server.sh 5050     # start looking at 5050
#     PORT=5050 bash deploy/run-dev-server.sh
#
# It starts the server **detached**, waits until the URL actually answers, and
# prints the pid and URL to hand to `register_preview`. Nothing else to remember.
#
# Why a script and not the one-liner from `.freebuff/run.md`
# ---------------------------------------------------------
# The one-liner is correct and every step of it is load-bearing, which is exactly
# why it is easy to get subtly wrong. This encodes the four traps:
#
#   * `FLASK_ENV=development` **for the process only**. `.env` sets it to
#     `production`, which turns on `SESSION_COOKIE_SECURE`; over plain HTTP the
#     session cookie is then dropped, CSRF tokens never round-trip, and *every
#     login answers 403* — which reads like a CSRF defect and is an env one.
#     Passed here, it wins over `.env` because `load_dotenv()` does not override
#     variables that already exist in the environment.
#
#   * `FLASK_DEBUG=1` **and** `--reload`. `FLASK_ENV=development` alone buys
#     neither: `flask run` overrides `app.debug` from `FLASK_DEBUG`, and Flask
#     sets `jinja_env.auto_reload` from `debug` when the first template renders.
#     Without both, no route *and no template* is re-read.
#
#   * `--no-debugger`. The reloader and the interactive Werkzeug debugger are
#     independent flags. The debugger executes code for anyone who can reach the
#     port, so it stays off.
#
#   * **Which pid to register.** `--reload` starts a supervisor that owns a child
#     holding the port; the child is replaced on every reload. Registering the
#     child makes a healthy server look dead after your first edit, so this prints
#     the *supervisor* and says which is which.
#
# The default port is the project's (5000). In a shared checkout another thread's
# `wsgi.py` usually holds it, so the script scans upward for a free one rather
# than failing — and says so, so a port you did not expect is never a surprise.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# The first argument only. An ambient `PORT` is deliberately ignored: tooling and
# hosts set it to 0 or to their own value, and `${PORT:-5000}` accepts "0" as a
# real choice — which then binds an ephemeral port and prints a URL nothing can
# reach.
START_PORT=${1:-5000}
if ! printf '%s' "$START_PORT" | grep -Eq '^[0-9]+$' \
   || [ "$START_PORT" -lt 1 ] || [ "$START_PORT" -gt 65535 ]; then
  echo "warning: '$START_PORT' is not a usable port — starting from 5000" >&2
  START_PORT=5000
fi
STATE_DIR="$REPO/.freebuff"

# ── the interpreter ──────────────────────────────────────────────────────────
# Prefer the checkout's venv. It is `scripts/` on Windows and `bin/` elsewhere.
PY=""
for candidate in "$REPO/.venv/Scripts/python.exe" "$REPO/.venv/bin/python"; do
  [ -x "$candidate" ] && PY="$candidate" && break
done
if [ -z "$PY" ]; then
  echo "!! no venv interpreter in $REPO/.venv" >&2
  echo "   create it first — see 'Reproduce the artifacts' in .freebuff/run.md." >&2
  echo "   the suite needs Python 3.12+: python3.12 -m venv .venv" >&2
  exit 1
fi

# A fresh checkout with no `.env` still boots, but with no Supabase project, which
# looks like an app bug rather than a missing file. Say so instead.
[ -f "$REPO/.env" ] || echo "warning: no .env in the checkout — copy it from the main checkout (see .freebuff/run.md)"

# ── a free port, from the default upward ─────────────────────────────────────
# Read from the *listener table*, not by probing with `bind()`. On Windows a bind
# to `127.0.0.1:PORT` **succeeds** while another process holds `0.0.0.0:PORT`
# (the wildcard listener does not conflict with a specific address), so a bind
# probe reports a port as free that the app then cannot bind — and the failure
# surfaces as "the server never answered" long after the real cause.
listening_ports() {
  case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*)
      netstat -ano 2>/dev/null | awk '/LISTENING/ { n = $2; sub(/.*:/, "", n); print n }' ;;
    *)
      (ss -ltn 2>/dev/null || netstat -ltn 2>/dev/null) \
        | awk 'NR > 1 { n = $4; sub(/.*:/, "", n); print n }' ;;
  esac
}

TAKEN=$(listening_ports | sort -u || true)
PORT=""
for candidate in $(seq "$START_PORT" "$((START_PORT + 39))"); do
  printf '%s\n' "$TAKEN" | grep -qx "$candidate" || { PORT="$candidate"; break; }
done
if [ -z "$PORT" ]; then
  echo "!! no free port in $START_PORT..$((START_PORT + 39))" >&2
  exit 1
fi

mkdir -p "$STATE_DIR"
LOG="$STATE_DIR/dev-server-$PORT.log"
ERR="$STATE_DIR/dev-server-$PORT.err"
PIDFILE="$STATE_DIR/dev-server-$PORT.pid"
: >"$LOG"; : >"$ERR"

URL="http://127.0.0.1:$PORT/"

# ── start it detached ────────────────────────────────────────────────────────
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*)
    # `-PassThru` is not used on purpose: piping its output is what makes this
    # call appear to hang while the server is already up. The pid is discovered
    # below instead, which also tells the supervisor from the child.
    # `</dev/null` is load-bearing, not tidiness. Without it the *detached* server
    # inherits this shell's stdin, and whoever launched the script (a terminal, an
    # agent tool) waits for that pipe to close — so the script appears to hang
    # while the server is already listening. Measured: 0.19s with it, forever
    # without.
    #
    # PowerShell is a native Windows program: it does not resolve this shell's
    # `/f/...` paths, and `Start-Process` fails with no output the redirects hide —
    # the symptom is the wait loop timing out with no server. Convert every path
    # handed across the boundary first.
    PYW=$(cygpath -w "$PY")
    REPOW=$(cygpath -w "$REPO")
    LOGW=$(cygpath -w "$LOG")
    ERRW=$(cygpath -w "$ERR")
    powershell -NoProfile -Command "
      \$env:FLASK_ENV='development'
      \$env:FLASK_DEBUG='1'
      \$env:FLASK_APP='wsgi.py'
      Start-Process -FilePath '$PYW' \
        -ArgumentList '-m','flask','run','--host','127.0.0.1','--port','$PORT','--reload','--no-debugger' \
        -WorkingDirectory '$REPOW' \
        -RedirectStandardOutput '$LOGW' -RedirectStandardError '$ERRW' \
        -WindowStyle Hidden
    " </dev/null >/dev/null 2>&1 || true
    ;;
  *)
    FLASK_ENV=development FLASK_DEBUG=1 FLASK_APP=wsgi.py \
      setsid "$PY" -m flask run --host 127.0.0.1 --port "$PORT" --reload --no-debugger \
      >"$LOG" 2>"$ERR" </dev/null &
    ;;
esac

# ── wait until it actually answers ───────────────────────────────────────────
# "Started" is not "listening": the reloader boots twice, so poll the URL rather
# than trust that the process exists.
answered=0
for _ in $(seq 1 60); do
  code=$(curl -s -o /dev/null -w "%{http_code}" "$URL" 2>/dev/null || true)
  if [ "$code" = "200" ]; then answered=1; break; fi
  sleep 0.5
done
if [ "$answered" -ne 1 ]; then
  echo "!! the server never answered on $URL" >&2
  echo "   last log lines:" >&2
  tail -n 20 "$ERR" >&2 || true
  exit 1
fi

# ── which pid, and which one to register ─────────────────────────────────────
CHILD=""
SUP=""
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*)
    CHILD=$(netstat -ano | grep LISTENING | grep -E "127\.0\.0\.1:$PORT[[:space:]]" \
              | awk '{print $NF}' | head -1)
    # The reloader child is *spawned by* the supervisor, so the listener's parent
    # process is the supervisor. Asking by parentage rather than "every python
    # whose command line carries this port": the `-m flask` shim leaves more
    # than two such processes, so that list cannot say which is which. The parent
    # is confirmed to be ours before it is trusted.
    if [ -n "$CHILD" ]; then
      PARENT=$(powershell -NoProfile -Command \
        "(Get-CimInstance Win32_Process -Filter \"ProcessId=$CHILD\").ParentProcessId" \
        2>/dev/null | tr -d '\r' | head -1)
      if [ -n "$PARENT" ] && powershell -NoProfile -Command \
          "(Get-CimInstance Win32_Process -Filter \"ProcessId=$PARENT\").CommandLine" \
          2>/dev/null | grep -q -- "--port $PORT"; then
        SUP="$PARENT"
      fi
    fi
    ;;
  *)
    CHILD=$( (ss -ltnp 2>/dev/null || netstat -ltnp 2>/dev/null) \
              | grep "127.0.0.1:$PORT" | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2 || true)
    SUP=$(pgrep -f "flask run.*--port $PORT" 2>/dev/null | grep -vx "${CHILD:-0}" | head -1 || true)
    ;;
esac
# Fall back to whatever we have, so the output is never empty.
[ -n "$SUP" ] || SUP="$CHILD"
[ -n "$SUP" ] || SUP="unknown"
echo "$SUP" >"$PIDFILE"

# ── prove which config it booted with ────────────────────────────────────────
# The one check that distinguishes the two failures the recipe is written around:
# a server in production config answers 200 too, and only drops the cookie later.
CONFIG=$("$PY" -c "
import requests
s = requests.Session()
try:
    s.get('$URL' + 'auth/login', timeout=10)
    print([c.secure for c in s.cookies][0])
except Exception:
    print('unknown')
" 2>/dev/null || echo unknown)

echo
echo "ScanGrade dev server"
if [ "$PORT" = "$START_PORT" ]; then
  echo "  port  $PORT (the project default)"
else
  echo "  port  $PORT — $START_PORT was taken"
fi
echo "  url   $URL"
echo "  pid   $SUP        <- register_preview this one (the supervisor)"
echo "  child ${CHILD:-?}         holds the port; replaced on every reload"
echo "  log   ${LOG#$REPO/}"
if [ "$CONFIG" = "False" ]; then
  echo "  config development (session cookie not Secure — logins work over HTTP)"
elif [ "$CONFIG" = "True" ]; then
  echo "  config PRODUCTION — the cookie is Secure, so logins over HTTP will 403 with"
  echo "         'CSRF token invalid'. FLASK_ENV did not reach the process; see .freebuff/run.md."
else
  echo "  config could not be read (the app still answered 200)"
fi
echo
echo "  stop:  kill $SUP   (or Stop-Process -Id $SUP -Force)"
