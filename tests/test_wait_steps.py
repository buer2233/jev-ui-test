"""等待步骤的门槛切分与标题构造的单测。

门槛 `≥200 ms 独立成步骤，短的并进所在步骤参数`是**已拍板的决策**（实施方案 §7.2）：
每轮都建会把 50 ms 级的输入同步等待也变成步骤，报告就成了流水账。
这里把那条决策钉住，顺带守住两个容易写错的点：

  · `_collect_waits` 是**唯一消费点**，不清空会让等待串轮、耗时归错步骤；
  · 同 kind 一轮出现两次要**累加**，覆盖会让第二次静默吃掉第一次。
"""

from jev_ultrafast.framework.runner import (
    _collect_waits,
    _emit_wait_steps,
    _wait_params,
    _wait_steps_split,
    _wait_title,
)


class _FakeBrowser:
    def __init__(self, waits):
        self.waits = list(waits)


class _FakeAgent:
    def __init__(self, waits):
        self.browser = _FakeBrowser(waits)


def _wait(kind, elapsed_ms, started_ms=1_700_000_000_000, **extra):
    return {"kind": kind, "elapsed_ms": elapsed_ms, "started_ms": started_ms, **extra}


# --------------------------- 门槛切分 ---------------------------

def test_long_wait_becomes_a_step_and_short_one_becomes_a_param():
    agent = _FakeAgent([_wait("stable", 2000, stable=True), _wait("input_sync", 50, trigger="fill")])
    long_waits, short = _collect_waits(agent, 200)

    assert [label for label, _ in long_waits] == ["页面稳定"]
    assert short == {"输入后控件同步等待ms": 50}


def test_threshold_is_inclusive():
    """正好等于门槛算"达到"——`>=` 而不是 `>`。

    边界写成 `>` 的话，门槛设 0 就没法"每次都独立成步骤"了（0 ms 的等待也是等待）。
    """
    agent = _FakeAgent([_wait("stable", 200)])
    long_waits, short = _collect_waits(agent, 200)
    assert len(long_waits) == 1 and short == {}

    agent = _FakeAgent([_wait("stable", 199)])
    long_waits, short = _collect_waits(agent, 200)
    assert long_waits == [] and short == {"页面稳定等待ms": 199}


def test_threshold_zero_makes_every_wait_a_step():
    """门槛 0 是合法配置，含义是"每次等待都独立成步骤"。"""
    agent = _FakeAgent([_wait("stable", 0), _wait("input_sync", 0)])
    long_waits, short = _collect_waits(agent, 0)
    assert len(long_waits) == 2
    assert short == {}


def test_same_kind_accumulates_instead_of_overwriting():
    """同一个 kind 一轮里出现两次（`settle` 在首屏与 follow_new_tab 各一次）→ 累加。

    覆盖会让第二次静默吃掉第一次的耗时，报告里那一步的"看不见的成本"就少了一块。
    """
    agent = _FakeAgent([_wait("settle", 30), _wait("settle", 12)])
    _, short = _collect_waits(agent, 200)
    assert short == {"文档就绪等待ms": 42}


def test_collect_is_the_only_consume_point():
    """取走即清空——这是"等待不会串轮"的唯一保证。

    不清的话，上一轮的等待会被算到下一轮的步骤上：耗时归错步骤，
    而报告上完全看不出来（时间区间是真的，只是归属错了）。
    """
    agent = _FakeAgent([_wait("stable", 1000, stable=True)])
    first_long, _ = _collect_waits(agent, 200)
    assert len(first_long) == 1
    assert agent.browser.waits == [], "取走之后必须清空"

    second_long, second_short = _collect_waits(agent, 200)
    assert second_long == [] and second_short == {}, "同一批等待不能被消费两次"


def test_empty_waits_is_fine():
    agent = _FakeAgent([])
    assert _collect_waits(agent, 200) == ([], {})


def test_unknown_kind_falls_back_to_its_own_name():
    """库里将来加了新的等待类型，旧版 runner 不该崩——退回用 kind 原文当标签。"""
    agent = _FakeAgent([_wait("network_idle", 500)])
    long_waits, _ = _collect_waits(agent, 200)
    assert [label for label, _ in long_waits] == ["network_idle"]


# --------------------------- 标题 ---------------------------

def test_titles():
    assert _wait_title("页面稳定", 6) == "第 6 步 · 等待（页面稳定）"
    # 首屏那一次不占步数编号，否则第 1 步的编号会被它占掉
    assert _wait_title("页面稳定", None) == "前置：等待页面稳定（首屏）"


# --------------------------- 参数表 ---------------------------

def test_params_only_include_what_this_kind_has():
    stable = _wait_params(_wait("stable", 2000, stable=False, polls=7, timeout=25))
    assert stable == {"等待耗时ms": 2000, "是否等到稳定": False, "轮询次数": 7, "超时上限ms": 25}

    settle = _wait_params(_wait("settle", 120, ready=True, timeout=15))
    assert settle == {"等待耗时ms": 120, "文档已就绪": True, "超时上限ms": 15}

    sync = _wait_params(_wait("input_sync", 50, trigger="fill"))
    assert sync == {"等待耗时ms": 50, "触发动作": "fill"}


def test_timeout_not_reached_is_visible():
    """没等到稳定（超时）必须以 `是否等到稳定=否` 出现在报告里。

    这是"页面可能还在渲染"的唯一信号——藏起来的话，
    后面若紧接着出现页面过期，就没有任何线索能对上。
    """
    assert _wait_params(_wait("stable", 25000, stable=False))["是否等到稳定"] is False


def test_params_render_through_report_params_text():
    """参数值要能通过 `report_params._text` 的假值过滤（否则落盘时会消失）。"""
    from jev_ultrafast.framework import report_params

    for value in _wait_params(_wait("stable", 0, stable=False, polls=0)).values():
        assert report_params._text(value), f"{value!r} 渲染成假值，落盘时会丢"


# ------------------- 真的建出步骤来（用真的 reporter，仍然离线） -------------------

def _last_step(reporter):
    return reporter.get_test(None).steps[-1]


def test_emit_wait_steps_carries_the_real_interval(real_reporter):
    """等待步骤的时间戳必须是**真实区间**，不能是 0 ms 空壳。

    这是 §7.3 的核心：等待发生在库里，runner 包不进 `with` 块，所以步骤的
    `start`/`stop` 只能事后改。这条用**真的 reporter** 验一遍"真的改上了"——
    这样就不必花一次付费运行去确认这件事。
    """
    started_ms = 1_700_000_000_000
    _emit_wait_steps(
        [("页面稳定", _wait("stable", 2034, started_ms=started_ms, stable=True, polls=4, timeout=25))],
        steps=3,
    )

    step = _last_step(real_reporter)
    assert step.name == "第 3 步 · 等待（页面稳定）"
    assert step.stop - step.start == 2034, "时间没改上——又变成 0 ms 空壳了"
    assert (step.start, step.stop) == (started_ms, started_ms + 2034), "区间起点也必须是真实的那个时刻"

    params = {p.name: p.value for p in step.parameters}
    assert params["等待耗时ms"] == "2034"
    assert params["是否等到稳定"] == "是"
    assert params["轮询次数"] == "4"


def test_initial_wait_becomes_a_prefix_step_without_a_number(real_reporter):
    """首屏等待渲染成【前置】步骤，**不占步数编号**。

    占了编号的话，第 1 步的决策/执行就会跟等待共用一个号，
    或者编号整体后移——两种都会让"第 N 步"对不上真实轮次。
    """
    _emit_wait_steps([("文档就绪", _wait("settle", 2496, ready=True, timeout=15))], steps=None)

    step = _last_step(real_reporter)
    assert step.name == "前置：等待文档就绪（首屏）"
    assert "第" not in step.name
    assert step.stop - step.start == 2496


def test_emitting_nothing_creates_no_step(real_reporter):
    before = len(real_reporter.get_test(None).steps)
    _emit_wait_steps([], steps=1)
    assert len(real_reporter.get_test(None).steps) == before


def test_split_helper_matches_collect():
    """`_wait_steps_split` 与 `_collect_waits` 对同一批等待给出同样的切分。"""
    waits = [_wait("stable", 2000, stable=True), _wait("settle", 10)]
    long_waits, short = _wait_steps_split(waits, 200)
    assert [label for label, _ in long_waits] == ["页面稳定"]
    assert short == {"文档就绪等待ms": 10}

    agent = _FakeAgent(waits)
    assert _collect_waits(agent, 200) == (long_waits, short)