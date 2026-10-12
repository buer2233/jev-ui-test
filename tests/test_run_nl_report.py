"""`scripts/run_nl_report.py` 里**可测的那几块**：参数解析、报告路径、命令构造。

为什么单独钉住"两份报告同目录、同时间戳"：这是交付契约——
验收的人打开**这一次运行的目录**就该拿到两份报告。分开算时间戳或分开放，
症状不是"报错"，而是"验收时找不到/对不上号"，而那种事在报告里看不出来。

整条链路（真跑 pytest + allure generate）不在这里测：那要钱、要浏览器，
由 `.claude/skills/nl-case-run/evals` 与 `nl-report-export/evals` 各管一段。
"""

import sys
from pathlib import Path

# scripts/ 不在包里，按路径加进来——和 scripts/*.py 里 `sys.path.insert(REPO_ROOT)` 同一套路。
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import run_nl_report  # noqa: E402

from jev_ultrafast.framework import company_report  # noqa: E402


def test_company_report_lands_in_the_run_directory_named_after_it():
    """公司报告写进**那次运行的** Allure 目录里，文件名带同一个时间戳。"""
    report_dir = Path("report/allure-report/20261012_104844")
    target = company_report.path_for_run(report_dir)

    assert target.parent == report_dir
    assert target.name == "公司格式测试报告_20261012_104844.xlsx"


def test_company_report_name_keeps_the_timestamp_even_though_the_dir_has_it(tmp_path):
    """目录名已经带时间戳了，文件名为什么还带？——

    这份 xlsx 一定会被**拷走**（发邮件、进评审附件）。离开那个目录之后，
    文件名上的时间戳就是它唯一的"出自哪一次运行"的线索。
    """
    target = company_report.path_for_run(tmp_path / "20261012_104844")
    assert "20261012_104844" in target.stem


def test_build_pytest_command_adds_reruns_once_and_keeps_passthrough():
    command = run_nl_report.build_pytest_command(["--cases-dir", "cases/e9"])

    assert command.count("--reruns") == 1
    assert command[command.index("--reruns") + 1] == "1"
    assert "--nl" in command
    assert command[-2:] == ["--cases-dir", "cases/e9"]          # 透传参数原样在尾部


def test_build_pytest_command_does_not_add_reruns_when_the_caller_did():
    """调用方自己给了 `--reruns N` 就以它为准——补两次会让 pytest 直接报错。

    判据是"整条命令里**只有一个** reruns 声明"，不是"数 `--reruns` 出现几次"：
    调用方写 `--reruns=3` 时那是一个 token，数 `--reruns` 会得 0 次（初版就这么写错了）。
    """
    def reruns_specs(command):
        return [arg for arg in command if arg == "--reruns" or arg.startswith("--reruns=")]

    for spelling in (["--reruns", "3"], ["--reruns=3"]):
        command = run_nl_report.build_pytest_command(spelling)
        assert len(reruns_specs(command)) == 1, spelling      # 没有补第二次
        # 值来自调用方（拼起来看，因为两种写法一个是两 token、一个是一 token）
        assert "3" in " ".join(command), spelling
        assert "--reruns 1" not in " ".join(command), spelling


def test_cases_dir_is_read_from_passthrough_in_both_spellings():
    """`--cases-dir` 两种写法都要认（`--cases-dir X` 与 `--cases-dir=X`）。

    取不到它的代价：公司报告的「测试项类别 / check项 / 预期」会去读默认的 `cases/`，
    于是报告与本次真正跑的那一批用例脱钩——大多数情况下按 id 仍能命中，但那是碰巧。
    """
    assert run_nl_report.cases_dir_from(["--cases-dir", "cases/e9"]) == "cases/e9"
    assert run_nl_report.cases_dir_from(["--cases-dir=cases/e9"]) == "cases/e9"
    assert run_nl_report.cases_dir_from(["--case", "tc01"]) is None


def test_cases_dir_at_the_end_without_a_value_does_not_crash():
    """`--cases-dir` 写在末尾且没值（pytest 自己会报错）时，这里不能先 IndexError。"""
    assert run_nl_report.cases_dir_from(["--reruns", "1", "--cases-dir"]) is None