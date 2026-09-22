#!/usr/bin/env bash
# Ensure a Chrome with a reachable CDP endpoint is running for the demo.
#
# Why this exists: Chrome refuses --remote-debugging-port on the default profile
# (136+), so the demo cannot use the browser the user normally has open. This
# starts a second Chrome with its own profile, which is what Playwright and
# similar tools do. browser-harness finds it by probing 127.0.0.1:9222, one of
# its documented fallbacks, so no chrome://inspect checkbox is involved.
#
# The window it opens must stay open. Closing it removes the only endpoint
# browser-harness can reach, and the resulting error ("DevToolsActivePort not
# found ... enable chrome://inspect") misdescribes the cause as a permission
# problem rather than a missing browser.
set -uo pipefail

PORT="${JEV_CDP_PORT:-9222}"
PROFILE_WIN="${JEV_CDP_PROFILE:-C:\\Users\\${USERNAME}\\AppData\\Local\\jev-demo-profile}"
CHROME_WIN="${JEV_CHROME:-C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe}"

probe() {
    [ "$(curl -s --noproxy '*' -o /dev/null -w '%{http_code}' \
        --max-time 2 "http://127.0.0.1:${PORT}/json/version" 2>/dev/null)" = "200" ]
}

if probe; then
    echo "OK   已有 Chrome 在 ${PORT} 上提供 CDP 端点"
else
    [ -f "$(cygpath "$CHROME_WIN" 2>/dev/null || echo /nonexistent)" ] || {
        echo "FAIL 找不到 Chrome: $CHROME_WIN（可用 JEV_CHROME 覆盖）"; exit 1; }
    echo ".... 启动专用 Chrome"
    echo "     profile: ${PROFILE_WIN}"
    powershell.exe -NoProfile -Command \
        "Start-Process -FilePath '${CHROME_WIN}' -ArgumentList \
'--remote-debugging-port=${PORT}','--user-data-dir=${PROFILE_WIN}','--no-first-run',\
'--no-default-browser-check','--disable-search-engine-choice-screen','about:blank'" >/dev/null 2>&1
    for _ in $(seq 1 20); do probe && break; sleep 1; done
fi

probe || { echo "FAIL ${PORT} 在 20 秒内未就绪"; exit 1; }

echo "OK   CDP 端点就绪: http://127.0.0.1:${PORT}"
version=$(curl -s --noproxy '*' "http://127.0.0.1:${PORT}/json/version" | grep -o '"Browser": *"[^"]*"')
echo "     ${version}"
echo
echo "注意：这个 Chrome 窗口必须保持开启。demo 在它的后台标签页里运行；"
echo "      关掉它之后，inspector 会报 'DevToolsActivePort not found'，"
echo "      而那会把「浏览器没在跑」误报成「权限没开」。"
echo
echo "下一步：打开 http://127.0.0.1:8766 点 Start demo。"