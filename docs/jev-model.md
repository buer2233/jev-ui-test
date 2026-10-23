# Jev 模型介绍和使用教程

本框架里「点哪个元素、做什么操作」不是人写死的，也不是让大模型现编的，而是由 **[Jev 决策模型](https://typesafe.ai)**
在页面的候选元素里**打分选出来**的。这份文档前半段讲清楚 **Jev 是什么、它和常规大模型差在哪、为什么这个项目要用它**，
后半段是**怎么拿到 Key 并把本仓库的用例跑起来**——**首选经 [OpenRouter](https://openrouter.ai/~typesafe/jev-latest) 调用 Jev**
（一个 Key 即可、大陆网络可直连）；也可直连 TypeSafe 官方端点，或换成本地部署的兼容服务。

> **决策模型可配置。** 框架打的是 System One `/v1/systemone` 协议，不是"TypeSafe 这一家"——
> 把请求指向任何兼容实现（含内网自托管的开源决策模型）只需改几个 `.env` 键，
> 不配则行为与以前完全一致。见文末「[换成别的决策模型](#换成别的决策模型自托管--其它厂商)」。

---

## Jev 是什么

Jev 是 **TypeSafe 的 System One 模型**（System One Model）的第一个公开版本，专门为**自动化**而训练——
它要做的不是「找人聊天」，而是「替软件做决定」：

- **它的输出是决策，不是文字**：一次调用返回「在这个候选集合里选哪一个」，附带**带校准的概率**
  （每个候选各自的概率，以及这一次的置信度），而不是一段还需要再解析的自然语言；
- **它的训练目标和聊天模型不同**：TypeSafe 的说法是，RLHF 把模型训练得很会遵循指令，代价是
  「模式坍缩、过度自信、不可靠」；他们改用 RLCD（Reinforcement Learning for Calibrated Decisions），
  让模型**知道自己有多确定**，不确定就把概率说出来，由调用方决定要不要往上抛；
- **在浏览器里，它的动作空间是动态且带索引的**（上游 [jev-ultrafast](https://github.com/browser-use/jev-ultrafast) 的定位）：
  每次观察页面得到一张候选元素表，Jev 只在这张表里选；可选操作限定为 `CLICK` / `TYPE_TEXT` / `SELECT` /
  `SCROLL_UP` / `SCROLL_DOWN` / `WAIT` / `DONE` / `BLOCKED`；
- **一次请求同时返回两个决策**：「做什么操作」和「对哪个目标」（推测式 fan-out），只有被选中操作
  对应的那个目标头会被执行；
- **它不负责措辞**：需要输入文本时（操作是 `TYPE_TEXT`），由另外一个小语言模型按目标生成字段值；
- **它的输出到不了危险的地方**：模型输出永远不会变成选择器、坐标、shell 命令或可执行 JS。

## Jev 与常规大模型的区别

| | 常规大语言模型（LLM） | Jev 决策模型 |
|---|---|---|
| **输出什么** | 给人读的文字 / JSON，还要再解析成动作 | 给机器用的**类型化决策**：在候选集合里选索引，+ 每个候选的概率 |
| **优化目标** | 人类偏好（RLHF），指令跟随强 | **校准的决策**：确定就是确定，不确定要能说出有多不确定 |
| **输出的边界** | 可以输出任意字符串——包括不存在的选择器、坐标、JS | 输出只能落在**本次观察到的候选**里 |
| **置信度** | 不是它的输出物（token 概率 ≠ 决策置信度） | **每次决策自带**，可以直接拿去和阈值比较 |
| **一次决策的开销**（厂商口径） | 同一组对比里 LLM 侧 8.566 s / $0.01388 | 0.114 s / $0.000081；官网称「快 193.6×、省 444.6×」 |

> 最后一行是 **TypeSafe 官网口径**，不是本仓库实测，此处仅作参考；本仓库侧的实测在下一节。

## 为什么本项目的 UI 自动化要用 Jev

UI 自动化的每一步，本质是**在已经看得见的候选里做一次选择**，不是创作；而测试要的是**可重复、可判定、
可审计**——生成式输出那点「不确定性」，恰恰是测试最不想要的东西。所以这里不把截图丢给大模型让它「想」，
而是让 Jev 在候选表里「选」：

| | 大模型直接驱动 | Jev 决策模型驱动（本框架） |
|---|---|---|
| **模型输出什么** | 一段自由文本或 JSON，还要再解析成动作 | 在**本次观察到的候选元素里选索引**，选出来就是动作 |
| **会不会点到不存在的元素** | 会——选择器、坐标都是模型「写」出来的 | **结构上不能**：`choice` 必须落在候选 id 里、概率须归一、被选中的那条必须是最大值，否则整条响应被拒收并重发（`model.py` 的 `validate_choice`） |
| **一步多久** | 4–5 秒，个别 10 秒（改造前的使用经验，本仓库无机器可读证据） | Jev 一次请求同时给出「操作 + 目标」，**中位 458 ms**（p90 919 ms，来自随仓库分发的演示报告） |
| **不确定性怎么处理** | 文字里没有可用的置信度，只能「信」或「不信」 | 每步返回**带校准的概率**，可以当证据用 |
| **谁判定通过** | 常常是模型自己说「完成了」 | 概率只是**证据**，阈值与判定留在 pytest 代码里（`expect: type: ai`） |
| **失败怎么归因** | 一段模型输出 + 一张截图，得自己重看 | 报告逐行记候选数、目标索引、操作/目标概率、决策耗时、重发次数 |
| **成本** | 按 token 付费，长时间跑回归很贵 | 演示用例整套跑一遍：**2,663 次决策请求 / 3,542 万 token / $1.41**（控制台账单，见下文截图） |

**Jev 在其中起的关键作用**——它把「看页面 → 决定下一步」从**生成**换成了**打分**：

1. **动作空间是闭合的**：模型只能在当次快照出的候选里选，模型输出永远不会变成选择器、坐标或可执行 JS；
2. **一次往返出两个决策**：「操作 + 目标」在同一请求里返回（推测式 fan-out），只有被选中操作对应的那个目标头会被执行，
   省掉了「先想操作、再想目标」的第二次往返；
3. **概率可编程**：带校准的概率让框架能把**判定权留在代码里**——语义断言（`noul`）拿它当证据，阈值由人定；
4. **可审计**：每一步都是索引 + 概率 + 耗时的结构化记录，能逐行走查「它当时为什么这么点」。

---

## 使用教程

### 1. 拿到调用 Jev 的 Key

#### 1.1 推荐：经 OpenRouter 调用

OpenRouter 是模型聚合服务，它把 Jev 以**同一个 `POST /v1/systemone` 契约**转出来——对框架来说只换了端点与 Key，
请求体、响应体、模型名都不用动（[官方迁移说明](https://openrouter.ai/docs/guides/community/typesafe-sdk)：
"Change the base URL and the API key. Everything else stays the same."）。选它当首选的两个现实理由：

- **大陆网络可直连**：实测（2026-10-19）`openrouter.ai` 直连返回 HTTP 200，而直连 `api.typesafe.ai` 会拿到
  `HTTP 451`、响应体 `Typesafe is not available in your region.`——走 OpenRouter 就不必再配出口代理；
- **一个账号一把 Key**：计费统一走 OpenRouter 账户（按 token 计费，当前价格见
  [模型页](https://openrouter.ai/~typesafe/jev-latest)）。

1. 打开 <https://openrouter.ai/> 注册（邮箱或第三方登录）；
2. 进 **Keys** 页（<https://openrouter.ai/settings/keys>）创建一把 Key，形如 `sk-or-v1-...`，复制出来；
3. 按账户情况充值 / 确认额度（Jev 是付费模型；控制台的 Activity 页可查每一笔用量）。

> 模型页 `openrouter.ai/~typesafe/jev-latest` 是**别名**：它永远指向 Jev 家族的最新版
> （2026-10-19 实测路由到 `typesafe/jev-1.14-20261014`）。框架默认发的模型名就是 `jev-latest`，
> OpenRouter 会自己映射，不用改。

#### 1.2 备选：直连 TypeSafe 官方端点

1. 打开 TypeSafe 控制台：<https://console.typesafe.ai/home>
2. 用邮箱注册（无需绑卡）；**注册即送 5 美元调用额度**；
3. 左侧 **API Keys** → 新建一把 Key，复制出来（只显示一次，丢了就重建）。

![TypeSafe 控制台 Usage 页：近 30 天 2,663 次请求、3,542 万 token、花费 $1.41，左下角余额 $3.60](typesafe用量截图.png)

> 上面是控制台的 **Usage** 页（近 30 天窗口）。本仓库的演示用例就是在这些请求里跑出来的：
> **2,663 次决策请求、3,542 万 token、$1.41**；左下角余额 **$3.60** —— 正好是注册送的 5 美元
> 花掉这些之后的余量。
> 换算成一句话：**把整套演示用例跑一遍，成本在「几美元」这个量级**，不是按分钟烧钱的那种。
>
> ⚠️ 这条路径的前提是**你的出口能到 typesafe.ai**。直连报 `HTTP 451`
> （响应体 `Typesafe is not available in your region.`）时，这是**出口路由**问题、不是服务故障：
> 本机有可用代理就把 `HTTPS_PROXY` / `HTTP_PROXY` / `NO_PROXY` 三个一起写进 `.env`
> （只填代理不填 `NO_PROXY` 会把内网 E9 地址一起带走）。**不想折腾代理，就直接走 1.1 的 OpenRouter。**

### 2. 填进本项目

`cp .env.example .env` 之后按选定路径填（**决策**与**写字**是两个不同的模型，别混）：

**推荐路径（经 OpenRouter 调 Jev）**——`.env` 里写下面几行（模型名留空即默认 `jev-latest`）：

```bash
DECISION_BASE_URL=https://openrouter.ai/api/v1/systemone   # ⚠️ 要写到 /v1/systemone，见下方坑 1
DECISION_API_KEY=sk-or-v1-...                              # §1.1 创建的 OpenRouter Key
TEXT_MODEL_API_KEY=...                                     # 只在 TYPE_TEXT 时用来写字，可用任意 OpenAI 兼容端点
```

**备选路径（直连 TypeSafe）**：

```bash
TYPESAFE_API_KEY=...        # §1.2 复制的那把：决定「操作 + 目标」
TEXT_MODEL_API_KEY=...      # 只在 TYPE_TEXT 时用来写字
```

> ⚠️ 换端点时有两个坑，都属"配置看起来对、报错看起来像服务故障"的那类：
>
> 1. **`DECISION_BASE_URL` 必须是完整 URL，要写到 `.../v1/systemone`**。本框架不像 SDK 那样
>    自动补路径：照抄 OpenRouter 文档里的 SDK base（`https://openrouter.ai/api`）会被直接
>    当作端点请求，结果是 404。写到 `https://openrouter.ai/api/v1/systemone` 才对。
> 2. **必须显式写 `DECISION_API_KEY`**。只要设了 `DECISION_BASE_URL`，框架就按
>    "自建端点、可能免鉴权"处理，**不会**再回退旧键 `TYPESAFE_API_KEY`——只改端点不填新 Key，
>    表现是 `HTTP 401`。

| 环境变量 | 谁提供 | 干什么 | 默认值 |
|---|---|---|---|
| `DECISION_BASE_URL` | OpenRouter | 决策端点（**完整 URL，推荐 `https://openrouter.ai/api/v1/systemone`**） | `https://api.typesafe.ai/v1/systemone` |
| `DECISION_API_KEY` | OpenRouter | 决策服务凭据（`sk-or-v1-...`）；默认端点未设时回退 `TYPESAFE_API_KEY` | 无 |
| `TYPESAFE_API_KEY` | TypeSafe | 直连官方端点时的决策凭据（旧键，等价原路径） | 无（必填，默认端点下） |
| `TEXT_MODEL_API_KEY` | DeepSeek / 通义 / OpenRouter… | **写字**：只有操作是 `TYPE_TEXT` 时才被调用 | 无（`TYPE_TEXT` 用例必填） |
| `DECISION_MODEL` | — | 请求体里的 `model` 字段；OpenRouter 接受裸名 `jev-latest` 并映射到最新版 | 回落 `TYPESAFE_MODEL` → `jev-latest` |
| `TEXT_MODEL` | 同上 | 写字的模型 | `deepseek-chat` |

### 3. 它一次调用长什么样

一次决策 = 一个只读请求：`POST <决策端点>`（见 `jev_ultrafast/model.py`）——直连 TypeSafe
时是 `https://api.typesafe.ai/v1/systemone`，经 OpenRouter（推荐）时是
`https://openrouter.ai/api/v1/systemone`，**两条路径的请求体与响应体完全同形**。

- **发出去的**：`state` 是这一时刻页面的结构化快照（url / 标题 / 可见文本 / 候选元素表），
  `questions` 里一次问两件事 —— `operation`（做什么操作）与 `click_target` / `type_text_target` / `select_target`
  （每个操作各自的目标候选），**两个决策一次网络往返**；
- **回来的**：每个问题都有 `choice` + `confidence` + 每个候选的 `probabilities`。

三条硬校验（`validate_choice`）：选中的必须在本次候选表里、概率必须归一（和 ≈ 1）、被选中的那条必须是最大值。
**校验前**，客户端会对**偏离超过 2%** 的概率做等比归一（`normalize_probabilities`），并把这个问题的原始概率和记进报告
（`raw_probability_sums` / `probability_normalized`）；偏差在阈值内的响应**逐位不动**（Jev 实测 0.99~1.00，不受影响）。
不满足就**整条拒收并重发一次**——只重发这个只读的决策请求，浏览器动作一律不重发。

### 4. 怎么查用量

- **OpenRouter（推荐路径）**：控制台的 Activity 页可逐笔查用量与花费；
- **直连 TypeSafe**：Usage 页看 Spend / Tokens / Requests 与余额（就是上面那张截图）；
- **本项目侧**：报告里每一步都记了 `决策耗时`、`重发次数`、`模型版本`，跑完可以对着账单核——
  走 OpenRouter 时「模型」一栏显示的是 OpenRouter 的模型 ID（如 `typesafe/jev-1.14-20261014`），
  直连时是 `jev-1.x.y`，**一眼就能看出这次跑的是哪条路径**。

### 5. 注意

- **Key 不要入库**：`.env` 已被 `.gitignore` 忽略，本仓库是公开仓库，提交上去就是泄漏；
- 免费额度用完需自行充值，用量与账单以对应控制台为准（OpenRouter 或 TypeSafe）；走 OpenRouter 时
  **计费落在 OpenRouter 账户**，TypeSafe 控制台不会记这部分的量；
- 决策请求只读、重试安全；浏览器变更操作一律不重试（理由见 AGENTS.md「重跑」一节）。

---

## 换成别的决策模型（自托管 / 其它厂商）

决策请求打的不是"TypeSafe 这家公司"，而是 **System One `/v1/systemone` 这个协议**：
`state`（页面快照）+ `questions`（类型化问题）进去，每个选项的**校准概率**出来。
任何实现这个协议的服务都能直接接上——包括内网自托管的开源决策模型。**最常见的替换就是经 OpenRouter
调官方 Jev 本体**（即 §1.1 / §2 的推荐路径）；本节收录其余选择：其他厂商的兼容服务与本地部署。
配置全部走 `.env`（`jev_ultrafast/model.py` 的 `decision_endpoint` / `decision_credential` / `decision_controls`）：

| 环境变量 | 作用 | 不配时 |
|---|---|---|
| `DECISION_BASE_URL` | 决策端点（完整 URL，含路径） | `https://api.typesafe.ai/v1/systemone` |
| `DECISION_API_KEY` | 端点凭据 | 默认端点回落 `TYPESAFE_API_KEY`；**自定义端点只认这个键，留空=发空凭据** |
| `DECISION_MODEL` | 请求里的模型名（本地服务器会忽略；聚合网关用它路由） | 回落 `TYPESAFE_MODEL` → `jev-latest` |
| `DECISION_TIMEOUT_S` | 决策请求超时（秒） | `25` |

**不配任何 `DECISION_*` = 行为与以前完全一致**（仍是 TypeSafe 托管 + `jev-latest` + 25s）。
凭据刻意与端点同源：一旦改了指了自定义端点，就**不会**再把 TypeSafe 的 key 发过去。

### 例：内网自托管 StartLux-Decision

[StartLux-Decision](https://github.com/StartLuxLabs/Startlux-Decision) 是 2026-10 开源的
决策模型家族（0.8B–27B，Instant / AutoThink 两种形态）。它的推理服务器**明确兼容
System One `/v1/systemone`**（服务端文档原话："TypeSafe clients work against it"），
在本框架里改三四个配置键即可接上：

```bash
# .env
DECISION_BASE_URL=http://<内网GPU主机>:8090/v1/systemone
DECISION_API_KEY=                     # 服务器没开鉴权就留空
DECISION_MODEL=startlux-decision-27b-autothink
DECISION_TIMEOUT_S=120                # 会思考的 AutoThink 响应可远超 25s
```

服务器侧（需要 GPU：27B ≥ 80G 级单卡；9B 约 20G；4B 消费级即可）：

```bash
git clone https://github.com/StartLuxLabs/Startlux-Decision.git
pip install -e '.[vllm]'
startlux-decide serve --model models/StartLux-Decision-v1.1-9B-AutoThink --launch-vllm --gpus 0 --port 8090
```

> 状态说明（2026-10-20）：**配置化改造与离线契约测试已完成**（默认路径逐字段不变已用测试钉住）；
> **经 OpenRouter 调用 Jev 已端到端验证**——真实 UI 用例跑通，决策与 `noul` 语义断言两条链路
> 回读到的模型 ID 均为 `typesafe/jev-1.14-20261014`（OpenRouter 侧的服务模型）；
> **概率归一化兼容已实施**：偏差 >2% 的响应在校验前等比缩放，原始概率和进报告，
> 对 Jev 是逐位 no-op（见 `tests/test_decision_model_normalization.py`）；
> **本地部署的端到端跑通尚未验证**——需要一台有 GPU 的机器真的把服务起起来才算数。

### 思考控制（StartLux 专有，可选）

AutoThink 模型"不确定就写推理再重读"，推理最长 4096 token——**默认 25s 超时会被它拖垮**。
三个可选键（留空则**不发送**，跟随服务器默认；对 Jev 端点无副作用）：

| 环境变量 | 取值 | 作用 |
|---|---|---|
| `DECISION_THINK_MODE` | `auto` / `never` / `always` | 起步建议 `never`（单遍回答，和 Jev 一样快）；稳定后再试 `auto` |
| `DECISION_GATE` | 0–1 | `auto` 模式下首遍读数低于它才思考（服务器默认 0.6） |
| `DECISION_THINK_BUDGET` | ≥1 | 推理最长 token 数，兜底控制单次耗时 |

⚠️ 给 **Instant** 模型显式发 `think_mode=auto` 会被服务器 422 拒绝——所以留空（不发）
是安全默认；不确定模型类型时优先 `never`。

### 四个必须知道的差异（协议兼容但行为不同）

1. **候选超过 26 个会分组两轮**：StartLux 对 >26 个选项先按顺序分组初筛（每组取 3）
   再决赛；Jev 是全量单次读。输出概率仍归一、执行与校验不受影响，但两家的决策
   分布形态不同——A/B 对比时别拿概率数值直接比。
2. **别给自托管服务器开 `--allow-none`**：开了之后 `choice` 可能返回 `null`，
   而框架的 `validate_choice` 要求"选中必须是候选之一"，整条响应会被拒收重发。
3. **报告自动记录实际模型与端点**（Allure 的「执行摘要」→ environment），
   A/B 对比时以它为准，不靠记忆。
4. **`DECISION_TIMEOUT_S` 要配得比最坏情况宽**：超时后 `post_json` 会做有界重试，
   配小了不是"立刻失败"，而是"每次都要等两轮超时"。

⚠️ **合规提醒**：
- StartLux 的**模型权重**是 **CC BY-NC 4.0（非商业使用；商用需向 StartLux 取得授权）**，
  只有推理代码是 Apache-2.0。企业环境使用前请先过法务；
- 把 `DECISION_BASE_URL` 指向任何**外部托管**服务（包括现状的 TypeSafe、以及
  第三方免费网关如 bamboonode.dev），都意味着**测试页面的内容会发出去**；
  只有**内网自托管**能让页面数据不出内网。