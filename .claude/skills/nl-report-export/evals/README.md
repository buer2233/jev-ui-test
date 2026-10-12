# nl-report-export 的 evals

验的是**这份 skill 说的每件事**：导出的 xlsx 真有三张表、图该嵌的嵌、缺图会喊、
对照校验有判别力。全部离线——不调模型、不起浏览器。

```bash
uv run python .claude/skills/nl-report-export/evals/run.py            # 免费档（含与参考模版对照）
uv run python .claude/skills/nl-report-export/evals/run.py --list

# 要拿另一份模版对照（公司换了模版），覆盖参照物：
JEV_REPORT_TEMPLATE=<另一份模版.xlsx> uv run python .claude/skills/nl-report-export/evals/run.py
```

夹具是**运行期写出来**的合成 `allure-results`（`fixtures.py` → `artifacts/skill-evals/`，
已 gitignore）：skill 的 evals 必须能在**新克隆的工作区**里跑。
**参照物不用夹具**——它是仓库里那份真实的公司模版（`../assets/`），
这条 eval 就是在验"生成物与它像不像"。
三态各一条（通过 / 失败 / 跳过），前两条带截图、跳过那条没有——
这恰好覆盖"图该嵌的嵌、缺图写占位"这条交付契约。

## 各条验什么

| id | 验什么 |
|---|---|
| `export-builds-the-three-sheets` | 真跑导出脚本，再**读回 xlsx**：三张表齐全、表名与顺序、表头文字、冻结窗格 |
| `screenshots-embedded-and-marked` | 有图的两条真嵌了图；没图的那条写「（无截图）」；编号是 T01–T03 |
| `missing-screenshots-are-loud` | 缺断言截图时**退出码 1** 并说清缺的是什么 |
| `skipped-case-is-claimed` | 跳过用例（没有参数、没有标签）也占一行、有类别与 check项，且**不打确认勾** |
| `template-comparison-can-fail` | 拿一份被改坏的"模版"去对照，必须报「不一致」——校验要有判别力 |
| `offline-suite-stays-green` | `tests/test_company_report.py` 全绿 |
| `matches-the-company-template` | 与**仓库里那份**参考模版逐项对照；模版被删/被挪走时**响亮失败**（不是 SKIP） |

## 查出过什么

- **`--verify-template` 的初始版没有判别力**（2026-10-10 开发期）：只比"结构相等"，
  而生成器里几个合并区写错了（`F3:K5` 应该是 `F3:K3`）也能过——因为比的是**生成物与它自己**。
  现在最后一条断言是拿**被改坏的参照物**验它必须红。
- **确认列最初给跳过用例也打了 `√`**：那是"确认过"的意思，给没跑的用例打勾
  等于把没做的活报成做了。现在 `skipped` 一律留空，并有 eval 守着。
- **表头字体照抄会不一致**：「测试截图」页的表头在模版里**没有指定字体名与字号**
  （`font.size is None`），生成时按正文的 12 号写就会被对照校验报出来。
- **`plain_font` 那支把 `bold=True` 写死了**：「测试截图」页的**数据格**于是整页变粗体。
  表头查得再细也看不见数据格，所以对照校验补了一条**数据格样式**（字体名/字号/加粗/底色）——
  `「测试截图」数据格样式` 这一项就是这么来的。眼睛扫过去只会觉得"有点抢眼"，
  不会觉得是错；能抓住它的只有一条专门的检查。
- **"缺图"的告警一开始把跳过用例也算进去了**：跳过用例永远不会有图（模版自己也有
  「（无截图）」的行），每轮都响一次，真缺图那次就没人看了。现在只报"跑过但没图"的，
  eval 里两条都验（T04 要出现在提示里、T03 不能出现）。
- **上面的修正又让 `missing-screenshots-are-loud` 失去了对象**：夹具里原本唯一的"缺图"
  就是那条跳过用例。补了一条 `probe-noshot`（跑过、结果里没有截图，等于旧版 runner 的产物）
  才真的验到了那条告警。
- **"模版本机产物、不入库"曾经让这条保障形同虚设**（2026-10-12）：那时
  `matches-the-company-template` 要人先设 `JEV_REPORT_TEMPLATE`，新克隆的工作区跑出来只能是
  **SKIP**——而 SKIP 与"验过了"在输出上几乎一样（都没有 `[不一致]`）。现在模版随仓库分发，
  这条默认就跑；模版不在时`matches-the-company-template` **判 FAIL**（缺参照物 ≠ 验过了）。
