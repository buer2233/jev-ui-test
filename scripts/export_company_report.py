"""把一次 Allure 运行结果导出成**公司规范格式**的 xlsx 测试报告。

三张工作表（测试方案 / 功能测试list及报告 / 测试截图），内容与版式的对应关系
见 `jev_ultrafast/framework/company_report.py` 的模块注释。

用法：

    uv run python scripts/export_company_report.py            # 最新一次结果 → report/公司格式测试报告_<时间戳>.xlsx
    uv run python scripts/export_company_report.py --results report/allure-results --out out.xlsx
    uv run python scripts/export_company_report.py --feature-title "【5017211】矩阵取值字段支持岗位类型（组织管理模块）"
    uv run python scripts/export_company_report.py --dev-owner 张三 --qa-owner 李四

    # 版式对照校验：与【随仓库分发的那份】公司模版逐项比（不加路径就用它）
    uv run python scripts/export_company_report.py --verify-template
    # 公司换了模版时，先拿新的那份验一遍（路径可覆盖默认）
    uv run python scripts/export_company_report.py --verify-template <新模版.xlsx>

退出码：0 正常；1 有**必须处理**的问题（用例缺断言截图、或对照校验没通过）。
"""

import argparse
import sys
from pathlib import Path

# 本机 Python stdout 是 GBK（cp936）：`⚠️` 这类字符在重定向到文件时会打成
# UnicodeEncodeError。编码不动（控制台里中文照常显示），只把编不出来的替换成 `?`。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(errors="replace")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from jev_ultrafast.framework import company_report  # noqa: E402


def build_parser():
    parser = argparse.ArgumentParser(description="导出公司规范格式的 xlsx 测试报告")
    parser.add_argument("--results", default=str(REPO_ROOT / "report" / "allure-results"),
                        help="Allure 原始结果目录（默认 report/allure-results）")
    parser.add_argument("--cases-dir", default=None,
                        help="用例目录（默认 cases/）。设计侧信息（测试项类别/check项/预期）从这里取")
    parser.add_argument("--out", default=None, help="输出 xlsx 路径（默认 report/公司格式测试报告_<时间戳>.xlsx）")
    parser.add_argument("--feature-title", default="", help="「研发功能」那一格的内容")
    parser.add_argument("--dev-owner", default="", help="研发负责人")
    parser.add_argument("--qa-owner", default="", help="测试负责人")
    parser.add_argument("--branch-bugs", default="", help="分支bug数")
    parser.add_argument("--release-date", default="", help="预计发布日期")
    # `nargs="?"`：不带值 = 用随仓库分发的那份模版；带值 = 用指定的那份。
    # 之所以要能"不带值"：新克隆的工作区里也只有这一份，命令越短越容易真的被执行。
    parser.add_argument("--verify-template", nargs="?", const="", default=None,
                        metavar="XLSX",
                        help="导出后与公司模版逐项对照版式；不带路径则用仓库里那份"
                             "（.claude/skills/nl-report-export/assets/）")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    out_path = Path(args.out) if args.out else company_report.default_output_path()

    summary = company_report.export(
        args.results, out_path,
        cases_dir=args.cases_dir,
        feature_title=args.feature_title,
        owners={
            "dev": args.dev_owner, "qa": args.qa_owner,
            "branch_bugs": args.branch_bugs, "release_date": args.release_date,
        },
    )

    print(f"报告已生成：{summary.path}")
    print(f"用例 {summary.cases} 条：通过 {summary.passed}、失败 {summary.failed}、跳过 {summary.skipped}")

    problem = False
    if summary.without_screenshot:
        # **必须响亮**：公司报告的「测试截图」页每条都要配图，缺图的那些只能写「（无截图）」。
        # 常见成因是那一次运行还在用旧版 runner（终局没拍图），重跑一次即可。
        print(f"[注意] {len(summary.without_screenshot)} 条用例没有断言截图：{summary.without_screenshot}")
        print("   「测试截图」页对应行会显示「（无截图）」。把用例重跑一次即可拿到图。")
        problem = True

    if args.verify_template is not None:
        # 空串 = 没给路径 = 用仓库里那份；`compare_with_template` 自己会解析默认值。
        checks = company_report.compare_with_template(summary.path, args.verify_template or None)
        print(f"\n对照模版：{args.verify_template or company_report.BUNDLED_TEMPLATE.name}")
        print(company_report.format_checks(checks))
        problem = problem or any(not check.ok for check in checks)

    return 1 if problem else 0


if __name__ == "__main__":
    raise SystemExit(main())