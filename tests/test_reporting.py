"""`framework/reporting.py` 里纯函数的单测。

只测不碰 allure 的那几个（`decision_params` / `_prob` / `_strip_page_text`）：
附件类函数的正确性只能靠落盘校验（实施方案 §4.6），不是单测能替代的。
"""

import copy
import json

from jev_ultrafast.framework import reporting


def _full_decision():
    """一份字段齐全的决策，形状照抄 model.choose() 的返回（model.py:192-208）。"""
    return {
        "choice": "e3",
        "operation": "CLICK",
        "target": "2",
        "confidence": 0.9,
        "probabilities": {"e3": 0.62, "e1": 0.38},
        "operation_probabilities": {"CLICK": 0.71, "TYPE_TEXT": 0.29},
        "target_probabilities": {"1": 0.38, "2": 0.62},
        "target_confidence": 0.8,
        "raw_answers": [{"choice": "CLICK"}],
        "model": "jev-1.13.0",
        "usage": {"total_tokens": 1234},
        "latency_ms": 393,
        "decision_attempts": 2,
        "request": {
            "model": "jev-1.13.0",
            "state": {
                "page": {"url": "https://example.com/a", "title": "A", "text": "x" * 500},
                "elements": [{"index": "1"}],
                "recent_actions": [],
            },
            "questions": {"operation": {"type": "choice"}},
        },
    }


def test_decision_params_pick_the_readable_numbers():
    params = reporting.decision_params(_full_decision())
    assert params["选中操作"] == "CLICK"
    assert params["目标索引"] == "2"
    assert params["操作概率"] == 0.71          # 取的是选中【操作】的那条路径
    assert params["目标概率"] == 0.62          # 取的是选中【目标】的那条路径
    assert params["决策耗时ms"] == 393
    assert params["重发次数"] == 2             # >1 是"响应不合法、重发过"的信号，要显眼
    assert params["模型"] == "jev-1.13.0"      # 用响应回报的模型名，不是环境变量默认值


def test_probabilities_are_rounded_for_the_table():
    decision = _full_decision()
    decision["operation_probabilities"] = {"CLICK": 0.7101234567}
    assert reporting.decision_params(decision)["操作概率"] == 0.71


def test_not_applicable_is_a_dash_not_zero():
    """不适用必须是「—」。

    0 会被读成"模型认为这条不可能"，而 DONE/BLOCKED 这类决策**没有**目标概率
    ——两者含义完全相反，不能混。
    """
    done = {
        "choice": "DONE", "operation": "DONE", "target": None,
        "confidence": 0.9, "operation_probabilities": {"DONE": 0.48, "CLICK": 0.52},
        "target_probabilities": {}, "latency_ms": 100, "decision_attempts": 1, "model": "m",
    }
    params = reporting.decision_params(done)
    assert params["目标索引"] == "—"
    assert params["目标概率"] == "—"
    assert params["操作概率"] == 0.48          # 操作概率仍然有


def test_minimal_decision_does_not_crash():
    """最小决策（只有 chance/operation/confidence）也要能出参数表。

    形状取自 tests/test_agent.py 的 `decision()` 替身——它是既有测试在用的最小契约。
    这里是【展示层】，缺字段时给「—」，不该抛。
    """
    params = reporting.decision_params({"choice": "e1", "operation": "TYPE_TEXT", "confidence": 1.0})
    assert params["选中操作"] == "TYPE_TEXT"
    assert params["目标索引"] == "—"
    assert params["重发次数"] is None          # 缺就是缺，如实给 None，不编一个 1


def test_strip_page_text_does_not_mutate_the_original():
    """剥离必须走深拷贝。

    那个 request dict 是 model.choose() 交给 API 的那一份，还留在
    snapshot["decisions"] 里。就地 pop 就是改调用方的对象——
    而同一份数据后面还要被「执行轨迹」用到。
    """
    request = _full_decision()["request"]
    before = copy.deepcopy(request)

    stripped = reporting._strip_page_text(request)

    assert request == before, "原对象被改了"
    assert "text" not in stripped["state"]["page"]
    assert request["state"]["page"]["text"] == "x" * 500, "原文必须还在"


def test_strip_page_text_keeps_everything_else():
    """只剥离 text，其余一个字不动——否则请求体附件就不再是"原样"了。"""
    request = _full_decision()["request"]
    stripped = reporting._strip_page_text(request)
    assert set(stripped) == set(request)
    assert stripped["questions"] == request["questions"]
    assert stripped["state"]["elements"] == request["state"]["elements"]
    assert stripped["state"]["page"]["url"] == "https://example.com/a"
    assert stripped["state"]["page"]["title"] == "A"
    # 剥离后的体积要真的降下来，否则这个函数就没意义
    assert len(json.dumps(stripped, ensure_ascii=False)) < len(json.dumps(request, ensure_ascii=False))


def test_strip_page_text_tolerates_missing_pieces():
    """没有 page / 没有 text 时不能炸。"""
    assert reporting._strip_page_text({}) == {}
    assert reporting._strip_page_text(None) is None
    assert reporting._strip_page_text({"state": {}}) == {"state": {}}
    assert reporting._strip_page_text({"state": {"page": {"url": "u"}}}) == {"state": {"page": {"url": "u"}}}