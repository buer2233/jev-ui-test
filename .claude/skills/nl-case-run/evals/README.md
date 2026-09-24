# nl-case-run 的 evals

真跑 pytest 的收集链路与报告链路，逐条验 SKILL.md 说的事。

```bash
uv run python .claude/skills/nl-case-run/evals/run.py          # 免费档 + 报告档
uv run python .claude/skills/nl-case-run/evals/run.py --paid   # 再加上要调付费 API 的两条
uv run python .claude/skills/nl-case-run/evals/run.py --list
```

## 三档成本

| 档 | 用例 | 花什么 |
|---|---|---|
| `free` | 收集、筛选、默认跳过、离线基线 | 不花钱、不碰浏览器 |
| `allure` | 报告非空、步骤导出 | 要 allure CLI（本机 2.13.8） |
| `paid` | 演示用例通过、负向对照失败 | **调付费 API**，会接管一个 Chrome 标签页 |

## 断言的是什么

| 用例 | 断言 |
|---|---|
| `collects-e9-dir-only` | `--cases-dir` 是**替换**默认目录，只收集该目录的用例 |
| `case-filter-narrows-to-one` | `--case` 精确选中一条 |
| `unknown-case-id-fails-loudly` | 写错 id ⇒ 非零退出 + 明确指出该 id 不存在，**不得是 INTERNALERROR** |
| `nl-cases-skipped-by-default` | 不带 `--nl` 时 NL 用例全部跳过（离线契约不破） |
| `offline-suite-stays-green` | `uv run pytest` 基线不破 |
| `allure-report-is-not-empty` | 报告里**真有用例、真有步骤与附件** |
| `demo-case-passes` | 公开网站演示用例通过（付费） |
| `negative-control-fails` | 负向对照**因断言而失败**（付费） |

## 两个刻意的设计

**一、报告那条用合成探针，不靠付费用例。**
`allure-pytest ≥2.14` 会写 `titlePath`，2.13.8 的 CLI 认不出来就丢掉全部结果，
却照样打印 `Report successfully generated`。防的是这个静默失效。
探针是一个带 `allure.step` 的最小用例——"步骤有没有被导出"跟付不付费无关，
用合成用例就能免费守住，不必等一条 NL 用例跑完。

**二、负向对照必须是【因断言】失败。**
只看"失败了"是不够的：缺 API Key、页面打不开、浏览器掉线都会让它失败，
那样 eval 会替一个**根本没跑起来**的用例背书。所以额外要求输出里出现
`AssertionError` 且出自 `framework/runner.py`。

这条不是假想——第一次跑 `--paid` 时，eval 漏传了 `--env-file .env`，
NL 用例以 `KeyError: 'TYPESAFE_API_KEY'` 死掉，而负向对照**被判成了通过**。
现在不会了。

## 失败时保留完整日志

runner 在失败时会把 pytest 的完整输出（含 `--tb=long`）写到
`artifacts/skill-evals/pytest-failure-*.log`。
把输出的尾巴塞进报错信息会把 traceback 截断，低频偶发就再也查不清了——
那个 `KeyError: 'TYPESAFE_API_KEY'` 就是这么被截断丢过一次。