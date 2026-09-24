<img src="docs/banner.svg" alt="Jev Ultrafast · Browser Use × TypeSafe" width="100%" />

# Jev Ultrafast ⚡

**一个拥有动态、索引化动作空间的浏览器 agent。**

给它一个目标。[TypeSafe 的 Jev](https://docs.typesafe.ai/introduction) 负责挑一个操作和一个元素；只有在操作是 `TYPE_TEXT` 时，才由一个小模型生成文字。

**Zürich → London，Google Flights 任务 7.1 秒完成。** 一个自然语言目标，包含真实文本生成与加载等待。

<a href="docs/demo.mp4"><img src="docs/demo.gif" alt="真实的 Google Flights 搜索，1× 速度，包含生成的城名与动态的操作/目标决策" width="100%" /></a>

[观看 MP4](docs/demo.mp4) · [实测数据](docs/performance.md) · [阅读主循环](jev_ultrafast/agent.py)

---

## 本仓库有两部分

| 部分 | 位置 | 说明 |
|---|---|---|
| **浏览器 agent 库** | `jev_ultrafast/`（除 `framework/`） | 上游 Browser Use 的原始内核：观测 → 决策 → 执行 |
| **自然语言驱动的 UI 自动化测试框架** | `jev_ultrafast/framework/` + `cases/` + `tests/` | 本期新增：用例用自然语言写，pytest 执行，Allure 出报告 |

框架是**外挂的一层**：内核的状态机、新鲜度校验、遮挡校验的**职责边界与语义**未变。
为了让它在真实站点上可用，对 `snapshot.js` / `browser.py` 做了若干**通用性扩展**
（元素发现范围、跟随新标签页、执行前滚动、登录态注入等，见下文「边界」），
这些扩展不针对任何特定站点。设计取舍与实测数据见 [`docs/一期改造/`](docs/一期改造/)。

---

## 动作空间

每一次观察都会产出一张新的元素表：

```text
[1] button    Change ticket type · Round trip
[2] combobox  Where from?        · San Francisco
[3] combobox  Where to?          · empty
[4] textbox   Departure          · empty
...
```

支持的操作有 `CLICK`、`TYPE_TEXT`、`SELECT`、`SCROLL_UP`、`SCROLL_DOWN`、`WAIT`、`DONE`、`BLOCKED`。
**只提供受支持的操作和与之匹配的目标。**

```text
                      one TypeSafe request
                     ┌───────────────────────────┐
page → element table → operation                 │
                     │ click_target              │
                     │ type_text_target          │
                     │ select_target, if present │
                     └─────────────┬─────────────┘
                         use the matching target
                                   │
                    CLICK [7] ─────┤──→ browser
                TYPE_TEXT [3] ─────┘
                          ↓
                   small LLM → text → browser
```

（框内的 `click_target` / `type_text_target` 等就是请求里的真实字段名，故保留原文；
上面的流程是：一次请求同时问出操作与各操作对应的目标 → 只消费匹配的那个 → 交给浏览器执行。）

目标分支是**推测式**的：如果操作选了 `CLICK`，只有 `click_target` 会被执行。两个决策，**一次网络往返**。每个目标分支里只放兼容的元素。原生下拉框的选项带已观测的元素/选项索引。

策略里**没有站点专用的动作脚本，也没有预置的字段字符串**。Flights 示例只提供一个目标并独立校验结果；截图渲染器只在事后加标注，不驱动浏览器。

---

## 快速开始

```bash
git clone https://github.com/buer2233/jev-ultrafast.git
cd jev-ultrafast
uv sync
```

这是一个 fork，上游是 [browser-use/jev-ultrafast](https://github.com/browser-use/jev-ultrafast)。

### 一、跑本地 inspector 演示

```bash
cp .env.example .env
# 填入 TYPESAFE_API_KEY 与 TEXT_MODEL_API_KEY
uv run jev
```

打开 **http://127.0.0.1:8766**，点 **Start demo → Run automatically**。inspector 会展示编号元素、操作概率、目标概率和已执行的动作；**Choose next** 会在执行前暂停。

Chrome 通过 [Browser Harness](https://github.com/browser-use/browser-harness) 连接（`uv sync` 已装）。需要时跑 `uv run browser-harness --doctor`，并按提示在 Chrome 里允许远程调试。

> **Windows 上起不来时**：若在导入期报 `UnicodeDecodeError` / `codec can't decode byte`，那是中文 Windows 的区域编码问题（不是依赖问题）；若报 `DevToolsActivePort not found`，多半是**没有可连接的 Chrome**，而不是权限没开。两种情况的排查步骤见 [`.claude/skills/run-jev/SKILL.md`](.claude/skills/run-jev/SKILL.md)。

### 二、作为库使用

```python
from jev_ultrafast import Agent

with Agent(
    "https://www.google.com/travel/flights?hl=en",
    "Find one-way flights from Zurich to London on September 20, 2026, "
    "for one adult in economy. Stop when matching flight options are visible.",
) as agent:
    for state in agent.run():
        print(state["elapsed_ms"], state["status"])
```

用 `uv run --env-file .env python your_script.py` 运行。同一个策略可以跑完全不同的任务：

```bash
uv run --env-file .env python examples/run.py \
  --url https://en.wikipedia.org/wiki/Main_Page \
  --goal 'Find and open the Wikipedia article about Gödel’s incompleteness theorems.'
```

`uv run --env-file .env python examples/flights.py --keep-open` 会真实执行机票搜索、校验实际航线/日期/结果并保存轨迹；它不会选座或下单。

### 三、跑自然语言用例（本期新增）

**1. 配置环境与账号**（结构与内部接口自动化框架 api-test-E9 的 `config.json` 保持一致）

```bash
cp config.example.json config.json
# 填入 base_url 与 admin / employee1~5 账号
```

> ⚠️ **`config.json` 已被 `.gitignore` 忽略，且必须保持忽略**——本仓库是公开仓库，
> 真实账号与内网地址提交上去就是凭据泄漏。入库的只有占位模板 `config.example.json`。

**2. 写用例**（`cases/**/*.yaml`，用户只写自然语言）

已经有两条路可以写：

- **手上已有功能测试用例**（纯文本 / Excel / XMind / Word / CSV）→ 用 `/nl-case-author`，
  它负责把源材料读进来并转成下面的 YAML；
- **想直接看一条能跑的**→ [`cases/demo/weaver_site_tour.yaml`](cases/demo/weaver_site_tour.yaml)，
  公开网站上跑、不需要 E9 环境也不需要 config.json，是一条 10 步的完整演示。

```yaml
cases:
  - id: e9-workflow-add-bym
    name: 新建 BYM专用测试 流程并提交
    url: "{{ base_url }}/wui/index.html#/main/workflow/add"
    login: employee1                 # 接口登录后把 cookie 注入浏览器，用例免登录开跑
    goal: |
      1. 打开新建流程页面，页面上按分组列出了各种流程名称；
      2. 点击「BYM专用测试」，它会打开该流程的表单页面；
      3. 在「签字意见」里输入「同意流程」；
      4. 点击「提交」。
      提交成功后表单页会自动关闭，回到新建流程列表页；此时停止。
    expect_mode: and                 # 多断言合成口径：and / or / min_pass，逐用例决定
    expect:
      - type: ai                     # 语义断言：Jev 的 noul 给概率，阈值比较在代码里
        claim: 页面上显示的是新建流程的流程名称列表
      - type: text_not_contains      # 确定性断言
        value: "流转设定"
      - type: url_contains
        value: "/wui/index.html#/main/workflow/add"
```

**3. 执行并出报告**

用 `/nl-case-run` 也可以——你说"测一下新建流程"或"跑 cases/e9 下的用例"，
它负责挑出对应的用例、执行、出报告；如果发现该功能还没写成用例，会先转 `/nl-case-author`。

```bash
# 默认离线，自然语言用例不会跑
uv run pytest                                          # 34 passed, 3 skipped

# 显式请求才会执行（会调付费 API 并接管一个 Chrome 标签页）
uv run --env-file .env pytest tests/test_nl_cases.py --nl --reruns 1 \
  --alluredir=report/allure-results
allure generate report/allure-results -o report/allure-report --clean
```

一条 YAML 用例 → 一个 pytest 用例 → 一个 Allure story；每个决策周期是一个 step，
带操作概率、写入的文本、断言证据与截图。

**断言分两级**：执行期用 Jev 的 `choice` 概率做连续决策；终局是 pytest 的确定性 `assert`——
语义性预期由 Jev 的 `noul` 提供 0–1 的证据，**阈值比较与判定留在代码里**，不允许"问模型通过了吗"。
可用断言类型见 [`framework/assertions.py`](jev_ultrafast/framework/assertions.py)。

**重跑策略**：只做**用例级**重跑（`--reruns` 或 YAML 的 `reruns` 字段）。
绝不做步骤级重试——浏览器变更操作重试可能重复提交。

---

## 为什么它快

- **一个决策周期一次请求。** 操作与目标分支共享同一份观测状态。
- **默认循环里没有截图。** Jev 消费结构化状态。inspector 才开截图；演示视频用独立的连续录屏。
- **一次快照一次浏览器调用。** 原子地读取可见控件及其名称、值、文本，并保留对真实 DOM 节点的引用。
- **校验被选中的目标。** 点击会检查文档、表单值、目标本身与邻近上下文；动画本身不会触发新的预测。执行前重新解析几何并拒绝被遮挡的控件。
- **等有用的状态。** 往 combobox 输入后等可见建议出现（上限 200 ms）；富文本编辑区等 500 ms（编辑器要把内容同步进自己的数据模型）；其余交互最多等两个动画帧或 50 ms。这些读取都发生在执行被记录之后。
- **让后台标签页保持渲染。** 焦点模拟避免后台动画被节流，同时不切换 Chrome 的可见标签页。
- **只发可见文本。** 屏幕外的正文与页脚不会塞满模型上下文。
- **复用被打断的文本请求。** 只有在整个文本辅助输入完全一致时，已生成的值才会在"页面已过期"的重试中存活。

每一个被执行的目标都从**已观测节点**解析而来。执行器重新校验页面新鲜度与点击遮挡。
**模型输出永远不会变成选择器、坐标、shell 命令或可执行 JavaScript。** 文本辅助模型的输出必须先解析成一个小的 JSON 对象才会被输入。

---

## 源码导读

| 文件 | 行数 | 职责 |
| --- | ---: | --- |
| [agent.py](jev_ultrafast/agent.py) | 174 | 完整的主循环与文本辅助交接 |
| [snapshot.js](jev_ultrafast/snapshot.js) | 153 | 原子 DOM 快照、索引化控件、新鲜度守卫 |
| [browser.py](jev_ultrafast/browser.py) | 357 | 浏览器连接、当前几何、执行、登录态注入 |
| [model.py](jev_ultrafast/model.py) | 218 | 动态的操作/目标分支与文本生成 |
| [questions.py](jev_ultrafast/questions.py) | 28 | 给模型的指令文本 |
| [demo.py](jev_ultrafast/demo.py) | 145 | 本地 inspector |
| [framework/](jev_ultrafast/framework/) | 1018 | 配置、用例加载、断言判定、Allure 报告、E9 登录接入 |

---

## 实测证据与能力边界

### 证据

当前演示视频是一次 **7,073 ms** 的 Google Flights 任务。计时从首次页面观察之后开始，
包含模型调用、文本生成、浏览器操作、过期决策与加载等待。一次独立的复查校验了
单程设置、Zürich、London、2026 年 9 月 20 日，以及可见的航班选项。
视频 1× 播放，无开场停顿，结尾停顿 0.5 秒。

在六次交替运行（相同模型与设置）中，两版均 **3/3** 通过。中位任务耗时
**9.450 s → 7.092 s**，降低 **25%**；中位浏览器协议调用 **1,092 → 101**。
这是同一任务在同一浏览器 profile 上的三次重复，不是通用的可靠性基准。

同一个策略打开指定 Wikipedia 条目用了 **2.798 s**，通过本地酒店搜索/筛选任务用了 **1.896 s**。
运行记录、失败、源码哈希与测量边界都在 [performance.md](docs/performance.md)。

> **注**：以上数字来自**本期改造之前**的版本，改造后未重新测量。
> 本期新增了页面稳定等待、执行前滚动、CDP 超时放宽等改动，实际耗时以新测量为准。

本期新增框架侧的实测（E9 免登录注入、元素发现缺口、断言判别力、用例通过率等）
见 [`docs/一期改造/`](docs/一期改造/)。

### 边界

`DONE` 只是一个选择，仍需独立校验真实结果。DOM 读取器覆盖常见 HTML 与 ARIA 控件，
不是完整的无障碍名称规范。**以下能力仍在本 MVP 之外**：

Shadow DOM、canvas、上传、嵌套滚动、任意键盘控件，以及**跨源** iframe。

以下能力**已在本次改造中补上**：

- **弹窗新标签页**：点击以 `target="_blank"` 打开的新标签页会被自动跟随
  （实测 E9 的流程表单就是这种情况），并排除会被立即关闭的临时空白页。
- **同源 iframe 内的富文本编辑区**（如 CKEditor）：会被发现并作为可填写目标暴露，
  字段名取自同容器内的标签元素。
- **无 `href` 的锚点**（`<a title="…">`）：纳入元素发现范围。
- **屏幕外的字段**：执行前自动滚动到视野内再输入。
- **登录态注入**：可由接口登录后把 cookie 注入浏览器，用例免登录直接开跑。

被拥有的标签页共用现有的 Chrome profile。

---

## 开发与校验

```bash
uv run ruff check .
uv run pytest                                   # 默认离线：34 passed, 3 skipped
node --check jev_ultrafast/static/app.js
node --check jev_ultrafast/snapshot.js
uv build
```

**测试默认离线**，不调付费 API；自然语言用例要靠 `--nl` 显式请求才会跑。另外两条按需执行：

```bash
# 在本地浏览器里校验真实控件的新鲜度/遮挡/执行路径，不调用模型
uv run python scripts/check_guards.py           # 期望 PASS: 21 browser guard checks

# 自然语言用例（会调用付费 API 并接管一个 Chrome 标签页）
uv run --env-file .env pytest tests/test_nl_cases.py --nl --reruns 1 \
  --alluredir=report/allure-results
```

`scripts/record_flights.py <新目录>` 抓取带原始浏览器时间戳的录制；
`scripts/render_demo.py <录制目录>` 把那次已验证的运行按 1× 渲染并裁掉 Google 账号条。
凭据与原始轨迹保持不入库。工程约定（分层边界、断言口径、重跑策略、凭据边界）见 [AGENTS.md](AGENTS.md)。

---

## 上游项目

这个仓库是 [browser-use/jev-ultrafast](https://github.com/browser-use/jev-ultrafast) 的 fork，
浏览器 agent 内核由上游维护。上游的云服务等候名单：

> [!IMPORTANT]
> **The Browser Use Cloud waitlist is open.** Get early access to ultrafast browser agents in the cloud.
> **[Join the waitlist →](https://browser-use.com/ultrafast?utm_source=github&utm_medium=readme&utm_campaign=jev-ultrafast)**

---

[Browser Use](https://github.com/browser-use/browser-use) · [Browser Harness](https://github.com/browser-use/browser-harness) · [TypeSafe speculative fan-out](https://docs.typesafe.ai/patterns/fan-out)