#!/usr/bin/env bash
# Cold-start the Jev inspector, wait for readiness, and prove every text route
# decodes as UTF-8. Leaves the server running for the caller. Exit 0 = healthy.
#
# Usage: bash .claude/skills/run-jev/scripts/smoke.sh
set -uo pipefail

PORT="${TYPESAFE_DEMO_PORT:-8766}"
BASE="http://127.0.0.1:${PORT}"

# A local proxy (Clash on :7890 is common here) intercepts loopback too. It
# forwards the request, and when the server dies mid-request the proxy answers
# 502 Bad Gateway instead of surfacing the dropped connection - which reads
# like a server fault when it is really an application crash. Bypass it for
# loopback so this script reports the real thing.
export NO_PROXY="127.0.0.1,localhost"
export no_proxy="$NO_PROXY"
# <root>/.claude/skills/run-jev/scripts -> four levels up is the project root.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
LOG="${TMPDIR:-/tmp}/jev-inspector.log"

cd "$ROOT" || { echo "FAIL cannot cd to $ROOT"; exit 1; }
echo "OK   project root: $ROOT"

if curl -sf --noproxy '*' -o /dev/null "$BASE/"; then
    echo "OK   already running on $BASE"
else
    uv run jev > "$LOG" 2>&1 &
    echo ".... starting uv run jev (log: $LOG)"
    for _ in $(seq 1 30); do
        curl -sf --noproxy '*' -o /dev/null "$BASE/" && break
        sleep 1
    done
fi

if ! curl -sf --noproxy '*' -o /dev/null "$BASE/"; then
    echo "FAIL server not ready on $BASE after 30s"
    tail -n 20 "$LOG" 2>/dev/null || true
    exit 1
fi
echo "OK   server up on $BASE"

# The encoding bug lived here: a 200 response with bytes that will not decode
# as UTF-8 still white-screens the UI. Assert the decode, not the status code.
uv run python - "$BASE" <<'PY'
import sys, urllib.request

base = sys.argv[1]
failures = []
# An empty ProxyHandler ignores HTTP_PROXY/NO_PROXY entirely, so a loopback
# failure surfaces as the connection error it is rather than a proxy 502.
open_direct = urllib.request.build_opener(urllib.request.ProxyHandler({})).open

for path in ("/", "/app.js", "/style.css", "/fixture.html"):
    try:
        body = open_direct(base + path).read()
        body.decode("utf-8")
        print(f"OK   {path:<16} {len(body):>6} bytes  valid UTF-8")
    except Exception as error:
        failures.append(f"{path}: {error}")

try:
    state = open_direct(base + "/api/state").read().decode("utf-8")
    print(f"OK   /api/state      {state}")
except Exception as error:
    failures.append(f"/api/state: {error}")

if failures:
    print("FAIL " + "; ".join(failures))
    sys.exit(1)
print("OK   inspector healthy")
PY