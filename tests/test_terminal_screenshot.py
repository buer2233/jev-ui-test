"""终局断言截图：**每条用例一张，通过与失败都要有**。

为什么单独一篇：这张图是公司规范格式测试报告「测试截图」页的配图
（`framework/company_report.py` 按 `reporting.SCREENSHOT_ATTACHMENT` 取图）。
它有三处容易写错，而每一处的症状都是**静默缺图**——报告照常生成，只是那一条没图：

  · 附件名不统一：一个叫「页面截图」、一个叫「断言截图」，取图方按名字找就找不到；
  · 终局那次观察忘了带 `screenshot=True`：页面里根本没有图，而 `attach_screenshot`
    对"没有图"是静默返回的；
  · 补拍写成了"无条件重拍"：白花一次 `Page.captureScreenshot`（实测约 3.3 秒）。

离线单测，不起浏览器。
"""

import base64
from unittest.mock import Mock

import pytest

from jev_ultrafast.framework import reporting
from jev_ultrafast.framework.reporting import SCREENSHOT_ATTACHMENT, attach_screenshot
from jev_ultrafast.framework.runner import _attach_crash_screenshot, _terminal_page

# 1×1 的透明 GIF 即可——这里只验"有没有附上"，不验图像内容。
DATA = base64.b64encode(b"fake-jpeg-bytes").decode()


class _FakeAgent:
    """只给 `_terminal_page` 真正用到的那两个字段。

    ⚠️ `browser` 挂在 **agent 上**（`agent.browser`），不是 `agent.state["browser"]`
    ——真实 Agent 两者都有（state 里那份是给 `_reset_after_discard` 用的），
    但 `_terminal_page` 读的是前者。挂错了的表现是**静默不补拍**：异常被吞掉、
    返回的还是那张没图的页面，而这恰恰是测试要抓的东西。
    """

    def __init__(self, page, reobserved=None, error=None):
        self.state = {"page": page}
        self.browser = Mock(observe=Mock(side_effect=error) if error is not None
                            else Mock(return_value=reobserved or page))


def _attached(monkeypatch):
    """把 `allure.attach` 换成一个记录器，返回 (调用列表, 记录函数)。"""
    calls = []
    monkeypatch.setattr(reporting.allure, "attach", lambda body, name, type: calls.append((name, body, type)))
    return calls


def test_attach_screenshot_says_out_loud_whether_it_attached(monkeypatch):
    """返回值就是"附上了没有"——调用方靠它决定要不要写「截图缺失」说明。

    旧版是 `return None` + 静默跳过："附上了"和"没有图"在调用方看来一模一样。
    """
    calls = _attached(monkeypatch)

    assert attach_screenshot({"screenshot": DATA}, name=SCREENSHOT_ATTACHMENT) is True
    assert attach_screenshot({}, name=SCREENSHOT_ATTACHMENT) is False
    assert attach_screenshot({"screenshot": None}, name=SCREENSHOT_ATTACHMENT) is False

    # 只有第一声真的附了东西，且名字就是我们约定的那一个
    assert [name for name, _, _ in calls] == [SCREENSHOT_ATTACHMENT]
    assert calls[0][1] == b"fake-jpeg-bytes"          # 落了 base64 解码后的字节
    assert calls[0][2] == reporting.allure.attachment_type.PNG


def test_step_level_screenshots_keep_a_different_name(monkeypatch):
    """逐步骤的调试图与终局那张图**必须不同名**。

    同名的话，取图方（公司报告）会随手挑到某个中间步骤的画面当"终局截图"——
    报告里看不出错，只是图配错了那一步。
    """
    calls = _attached(monkeypatch)
    attach_screenshot({"screenshot": DATA})           # 默认名 = 执行步骤的调试图
    assert calls[0][0] != SCREENSHOT_ATTACHMENT


def test_terminal_page_reuses_the_screenshot_it_already_has(monkeypatch):
    """页面已经带图时不重拍：截图实测约 3.3 秒，白拍一次就是白等 3 秒。"""
    page = {"screenshot": DATA, "url": "u"}
    agent = _FakeAgent(page)

    assert _terminal_page(agent) is page
    agent.browser.observe.assert_not_called()


def test_terminal_page_re_observes_only_when_the_screenshot_is_missing():
    """落定那次观察失败吞了异常 → 页面没图 → 这里补一次只读观察。"""
    page, retaken = {"url": "u"}, {"screenshot": DATA, "url": "u"}
    agent = _FakeAgent(page, reobserved=retaken)

    assert _terminal_page(agent) is retaken
    agent.browser.observe.assert_called_once_with(screenshot=True)
    assert agent.state["page"] is retaken             # 判定依据跟着更新（与终局落定同一语义）


def test_terminal_page_keeps_the_old_page_when_re_observing_fails():
    """补拍失败**不许把用例判成异常**：缺图如实缺着，结论照常。

    真·截图失败（重页面超时、浏览器已不可用）时抛异常，这次要保住的是
    "用例还能继续判"，而不是"一定要有图"——图缺了会在报告里写成一条说明。
    """
    page = {"url": "u"}
    agent = _FakeAgent(page, error=RuntimeError("Page.captureScreenshot timed out"))

    assert _terminal_page(agent) == {"url": "u"}
    assert agent.state["page"] == {"url": "u"}


def test_crash_path_attaches_the_same_named_screenshot(monkeypatch):
    """崩溃路径共用同一个附件名：对读报告的人，"终局画面"只有一个含义。"""
    calls = _attached(monkeypatch)
    _attach_crash_screenshot({"page": {"screenshot": DATA}})

    assert calls[0][0] == SCREENSHOT_ATTACHMENT


def test_crash_path_without_a_page_leaves_a_note_instead_of_raising(monkeypatch):
    """崩溃时连页面都取不到 → 写说明；**绝不向上抛**（真正要报的是那个异常）。"""
    calls = _attached(monkeypatch)
    _attach_crash_screenshot({"page": None})          # 不抛

    assert calls[0][0] == "断言截图缺失"


@pytest.mark.parametrize("page", [{}, {"page": {}}, {"page": {"screenshot": ""}}])
def test_crash_path_tolerates_an_empty_page(monkeypatch, page):
    calls = _attached(monkeypatch)
    _attach_crash_screenshot(page)
    assert calls[0][0] == "断言截图缺失"