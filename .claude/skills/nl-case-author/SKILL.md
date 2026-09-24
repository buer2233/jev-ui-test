---
name: nl-case-author
description: 把各种格式的功能测试用例（纯文本、Excel/xlsx、XMind 脑图、Word、CSV、Markdown）转换成本项目的 YAML 自然语言用例，落到 cases/e9/ 下。只要用户说"把这份用例转成 YAML"、"编写用例"、"新增一条用例"、"这几条测试用例帮我加到项目里"、"Excel/XMind 里的用例导进来"、"这条用例怎么写"，或者手上有一份功能测试用例要变成可执行的 UI 自动化用例，就用本 skill。写完之后要跑，转用 nl-case-run。
---

# 把功能测试用例转成 YAML 自然语言用例

这个项目里，一条用例 = 一个 YAML 文件里的一项，只有**自然语言的目标（goal）**和**预期（expect）**。
没有选择器、没有坐标、没有站点脚本——`snapshot.js` 从真实 DOM 观测出候选元素，模型只在候选里选。

所以转写的本质是：**把「操作步骤 + 预期结果」翻译成「一个自然语言目标 + 若干可判定的断言」**，
而不是把步骤机械地映射成 API 调用。

读 [AGENTS.md](../../../AGENTS.md) 的分层与断言两节再动手。

## 一、先读源材料

各种格式先转成纯文本再读。用本 skill 的脚本（输出写文件而不是打屏——本机控制台是 cp936，中文打屏会变问号）：

```bash
uv run python .claude/skills/nl-case-author/scripts/read_source.py <源文件>
# -> 已写出 artifacts/case-source/<文件名>.txt，然后读那个文件
```

| 格式 | 支持情况 |
|---|---|
| `.txt` `.md` `.csv` `.json` | 直接读 |
| `.xmind` | 直接读（zip 里的 `content.json` / `content.xml`） |
| `.docx` | 直接读（zip 里的 `word/document.xml`） |
| `.xlsx` | 需要 openpyxl，**用 uv 临时带上**：`uv run --with openpyxl python .claude/skills/nl-case-author/scripts/read_source.py 用例.xlsx` |
| `.xls` | 不支持，让用户另存为 `.xlsx` 或 CSV |
| 截图 / PDF | **让用户把用例手抄成文本**。不要看着图片猜断言——猜出来的断言会在评审时被发现是编的 |

源材料里通常有「用例编号 / 用例名称 / 前置条件 / 操作步骤 / 预期结果 / 优先级」这几列。
对应关系：编号 → `id`，名称 → `name`，操作步骤 → `goal`，预期结果 → `expect`，优先级 → `severity`。

**前置条件里凡是「已登录 / 已进入某页面」这类**，不要写进 goal：登录是框架的前置（`login:` 字段），
起始页面是 `url:` 字段。让 goal 从页面已经打开之后开始描述。

## 二、写进哪个文件

默认落在 `cases/e9/`。一个**模块**一个文件（例如 `workflow_add.yaml`、`doc_search.yaml`），
文件里可以放多条同模块的用例。文件名用小写加下划线。

`cases/demo/` 放的是公开网站上跑、不需要 E9 环境的演示用例——功能用例不要放这里。

`id` 全局唯一，推荐 `<模块>-<动作>` 形式（`e9-workflow-add-bym`）。
重复 id 会在加载期直接报错，不会静默覆盖。

## 三、YAML 结构

```yaml
version: 1

# defaults 里的键会被每条用例继承，用例内的同名键覆盖它。
defaults:
  login: employee1          # employee1~5 / admin / none
  epic: E9-UI自动化
  feature: 流程管理
  severity: critical
  timeout_s: 420
  max_steps: 30

cases:
  - id: e9-workflow-add-bym
    name: 新建 BYM专用测试 流程并提交      # 会同时成为 Allure 的 story 与 title
    tags: [e9, workflow, smoke]
    url: "{{ base_url }}/wui/index.html#/main/workflow/add"
    goal: |
      用自然语言写清楚"做什么"。多步骤用编号列出来，每步一句话。
      提交成功后该表单页会自动关闭，回到新建流程的列表页；此时停止。
    expect_mode: and
    expect:
      - type: ai
        desc: 已回到流程名称列表页
        claim: 页面上显示的是新建流程的流程名称列表
      - type: text_not_contains
        value: "流转设定"
```

### 可用字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` | ✅ | 全局唯一 |
| `name` | ✅ | 中文用例名，Allure 里直接展示 |
| `url` | ✅ | 起始地址。E9 用 `{{ base_url }}/...`，**不要写真实内网地址** |
| `goal` | ✅ | 自然语言目标，上限 2000 字 |
| `expect` | ✅ | 非空断言列表 |
| `login` | | `employee1`~`employee5` / `admin` / `none`（默认 none） |
| `epic` `feature` `severity` `tags` | | Allure 分组；severity ∈ blocker/critical/normal/minor/trivial |
| `expect_mode` | | `and` / `or` / `min_pass`，**逐用例决定**，见下 |
| `min_pass` | | 仅 `expect_mode: min_pass` 时需要，落在 1~断言条数之间 |
| `threshold` | | 用例级语义断言阈值；不写就用全局默认 0.75 |
| `timeout_s` `max_steps` | | 执行预算，默认 300s / 60 步 |
| `reruns` | | 用例级重跑次数。设计上就该失败的负向用例设 `0` |

字段名写错**会在加载期报错**，不会静默忽略——这是刻意的，静默忽略会让整条断言悄悄失效。

## 四、写 goal：把步骤翻译成意图

goal 是给模型看的，不是给解析器看的。三条规则：

1. **写"要达成什么"，不写"点哪个坐标"**。`点击「BYM专用测试」` 是对的；
   `点击 #ecid-29384` 是错的（ecid 里是随机数，下次就变）。
2. **每个动作点到具体的元素名字**。模型靠名字在候选表里认元素，含糊的描述会让它选错。
3. **结尾明确"什么时候停"**。不写终点，模型不知道何时该选 `DONE`。

### 两个必须知道的行为事实

它们是实测出来的，不写进 goal 就会踩：

- **断言只对【终局页面】求值。** 中间步骤做过什么，终局断言看不见。
  所以**最后一步必须承载全部证据**：要校验某个输入框的值，就让那个页面成为最后一站
  （见 `cases/demo/weaver_site_tour.yaml` 为什么把案例页放在最后一站）。
- **agent 没有"后退"。** 路径只能前进。站点内换页要用页面上的导航，不能指望浏览器历史。

### 写 goal 时常见的坑

| 坑 | 表现 | 怎么办 |
|---|---|---|
| 元素在屏幕外 | 模型看不到该元素，于是跳过它 | 元素必须落在视口内才会成为候选。让 goal 明确写"向下滚动，找到 X"，或把目标拆成两步 |
| 异步加载 | 点完后页面还是空壳，模型以为没结果 | goal 里写"结果要等一下才出来，如果还没出现就先等待" |
| 目标名有随机数 | 每次都不一样 | 用稳定文案定位（`title` 属性、标签文字），避开 id/ecid |
| CJK 字间空格 | 界面上是「提 交」不是「提交」 | 别在 goal 里逐字抠字面量；让模型按语义找，断言侧用 `text_contains` 时也要注意 |
| **字段的可访问名是无关内容** | 文本助手判定"goal 要的值不在这个字段上"，返回 `{"text": null}`，或从 goal 的多个可填值里挑错一个 | 见下 |

**关于"字段名是无关内容"这条，值得单独说清楚**（实测，2026-09）：百度首页那个输入框的
可访问名来自 placeholder，内容是**当天的热搜词**——一条与搜索毫无关系的新闻标题。
于是 goal 说"在搜索框里输入泛微网络"，模型只能落在它身上（它是唯一可见的 textbox），
而文本助手看到字段名是新闻标题，判定值不匹配：

- 同一输入连测两次都返回 `null`；
- 把字段名换成正常占位符（"请输入搜索关键词"），连测两次都正确返回「泛微网络」；
- 在 goal 里额外说明"忽略那段占位文字"**也没用**，仍返回 `null`；
- goal 里只有一个可填值时，结果在 `null` 和正确值之间**摇摆**（1/2）。

这是可复现的确定性阻塞，不是抖动。**遇到就换路径**——换个起点 URL、换一条不经过该字段的
路线，或换一个字段名与用途对得上的站点。不要靠调措辞去绕，也不要为它放宽断言。

推论：**一条 goal 里尽量只留一个待填值。** goal 里有多个 `TYPE_TEXT` 目标时，
字段名不明确的那些会被填错（实测：该填「泛微网络」的框被填进了 goal 里另一个值「制造」）。

## 五、写 expect：断言的两级边界

**执行期**用 Jev 的 `choice` 概率做连续决策——那是模型的事，不用你写。
**终局**必须是 pytest 的确定性 `assert`。语义性预期由 Jev 的 `noul` 给 0–1 的**证据**，
**阈值比较与判定留在代码里**——不允许"问模型通过了吗"。

### 断言类型

| type | 必填 | 判据 |
|---|---|---|
| `ai` | `claim` | 语义断言。`noul ≥ threshold` 才算通过。**写正面、原子的事实描述** |
| `url_contains` / `url_equals` | `value` | URL 包含 / 等于（`url_equals` 可加 `ignore_query: true`） |
| `text_contains` / `text_not_contains` | `value` | 页面可见文本包含 / 不包含。`text_contains` 可加 `count: N` 要求至少出现 N 次 |
| `element_exists` / `element_absent` | `label` | 按可操作元素的 label 找得到 / 找不到。可加 `exact: true`、`role: button` |
| `element_value` | `label` + `equals` 或 `contains` | 校验元素当前的值 |
| `element_count` | `label_contains` + `equals`/`min`/`max` | 匹配某子串的元素个数 |
| `element_enabled` | `label` | 元素存在且未被禁用 |

### 选型原则：**先找确定性信号，找不到才用 `ai`**

确定性断言能表达就别用 `ai`——它免费、不抖、失败原因一眼可见。
只有当预期本身是语义判断（"页面上显示的是不是 XX 列表"）时才用 `ai`。

### 把预期写成"原子、正向"

实测：同一事实，否定式表述只能拿到约 **0.47**，正面陈述能到 **0.9+**。

```yaml
# ❌ 否定式，实测 0.47 —— 会失败
- type: ai
  claim: 表单页已经关闭，当前不在新建流程页面上

# ✅ 正面陈述同一件事，实测 0.9+
- type: ai
  claim: 页面上显示的是新建流程的流程名称列表
```

### `expect_mode` 逐用例决定，没有全局默认

- `and` —— 全部通过才算通过。默认选它，但**确认每条断言都真的必要**：
  一条弱断言会把整条用例拖成假失败。
- `or` —— 任一通过即可。用于"两种正确结果都算对"的场景。
- `min_pass` + `min_pass: N` —— 至少 N 条通过。用于多条弱证据互补的场景。

## 六、写完必须校验

```bash
# 1) 结构校验：加载期会把字段名、必填项、枚举值、id 重复全查出来
uv run python -c "
from jev_ultrafast.framework.loader import load_cases
for c in load_cases():
    print(c['id'], '|', c['name'], '|', c.get('__source__'), '|', c.get('_skip') or 'OK')
"

# 2) 离线回归：确认没把默认离线的测试弄坏
uv run pytest -q

# 3) 静态检查
uv run ruff check .
```

`load_cases()` 打出 `_skip` 说明用例依赖的变量没解析（例如 `{{ base_url }}` 而没配 E9 环境）。
这不是结构错误——用例仍会被收集，执行时以明确原因跳过。

上面这些说法都有 eval 逐条守着，见 [evals/](evals/)：

```bash
uv run python .claude/skills/nl-case-author/evals/run.py
```

它们会现场生成各格式的样本（xmind / docx / csv / xlsx），真跑一遍读源脚本，
再用项目规则体检产出的 YAML。全免费，不调用模型。

**写完不要自己跑真实用例**（会调付费 API、会在 E9 上产生真实数据）。
要跑就转 [nl-case-run](../nl-case-run/SKILL.md)，由它跟用户确认。

## 七、交付时说什么

告诉用户：

1. 转出了几条用例、落在哪个文件；
2. **哪几条断言是你替用户做的判断**（尤其是 `ai` 的 claim 措辞和 `expect_mode`）——
   这些是需要用户确认的业务语义，不是格式转换；
3. 源材料里**没能转过来的部分**和原因（例如"前置条件依赖另一条用例的中间状态，
   而 agent 没有后退动作，这条路径走不通"）。

源材料里读不懂或自相矛盾的地方，**回头问用户**，不要自己补一个看起来合理的断言。
编出来的断言会在执行时变成一条永远失败或永远通过的用例，比缺一条用例更糟。