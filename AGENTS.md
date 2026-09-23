# Jev Ultrafast

改任何东西之前先读 README.md。保持循环足够小：页面 → 索引化元素 → 操作 + 目标 → 执行。

## 核心契约

- **输入是一个自然语言目标。** 不要在策略里加入站点专用的步骤计划或写死的字段值——那是用例（`cases/*.yaml`）该做的事。
- **一次请求同时选出操作和该操作对应的目标。** TypeSafe 返回多个目标分支（target head），只消费被选中操作的那一个。
- **目标必须落在已观测元素与受支持操作上。** 绝不让模型产出选择器或可执行代码。
- `TYPE_TEXT` 才调用文本大模型。过期重试时，只有在整个辅助模型输入完全一致的前提下才复用已生成的值。
- **绝不重试浏览器变更操作。** 先记录执行，再去观察结果。
- 截图可选，模型不消费截图。演示素材保持原始速度。
- **凭据保存在服务端**，`.env` 不入库。测试不得调用付费 API。
- **独立验证最终结果。** 模型选了 `DONE` 不等于成功。
- 示例、README 的说法、原始证据、模型调用次数四者必须保持一致。

## 分层与边界

| 层 | 位置 | 职责 |
|---|---|---|
| 库本体 | `jev_ultrafast/agent.py`、`browser.py`、`model.py`、`snapshot.js`、`questions.py` | 观测 → 决策 → 执行的循环，以及全部安全校验 |
| 框架层 | `jev_ultrafast/framework/` | 环境/账号配置、用例加载、断言判定、Allure 报告、E9 登录接入 |
| 用例 | `cases/**/*.yaml` | 用户只写自然语言目标与预期结果 |
| 用例入口 | `tests/test_nl_cases.py` + `tests/conftest.py` | 把 YAML 收集成 pytest 用例 |

- **框架是外挂的一层。** 库本体的状态机与各类校验的**语义**不因框架需求而改变；框架只负责用例加载、断言判定与报告。
- 为让内核在真实站点上可用，已对 `snapshot.js` / `browser.py` 做过一批**通用性扩展**（不针对任何特定站点）：元素发现范围、跟随新标签页、执行前滚动、登录态注入、重页面的 CDP 超时、页面稳定等待。**这些扩展只扩大"能看见/能操作什么"，不放松"怎么校验"**——新增动作同样要过新鲜度与遮挡校验。
- 继续扩展时守住这条线：**不要**因为某个站点难搞就放宽安全校验（例如为绕开遮挡检查而跳过 `elementFromPoint`）。那类改法会把"安全的自动化"变成"会误点的自动化"。
- **不要给库本体打 `allure.step` 之类装饰器。** `demo.py`（inspector）也依赖库本体，不应被报告框架污染；而且步骤标题需要动态内容，装饰器做不到。步骤包装放在 `framework/runner.py`。

## 断言

两级，边界不能混：

- **执行期**用 Jev 的 `choice` 概率做连续决策（`model.choose()`）。
- **终局**必须是 pytest 的确定性 `assert`。语义性预期由 Jev 的 `noul` 提供 0–1 的**证据**，**阈值比较与判定留在代码里**——不允许"问模型通过了吗"。
- 断言默认阈值 0.75，收敛在 `framework/config.py: DEFAULT_AI_THRESHOLD`，可按断言/用例覆盖。改阈值前先看 Allure 里的实测概率分布。
- 多断言的合成口径（`and` / `or` / `min_pass`）**逐用例声明**，不设全局默认。
- 断言要写得**原子、正向**：实测否定式表述只能拿到约 0.47，正面陈述同一事实可到 0.9+。

## 重跑

- **只允许用例级重跑**（`pytest --reruns N`，或 YAML 里的 `reruns` 字段）。
- **绝不做步骤级重试**：浏览器变更操作重试可能重复提交、产生垃圾数据。
- 设计上就该失败的负向对照用例设 `reruns: 0`，避免白跑。

## 凭据与仓库边界（**务必遵守**）

本仓库的 remote 是 **GitHub 公开仓库**（未鉴权即可读取），因此以下文件**只存本机，绝不提交**：

| 文件 | 内容 | 入库的替代物 |
|---|---|---|
| `config.json` | E9 测试环境地址、账号、图谱 MCP 地址 | `config.example.json`（占位模板） |
| `.mcp.json` | 图谱 MCP 的内网地址 | 由 `config.json` 的 `mcp` 块重建 |
| `.env` | 各模型 API Key | `.env.example` |

提交前自检：

```bash
git check-ignore -v config.json .mcp.json .env   # 三者都必须命中
git status --short | grep -E "config\.json|\.mcp\.json|\.env" && echo "❌ 敏感文件将入库" || echo "✅ 干净"
```

用例里也不要写真实内网地址，用 `{{ base_url }}` 变量。

## 校验

```bash
uv run ruff check .
uv run pytest                                   # 默认离线：32 passed, 3 skipped
node --check jev_ultrafast/static/app.js
node --check jev_ultrafast/snapshot.js
uv build
```

另外两条按需执行：

```bash
# 浏览器真实控件校验，不调模型
uv run python scripts/check_guards.py           # 期望 PASS: 21 browser guard checks

# 自然语言用例（会调用付费 API 并接管一个 Chrome 标签页）
uv run --env-file .env pytest tests/test_nl_cases.py --nl --reruns 1 \
  --alluredir=report/allure-results
```

## 其它规则

1. 大模型的思考和回复优先使用简体中文，新增的文档与代码备注也优先使用简体中文。
2. 未经用户明确要求，不要 commit 或 push。