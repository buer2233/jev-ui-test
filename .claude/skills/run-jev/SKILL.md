---
name: run-jev
description: Cold-start, verify, and drive the Jev Ultrafast browser-agent inspector (uv run jev, http://127.0.0.1:8766) on Windows. Use this whenever the user wants to run, start, launch, smoke-test, or take a screenshot of this project — including "把项目跑起来", "跑一下 jev", "启动 inspector", "看看 demo UI". Also use it when uv run jev fails at import with UnicodeDecodeError or "codec can't decode byte", which on Chinese Windows is a locale-encoding bug in text file reads, not a dependency problem.
---

# Run Jev Ultrafast

The app is a local, loopback-only inspector server plus a Chrome-driving agent.
`uv run jev` starts only the **server**; the browser agent and its paid model
calls begin later, when someone starts a demo. Keeping that split in mind is
most of what makes this skill work — you can prove the app runs without
spending a cent or touching the user's Chrome.

Entry point: `jev_ultrafast/demo.py:main` (wired as the `jev` script in
`pyproject.toml`). It serves `jev_ultrafast/static/` and never escapes
`127.0.0.1`.

## Prerequisites

| Requirement | Check | Notes |
|---|---|---|
| `uv` installed | `uv --version` | Declared `requires-python = ">=3.12"` |
| Dependencies synced | `ls .venv` | If missing: `uv sync` |
| `.env` present | `test -f .env` | Copy `.env.example`; both keys are needed for a *live run*, not for the server to boot |

`TYPESAFE_API_KEY` drives the operation/target choice; `TEXT_MODEL_API_KEY` is
only consulted when an action is `TYPE_TEXT`. The server starts fine with neither.

## The Windows locale prerequisite

This is the failure that blocks cold start, and it does not reproduce on macOS
or Linux, so treat it as the first thing to check on Windows.

Python resolves the default encoding for `Path.read_text()` and `open()` from
`locale.getencoding()`, **not** from `sys.getdefaultencoding()`. On Chinese
Windows that is `cp936` (GBK), while this repository's assets are UTF-8. The
mismatch raises `UnicodeDecodeError` — and because `jev_ultrafast/browser.py`
reads `snapshot.js` at *module scope*, it fires during import, before `main()`
runs a single line.

Diagnose before changing anything:

```bash
uv run python -c "import locale; print(locale.getencoding())"
# cp936  -> this section applies
# utf-8  -> skip to Run
```

The fix is an explicit `encoding="utf-8"` on every text read/write, which is
what upstream should have written. Do not "fix" it by setting `PYTHONUTF8=1`
only: that repairs this machine and leaves every other clone broken.

`tests/test_encoding.py` now guards this class of bug by decoding every
`git ls-files`-tracked text file. If it fails, the message names the file and
the byte offset.

## Run

Launch in the background — a foreground `uv run jev` blocks the shell forever:

```bash
cd <project-root> && uv run jev > /tmp/jev-inspector.log 2>&1 &
```

Readiness is a specific line, not a sleep. Wait for it, then confirm:

```bash
for i in $(seq 1 30); do
  curl -sf -o /dev/null http://127.0.0.1:8766/ && break
  sleep 1
done
cat /tmp/jev-inspector.log     # -> Jev Ultrafast: http://127.0.0.1:8766
```

`scripts/smoke.sh` in this skill does the launch, the readiness poll, and the
route checks below in one command. Prefer it.

Override the port with `TYPESAFE_DEMO_PORT`. Whatever you set, `demo.py`
rejects any request whose `Host` header is not exactly `127.0.0.1:<port>`, so
always address the server as `127.0.0.1`, never `localhost` and never by
omitting the port.

## Verify

Hitting `/` returns 200 but proves little. The encoding bug lived in the static
routes, so check that the bytes are decodable as UTF-8 rather than that the
status is green:

```bash
uv run python -c "
import urllib.request
base = 'http://127.0.0.1:8766'
for path in ('/', '/app.js', '/style.css', '/fixture.html'):
    body = urllib.request.urlopen(base + path).read()
    body.decode('utf-8')                      # raises if the fix regressed
    print(f'{path:<16} {len(body):>6} bytes  valid UTF-8')
print(urllib.request.urlopen(base + '/api/state').read().decode())
"
```

`/api/state` should report `"status": "idle"` and echo the `TEXT_MODEL` from
`.env`, which is also the cheapest proof that `.env` loaded.

Then look at the UI, because a green curl has missed white-screen failures
before. Navigate to `http://127.0.0.1:8766/` with Playwright and screenshot it.
A correct load shows the "Every page is a set of possibilities." heading, a
task textarea, and an element panel reading "Waiting for a page".

Three things that look like errors and are not:

- A `favicon.ico` 404 in the console. `demo.py` has no favicon route.
- Non-ASCII characters rendering as `?` or `□` **in your terminal**. That is
  the console's codepage, not the data. Trust the `decode('utf-8')` call above
  over what the terminal prints.
- **`502 Bad Gateway` from a loopback request.** This machine sets
  `HTTP_PROXY=http://127.0.0.1:7890` (a local Clash-style client), which
  intercepts loopback traffic too. It forwards the request, and when the server
  dies mid-request the proxy answers 502 rather than surfacing the dropped
  connection — so an application crash is reported as a gateway fault. Bypass
  it with `curl --noproxy '*'` or `ProxyHandler({})` in urllib; `smoke.sh`
  already does. Check the server's log for the real traceback.

## Windows shell traps

`bash script.sh` does not always mean the bash you expect. On this machine it
can land in WSL, where the project appears as `/mnt/d/AI/...` and `uv` is not
installed — the script then fails with `uv: command not found` and a
`/mnt/` path, which looks like a broken script rather than a wrong shell. Run
these from Git Bash (`/usr/bin/bash`, paths as `/d/AI/...`) or PowerShell. If
you see `/mnt/`, you are in the wrong shell.

The same class of mistake bites from Python: `subprocess.run(..., text=True)`
decodes the child's output using the locale encoding, so a subprocess printing
UTF-8 to a cp936 parent raises `UnicodeDecodeError` inside the *caller*. Pass
`encoding="utf-8"` there too.

## Stop

```bash
netstat -ano | grep ':8766' | grep LISTENING    # then: taskkill //PID <pid> //F
```

Prefer the listener's PID over `pkill -f jev`, which can match your own command
line and kill the session that ran it.

## Running a live demo (costs money, drives Chrome)

Only do this when the user asks for an actual task run. It is a different
activity from starting the app.

Clicking **Start demo** posts `/api/reset`, which constructs `Agent` and
therefore `Browser`, which calls `ensure_daemon()` and attaches to Chrome over
CDP. That needs Chrome to allow remote debugging; check with:

```bash
uv run browser-harness --doctor
```

`[ok] chrome running` with `[FAIL] daemon alive` is the **expected** state
before any demo starts — the daemon is launched on demand, so that FAIL is not
a problem to fix. `[FAIL] Browser Use cloud auth` is optional and unrelated to
the local demo.

Each step then makes a paid TypeSafe call, plus a text-model call for every
`TYPE_TEXT`. Confirm with the user before starting one, and note that
`examples/flights.py` and `scripts/record_flights.py` hit live Google Flights.

## What this skill does not cover

The live demo path above is described from reading the code and the doctor
output, not from a verified end-to-end run in this environment. If you complete
one, tighten this section with what actually happened.