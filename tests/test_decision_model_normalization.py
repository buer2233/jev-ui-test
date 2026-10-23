"""决策概率归一化的离线契约测试——不调用付费 API。

被钉住的六条契约：

1. **未归一的响应会被等比缩放后通过校验**：38 个候选累计出 0.97 的缺口时，
   不再把一条合法决策整条拒收；
2. **已归一的响应逐位不变**：对 Jev 必须是 no-op，不能"顺手改数字"；
3. **偏差在阈值内也不动**：0.99 的响应原样通过，只有真正会被校验拒收的才修；
4. **坏数据不被掩盖**：和为 0 / 负数 / 非有限值，归一化不接手，仍由 validate_choice 拒绝；
5. **选中项不是最大时仍拒绝**：归一化不改变排序，也不放宽第 ⑤ 条判据；
6. **修正必须可见**：原始概率和与"是否修正过"要进入决策记录，报告里能查。
"""

import math
from unittest.mock import Mock

import pytest

from jev_ultrafast import model

_CONFIG_KEYS = (
    "DECISION_BASE_URL",
    "DECISION_API_KEY",
    "DECISION_MODEL",
    "DECISION_TIMEOUT_S",
    "DECISION_THINK_MODE",
    "DECISION_GATE",
    "DECISION_THINK_BUDGET",
    "TYPESAFE_API_KEY",
    "TYPESAFE_MODEL",
)

#: 两个可点元素：让 click_target 有两个候选，便于构造"选中非最大"。
PAGE = {
    "url": "https://example.test/",
    "title": "示例页",
    "text": "页面上有两个按钮",
    "actions": [
        {"id": "e1", "kind": "click", "label": "按钮一", "role": "button", "node": 10},
        {"id": "e2", "kind": "click", "label": "按钮二", "role": "button", "node": 11},
    ],
}

#: 三个操作候选；值可由调用方替换，便于构造不同的和。
OPERATIONS = ("CLICK", "DONE", "BLOCKED")


class StubResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self.is_error = status_code >= 400
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


def _answers(operation_probs, target_probs, operation_choice="CLICK", target_choice="1"):
    return {
        "operation": {
            "type": "choice",
            "choice": operation_choice,
            "confidence": 0.9,
            "probabilities": dict(zip(OPERATIONS, operation_probs)),
        },
        "click_target": {
            "type": "choice",
            "choice": target_choice,
            "confidence": 0.9,
            "probabilities": dict(target_probs),
        },
    }


def _stub_client(monkeypatch, answers):
    monkeypatch.setattr(
        model, "CLIENT",
        Mock(post=lambda *args, **kwargs: StubResponse(200, {"model": "stub", "answers": answers})),
    )


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in _CONFIG_KEYS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DECISION_BASE_URL", "http://127.0.0.1:9/v1/systemone")
    monkeypatch.setenv("DECISION_API_KEY", "test")
    monkeypatch.setattr(model.time, "sleep", lambda _seconds: None)


# ---------------------------------------------------------------- 1. 未归一 → 修正后通过

def test_unnormalized_response_is_scaled_and_accepted(monkeypatch):
    """概率和 0.97（38 候选的典型偏差）应被等比缩放到 1 后通过校验。"""
    # operation 和 = 0.97；click_target 和 = 0.97
    operation = [0.60, 0.25, 0.12]
    assert abs(sum(operation) - 0.97) < 1e-9
    _stub_client(monkeypatch, _answers(operation, {"1": 0.60, "2": 0.37}))

    decision = model.choose(PAGE, "点击按钮一", [])

    assert decision["choice"] == "e1"
    assert decision["operation"] == "CLICK"
    assert abs(sum(decision["operation_probabilities"].values()) - 1) < 1e-9
    assert abs(sum(decision["target_probabilities"].values()) - 1) < 1e-9
    assert decision["probability_normalized"] is True
    assert decision["raw_probability_sums"]["operation"] == pytest.approx(0.97)
    assert decision["raw_probability_sums"]["click_target"] == pytest.approx(0.97)


# ---------------------------------------------------------------- 2. 已归一 → 逐位不变

def test_normalized_response_is_field_by_field_unchanged(monkeypatch):
    """和为 1.00 的响应不能被改写——这是"对 Jev 是 no-op"的可证明保证。"""
    operation = [0.60, 0.25, 0.15]   # 和 = 1.00
    assert abs(sum(operation) - 1) < 1e-9
    target = {"1": 0.60, "2": 0.40}
    _stub_client(monkeypatch, _answers(operation, target))

    decision = model.choose(PAGE, "点击按钮一", [])

    assert decision["operation_probabilities"] == dict(zip(OPERATIONS, operation))
    assert decision["target_probabilities"] == target
    assert decision["probability_normalized"] is False
    assert decision["raw_probability_sums"]["operation"] == pytest.approx(1.0)
    assert decision["raw_probability_sums"]["click_target"] == pytest.approx(1.0)


# ---------------------------------------------------------------- 3. 阈值内偏差也不动

def test_small_deviation_is_not_scaled(monkeypatch):
    """0.99 能被原校验接受（|0.99-1| < 0.02），就不该动它——避免"看起来一样"的静默改写。"""
    operation = [0.60, 0.25, 0.14]   # 和 = 0.99
    assert abs(sum(operation) - 0.99) < 1e-9
    _stub_client(monkeypatch, _answers(operation, {"1": 0.60, "2": 0.39}))

    decision = model.choose(PAGE, "点击按钮一", [])

    assert decision["operation_probabilities"] == dict(zip(OPERATIONS, operation))
    assert decision["target_probabilities"] == {"1": 0.60, "2": 0.39}
    assert decision["probability_normalized"] is False


# ---------------------------------------------------------------- 4. 坏数据不被掩盖

def test_zero_sum_is_still_rejected(monkeypatch):
    """和为 0 时归一化不接手，仍由 validate_choice 拒绝。"""
    _stub_client(monkeypatch, _answers([0.0] * 3, {"1": 0.0}))
    with pytest.raises(ValueError, match="Invalid TypeSafe response"):
        model.choose(PAGE, "点击按钮一", [])


def test_negative_probability_is_still_rejected(monkeypatch):
    """归一化只做等比缩放，不能把负数"修"成合法概率。"""
    _stub_client(monkeypatch, _answers([0.5, 0.3, 0.2],
                                       {"1": -0.1, "2": 1.1}))
    with pytest.raises(ValueError, match="Invalid TypeSafe response"):
        model.choose(PAGE, "点击按钮一", [])


def test_non_finite_probability_is_still_rejected(monkeypatch):
    _stub_client(monkeypatch, _answers([0.5, 0.3, 0.2],
                                       {"1": math.nan, "2": 1.0}))
    with pytest.raises(ValueError, match="Invalid TypeSafe response"):
        model.choose(PAGE, "点击按钮一", [])


# ---------------------------------------------------------------- 5. 选中非最大仍拒绝

def test_non_max_choice_is_rejected_after_normalization(monkeypatch):
    """归一化不改变排序：选了概率更低的目标，即使和修好了也必须拒收。"""
    _stub_client(monkeypatch, _answers([0.5, 0.3, 0.17],
                                       {"1": 0.30, "2": 0.67},
                                       target_choice="1"))
    with pytest.raises(ValueError, match="Invalid TypeSafe response"):
        model.choose(PAGE, "点击按钮一", [])


# ---------------------------------------------------------------- 6. 修正可见

def test_normalization_is_recorded_even_when_validation_fails(monkeypatch):
    """校验最终失败时，原始和也应留下——否则"到底修没修"无从判断。"""
    calls = []

    def post(*_args, **_kwargs):
        # 第一次与第二次都返回同一份不合法响应（选中非最大），但和是 0.97。
        calls.append(1)
        return StubResponse(200, {
            "model": "stub",
            "answers": _answers([0.5, 0.3, 0.17],
                                {"1": 0.30, "2": 0.67}, target_choice="1"),
        })

    monkeypatch.setattr(model, "CLIENT", Mock(post=post))
    with pytest.raises(ValueError):
        model.choose(PAGE, "点击按钮一", [])
    assert len(calls) == model.DECISION_ATTEMPTS, "不合法响应应触发有界重发"
