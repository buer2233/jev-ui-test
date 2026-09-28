"""步骤参数与时间的两个把手。

## 为什么单独一个文件

「把值补进一个**已经开着**的步骤」这件事 allure 没有公开 API，只能直接操作步骤对象
（`reporter.get_item(uuid)` 拿到的 `TestStepResult` 是个 attrs 实例）。那是**非公开契约**，
所以全部收口在本文件：

  · 升级 allure-pytest 时只需重验这一个文件，不必满仓库找；
  · 调用点一眼能看出"这里用了私有路径"。

## 两条实测约束（踩过才知道，别再踩）

1. **进入 `with` 之前设 `.params` 才有效。** 进入之后再改 `ctx.params` 是**无效**的
   —— params 只在 `start_step` 那一刻被读走（分析报告 §2.3，探针 4 反向验证过）。
2. **补参数 / 改时间必须在 `with` 块【内】做。** 出了块 `get_item` 返回 `None`，
   因为 `stop_step()` 里有 `_items.pop(uuid)`（`allure_commons/reporter.py:85`）。
   注意措辞：所谓"事后补"是"**在步骤体内、晚于进入的那一刻**"，**不是**"步骤结束后"。

第 2 条会让"写错位置"变成**静默失败**：拿到 None、什么都不补、报告照常生成、看不出来。
所以本模块每个取值点都**响亮报错**，绝不返回 None 让调用方自己判。

## 用法

    ctx = report_params.step("第 1 步 · 决策", {"步序": 1})        # 进入前就知道的
    with ctx:
        decision = choose(...)
        report_params.fill_outputs(ctx.uuid, {"选中操作": decision["operation"]})
"""

import allure
import allure_commons
from allure_commons.model2 import Parameter


def _text(value):
    """参数值统一成字符串，并保证它【不会在落盘时被悄悄丢掉】。

    ⚠️ 这不是为了好看，是为了不丢数据。`AllureFileLogger` 落盘时的过滤器是
    `asdict(item, filter=lambda _, v: v or v is False)`（`allure_commons/logger.py:24`）
    —— **假值全部被丢掉**：`""`、`0`、`0.0`、`None`、`[]` 都不写进 JSON，
    而报告照常生成、照常打开，只是那个参数不见了。

    实测确实丢过（M2 落盘校验）：`执行前值=""` 的参数在 JSON 里只剩 `{"name": "执行前值"}`。

    所以每一条转换都是在堵一个具体的洞：
      · **非字符串一律 `str()`**：`0` → `"0"`（真值）→ 保住。直接传 int `0`
        会被过滤器丢掉，这是最容易漏的一个；
      · **`None` → `"—"`**：表示"没有值"。直接传 None 同样会被丢掉；
      · **空字符串 → `"—"`**：同上，且与 目标索引 / 目标概率 的写法一致；
      · **布尔 → 「是 / 否」**：`False` 恰好是过滤器唯一放行的假值，但「否」更好读。

    改这个函数之前先想清楚：**放宽任何一条，对应的参数就会从报告里静默消失。**
    """
    if isinstance(value, bool):
        return "是" if value else "否"
    if value is None or value == "":
        return "—"
    return value if isinstance(value, str) else str(value)


def step(title, params=None):
    """公开路：建步骤，并在**进入之前**把参数设好。

    返回 StepContext，调用方负责 `with`。进入之后再改 `.params` 无效（见模块 docstring）。
    """
    ctx = allure.step(title)
    ctx.params = {str(name): _text(value) for name, value in (params or {}).items()}
    return ctx


def _reporter():
    """拿 allure 的 reporter；**没有报告会话时返回 `None`**。

    ⚠️ 拿法**不是**按名字查（实测，探针 7/8）：
    `allure_commons.plugin_manager.get_plugin("allure_listener")` **返回 None**——
    `allure_pytest/plugin.py:164` 注册 listener 时没给名字，pluggy 的键是它自己推的规范名。
    试过 "allure_listener" / "AllureListener" / "allure_listener.AllureListener" 三个键，
    **全部返回 None**。所以只能扫 `get_plugins()`（那返回的是**对象**列表）按鸭子类型认。

    （runner 够不着 `request.config.pluginmanager`，只有那里的键才叫 "allure_listener"。）

    ## 为什么"没有 reporter"返回 None 而不是报错

    因为那种情况下**整条 allure 管线根本没装**：listener 的注册在 `plugin.py:160`
    的 `if report_dir` 里，**没传 `--alluredir` 就什么都没有**。此时 `allure.attach`
    本来也是静默丢弃的。本模块跟着一起当没事发生，行为才一致。

    反过来说：要是这里报错，"没传 --alluredir"会在**第一次补参数时**崩掉，
    而 `.claude/skills/nl-case-run/evals/run.py:204,219` 就是不带 `--alluredir` 跑的
    ——那会把"忘了传参数"变成一条看起来与报告无关的崩溃。

    ## 真正该响亮报错的是另一种情形

    **有报告会话，但要找的步骤不在**——那意味着报告里会悄悄少掉这些参数。
    见 `_get_item`。
    """
    for plugin in allure_commons.plugin_manager.get_plugins():
        logger = getattr(plugin, "allure_logger", None)
        if logger is not None and hasattr(logger, "get_item"):
            return logger
    return None


def _get_item(uuid, *, what):
    """取回正在进行的步骤对象。

    返回 `None` 只表示【没有报告会话，整层是空的】；只要有会话却找不到步骤，就报错。
    这个区分是刻意的：前者没有报告可损坏，后者有。
    """
    reporter = _reporter()
    if reporter is None:
        return None
    step_result = reporter.get_item(uuid)
    if step_result is None:
        raise RuntimeError(
            f"补{what}失败：步骤 {uuid} 已不在 allure 的注册表里。"
            "最常见的原因是调用点写在了 with 块【之外】—— 步骤一关闭就被 stop_step() "
            "摘掉了（allure_commons/reporter.py:85）。请把调用挪进 with 内。"
        )
    return step_result


def fill_outputs(uuid, params):
    """路 4：把"只有拿到结果才知道"的量补进参数表。**必须在 with 块内调。**

    参数名用 dict 传而不是 `**kwargs`：参数名里可能出现不是合法标识符的字符
    （以及中文名混排时更难读），dict 没有这个限制，也和 `step(title, params)` 一致。
    """
    step_result = _get_item(uuid, what="参数")
    if step_result is None:                 # 没有报告会话，整层失效
        return
    for name, value in params.items():
        step_result.parameters.append(Parameter(name=str(name), value=_text(value)))


def step_with_real_times(title, params, start_ms, stop_ms):
    """建一个时间戳是**真实区间**的步骤，返回 ctx。给「等待」步骤专用。

    等待发生在库里，runner 包不进 `with` 块，所以步骤时间戳会退化成进入/退出的瞬时值
    （0 ms 空壳——正是「执行」步骤那个缺陷的同一成因）。

    ## ⚠️ 为什么不是"在块内把 start/stop 都改掉"

    那样 `stop` **从构造上就不可能生效**。两件事在相反的时机发生（实测，探针 9）：

      · `start_step` 在 `__enter__` 时写入 → 之后改，**能保住**；
      · `stop_step` 在 `__exit__` 时用 `now()` **覆盖** `stop`（`listener.py:54-57`）
        → 块内改的一出块就被冲掉。实测把块内设的 2034 冲成了 1790225109251。

    > 探针 6 当初的结论"事后改 start/stop 生效（351 ms 落盘）"**是错的**：
    > 它把 `time.sleep` 放在 `__exit__` 紧前面，`now()` 恰好落在真实结束点附近
    > —— 看着对，其实是巧合。

    所以顺序必须是**块内抓对象、块外写时间**：出了块 `get_item(uuid)` 虽然返回 None
    （`_items.pop`），但提前抓住的那个对象**就是容器里那一份**（探针 9 的 B 组），
    之后改它照样落盘（C 组）——因为整个用例的结果是到 `close_test` 才序列化的。

    时间基准与 allure 自己的 `now()` 一致，都是 Unix 纪元毫秒。
    """
    ctx = step(title, params)
    with ctx:
        # 必须在块【内】抓住对象：出了块 get_item 就返回 None
        step_result = _get_item(ctx.uuid, what="时间区间")

    # 而写入必须在块【外】：stop_step 已经跑完，这时写才不会被它覆盖
    if step_result is None:                 # 没有报告会话，整层失效
        return ctx
    start_ms, stop_ms = int(start_ms), int(stop_ms)
    if stop_ms < start_ms:
        # 宁可报错也不要往报告里写一个负时长的步骤
        raise ValueError(f"步骤结束时间早于开始时间：{start_ms} → {stop_ms}")
    step_result.start, step_result.stop = start_ms, stop_ms
    return ctx


def mark(uuid, note, name="备注"):
    """补一条文本参数，用于留痕（如"页面已过期，本次执行被丢弃"）。**必须在 with 块内调。**

    为什么需要：实测 12 次决策只产生 10 条动作，其中一次因页面过期被丢弃，
    而在报告里"决策被丢弃"与"决策是 DONE"长得完全一样（分析报告 §1.4 第 4 点）。
    这类"看起来正常其实没做"的情况必须可见，不能静默。
    """
    fill_outputs(uuid, {name: note})