# run-jev 的 evals

真起一个 inspector，然后逐条断言 SKILL.md 里写下的那些"应该是什么样"。
说法会过期，断言不会。

```bash
uv run python .claude/skills/run-jev/evals/run.py              # 全部
uv run python .claude/skills/run-jev/evals/run.py --no-browser # 跳过需要 Chrome 的
uv run python .claude/skills/run-jev/evals/run.py --list
```

已经在跑的 inspector 会被复用，**不会被脚本杀掉**；只有本次启动的才在结束时收掉。

## 断言的是什么

| 用例 | 断言 |
|---|---|
| `text-routes-are-utf8` | `/` `/app.js` `/style.css` `/fixture.html` 都是 200 **且字节能按 UTF-8 解码**（状态码绿不代表不白屏） |
| `api-state-reports-idle` | 未跑 demo 时 `/api/state` 是 `idle`，且带 `text_model` / `max_steps` |
| `token-is-substituted` | 返回的 HTML 里 `__TOKEN__` 已被替换 |
| `foreign-host-is-rejected` | Host 不是 `127.0.0.1:<port>` 时返回 403（防 DNS rebinding） |
| `post-without-token-rejected` | 无 token、或伪 token + 外部 Origin 的 POST 都返回 403 |
| `unknown-path-is-404` | 未知路径返回 404，没有兜底到 index.html |
| `browser-script-runs-in-git-bash` | `browser.sh` 在 **MSYS/Git Bash** 里 exit 0 并报出 CDP 端点（需 Chrome） |

## 两个把自己也写进去的坑

这些是写这套 evals 时踩出来的，记在这里免得下次重踩：

**一、`subprocess.run(["bash", ...])` 在这台机器上会落进 WSL。**
不是 PATH 的问题：Windows 的 `CreateProcess` **先搜 System32 并自行补 `.exe`**，
于是命中 `C:\Windows\System32\bash.exe`（WSL 启动器），根本轮不到 PATH 里的 Git bash。
落进 WSL 之后项目路径是 `/mnt/d/...`、没有 `uv`、`USERNAME` 未定义，
`browser.sh` 会倒在 `USERNAME: unbound variable`——看起来像脚本坏了。
所以 `git_bash()` 按**完整路径**调用，并用 `uname -s` **验证它真的是 MINGW/MSYS**。

**二、绝对 Windows 路径交给别的程序，反斜杠会被当转义吃掉。**
`D:\AI\...\browser.sh` 变成 `D:AIE9...browser.sh`，报错是
`No such file or directory`——看起来像脚本不存在，其实是路径传错了。
一律改用相对路径 + `cwd=`。

另外这里刻意用 `http.client` 而不是 urllib：它不走 `HTTP_PROXY`
（本机 Clash 连回环都拦，走代理会把"服务端崩了"显示成 502），
而且允许 `skip_host=True`，这才测得了伪造 Host。