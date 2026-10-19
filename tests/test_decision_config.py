"""决策模型可配置化的离线契约：回落链、控制字段、以及"默认行为零变化"。

背景：决策端点从写死的 TypeSafe 改成可配置（DECISION_* → TYPESAFE_* → 内置默认），
以便指向本地部署的 System One 兼容决策模型（协议逐字段证据见 docs/jev-model.md）。
这批测试钉住两条红线：

  · **什么都不配 = 与改造前一致**：端点 / 模型名 / 超时 / 凭据报错行为逐条比对；
  · **配置了什么就真的发什么**：不是在解析层测一测就完——URL、模型名、控制字段、
    超时都要在 `choose()` / `judge()` 真正发出的请求里核对（读了不用 = 静默失效，
    这是本仓库最防的那类错）。
"""

import pytest

from jev_ultrafast import model
from jev_ultrafast.browser import fingerprint
from jev_ultrafast.framework import judge

# clean_env 要清掉的键：本机 .env / 环境变量里的任何残留都会让"默认值"测试失去意义。
DECISION_KEYS = (
    "DECISION_BASE_URL", "DECISION_API_KEY", "DECISION_MODEL", "DECISION_TIMEOUT_S",
    "DECISION_THINK_MODE", "DECISION_GATE", "DECISION_THINK_BUDGET",
    "TYPESAFE_API_KEY", "TYPESAFE_MODEL",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in DECISION_KEYS:
        monkeypatch.delenv(key, raising=False)


def page():
    state = {
        "url": "https://example.test/",
        "title": "Search",
        "text": "Search",
        "scroll": {"y": 0},
        "actions": [
            {"id": "e1", "kind": "click", "label": "Go", "role": "button", "value": "", "node": 20},
        ],
    }
    state["fingerprint"] = fingerprint(state)
    return state


def ok_response(body, *, model_name="test", operation="CLICK", target="1"):
    """给 choose() 的合法响应：选中项即最大概率项，概率对 criteria 的键归一。"""
    return {
        "model": model_name,
        "answers": {
            "operation": {
                "choice": operation,
                "confidence": 1.0,
                "probabilities": {o: float(o == operation) for o in body["questions"]["operation"]["criteria"]},
            },
            "click_target": {
                "choice": target,
                "confidence": 1.0,
                "probabilities": {i: float(i == target) for i in body["questions"]["click_target"]["criteria"]},
            },
        },
    }


# ---------------------------------------------------------------- 端点解析

def test_defaults_are_typesafe_jev_25s():
    """硬编码比对（不引用同模块常量）：默认值被谁改动，这里必须红。"""
    assert model.decision_endpoint() == model.DecisionEndpoint(
        "https://api.typesafe.ai/v1/systemone", "jev-latest", 25.0
    )


def test_model_falls_back_to_legacy_key_then_new_key_wins(monkeypatch):
    monkeypatch.setenv("TYPESAFE_MODEL", "jev-legacy")
    assert model.decision_endpoint().model == "jev-legacy"          # 旧键继续有效
    monkeypatch.setenv("DECISION_MODEL", "startlux-decision-27b-autothink")
    assert model.decision_endpoint().model == "startlux-decision-27b-autothink"  # 新键优先


def test_base_url_and_timeout_are_configurable(monkeypatch):
    monkeypatch.setenv("DECISION_BASE_URL", "http://10.0.0.5:8090/v1/systemone")
    monkeypatch.setenv("DECISION_TIMEOUT_S", "120")
    endpoint = model.decision_endpoint()
    assert endpoint.base_url == "http://10.0.0.5:8090/v1/systemone"
    assert endpoint.timeout == 120.0


def test_bad_timeout_fails_loudly(monkeypatch):
    monkeypatch.setenv("DECISION_TIMEOUT_S", "30s")
    with pytest.raises(ValueError, match="DECISION_TIMEOUT_S"):
        model.decision_endpoint()


# ---------------------------------------------------------------- 凭据

def test_credential_keeps_legacy_behaviour_on_default_endpoint(monkeypatch):
    """默认端点上的报错文本保持 KeyError('TYPESAFE_API_KEY')——与历史、技能文档一致。"""
    with pytest.raises(KeyError, match="TYPESAFE_API_KEY"):
        model.decision_credential()
    monkeypatch.setenv("TYPESAFE_API_KEY", "old")
    assert model.decision_credential() == "old"
    monkeypatch.setenv("DECISION_API_KEY", "new")
    assert model.decision_credential() == "new"


def test_custom_endpoint_never_sends_the_typesafe_key(monkeypatch):
    """凭据与端点同源：改了端点就不再回落 TypeSafe 的 key，没配则空（自托管无鉴权可用）。"""
    monkeypatch.setenv("TYPESAFE_API_KEY", "typesafe-secret")
    monkeypatch.setenv("DECISION_BASE_URL", "http://10.0.0.5:8090/v1/systemone")
    assert model.decision_credential() == ""
    monkeypatch.setenv("DECISION_API_KEY", "local-key")
    assert model.decision_credential() == "local-key"


# ---------------------------------------------------------------- 控制字段

def test_controls_are_omitted_unless_configured():
    """留空 = 不发（跟随服务器默认）——这条是防"给 Instant 模型发 think_mode=auto 被 422"。"""
    assert model.decision_controls() == {}


def test_controls_parse_typed_values(monkeypatch):
    monkeypatch.setenv("DECISION_THINK_MODE", "never")
    monkeypatch.setenv("DECISION_GATE", "0.9")
    monkeypatch.setenv("DECISION_THINK_BUDGET", "512")
    assert model.decision_controls() == {"think_mode": "never", "gate": 0.9, "think_budget": 512}


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("DECISION_THINK_MODE", "sometimes"),
        ("DECISION_GATE", "1.5"),
        ("DECISION_GATE", "x"),
        ("DECISION_THINK_BUDGET", "0"),
        ("DECISION_THINK_BUDGET", "many"),
    ],
)
def test_bad_controls_fail_loudly(monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match=key):
        model.decision_controls()


# ---------------------------------------------------------------- 集成：真的发出去了

def test_choose_sends_configured_endpoint_model_controls_and_timeout(monkeypatch):
    monkeypatch.setenv("DECISION_API_KEY", "local-key")
    monkeypatch.setenv("DECISION_BASE_URL", "http://10.0.0.5:8090/v1/systemone")
    monkeypatch.setenv("DECISION_MODEL", "startlux-decision-27b-autothink")
    monkeypatch.setenv("DECISION_THINK_MODE", "never")
    seen = {}

    def post(url, key, body, *, trace=None, timeout=None):
        seen.update(url=url, key=key, body=body, timeout=timeout)
        return ok_response(body, model_name="startlux-decision-27b-autothink")

    monkeypatch.setattr(model, "post_json", post)
    decision = model.choose(page(), "Press Go", [])
    assert seen["url"] == "http://10.0.0.5:8090/v1/systemone"
    assert seen["key"] == "local-key"
    assert seen["timeout"] == 25.0                      # 没配 TIMEOUT_S → 默认 25
    assert seen["body"]["model"] == "startlux-decision-27b-autothink"
    assert seen["body"]["think_mode"] == "never"
    assert decision["operation"] == "CLICK"


def test_choose_keeps_the_default_request_shape(monkeypatch):
    """默认配置下：打的还是 TypeSafe、25s、body 里没有控制字段——与改造前一致。"""
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    seen = {}

    def post(url, key, body, *, trace=None, timeout=None):
        seen.update(url=url, key=key, body=body, timeout=timeout)
        return ok_response(body)

    monkeypatch.setattr(model, "post_json", post)
    model.choose(page(), "Press Go", [])
    assert seen["url"] == "https://api.typesafe.ai/v1/systemone"
    assert seen["key"] == "test"
    assert seen["timeout"] == 25.0
    assert not {"think_mode", "gate", "think_budget"} & set(seen["body"])


def test_timeout_is_forwarded_to_post_json(monkeypatch):
    """配了超时就传下去；post_json 的默认参数形态（timeout=None → 用 CLIENT 默认）不承担含义。"""
    monkeypatch.setenv("DECISION_API_KEY", "k")
    monkeypatch.setenv("DECISION_TIMEOUT_S", "120")
    seen = {}

    def post(url, key, body, *, trace=None, timeout=None):
        seen["timeout"] = timeout
        return ok_response(body)

    monkeypatch.setattr(model, "post_json", post)
    model.choose(page(), "Press Go", [])
    assert seen["timeout"] == 120.0


# ---------------------------------------------------------------- 断言也走同一端点

def test_judge_uses_the_configured_endpoint(monkeypatch):
    """语义断言与主决策必须同源——否则"执行用 A、断言用 B"，证据链就断了。"""
    monkeypatch.setenv("DECISION_API_KEY", "local-key")
    monkeypatch.setenv("DECISION_BASE_URL", "http://10.0.0.5:8090/v1/systemone")
    seen = {}

    def post(url, key, body, *, trace=None, timeout=None):
        seen.update(url=url, key=key, timeout=timeout)
        return {"model": "x", "answers": {"c0": {"type": "noul", "noul": 0.9}}}

    monkeypatch.setattr(judge, "post_json", post)
    out = judge.judge(["页面上有「保存」按钮"], {"url": "u", "title": "t", "text": "x"})
    assert out[0]["noul"] == 0.9
    assert seen["url"] == "http://10.0.0.5:8090/v1/systemone"
    assert seen["key"] == "local-key"


def test_judge_without_any_credential_names_the_keys():
    with pytest.raises(RuntimeError, match="DECISION_API_KEY"):
        judge.judge(["x"], {"url": "u", "title": "t", "text": "x"})