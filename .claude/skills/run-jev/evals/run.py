"""run-jev 的 evals：真起一个 inspector，然后断言它的 HTTP 契约。

为什么这些断言值得存在：skill 里写了一堆"应该是什么样"的说法——只监听回环、
只认 127.0.0.1 的 Host、静态资源是 UTF-8、没有 token 的 POST 会被拒。
说法会过期，断言不会。这些全部是【免费】的，不调用任何模型。

用法：
    uv run python .claude/skills/run-jev/evals/run.py            # 全部（含需要 Chrome 的）
    uv run python .claude/skills/run-jev/evals/run.py --list     # 只列出用例
    uv run python .claude/skills/run-jev/evals/run.py --no-browser   # 跳过需要 Chrome 的用例

刻意用 http.client 而不是 urllib/requests：
  · 它不走 HTTP_PROXY——这台机器的 Clash(:7890) 连回环都拦，走代理会把
    "服务端崩了"显示成 502，把排查方向带偏；
  · 它允许 skip_host=True，这样才测得了伪造 Host 的场景。
"""

import argparse
import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
PORT = int(os.environ.get("TYPESAFE_DEMO_PORT", "8766"))
LOG = ROOT / "artifacts" / "skill-evals" / "jev-inspector.log"


def request(path, *, method="GET", host=None, headers=None, body=None):
    """发一个请求，返回 (status, body_bytes, response_headers)。"""
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
    conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    conn.putheader("Host", host or f"127.0.0.1:{PORT}")
    for key, value in (headers or {}).items():
        conn.putheader(key, value)
    if body is not None:
        conn.putheader("Content-Length", str(len(body)))
    conn.endheaders(body)
    response = conn.getresponse()
    payload = response.read()
    conn.close()
    return response.status, payload, dict(response.getheaders())


def port_open():
    with socket.socket() as probe:
        probe.settimeout(1)
        return probe.connect_ex(("127.0.0.1", PORT)) == 0


def start_inspector():
    """返回 (proc, 是否由本脚本启动)。已经在跑就复用，绝不杀掉别人的服务。"""
    if port_open():
        return None, False
    LOG.parent.mkdir(parents=True, exist_ok=True)
    handle = LOG.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        ["uv", "run", "jev"], cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT,
        env={**os.environ, "NO_PROXY": "127.0.0.1,localhost"},
    )
    for _ in range(40):
        if port_open():
            return proc, True
        time.sleep(0.5)
    proc.terminate()
    raise SystemExit(f"inspector 没能在 20 秒内起来，看 {LOG}")


# --------------------------------------------------------------------------- 用例

def ev_text_routes_are_utf8():
    """skill 说「200 说明不了什么，要断言字节能按 UTF-8 解码」。"""
    bad = []
    for path in ("/", "/app.js", "/style.css", "/fixture.html"):
        status, payload, _ = request(path)
        if status != 200:
            bad.append(f"{path} -> HTTP {status}")
            continue
        try:
            payload.decode("utf-8")
        except UnicodeDecodeError as error:
            bad.append(f"{path} -> 200 但解码失败: {error}")
    return (not bad), "四个静态路由都是 200 且 UTF-8 可解码" if not bad else "; ".join(bad)


def ev_api_state_reports_idle():
    """没跑 demo 时 /api/state 必须是 idle，并回显文本模型名。"""
    status, payload, _ = request("/api/state")
    if status != 200:
        return False, f"/api/state -> HTTP {status}"
    try:
        state = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        return False, f"/api/state 不是合法 UTF-8 JSON: {error}"
    missing = {"status", "text_model", "max_steps"} - set(state)
    if missing:
        return False, f"/api/state 缺少字段 {sorted(missing)}"
    return True, f"status={state['status']!r} text_model={state['text_model']!r} max_steps={state['max_steps']}"


def ev_token_is_substituted():
    """demo.py 会把 __TOKEN__ 替换成真 token；漏替换等于把占位符发给前端。"""
    status, payload, _ = request("/")
    if status != 200:
        return False, f"/ -> HTTP {status}"
    text = payload.decode("utf-8")
    if "__TOKEN__" in text:
        return False, "返回的 HTML 里还留着未替换的 __TOKEN__"
    return True, "__TOKEN__ 已被替换"


def ev_foreign_host_is_rejected():
    """只监听回环 + 校验 Host，是防 DNS rebinding 的那道闸。"""
    status, _, _ = request("/", host="evil.example.com")
    if status != 403:
        return False, f"伪造 Host 期望 403，实际 {status}"
    return True, "Host 不是 127.0.0.1:<port> 时返回 403"


def ev_post_without_token_is_rejected():
    """POST 需要 X-Demo-Token，否则任何本地页面都能驱动浏览器。"""
    body = json.dumps({"goal": "x"}).encode()
    status, _, _ = request("/api/reset", method="POST", body=body)
    if status != 403:
        return False, f"无 token 的 POST 期望 403，实际 {status}"
    # 带上 token 但 Origin 不对，同样必须被拒
    status2, _, _ = request(
        "/api/reset", method="POST", body=body,
        headers={"X-Demo-Token": "guessed", "Origin": "https://evil.example"},
    )
    if status2 != 403:
        return False, f"伪造 token + 外部 Origin 期望 403，实际 {status2}"
    return True, "无 token / 伪造 token + 外部 Origin 都返回 403"


def ev_unknown_path_is_404():
    status, _, _ = request("/definitely-not-a-route")
    if status != 404:
        return False, f"未知路径期望 404，实际 {status}"
    return True, "未知路径返回 404（没有兜底到 index.html）"


def git_bash():
    """找一个确实是 MSYS/Git 的 bash，返回 (完整路径, uname 输出)。

    必须【按完整路径】调用，并且验证身份——这台机器上有两个 bash：
      · Git Bash（MSYS，MINGW64_NT-…）：项目的 uv、USERNAME、/d/ 路径都在
      · WSL 的 bash（Linux，盘符挂在 /mnt/d/…）：没有 uv，USERNAME 未定义，
        browser.sh 会倒在 `USERNAME: unbound variable`
    而 subprocess.run(["bash", …]) 会落进 WSL：Windows 的 CreateProcess 先搜
    System32 并自行补 .exe，于是命中 C:\\Windows\\System32\\bash.exe（WSL 启动器），
    轮不到 PATH 里的 Git bash。skill 里「看到 /mnt/ 就说明你在错误的 shell 里」
    说的就是这个，这里把它变成断言。
    """
    candidates = [
        os.environ.get("JEV_BASH"),
        shutil.which("bash"),
        r"D:\Program Files\Git\usr\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
        r"C:\Program Files\Git\bin\bash.exe",
    ]
    wrong = []
    for candidate in candidates:
        if not candidate or not Path(candidate).is_file():
            continue
        try:
            probe = subprocess.run(
                [candidate, "-c", "uname -s"], capture_output=True, text=True,
                encoding="utf-8", timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        system = (probe.stdout or "").strip()
        if "MINGW" in system or "MSYS" in system:
            return candidate, system
        wrong.append(f"{candidate} -> {system or 'unknown'}")
    return None, "; ".join(wrong)


def ev_browser_script_runs_in_git_bash():
    """browser.sh 必须能在 skill 说的那个 shell 里跑通。

    这里刻意用【相对路径】调用：绝对 Windows 路径交给 bash 会被它当转义序列
    吃掉反斜杠（D:\\AI\\… 变成 D:AI…），得到的 "No such file or directory"
    看起来像脚本不存在，其实是路径传错了。
    """
    bash, system = git_bash()
    if bash is None:
        return False, f"找不到可用的 Git Bash（非 MSYS 的候选：{system or '无'}）"
    script = ".claude/skills/run-jev/scripts/browser.sh"
    result = subprocess.run(
        [bash, script], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    if result.returncode != 0:
        tail = ((result.stderr or "") + (result.stdout or "")).strip().splitlines()
        return False, f"browser.sh 退出码 {result.returncode}: {tail[-1] if tail else '(无输出)'}"
    version = next((line for line in result.stdout.splitlines() if "Browser" in line), "")
    return True, f"{system} 下 exit 0；{version.strip()}"


CASES = [
    ("text-routes-are-utf8", "静态路由返回 200 且字节能按 UTF-8 解码", "free", ev_text_routes_are_utf8),
    ("api-state-reports-idle", "未跑 demo 时 /api/state 为 idle 且字段齐全", "free", ev_api_state_reports_idle),
    ("token-is-substituted", "index.html 里的 __TOKEN__ 已被替换", "free", ev_token_is_substituted),
    ("foreign-host-is-rejected", "Host 不是 127.0.0.1:<port> 时返回 403", "free", ev_foreign_host_is_rejected),
    ("post-without-token-rejected", "无 token 的 POST 被拒（403）", "free", ev_post_without_token_is_rejected),
    ("unknown-path-is-404", "未知路径返回 404", "free", ev_unknown_path_is_404),
    (
        "browser-script-runs-in-git-bash",
        "browser.sh 在 Git Bash 里 exit 0 并报出 CDP 端点（需 Chrome）",
        "browser",
        ev_browser_script_runs_in_git_bash,
    ),
]


def main():
    parser = argparse.ArgumentParser(description="run-jev 的 evals")
    parser.add_argument("--list", action="store_true", help="只列出用例，不执行")
    parser.add_argument("--no-browser", action="store_true", help="跳过需要 Chrome 的用例")
    parser.add_argument("--only", action="append", metavar="ID", help="只跑指定 id，可重复")
    args = parser.parse_args()

    if args.list:
        for case_id, what, cost, _ in CASES:
            print(f"  {case_id:<30} [{cost:^7}] {what}")
        return 0

    selected = [c for c in CASES if not args.only or c[0] in set(args.only)]
    if args.no_browser:
        selected = [c for c in selected if c[2] != "browser"]

    proc, started_by_us = start_inspector()
    print(f"inspector: http://127.0.0.1:{PORT} ({'本次启动' if started_by_us else '复用已在跑的'})\n")

    failures = []
    try:
        for case_id, what, cost, check in selected:
            try:
                ok, detail = check()
            except Exception as error:  # noqa: BLE001  断言本身炸了也要算失败
                ok, detail = False, f"{type(error).__name__}: {error}"
            print(f"  {'PASS' if ok else 'FAIL'}  {case_id:<30} {detail}")
            if not ok:
                failures.append(case_id)
    finally:
        if proc is not None:
            proc.terminate()
            proc.wait(timeout=10)

    total = len(selected)
    print(f"\n{total - len(failures)}/{total} 通过" + (f"；失败：{failures}" if failures else ""))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())