"""用例 YAML 的 `report:` 块：公司规范格式测试报告的**设计侧信息**。

为什么值得单独钉住：这三句（测试项类别 / check项 / 预期）是报告里唯一"人写的话"，
而它们**没有任何东西会替你检查内容**——写错了、键名拼错了，报告照常生成，
只是那一格少了一段话（或者回落到断言的技术口径）。所以校验必须在**加载期**响亮报错。

对照组件的写法沿用 `tests/test_e9_setup.py`（同一批 loader 测试在那儿）。
"""

import textwrap

import pytest

from jev_ultrafast.framework.loader import CaseError, load_cases

CASES_YAML = """\
version: 1
cases:
  - id: t-one
    name: 一号
    report:
      category: 发起·提交
      check: 提交后表单关闭并回到列表
      expect: 点「提 交」后表单关闭、回到流程名称列表
    url: "{{ base_url }}/a"
    goal: 演示
    expect:
      - type: url_contains
        value: /a
"""


def _load(tmp_path, body=CASES_YAML):
    (tmp_path / "cases.yaml").write_text(textwrap.dedent(body), encoding="utf-8")
    return load_cases(tmp_path)


def test_report_block_survives_loading(tmp_path):
    case = _load(tmp_path)[0]
    assert case["report"] == {
        "category": "发起·提交",
        "check": "提交后表单关闭并回到列表",
        "expect": "点「提 交」后表单关闭、回到流程名称列表",
    }


def test_missing_report_block_is_allowed(tmp_path):
    """**刻意不设必填**：老用例没写这一块时报告生成器有回落，报告仍然出得来。"""
    without = CASES_YAML.replace(
        '    report:\n      category: 发起·提交\n      check: 提交后表单关闭并回到列表\n'
        '      expect: 点「提 交」后表单关闭、回到流程名称列表\n', "")
    assert _load(tmp_path, without)[0].get("report") is None


def test_misspelled_key_names_the_alternatives(tmp_path):
    """键名拼错必须报错。

    静默忽略的代价很隐蔽：报告生成器按 `category` 取，写 `catagory` 就永远取不到，
    于是那一格回落到 feature——报告看起来完全正常，只是那一段话不是你想说的。
    """
    broken = CASES_YAML.replace("      category: 发起·提交", "      catagory: 发起·提交")
    with pytest.raises(CaseError, match="report 中未知字段"):
        _load(tmp_path, broken)


def test_empty_value_is_rejected(tmp_path):
    """空格串不是"没写"：它会让报告里那一格**看着有内容**（空白），实际什么都没有。"""
    broken = CASES_YAML.replace("      check: 提交后表单关闭并回到列表", '      check: "   "')
    with pytest.raises(CaseError, match="必须是非空字符串"):
        _load(tmp_path, broken)


def test_report_must_be_a_mapping(tmp_path):
    broken = CASES_YAML.replace(
        "    report:\n      category: 发起·提交\n      check: 提交后表单关闭并回到列表\n"
        '      expect: 点「提 交」后表单关闭、回到流程名称列表\n',
        "    report: 发起·提交\n")
    with pytest.raises(CaseError, match="report 必须是映射"):
        _load(tmp_path, broken)