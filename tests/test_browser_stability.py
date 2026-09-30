"""`wait_until_stable` 稳定判据的单测。

判据原先只有一条："(元素数, marker) 连续 N 次采样不变"。这条路在 2026-09-29 被证伪两次，
每次都能让决策落在半渲染的页面上、模型拿着不完整的一页做终局决策：

  ① **空页面天然"稳定"**：0 元素、0 字文本，签名恒定，约 1.2 秒就判定稳定。
     模型看到 0 个候选、0 字文本，「BLOCKED：没有可推进的操作」是【字面正确】的答案，
     三条 E9 用例各花不到 2 秒白跑。
  ② **静默不是加载完成**：元素数与页面指纹整整 13.5 秒一动不动，资源条数也从 105 才涨到
     110，中间还有一段 6.1 秒连资源条数都不动——但请求早就发出去了，只是服务器回得慢。

所以现在有三道闸，本文件逐条钉住：
  · 没画出来 → 不算稳定（`_has_rendered`）；
  · 资源条数还在涨 → 不算稳定（签名带 resources）；
  · 静默必须够久（`steady_seconds`）→ 1.8 秒窗口那种"看着不动"不算数。
"""

from jev_ultrafast.browser import Browser


def _page(actions=0, text="", marker="m", *, resources=0):
    """造一帧观察结果。actions 的元素内容与判据无关，只看条数。"""
    return {"actions": [{} for _ in range(actions)], "text": text, "marker": marker,
            "resources": resources}


class _FakeBrowser:
    """只实现 `wait_until_stable` 真正用到的那几样。

    刻意不碰 CDP：这条判据是纯逻辑，接上真实浏览器只会把它变成慢且不稳的测试。
    页面序列按调用次数推进，最后一帧**粘住**（反复返回），模拟"渲染完成后不再变化"。

    `_has_rendered` 直接借生产实现，不在这里重抄一份——否则改坏了生产代码，
    测试照样绿，这条测试就只是在测它自己的副本。元素可以是 callable：
    用来造"永远在变"的序列（固定列表最后一帧会粘住，粘住就不再变化，测不出"一直在涨"）。
    """

    _has_rendered = staticmethod(Browser._has_rendered)

    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = 0
        self.waits = []

    def observe(self, screenshot=True):
        index = min(self.calls, len(self.pages) - 1)
        self.calls += 1
        item = self.pages[index]
        return item(self.calls) if callable(item) else item

    def _record_wait(self, kind, started, **extra):
        self.waits.append({"kind": kind, **extra})


def _wait(fake, *, timeout=1, interval=0.02, steady_seconds=0.0):
    """steady_seconds=0：判据与真实时间无关时把它压到 0，测试快且确定性好。
    要验证"静默必须够久"的那条，单独传一个大于 timeout 的值。

    interval 取得极小：压缩采样间隔不改变被测逻辑，只让"会耗满 timeout"那几条跑得快些。
    """
    return Browser.wait_until_stable(fake, timeout=timeout, interval=interval,
                                     steady_seconds=steady_seconds)


# --------------------------- 闸一：没画出来不算稳定 ---------------------------

def test_blank_page_is_never_stable():
    """空页面不是"稳定"，是"还没画出来"——旧判据会在这里返回 True。"""
    fake = _FakeBrowser([_page()])

    assert _wait(fake) is False
    # 关键：它必须**一直**在轮询，而不是攒够几次相同采样就收工。
    assert fake.calls > 3, f"空白页只采样了 {fake.calls} 次就放弃等待"


def test_whitespace_only_text_counts_as_blank():
    """只剩空白的可见文本等同于没画出来。"""
    fake = _FakeBrowser([_page(actions=2, text="   \n  ")])

    assert _wait(fake) is False
    assert fake.calls > 3


def test_page_with_actions_but_no_text_is_not_stable():
    """实测到的中间态：已有若干元素、但文本还是 0 字。

    元素不为空【不足以】说明画好了——新 E9 上决策就是落在这种帧上的。
    """
    fake = _FakeBrowser([_page(actions=1, text="")])

    assert _wait(fake) is False


def test_text_without_actions_is_still_stable():
    """反向：有文本、没有可点元素是合法状态（纯展示页），不能一直等。

    这是选"文本"而不是"元素数"当判据的理由——按元素数判会让这类页面耗满 timeout。
    """
    fake = _FakeBrowser([_page(actions=0, text="路径设置 路径名称")])

    assert _wait(fake) is True


# --------------------------- 闸二：资源还在涨不算稳定 ---------------------------

def test_resource_growth_alone_breaks_stability():
    """资源条数在涨就不算稳定——识破"元素没变、其实还在加载"。"""
    fake = _FakeBrowser([lambda call: _page(actions=57, text="路径设置", resources=100 + call)])

    assert _wait(fake) is False
    assert fake.calls > 4


def test_stability_resumes_after_resources_stop_growing():
    """资源停下之后要重新开始计静默，不能拿加载期的采样抵账。"""
    pages = [_page(actions=57, text="路径设置", resources=n) for n in (105, 106, 107)]
    pages += [_page(actions=57, text="路径设置", resources=107)]
    fake = _FakeBrowser(pages)

    assert _wait(fake) is True
    # 3 帧在涨（105→106→107）+ 1 帧重复才判稳。若加载期的帧能抵账，
    # 第 2、3 帧之间就会提前收工，采样数会少于 4。
    assert fake.calls >= 4, f"只采样了 {fake.calls} 次，加载期疑似被计入了静默"


# --------------------------- 闸三：静默必须够久 ---------------------------

def test_a_stable_page_still_needs_the_quiet_window():
    """页面完全不动，但静默要求比 timeout 还长 → 超时前不该判稳定。

    这条钉住的是"从采样次数改成秒数"的那个语义：1.8 秒窗口会放过的假静默，
    在这里必须等够 `steady_seconds` 才算数。
    """
    fake = _FakeBrowser([_page(actions=57, text="路径设置")])

    assert _wait(fake, timeout=1, steady_seconds=30) is False
    assert fake.calls > 3


def test_page_becomes_stable_only_after_it_has_rendered():
    """先空白若干帧、再渲染出来：应当等到渲染之后才判定稳定。"""
    fake = _FakeBrowser([_page()] * 5 + [_page(actions=9, text="路径设置")])

    assert _wait(fake) is True
    # 5 帧空白 + 2 帧判稳；少于此说明空白期被算进了静默。
    assert fake.calls >= 6, f"只采样了 {fake.calls} 次，空白期疑似被计入了静默"


def test_criterion_is_recorded_in_the_wait_log():
    """等待记录照常落一份，报告层要靠它渲染「页面稳定」步骤。"""
    fake = _FakeBrowser([_page(actions=9, text="路径设置")])

    _wait(fake)

    assert [w["kind"] for w in fake.waits] == ["stable"]
    assert fake.waits[0]["stable"] is True
    assert fake.waits[0]["polls"] >= 2