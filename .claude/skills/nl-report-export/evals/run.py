"""nl-report-export 的 evals：在**合成的结果目录**上真跑一遍导出，再读回 xlsx 校验。

分两档：
  free     全离线：真跑 `scripts/export_company_report.py`、真读生成出来的 xlsx
  template 与**随仓库分发的公司模版**逐项对照版式（`assets/` 下那份，**不需要任何环境变量**）：

             uv run python .claude/skills/nl-report-export/evals/run.py

         想拿另一份模版验（公司换了模版时），用 `JEV_REPORT_TEMPLATE=<路径>` 覆盖。

用法：
    uv run python .claude/skills/nl-report-export/evals/run.py
    uv run python .claude/skills/nl-report-export/evals/run.py --list
    uv run python .claude/skills/nl-report-export/evals/run.py --only export-builds-the-three-sheets
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
SCRATCH = ROOT / "artifacts" / "skill-evals" / "nl-report-export"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixtures import write_results  # noqa: E402

from jev_ultrafast.framework import company_report as cr  # noqa: E402

RUN_ENV = {"PYTHONIOENCODING": "utf-8"}


def export(*args, timeout=300):
    """跑导出脚本，返回 (退出码, 合并输出)。"""
    result = subprocess.run(
        [sys.executable, "scripts/export_company_report.py", *args],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, env={**os.environ, **RUN_ENV},
    )
    return result.returncode, (result.stdout or "") + (result.stderr or "")


def _fresh():
    shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)
    results = write_results(SCRATCH)
    cases_dir = SCRATCH / "cases"
    out = SCRATCH / "报告.xlsx"
    code, output = export("--results", str(results), "--cases-dir", str(cases_dir), "--out", str(out))
    return results, cases_dir, out, code, output


# ------------------------------------------------------------------ free


def ev_export_builds_the_three_sheets():
    """三张表齐全、表名与顺序与模版一致、表头文字对得上。

    这是 skill 的主承诺。**读回 xlsx 校验**而不是只看退出码：
    导出脚本返回 0 不代表文件里真有三张表（openpyxl 能在各种情况下"成功"存出半成品）。
    """
    import openpyxl

    _, _, out, code, output = _fresh()
    if code not in (0, 1):                      # 1 = 有缺图提示；这里夹具确实有一条缺图
        return False, f"导出退出码 {code}：{output.strip()[-200:]!r}"
    if not out.is_file():
        return False, "导出脚本没生成 xlsx"

    workbook = openpyxl.load_workbook(out)
    expected = ["测试方案", "功能测试list及报告", "测试截图"]
    if workbook.sheetnames != expected:
        return False, f"工作表不对：{workbook.sheetnames}"

    shots = workbook["测试截图"]
    headers = [shots[f"{column}1"].value for column in "ABCD"]
    if headers != ["用例编号", "用例名称", "结论 / 实际结果", "截图"]:
        return False, f"「测试截图」表头不对：{headers}"
    if shots.freeze_panes != "A2":
        return False, f"「测试截图」没冻结表头：{shots.freeze_panes!r}"

    listing = workbook["功能测试list及报告"]
    if listing["D7"].value != "检查点、要求及备注" or listing["B7"].value != "测试项类别":
        return False, f"「功能测试list及报告」表头不对：{listing['B7'].value!r} / {listing['D7'].value!r}"
    if listing["A1"].value != "测试用例清单":
        return False, f"标题不对：{listing['A1'].value!r}"

    plan = workbook["测试方案"]
    if plan["A18"].value != "测试大纲及要点":
        return False, f"「测试方案」少了测试大纲：{plan['A18'].value!r}"
    return True, "三张表齐全，表名/顺序/表头/冻结窗格与模版一致"


def ev_screenshots_are_embedded_and_missing_ones_are_marked():
    """有图的嵌图、没图的写「（无截图）」——**一条用例一行**，不许静默留白。"""
    import openpyxl

    _, _, out, _, _ = _fresh()
    shots = openpyxl.load_workbook(out)["测试截图"]
    if len(shots._images) != 2:
        return False, f"夹具里 2 条有图，实际嵌了 {len(shots._images)} 张"
    values = [cell.value for row in shots.iter_rows() for cell in row]
    if cr.NO_SCREENSHOT not in values:
        return False, "没有图的那条没有写「（无截图）」——留白会被读成漏填"
    codes = [shots[f"A{row}"].value for row in range(2, 6)]
    if codes != ["T01", "T02", "T03", "T04"]:
        return False, f"用例编号不对：{codes}"
    return True, "2 条用例嵌了断言截图，另 2 条（跳过 / 跑过但没图）写「（无截图）」，编号 T01–T04"


def ev_missing_screenshots_make_the_cli_complain():
    """缺图必须**响亮**（退出码 1 + 列出编号）：公司报告的配图是交付契约的一部分。

    静默通过会让"这次报告有 12 行（无截图）"完全没人发现——直到评审会上被人问。
    """
    results, _, _, code, output = _fresh()
    if code != 1:
        return False, f"有缺图时退出码应为 1，实际 {code}"
    if "断言截图" not in output:
        return False, f"提示里没说清缺的是什么：{output.strip()[-200:]!r}"
    if "T04" not in output:
        return False, f"提示里没列出缺图的编号（T04）：{output.strip()[-200:]!r}"
    if "T03" in output.split("[注意]")[-1]:
        return False, "把**跳过**的用例也报成缺图了——那是假警报（它压根没跑过）"
    return True, "缺断言截图时退出码 1 并列出编号（跳过的不算）"


def ev_skipped_case_is_claimed_by_its_parameterized_id():
    """跳过的用例（没有参数、没有标签）也要在报告里占一行、且认领到设计信息。"""
    import openpyxl

    _, _, out, _, _ = _fresh()
    listing = openpyxl.load_workbook(out)["功能测试list及报告"]
    rows = [listing[f"C{row}"].value for row in range(10, 13)]
    if "跳过用例也要在报告里占一行" not in rows:
        return False, f"跳过用例没认领到 check项，实际列表：{rows}"
    skip_row = 10 + rows.index("跳过用例也要在报告里占一行")
    if listing[f"E{skip_row}"].value not in (None, ""):
        return False, "跳过用例被打了「确认」勾——等于把没做的活报成做了"
    return True, "跳过用例占一行、有类别与 check项，且确认列为空"


def ev_template_comparison_can_actually_fail():
    """对照校验要有判别力：拿一份**被改坏**的参照物，它必须报「不一致」。

    没有这一条，`--verify-template` 全绿只证明"比过了"，不证明"比得对"。
    """
    import openpyxl

    _, _, out, _, _ = _fresh()
    reference = SCRATCH / "被改坏的参照.xlsx"
    workbook = openpyxl.load_workbook(out)
    workbook["功能测试list及报告"]["C7"] = "被改坏的列名"
    workbook.save(reference)

    checks = cr.compare_with_template(out, reference)
    if not any(not check.ok for check in checks):
        return False, "参照物的表头被改坏了，对照校验却全过——它没有判别力"
    text = cr.format_checks(checks)
    if "[不一致]" not in text:
        return False, f"输出里看不出哪一项不一致：{text[-200:]!r}"
    return True, "改坏一处表头后对照校验如实报「不一致」"


def ev_offline_suite_stays_green():
    """skill 承诺的离线基线：`uv run pytest` 不破（公司报告的单测在其中）。"""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_company_report.py", "-q"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300, env={**os.environ, **RUN_ENV},
    )
    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        return False, f"公司报告的单测没通过：{output.strip()[-300:]!r}"
    return True, output.strip().splitlines()[-1]


# ------------------------------------------------------------------ template


def ev_matches_the_company_template():
    """与**随仓库分发的**公司模版逐项对照版式（`assets/` 下那份）。

    不需要任何环境变量：新克隆的工作区里也就这一份，**它就是参照物**。
    想换一份验（公司换了模版）用 `JEV_REPORT_TEMPLATE=<路径>` 覆盖。
    """
    override = os.environ.get("JEV_REPORT_TEMPLATE")
    if override and not Path(override).is_file():
        return False, f"JEV_REPORT_TEMPLATE 指向的文件不存在：{override}"
    if not override:
        try:
            cr.bundled_template_path()
        except FileNotFoundError as error:
            # 模版被删/被移走 → **响亮失败**，不是"跳过"：没有参照物就验不了版式，
            # 而"跳过了"与"验过了"在输出上几乎一样（都没有 [不一致]）。
            return False, str(error)

    _, cases_dir, out, _, _ = _fresh()
    command = ["--results", str(SCRATCH / "allure-results"),
               "--cases-dir", str(cases_dir), "--out", str(out), "--verify-template"]
    if override:
        command.append(override)
    _, output = export(*command)
    text = output[output.find("[通过]") if "[通过]" in output else 0:]
    if "[不一致]" in output:
        return False, text.strip()[-500:]
    return True, text.strip()[-200:] or "对照校验全过"


CASES = [
    ("export-builds-the-three-sheets", "导出的 xlsx 三张表齐全、表头与模版一致", "free",
     ev_export_builds_the_three_sheets),
    ("screenshots-embedded-and-marked", "每条用例一张图；缺图写「（无截图）」", "free",
     ev_screenshots_are_embedded_and_missing_ones_are_marked),
    ("missing-screenshots-are-loud", "缺断言截图时退出码 1 并列出编号", "free",
     ev_missing_screenshots_make_the_cli_complain),
    ("skipped-case-is-claimed", "跳过的用例也占一行且不打确认勾", "free",
     ev_skipped_case_is_claimed_by_its_parameterized_id),
    ("template-comparison-can-fail", "对照校验有判别力（改坏表头会红）", "free",
     ev_template_comparison_can_actually_fail),
    ("offline-suite-stays-green", "公司报告的单测全绿", "free",
     ev_offline_suite_stays_green),
    ("matches-the-company-template", "与本机提供的公司模版逐项对照", "template",
     ev_matches_the_company_template),
]


def main():
    parser = argparse.ArgumentParser(description="nl-report-export 的 evals")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--only", action="append", metavar="ID")
    args = parser.parse_args()

    if args.list:
        for case_id, what, cost, _ in CASES:
            print(f"  {case_id:<34} [{cost:^8}] {what}")
        return 0

    selected = [case for case in CASES if not args.only or case[0] in set(args.only)]
    failures, skipped = [], []
    for case_id, what, cost, check in selected:
        try:
            ok, detail = check()
        except Exception as error:              # noqa: BLE001
            ok, detail = False, f"{type(error).__name__}: {error}"
        if ok is None:
            skipped.append(case_id)
            print(f"  SKIP  {case_id:<34} {detail}")
            continue
        print(f"  {'PASS' if ok else 'FAIL'}  {case_id:<34} {detail}")
        if not ok:
            failures.append(case_id)

    ran = len(selected) - len(skipped)
    print(f"\n{ran - len(failures)}/{ran} 通过" + (f"；跳过 {skipped}" if skipped else ""))
    if failures:
        print(f"失败：{failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())