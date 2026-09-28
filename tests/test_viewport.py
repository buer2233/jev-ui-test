"""逐用例的浏览器窗口尺寸：解析与"不许静默回落"。

为什么要有这个口子：**视口外的元素不会成为候选**（snapshot.js 的几何过滤）。
E9 的流程设计器是 1728×864 的覆盖层，在默认 1120×780 下左边缘被切掉，工具栏最左边的
「创建」因此落在视口外、对 agent 不存在。人换个大窗口就能看见，所以用例侧如实放大窗口——
而不是去放宽"必须落在视口内"那条几何判据（AGENTS.md 明令不许为绕开站点难处放宽校验）。

这里钉住两件事：
  · 两种写法（YAML 列表 / "1920x1080" 字符串）都收；
  · **不合法就响亮报错，不静默回落默认视口**——回落会让用例在一个更小的窗口里跑，
    症状是"模型找不到明明存在的按钮"，而且报告上看不出来。
"""

import pytest

from jev_ultrafast.agent import Agent
from jev_ultrafast.browser import VIEWPORT, Browser
from jev_ultrafast.framework import loader
from jev_ultrafast.framework.config import parse_viewport


def test_viewport_accepts_both_spellings():
    assert parse_viewport([1920, 1080]) == (1920, 1080)
    assert parse_viewport("1920x1080") == (1920, 1080)
    assert parse_viewport("1920×1080") == (1920, 1080)      # 中文全角乘号
    assert parse_viewport(" 1600 x 900 ") == (1600, 900)
    assert parse_viewport(None) is None                     # 没配就用库的默认


@pytest.mark.parametrize("bad", ["1920", [1920], [1, 2, 3], "1920x", "axb", [0, 100], [-1, 100]])
def test_bad_viewport_raises_instead_of_falling_back(bad):
    """不合法必须抛错。

    静默回落到 1120×780 会让"编辑器左边缘被切掉"这类问题在报告里变成"模型乱点"，
    排查代价极高（这正是本次加这个开关的起因）。
    """
    with pytest.raises(ValueError):
        parse_viewport(bad)


def test_yaml_case_may_declare_a_viewport():
    """`viewport` 必须是用例的合法键（顶层与 defaults 都认），否则写了会被 schema 拒掉。"""
    assert "viewport" in loader.CASE_KEYS
    assert "viewport" in loader.DEFAULTS_KEYS


def test_browser_defaults_to_the_shared_baseline():
    """不传就还是那个"一个基准三处共用"的 VIEWPORT —— 老用例的行为一字不改。"""
    assert Browser.viewport == VIEWPORT
    assert Agent.__init__.__defaults__ is None       # 关键字参数，靠签名而不是默认元组
    assert "viewport" in Agent.__init__.__code__.co_varnames
    assert "viewport" in Browser.__init__.__code__.co_varnames