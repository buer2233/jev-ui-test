---
name: nl-report-export
description: 把一次 Allure 用例运行结果导出成**公司规范格式**的 xlsx 测试报告（三张表：测试方案 / 功能测试list及报告 / 测试截图），供交付给公司内部评审。参考的公司模版**随仓库分发**（本 skill 的 assets/ 下），不用另找。只要用户说"出一份公司格式的测试报告""把 allure 报告导成 xlsx""转成测试报告模版那个格式""给测试报告套公司模版""这份报告要交付"，或者给了公司模版的 xlsx 让你照着生成，就用本 skill。它**只做转换**：不跑用例、不调模型、不改判结论；要跑用例先转 [nl-case-run](../nl-case-run/SKILL.md)。
---

# 把 Allure 结果导成公司规范格式的测试报告

一次运行 → 一份 xlsx。**纯离线**：不调模型、不起浏览器，只读 `report/allure-results`。

## 一、先确认"导哪一次"

导出器读的是 **Allure 原始结果目录**（`report/allure-results`），
不是 HTML 报告目录（`report/allure-report/<时间戳>/`）——HTML 里没有原始数据。

```bash
# 结果目录里有多少条、分别是什么状态（一眼看出是不是"上一次的残渣"）
uv run python -c "
from jev_ultrafast.framework import company_report as cr
for r in cr.load_results('report/allure-results'):
    print(f'{r.status:8s} {r.name}  重跑{r.retries}次')
"
```

⚠️ **`allure-results` 会跨次累积**。用户说"导刚才那一次"时，先确认目录里**只有那一次**的结果：
混进上一次的用例，报告里会多出几条谁也说不清的记录。要干净的运行，跑之前清一次结果目录
（`nl-case-run` 的 `scripts/run_nl_report.py` 会自动清）。

## 二、导出

**通常不用手工跑这一节**：`nl-case-run` 的 `scripts/run_nl_report.py` 跑完用例会把这份报告
**默认出好**，放在那次运行的 Allure 目录里、与它同一个时间戳。
只有"重出""换结果目录""换一次批量"时才手工调：

```bash
# 重出到某次运行的目录里（目录名就是时间戳）
uv run python scripts/export_company_report.py --cases-dir cases/e9 \
  --out "report/allure-report/<那次的时间戳>/公司格式测试报告_<那次的时间戳>.xlsx"

# 或临时指定一个位置
uv run python scripts/export_company_report.py --out artifacts/xxx/临时.xlsx
```

可选的表头信息（不传就留空，**不要瞎填**）：

| 参数 | 落到哪 |
|---|---|
| `--feature-title "【5017211】…（组织管理模块）"` | 「研发功能」那一格；缺省用用例的 epic·feature |
| `--dev-owner` / `--qa-owner` | 研发负责人 / 测试负责人 |
| `--branch-bugs` / `--release-date` | 分支bug数 / 预计发布日期 |
| `--results` / `--cases-dir` | 换结果目录 / 换用例目录（缺省 `report/allure-results` 与 `cases/`） |

## 三、三张表的内容从哪来

| 表 | 内容 | 来源 |
|---|---|---|
| 测试方案 | 测试环境（服务器/客户端） | Allure 结果里的 `_base_url`、执行摘要的视口与模型、生成时刻的操作系统与源码版本。**取不到就写「本次未采集」** |
| 测试方案 | 测试大纲及要点 | 每条用例一行：`<check项>（<测试项类别>）` |
| 功能测试list及报告 | 测试项类别 / check项 / 检查点（预期） | 用例 YAML 的 `report:` 块（按用例 id 对齐） |
| 功能测试list及报告 | 检查点（实际）/ 其他备注 | Allure 的「断言汇总」「执行摘要」——**逐条 detail 原文，不经过任何模型** |
| 测试截图 | 用例编号 / 用例名称 / 结论·实际结果 / 截图 | 结果里的 `断言截图` 附件（`reporting.SCREENSHOT_ATTACHMENT`） |

用例 YAML 里那三句长这样（写用例时就该写，见 `cases/e9/workflow_submit.yaml`）：

```yaml
    report:
      category: 发起·创建草稿
      check: 发起人只保存不提交时，流程停在创建节点（草稿未提交）
      expect: 点「保存」后表单不关闭：URL 由 iscreate=1 变为带 requestid=，……
```

**没写 `report:` 的老用例也有回落**（用 feature / 用例名 / 断言描述），报告出得来，
但「预期」那一段会是技术口径（`url_contains；text_contains；…`）——那种东西贴进公司报告不好看，
值得回头补上。

## 四、参考模版在仓库里（不用问别人要）

公司模版**随仓库分发**，就在本 skill 下：

```
.claude/skills/nl-report-export/assets/公司内部的测试报告模板.xlsx
```

它就是「公司规范格式」的**参照物**（三张表：测试方案 / 功能测试list及报告 / 测试截图）。
新克隆一份项目下来，不用再四处找模版——`--verify-template` 不带路径时用的就是它。

> ⚠️ 这份模版含**真实项目信息与内网系统截图**。它是**已确认的取舍**
> （与 `examples/` 里的演示报告、录屏同一类，见 AGENTS.md「凭据与仓库边界」的例外），
> 目的是让格式有据可依。**不要**因为"里面有内网信息"把它删掉或加 gitignore——
> 删了之后所有版式校验都会失去参照物。
> 已逐表扫过：无账号口令/密钥/cookie；**人名与账号已脱敏**成占位符
> （研发负责人→开发人员、测试负责人→测试人员、其余真人名→客户名）。
>
> 生成出来的报告**不进 git**（`report/` 已 gitignore），交付走邮件/IM 就行，
> 不必为"入库"另做确认。

公司换了模版怎么办：把新模版替换掉 `assets/` 下这一份，然后跑一次
`tests/test_company_report.py` 与下文的版式对照——**校验会告诉你哪些版式漂了**，
再决定是改 `framework/company_report.py` 顶部的布局常量，还是把新模版换回去。

## 五、导出后必须做的两件事

1. **看退出码与提示**。脚本在有 **用例缺断言截图** 时退出码为 1 并列出是哪几条——
   那些行在「测试截图」页只能写「（无截图）」。成因通常是那一次运行用的是旧版 runner，
   **把那几条用例重跑一遍**就有了（重跑属于执行，转 [nl-case-run](../nl-case-run/SKILL.md)）。
2. **版式对照**（交付前/改了版式常量后）：

   ```bash
   uv run python scripts/export_company_report.py --verify-template
   ```

   不带路径 = 与 `assets/` 里那份模版逐项比（要换参照物就 `--verify-template <另一份.xlsx>`）。
   它比表名/列宽/表头值/字体/底色/合并/冻结窗格/数据格样式，打印 `[通过]` / `[不一致]` 清单。
   **只比版式，不比内容**——内容必然不同（模版里是另一个功能的业务数据）。

## 六、不要做的事

- **不要手改生成出来的 xlsx**。版式要改就改 `framework/company_report.py` 顶部的
  布局常量（那是版式的唯一出处），改完跑 `--verify-template` 与
  `tests/test_company_report.py` 复查。手改的 xlsx 下次导出就没了。
- **不要为了"好看"调整结论**。通过/失败/跳过、断言明细、重跑次数都是执行事实；
  报告里的「其他备注」会显式写出"重跑 N 次后才得到这个结果"——**不要删掉它**。
- **不要把 `assets/` 里那份参考模版删掉或改成"本机才有的路径"**：参照物一旦不在仓库里，
  版式校验就只能 SKIP（而 SKIP 与"验过了"在输出上几乎一样）。要使另一份模版，
  是**替换**它，不是把它挪走。
- **不要拿报告去替代 Allure 报告**。报告是交付件，排查问题还是要看 Allure
  （步骤、决策概率、执行轨迹都在那边）。

## 七、evals

```bash
uv run python .claude/skills/nl-report-export/evals/run.py                  # 免费档（全离线）
uv run python .claude/skills/nl-report-export/evals/run.py --list
```

免费档跑的是**合成结果目录**上的端到端导出（真跑导出脚本、真读 xlsx 校验三张表），
外加两项：对照校验的自证（拿一份被改坏的"模版"验它**会红**）、
以及**与仓库里那份参考模版逐项对照**（不需要任何环境变量；要换参照物用 `JEV_REPORT_TEMPLATE`）。
各条验了什么、查出过什么，见 [evals/README.md](evals/README.md)。