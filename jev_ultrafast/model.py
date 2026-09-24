"""TypeSafe makes choices; an optional small OpenAI-compatible model writes field values."""

import json
import math
import os
import time

import httpx

from .questions import NEXT_ACTION, TARGET, TEXT_VALUE

CLIENT = httpx.Client(http2=True, timeout=25)

# 一次决策最多发几次请求。只重试【决策】，绝不重试浏览器变更操作——这是两件不同的事：
#   · 这里重试的是一次只读的 TypeSafe 请求。走到重试时校验已经失败，而校验失败发生在
#     任何浏览器动作之前，所以重试不可能重复点击、重复提交。
#   · 变更操作的重试会真的再点一次，所以一律不重试（见 AGENTS.md 的「唯一重跑策略」）。
# 网络层的重试不在这里：post_json 已经处理了 429/529/503 与连接失败。
#
# 为什么需要：实测（2026-09）TypeSafe 偶尔返回自相矛盾的响应——choice 选了 DONE（0.37），
# 但 CLICK 的概率更高（0.38），validate_choice 拒收，整条用例就此死掉，尽管浏览器一动没动。
# 5 次运行里撞到 1 次。重发一次即可自愈。
DECISION_ATTEMPTS = 2


def post_json(url, key, body):
    """发一个只读的模型请求；瞬时故障有界重试。

    重试的判据与 DECISION_ATTEMPTS 那条一致——**有没有东西被执行过**。这里没有：
    三个调用方（决策、文本生成、语义断言）都是只读请求，重试不会重复点击、
    也不会重复提交。所以传输层故障（连接失败/超时/重置）与 429/529/503 同等对待。

    以前连接失败是【直接抛】不重试的，而它和 429 一样是瞬时的、也一样安全。
    实测（2026-09）：演示用例的首次尝试就死在这里，靠 pytest 的用例级重跑才过——
    那次失败本可以在这一层自愈。
    """
    for attempt in range(3):
        try:
            response = CLIENT.post(url, json=body, headers={"Authorization": f"Bearer {key}"})
        except httpx.HTTPError as error:
            if attempt < 2:
                time.sleep(0.5 * 2**attempt)
                continue
            raise RuntimeError("Model connection failed; no action executed.") from error
        if response.status_code in {429, 529, 503} and attempt < 2:
            time.sleep(0.5 * 2**attempt)
            continue
        if response.is_error:
            raise RuntimeError(f"Model provider returned HTTP {response.status_code}; no action executed.")
        return response.json()
    raise RuntimeError("Model unavailable")


def validate_choice(answer, ids):
    try:
        probabilities = answer["probabilities"]
        numbers = [*probabilities.values(), answer["confidence"]]
        valid = (
            answer["choice"] in ids
            and set(probabilities) == set(ids)
            and all(type(n) in (int, float) and math.isfinite(n) and 0 <= n <= 1 for n in numbers)
            and abs(sum(probabilities.values()) - 1) < 0.02
            and probabilities[answer["choice"]] >= max(probabilities.values()) - 1e-6
        )
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError("Invalid TypeSafe response; no action executed.")
    return answer


def action_space(actions):
    """One index per observed element; each operation has its own valid target choices."""
    elements, indices, targets, controls = [], {}, {}, {}
    operations = {"click": "CLICK", "fill": "TYPE_TEXT", "select": "SELECT"}
    for action in actions:
        kind = action["kind"]
        if kind not in operations:
            controls[action["id"].upper()] = action
            continue
        node = action["node"]
        if node not in indices:
            index = str(len(elements) + 1)
            indices[node] = index
            element = {k: action[k] for k in ("role", "value", "checked", "selected", "expanded") if k in action}
            element.update(index=index, label=action["label"].split(" → ")[0], operations=[])
            if kind == "select":
                element["value"] = action.get("current_value", "")
                element["options"] = []
            elements.append(element)
        index = indices[node]
        operation = operations[kind]
        group = targets.setdefault(operation, {})
        element = elements[int(index) - 1]
        if operation not in element["operations"]:
            element["operations"].append(operation)
        target = index
        if kind == "select":
            target = f"{index}:{len(element['options']) + 1}"
            element["options"].append({"index": target, "label": action["label"], "value": action["value"]})
        group[target] = action
    return elements, targets, controls


def _decision_once(body, operations, targets):
    """发一次决策请求并校验，返回 (result, operation_answer, target_answer)。

    任一步校验不过就抛错，由 choose() 决定是否重发。刻意不在这里吞掉异常：
    重发的安全性论证见 DECISION_ATTEMPTS 的注释。
    """
    result = post_json("https://api.typesafe.ai/v1/systemone", os.environ["TYPESAFE_API_KEY"], body)
    operation_answer = validate_choice(result["answers"].get("operation", {}), operations)
    operation = operation_answer["choice"]
    target_answer = None
    if operation in targets:
        # Unused target heads cannot cause an action. Validate the head selected by the operation.
        target_answer = validate_choice(
            result["answers"].get(operation.lower() + "_target", {}), targets[operation]
        )
    return result, operation_answer, target_answer


def _decision(body, operations, targets):
    """向 TypeSafe 要一次决策，响应不合法时有界重发。

    返回 (result, operation_answer, target_answer, 实际请求次数)。
    返回请求次数是为了让重发在报告里【看得见】——静默自愈会让偶发的服务端不一致
    永远不被发现，而这个项目的一贯做法是把这类事留痕（见 attach_decision）。
    """
    for attempt in range(1, DECISION_ATTEMPTS + 1):
        try:
            result, operation_answer, target_answer = _decision_once(body, operations, targets)
        except (ValueError, KeyError, TypeError):
            # 响应不合法：这次决策作废，但没有任何浏览器动作被执行过，重发是安全的。
            if attempt >= DECISION_ATTEMPTS:
                raise
            continue
        return result, operation_answer, target_answer, attempt
    raise RuntimeError("Decision request never produced a usable response.")


def choose(state, goal, history):
    elements, targets, controls = action_space(state["actions"])
    labels = {
        "CLICK": "Click an element, button, menu option, autocomplete suggestion, or calendar day.",
        "TYPE_TEXT": "Enter or replace text in an editable field. A small LLM will supply the value from the goal.",
        "SELECT": "Select an observed dropdown value.",
    }
    operations = {key: labels[key] for key in targets}
    operations.update({key: value["label"] for key, value in controls.items()})
    operations.update(DONE="Every requirement is visibly satisfied.", BLOCKED="No supported operation can progress.")
    questions = {
        "operation": {"type": "choice", "criteria": operations, "instructions": {"goal": goal, "rules": NEXT_ACTION}}
    }
    for operation, candidates in targets.items():
        questions[operation.lower() + "_target"] = {
            "type": "choice",
            "criteria": {
                index: {
                    "element": f"[{index}] {a['label']}",
                    "current_value": a.get("current_value", a.get("value", "")),
                    **{k: a[k] for k in ("role", "checked", "selected", "expanded") if k in a},
                }
                for index, a in candidates.items()
            },
            "instructions": {"goal": goal, "operation": operation, "rules": [NEXT_ACTION, TARGET]},
        }
    body = {
        "model": os.environ.get("TYPESAFE_MODEL", "jev-latest"),
        "state": {
            "page": {k: state[k] for k in ("url", "title", "text")},
            "elements": elements,
            "recent_actions": [
                {k: h.get(k) for k in ("action", "kind", "text", "page_changed")} for h in history[-10:]
            ],
        },
        "questions": questions,
    }
    started = time.perf_counter()
    result, operation_answer, target_answer, attempts = _decision(body, operations, targets)

    operation = operation_answer["choice"]
    target = None
    probabilities = {}
    if operation in targets:
        target = target_answer["choice"]
        choice = targets[operation][target]["id"]
        probabilities = {a["id"]: target_answer["probabilities"][index] for index, a in targets[operation].items()}
    else:
        choice = controls[operation]["id"] if operation in controls else operation
        probabilities[choice] = operation_answer["probabilities"][operation]
    return {
        "choice": choice,
        "operation": operation,
        "target": target,
        "confidence": operation_answer["confidence"],
        "probabilities": probabilities,
        "operation_probabilities": operation_answer["probabilities"],
        "target_probabilities": target_answer["probabilities"] if target_answer else {},
        "target_confidence": target_answer["confidence"] if target_answer else None,
        "raw_answers": result["answers"],
        "model": result["model"],
        "usage": result.get("usage", {}),
        "latency_ms": round((time.perf_counter() - started) * 1000),
        # >1 表示这次决策重发过：响应不合法但浏览器没被动过。报告里应当看得见。
        "decision_attempts": attempts,
        "request": body,
    }


def field_context(goal, action, page, history):
    return {
        "goal": goal,
        "field": {k: action.get(k) for k in ("label", "role", "value")},
        "page": {"title": page["title"], "text": page["text"][:6000]},
        "recent_actions": [{k: h.get(k) for k in ("action", "text")} for h in history[-6:]],
    }


def reasoning_fields(base):
    """按端点选择真正能关掉思考的参数形态。

    不同网关认不同的字段名，发错了不会报错，只会静默继续思考：
      阿里云百炼/DashScope 兼容模式 → enable_thinking
      DeepSeek 官方                 → thinking.type
      OpenRouter / 通用             → reasoning.enabled
    实测：阿里云端点上 reasoning.enabled=false 被忽略（推理 token 31/39），
    改用 enable_thinking=false 后为 0/7，且省约 900 ms。
    见 docs/一期改造/一期改造可行性分析报告.md §3.4。
    """
    override = os.environ.get("TEXT_MODEL_REASONING", "none")
    if override not in ("none", ""):
        # 显式覆盖：允许诊断时切成 low/medium/high 等档位
        return {"reasoning": {"effort": override}}
    if "aliyuncs.com" in base or "dashscope" in base:
        return {"enable_thinking": False}
    if "api.deepseek.com" in base:
        return {"thinking": {"type": "disabled"}}
    return {"reasoning": {"enabled": False}}


def field_text(context):
    key = os.environ.get("TEXT_MODEL_API_KEY")
    if not key:
        raise ValueError("TYPE_TEXT needs TEXT_MODEL_API_KEY; no text is hardcoded or guessed by the executor.")
    base = os.environ.get("TEXT_MODEL_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
    model = os.environ.get("TEXT_MODEL", "deepseek-chat")
    reasoning = reasoning_fields(base)
    started = time.perf_counter()
    result = post_json(
        base + "/chat/completions",
        key,
        {
            "model": model,
            "max_tokens": 1024,
            "response_format": {"type": "json_object"},
            **reasoning,
            "messages": [
                {"role": "system", "content": TEXT_VALUE},
                {
                    "role": "user",
                    "content": json.dumps(context),
                },
            ],
        },
    )
    try:
        output = json.loads(result["choices"][0]["message"]["content"])
        value = output["text"]
        if set(output) != {"text"} or not isinstance(value, str) or not value.strip() or len(value) > 2000:
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise ValueError("Text helper returned no valid field value; nothing typed.") from None
    return value, {
        "model": model,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "usage": result.get("usage", {}),
    }
