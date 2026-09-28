"""`framework/report_params.py` 的单测。

分两类：

  · **纯逻辑**：用假的 reporter，守住参数拼装、值归一化、以及"取不到就报错"；
  · **真契约**：用真的 `AllureListener` + `AllureReporter`，守住两条**实测出来的
    allure 内部行为**——进入 `with` 后再改 `params` 无效、出了 `with` 块 `get_item`
    返回 `None`。这两条是私有契约，升级 allure 时最可能悄悄变，所以要钉在测试里。

第二类**不需要 `--alluredir`**：见 `real_reporter` fixture 的说明——被测的那段
（`start_step` 怎么读 `params`、`stop_step` 怎么 `_items.pop`）与真实会话是同一份代码。
"""

import json

import allure
import pytest
from allure_commons import model2
from allure_commons.logger import AllureFileLogger
from allure_commons.utils import uuid4

from jev_ultrafast.framework import report_params

# ------------------------------- 纯逻辑 -------------------------------

class _FakeItem:
    def __init__(self):
        self.parameters = []
        self.start, self.stop = 0, 0


class _FakeLogger:
    def __init__(self):
        self.items = {}

    def get_item(self, uuid):
        return self.items.get(uuid)


@pytest.fixture
def fake(monkeypatch):
    """把 `_reporter()` 换成一个记账用的假 logger，返回那个 logger。"""
    logger = _FakeLogger()
    monkeypatch.setattr(report_params, "_reporter", lambda: logger)
    return logger


def _pairs(item):
    return [(p.name, p.value) for p in item.parameters]


def test_text_normalizes_values():
    """值统一成字符串；布尔转「是/否」为了中文报告好读。"""
    assert report_params._text("CLICK") == "CLICK"
    assert report_params._text(12) == "12"
    assert report_params._text(0.75) == "0.75"
    assert report_params._text(True) == "是"
    assert report_params._text(False) == "否"
    # 布尔要在数字之前判：Python 里 True 也是 int，顺序反了会变成 "1"
    assert report_params._text(True) != "1"


def test_text_keeps_falsy_values_alive():
    """**每个假值都必须变成非空字符串**——否则它在落盘时会被悄悄丢掉。

    依据（实测，M2 落盘校验）：`AllureFileLogger` 的过滤器是
    `asdict(item, filter=lambda _, v: v or v is False)`（`allure_commons/logger.py:24`），
    假值全部不写进 JSON。实测丢过一次：`执行前值=""` 的参数在 JSON 里只剩 `{"name": ...}`。

    这条测试守的不是"好看"，是"数据还在不在"。
    """
    # 核心不变式只有一条：渲染结果必须是【真值】，否则落盘时会被过滤器丢掉
    for falsy in ("", 0, 0.0, None):
        rendered = report_params._text(falsy)
        assert rendered, f"{falsy!r} 渲染成了假值 {rendered!r}，落盘时会被丢弃"

    # 数字要变成字符串形式的数字，而不是被当成"没有值"
    assert report_params._text(0) == "0"
    assert report_params._text(0.0) == "0.0"
    # "没有值"统一写成「—」，与 目标索引 / 目标概率 一致
    assert report_params._text(None) == "—"
    assert report_params._text("") == "—"


def test_falsy_parameter_values_really_vanish_on_disk(tmp_path):
    """把 allure 的落盘过滤器**钉在测试里**：假值参数真的会从报告里消失。

    这是 `_text()` 存在的根本理由，也是它最容易被误当成"只是格式化"的地方。
    必须用真的 `AllureFileLogger` 落一遍盘再读回来——该行为**只发生在写文件那一步**，
    内存里的 `Parameter` 对象是完好的，所以造一个假的 writer 测不出来。

    依据：`allure_commons/logger.py:24` 的
    `asdict(item, filter=lambda _, v: v or v is False)`。
    哪天它改了，这条会红 —— 那时才能放宽 `_text()`。
    """
    logger = AllureFileLogger(str(tmp_path), clean=True)
    result = model2.TestResult(name="t", uuid=str(uuid4()), start=1, stop=2)
    step = model2.TestStepResult(name="s", start=1, stop=2)
    step.parameters = [
        model2.Parameter(name="空字符串", value=""),
        model2.Parameter(name="零", value=0),
        model2.Parameter(name="None", value=None),
        model2.Parameter(name="正常", value="OK"),
    ]
    result.steps = [step]
    logger.report_result(result)

    written = json.loads(next(tmp_path.glob("*-result.json")).read_text(encoding="utf-8"))
    # 注意看的是 **value 键在不在**，不是 name：name 都非空，本来就都会活下来。
    # （第一版这条断言写成比 name，结果永远"通过"——幸好它报错了才发现。）
    kept = {p["name"]: p.get("value") for p in written["steps"][0]["parameters"]}

    assert kept == {"正常": "OK", "空字符串": None, "零": None, "None": None}, (
        "落盘过滤器变了？假值参数居然活下来了——"
        "那 report_params._text 里那几条转换的存在理由要重验（见 framework/report_params.py）"
    )


def test_outputs_are_appended(fake):
    fake.items["u1"] = _FakeItem()
    report_params.fill_outputs("u1", {"操作": "CLICK", "目标索引": 3, "是否生成文本": False})
    assert _pairs(fake.items["u1"]) == [("操作", "CLICK"), ("目标索引", "3"), ("是否生成文本", "否")]


def test_mark_adds_a_named_note(fake):
    fake.items["u1"] = _FakeItem()
    report_params.mark("u1", "页面已过期，本次执行被丢弃")
    assert _pairs(fake.items["u1"]) == [("备注", "页面已过期，本次执行被丢弃")]


class _RecordingLogger:
    """给任何 uuid 都自动建条目的假 logger —— 模拟"有报告会话"。

    与 `_FakeLogger` 的差别正是本文件要区分的那件事：
    `_FakeLogger` 对未知 uuid 返回 None（模拟"步骤找不到"），
    而这里总是有一个可写的条目（模拟"步骤就在那儿"）。
    """

    def __init__(self):
        self.items = {}

    def get_item(self, uuid):
        return self.items.setdefault(uuid, _FakeItem())


@pytest.fixture
def fake_session(monkeypatch):
    logger = _RecordingLogger()
    monkeypatch.setattr(report_params, "_reporter", lambda: logger)
    return logger


def test_times_override_the_automatic_ones(fake_session):
    report_params.step_with_real_times(
        "等待页面稳定", {"等待耗时ms": 351}, 1_700_000_000_000, 1_700_000_000_351
    )
    (item,) = fake_session.items.values()
    assert (item.start, item.stop) == (1_700_000_000_000, 1_700_000_000_351)


def test_negative_interval_is_rejected(fake_session):
    """宁可报错，也不要往报告里写一个负时长的步骤。"""
    with pytest.raises(ValueError, match="早于"):
        report_params.step_with_real_times("等待", None, 2000, 1000)


def test_missing_item_fails_loudly(fake):
    """步骤不在注册表里必须报错——这正是"调用点写到 with 之外"的失败形态。

    先确认"在的时候是通的"，再确认"不在的时候会响"：
    否则一个永远报错的实现也能让这条测试通过。
    """
    fake.items["u1"] = _FakeItem()
    report_params.mark("u1", "存在时正常")
    assert _pairs(fake.items["u1"]) == [("备注", "存在时正常")]

    del fake.items["u1"]
    with pytest.raises(RuntimeError, match="with 块"):
        report_params.fill_outputs("u1", {"操作": "CLICK"})


@pytest.fixture
def no_report_session(monkeypatch):
    """模拟"没传 --alluredir"：插件管理器里一个能当 reporter 的东西都没有。

    真实情况下这也是**整条 allure 管线没装**（listener 的注册在 plugin.py:160
    的 `if report_dir` 里），此时 `allure.attach` 本来就是静默丢弃的。
    """

    class _Empty:
        @staticmethod
        def get_plugins():
            return []

    monkeypatch.setattr(report_params.allure_commons, "plugin_manager", _Empty)


def test_no_report_session_is_inert(no_report_session):
    """没有报告会话时整层静默失效——与 `allure.attach` 的行为保持一致。

    为什么**不**在这里报错：没有报告，就不存在"报告里少了参数"这回事。
    反过来，若这里报错，"没传 --alluredir"会在第一次补参数时崩掉，
    而 `.claude/skills/nl-case-run/evals/run.py:204,219` 正是**不带 `--alluredir`** 跑的
    ——那会把"忘了传参数"变成一条看起来与报告无关的崩溃。
    """
    assert report_params._reporter() is None
    # 三个入口都必须安静地什么都不做，而不是抛异常
    report_params.fill_outputs("任意 uuid", {"操作": "CLICK"})
    report_params.mark("任意 uuid", "留痕")
    report_params.step_with_real_times("任意步骤", None, 1000, 2000)


def test_reporter_present_but_item_missing_fails_loudly(fake):
    """**有**报告会话却找不到步骤 → 必须报错。

    这才是真正要防的静默损坏：报告会生成、会打开、只是这些参数悄悄没了。
    （另一种情形——没有报告会话——见上一条：那种情况该静默。）
    """
    with pytest.raises(RuntimeError, match="with 块"):
        report_params.step_with_real_times("找不到的步骤", None, 1000, 2000)


# ------------------------------- 真契约 -------------------------------

def test_reporter_is_found_by_scanning(real_reporter):
    """`_reporter()` 要能在插件堆里认出它——按鸭子类型，不按名字。"""
    assert report_params._reporter() is real_reporter


def test_params_set_before_enter_are_recorded(real_reporter):
    ctx = report_params.step("进入前设参数", {"操作": "CLICK", "候选元素数": 12})
    with ctx:
        item = real_reporter.get_item(ctx.uuid)
        assert _pairs(item) == [("操作", "CLICK"), ("候选元素数", "12")]


def test_params_set_after_enter_are_ignored(real_reporter):
    """**反向断言**：守住那条坑——进入 `with` 之后再改 `ctx.params` 无效。

    `StepContext.__enter__` 里是 `start_step(params=self.params)`，params 在那一刻
    就被读走了。所以 report_params.step() 必须在 `with` **之前**赋值。
    哪天 allure 改成延迟读取，这条会红——那时 report_params 的实现可以简化。
    """
    ctx = allure.step("进入后才设参数")
    with ctx:
        ctx.params = {"操作": "CLICK"}
        item = real_reporter.get_item(ctx.uuid)
        assert "操作" not in [p.name for p in (item.parameters or [])]


def test_get_item_after_the_block_returns_none(real_reporter):
    """**硬约束**：出了 `with` 块 `get_item` 就返回 `None`。

    原因是 `stop_step()` 里有 `_items.pop(uuid)`（`allure_commons/reporter.py:85`）。
    这条不是在守功能，而是把 allure 的内部行为**钉在测试里**：
    哪天它改了，这里会红，而不是让"补参数悄悄失效、报告照常生成"。
    """
    ctx = report_params.step("出了块就找不回来")
    with ctx:
        assert real_reporter.get_item(ctx.uuid) is not None
    assert real_reporter.get_item(ctx.uuid) is None, (
        "allure 改了 stop_step 的行为？report_params 里"
        "「补参数必须在 with 内」这条约束需要重验（见 framework/report_params.py 的模块 docstring）"
    )


def test_stop_step_overwrites_a_stop_written_inside_the_block(real_reporter):
    """**这就是那条把 `set_times` 判死的实测**：块内写的 `stop` 会被冲掉。

    留着它是因为它解释了 `step_with_real_times` 为什么长得那么别扭
    （块内抓对象、块外写值）。哪天 allure 不再覆盖 `stop`，这条会红——
    那是好消息：说明可以用更简单的写法，而不是现在这样分两步。
    """
    ctx = report_params.step("块内写时间")
    with ctx:
        item = real_reporter.get_item(ctx.uuid)
        item.start, item.stop = 1_700_000_000_000, 1_700_000_002_034

    assert item.start == 1_700_000_000_000, "start 应该保住（start_step 之后没人再动它）"
    assert item.stop != 1_700_000_002_034, (
        "stop 居然没被 stop_step 覆盖？那 step_with_real_times 可以简化成"
        "「块内一次写完」——见 framework/report_params.py"
    )


def test_real_times_survive_the_stop_step_override(real_reporter):
    """`step_with_real_times` 的结果必须真的落在容器里那份对象上。

    这条是「等待步骤有真实区间」的**唯一离线保证**：它真跑了一遍
    `__enter__` / `__exit__` / `stop_step`，再读容器里最终的步骤对象。
    没有它，就只能靠一次付费运行去发现"时长又变成 0 了"。
    """
    report_params.step_with_real_times(
        "等待页面稳定", {"等待耗时ms": 2034}, 1_700_000_000_000, 1_700_000_002_034
    )

    step = real_reporter.get_test(None).steps[-1]
    assert step.name == "等待页面稳定"
    assert (step.start, step.stop) == (1_700_000_000_000, 1_700_000_002_034)
    assert step.stop - step.start == 2034
    assert {p.name: p.value for p in step.parameters}["等待耗时ms"] == "2034"