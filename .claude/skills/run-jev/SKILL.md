---
name: run-jev
description: 在 Windows 上冷启动、验证并驱动 Jev Ultrafast 浏览器 agent inspector（uv run jev，http://127.0.0.1:8766）。只要用户想运行、启动、拉起、冒烟测试这个项目，或要看 demo UI 截图，就用本 skill——包括"把项目跑起来"、"跑一下 jev"、"启动 inspector"、"看看 demo UI"、"跑演示案例"。当 uv run jev 在导入期报 UnicodeDecodeError 或 "codec can't decode byte" 时也必须用它：中文 Windows 上这是文本读取的区域编码问题，不是依赖问题。若演示报 daemon 起不来、DevToolsActivePort not found，或提示启用 chrome://inspect/#remote-debugging，同样用本 skill。
---

# 运行 Jev Ultrafast

这个应用由两部分组成：一个只监听回环地址的本地 inspector 服务，加一个驱动 Chrome 的 agent。

`uv run jev` **只启动服务**；浏览器 agent 和它的付费模型调用要等到有人真的开始一次演示才会发生。理解这条分界线是本 skill 大部分价值所在——你可以在不花一分钱、也不碰用户 Chrome 的前提下证明应用跑得起来。

入口：`jev_ultrafast/demo.py:main`（在 `pyproject.toml` 里注册为 `jev` 命令）。它提供 `jev_ultrafast/static/` 下的静态资源，且永不脱离 `127.0.0.1`。

## 前置条件

| 要求 | 检查方式 | 说明 |
|---|---|---|
| 已安装 `uv` | `uv --version` | 项目声明 `requires-python = ">=3.12"` |
| 依赖已同步 | `ls .venv` | 缺失时执行 `uv sync` |
| `.env` 已存在 | `test -f .env` | 从 `.env.example` 复制；两个密钥只在**实跑演示**时需要，服务启动本身不需要 |

`TYPESAFE_API_KEY` 负责决策"选哪个操作、哪个目标"；`TEXT_MODEL_API_KEY` 只在动作为 `TYPE_TEXT` 时才会被读取。两个都没有，服务照样能起来。

## Windows 区域编码这个前提

这是阻塞冷启动的故障，且**在 macOS 和 Linux 上不会复现**，所以在 Windows 上应当把它作为第一个排查项。

Python 解析 `Path.read_text()` 和 `open()` 的默认编码时，依据是 `locale.getencoding()`，**不是** `sys.getdefaultencoding()`。中文 Windows 上它是 `cp936`（GBK），而本仓库的资源文件是 UTF-8。这个不匹配会抛 `UnicodeDecodeError`——又因为 `jev_ultrafast/browser.py` 是在**模块作用域**读取 `snapshot.js` 的，所以它发生在导入期，`main()` 连一行都没跑到。

动手改任何东西之前先诊断：

```bash
uv run python -c "import locale; print(locale.getencoding())"
# cp936  -> 本节适用
# utf-8  -> 跳到「运行」
```

修法是在每一处文本读写上显式写 `encoding="utf-8"`，这也是上游本来就该写的写法。**不要**只用 `PYTHONUTF8=1` 去"修"：那只修好本机，会让其他所有克隆依旧坏着。

`tests/test_encoding.py` 现在通过解码每一个 `git ls-files` 跟踪的文本文件来守住这一类 bug。它失败时，报错信息会指出文件名和字节偏移。

## 运行

后台启动——前台运行 `uv run jev` 会永久阻塞 shell：

```bash
cd <项目根目录> && uv run jev > /tmp/jev-inspector.log 2>&1 &
```

就绪判据是一行特定输出，不要用 `sleep` 蒙。等它出现，再确认：

```bash
for i in $(seq 1 30); do
  curl -sf -o /dev/null http://127.0.0.1:8766/ && break
  sleep 1
done
cat /tmp/jev-inspector.log     # -> Jev Ultrafast: http://127.0.0.1:8766
```

本 skill 的 `scripts/smoke.sh` 把启动、就绪轮询、以及下面那些路由检查合成了一条命令。优先用它。

端口可以用 `TYPESAFE_DEMO_PORT` 覆盖。无论设成什么，`demo.py` 都会拒绝任何 `Host` 头不等于 `127.0.0.1:<端口>` 的请求，所以**永远要用 `127.0.0.1` 寻址**，不要用 `localhost`，也不要省略端口。

## 验证

`/` 返回 200 说明不了什么。那个编码 bug 就藏在静态路由里，所以要检查的是**字节能否按 UTF-8 解码**，而不是状态码是否绿：

```bash
uv run python -c "
import urllib.request
base = 'http://127.0.0.1:8766'
for path in ('/', '/app.js', '/style.css', '/fixture.html'):
    body = urllib.request.urlopen(base + path).read()
    body.decode('utf-8')                      # 修复回归时这里会抛异常
    print(f'{path:<16} {len(body):>6} bytes  valid UTF-8')
print(urllib.request.urlopen(base + '/api/state').read().decode())
"
```

`/api/state` 应返回 `"status": "idle"`，并回显 `.env` 里的 `TEXT_MODEL`——这也是"`.env` 确实被加载了"最省事的证据。

然后要**看一眼 UI**，因为绿色的 curl 漏掉过白屏故障。用 Playwright 打开 `http://127.0.0.1:8766/` 并截图。正常加载应显示 "Every page is a set of possibilities." 标题、一个任务输入框、以及右侧写着 "Waiting for a page" 的元素面板。

有三种"看着像错误、其实不是"的情况：

- 控制台里的 `favicon.ico` 404。`demo.py` 没有 favicon 路由。
- 非 ASCII 字符**在你的终端里**渲染成 `?` 或 `□`。那是控制台代码页的问题，不是数据的问题。以上面 `decode('utf-8')` 的结果为准，不要相信终端打印出来的样子。
- **回环请求返回 `502 Bad Gateway`。** 这台机器设了 `HTTP_PROXY=http://127.0.0.1:7890`（本地 Clash 类客户端），它**连回环流量也拦截**。它转发请求，而服务端在请求中途崩溃时，代理会回一个 502 而不是把"连接被掐断"暴露出来——于是应用崩溃被报告成了网关故障。用 `curl --noproxy '*'` 或 urllib 的 `ProxyHandler({})` 绕过它；`smoke.sh` 已经这样做了。真正的原因要去服务端日志里找 traceback。

## Windows shell 陷阱

`bash script.sh` 里的 `bash` **不一定是你想的那个**。这台机器上它可能落到 WSL 里，那里项目路径显示为 `/mnt/d/AI/...` 且没装 `uv`——脚本会以 `uv: command not found` 加一个 `/mnt/` 路径失败，看起来像脚本坏了，其实是 shell 选错了。请从 Git Bash（`/usr/bin/bash`，路径形如 `/d/AI/...`）或 PowerShell 运行。**看到 `/mnt/` 就说明你在错误的 shell 里。**

同类错误会从 Python 侧咬人：`subprocess.run(..., text=True)` 用区域编码解码子进程输出，所以一个输出 UTF-8 的子进程会让 cp936 的**调用方**抛 `UnicodeDecodeError`。那里同样要传 `encoding="utf-8"`。

## 停止

```bash
netstat -ano | grep ':8766' | grep LISTENING    # 然后：taskkill //PID <pid> //F
```

优先用监听端口对应的 PID，而不是 `pkill -f jev`——后者可能匹配到你自己所在的命令行，把执行它的那个会话杀掉。

## 跑一次真实演示（要花钱，会接管 Chrome）

只在用户明确要求实跑任务时才做。这是与"把应用启动起来"完全不同的另一件事。

点击 **Start demo** 会 POST 到 `/api/reset`，从而构造 `Agent`、进而构造 `Browser`，后者会调用 `ensure_daemon()` 并通过 CDP 连接 Chrome。

### 远程调试这道闸门

Demo 要能跑，机器上必须有一个**可被 CDP 连接**的 Chrome。这一步失败时的报错极具误导性，值得先说清楚。

`uv run browser-harness --doctor` **在这件事上是低报的**：无论 daemon 真的起不来，还是根本没有可连的浏览器，它都只打印 `[FAIL] daemon alive — see install.md`。而且 daemon 是**按需启动**的，所以首次使用前看到这个 FAIL 是正常状态，不是故障——别把它读成"daemon 有问题"。

有指向性的错误只在演示实际启动时出现，在 inspector 的红色错误栏里：

```text
daemon default didn't come up -- check C:\Users\<用户名>\.config\browser-harness\tmp\bu-default.log
```

日志里最常见的是：

```text
fatal: DevToolsActivePort not found in ['C:\Users\<用户名>\AppData\Local\Google\Chrome\User Data', ...]
  — enable chrome://inspect/#remote-debugging, or set BU_CDP_WS for a remote browser
```

**这条信息会把"浏览器没在跑"误报成"权限没开"。** 它永远建议你去做 `chrome://inspect` 那套操作，即使真正的原因是你要连的浏览器已经被关掉了。排查时先看端点，不要先看权限。

### 先做飞行前检查

点 Start demo 之前，先确认有可连的端点。这能避免上面那种误诊：

```bash
bash .claude/skills/run-jev/scripts/browser.sh
```

它会：端点已在 → 直接报告；端点不在 → 启动一个带调试端口的 Chrome 并等它就绪。退出码 0 才继续。

### 推荐方案：独立 profile + 9222 端口（已实测）

`browser.sh` 起的就是这条路：

```bash
chrome.exe --remote-debugging-port=9222 \
  --user-data-dir="C:\Users\<用户名>\AppData\Local\jev-demo-profile"
```

为什么是独立 profile 而不是用户的日常 Chrome：**Chrome 136+ 在默认 profile 上会忽略 `--remote-debugging-port`**。这不是绕过限制，而是常规做法——Playwright 自己就是这么做的（`--user-data-dir=...\ms-playwright-mcp\mcp-chrome-*`）。附带的好处是权限暴露面更小：那个 profile 里没有用户的登录态，而 `chrome://inspect` 那条路开的是**主力 profile** 的调试权限，页面上原话是 "read access to your saved data, cookies and site data"。

**这个 Chrome 窗口必须保持开启。** Demo 在它的后台标签页里运行，不弹新窗口。关掉它，端点就没了，下一次 Start demo 会以本节开头那条误导性错误失败——这个坑已经真实发生过一次。

### 已验证的机制细节

| 事实 | 验证方式 |
|---|---|
| `browser-harness` 会探测 **9222 / 9223** 作为兜底（`daemon.py:331`） | 起一个监听 9222 的 Chrome，`browser-harness` 成功连上并返回 `page_info()` |
| 带 `--remote-debugging-port` + 自定义 profile 时，**`DevToolsActivePort` 文件不会生成** | 实测：端口正常响应，文件不存在 |
| 因此"扫描 profile 目录"这条主路径在新版 Chrome 上是脆弱的 | 与上一条互证 |
| 连接成功后 `doctor` 会转为 `[ok] daemon alive` + `[ok] active browser connections — 1` | 实测 |

### 备选：`chrome://inspect` 勾选框（上游的官方路径）

上游把它称为 "intentionally a one-time manual Chrome setup step"：

1. 打开 `chrome://inspect/#remote-debugging`
2. 勾选 **Allow remote debugging for this browser instance**
3. 重试 **Start demo**

这条路**在本环境未成功验证过**：勾选后 `Local State` 里的 `devtools` 键仍为 `null`，原因未查明（可能是没勾上，也可能 Chrome 尚未落盘）。macOS 上有 `mac-approve` 处理后续授权弹窗，**Windows 没有对应物**。要用这条路，先复检开关是否真的写入了：

```bash
uv run python -c "
import json, pathlib
p = pathlib.Path.home() / 'AppData/Local/Google/Chrome/User Data'
s = json.loads((p / 'Local State').read_text(encoding='utf-8', errors='replace'))
print('toggle :', (s.get('devtools') or {}).get('remote_debugging'))
print('port   :', (p / 'DevToolsActivePort').exists())
"
```

`toggle: None` 表示从未写入。要看的键是 `user-enabled`（完整路径 `devtools` → `remote_debugging` → `user-enabled`）；`daemon.py` 会区分它：明确记录为 `False` 时报的是 "remote debugging is turned off for this browser instance"，而非上面那条通用信息。

错误信息里提到的 `BU_CDP_WS` 是给云端/远程浏览器用的，不是本地 Chrome 的替代方案。

`[FAIL] Browser Use cloud auth` 与此无关且是可选项——本地 Chrome 不需要任何 API key。此后每一步演示都会产生一次付费的 TypeSafe 调用，外加每个 `TYPE_TEXT` 一次文本模型调用，所以启动前先跟用户确认。`examples/flights.py` 和 `scripts/record_flights.py` 会真实访问 Google Flights。

## 本 skill 尚未覆盖的部分

"可连的 Chrome + daemon 连上 + inspector 健康"这条链**已在本环境端到端实测通过**（doctor 三项全绿）。**尚未验证的是点击 Start demo 之后的完整付费演示跑通**。如果完整跑通了一次，请把结果补进本节。