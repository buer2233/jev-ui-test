"""用 Jev 的 noul 问题类型做语义断言判定。

设计要点：本模块只产出【证据】（0–1 的概率），不产出【判定】。
阈值比较与 assert 由调用方（assertions.py / runner.py）负责，
这样 AI 的非确定性被隔离在这一层之内，用例的通过与否始终由确定性代码决定。
"""

import os
import time

from ..model import post_json

SYSTEM_ONE_URL = "https://api.typesafe.ai/v1/systemone"


def judge(claims, page, *, history=()):
    """对一组自然语言陈述做真值判断。

    Args:
        claims: list[str]，每条是一个【原子】陈述，如 "页面上显示了 Casa Flora 的详情介绍"。
                一条只判一件事——这是拉开 0.97 与 0.53 差距的关键。
        page: 当前页面状态（需含 url / title / text）。
        history: 最近的执行轨迹，作为判定时的上下文。

    Returns:
        list[dict]: [{"claim": str, "noul": float}, ...]，顺序与入参一致。

    Raises:
        RuntimeError: 缺少 TYPESAFE_API_KEY，或接口不可用/响应异常。
    """
    if not claims:
        return []
    key = os.environ.get("TYPESAFE_API_KEY")
    if not key:
        raise RuntimeError("语义断言需要 TYPESAFE_API_KEY")

    questions = {
        f"c{index}": {"type": "noul", "instructions": {"claim": claim}}
        for index, claim in enumerate(claims)
    }
    body = {
        "model": os.environ.get("TYPESAFE_MODEL", "jev-latest"),
        "state": {
            "page": {k: page[k] for k in ("url", "title", "text") if k in page},
            "recent_actions": [
                {k: h.get(k) for k in ("action", "kind", "text", "page_changed")} for h in list(history)[-10:]
            ],
        },
        "questions": questions,
    }

    started = time.perf_counter()
    result = post_json(SYSTEM_ONE_URL, key, body)
    latency_ms = round((time.perf_counter() - started) * 1000)

    answers = result.get("answers", {})
    output = []
    for index, claim in enumerate(claims):
        answer = answers.get(f"c{index}")
        if not isinstance(answer, dict) or not isinstance(answer.get("noul"), (int, float)):
            raise RuntimeError(f"TypeSafe 未返回可用的 noul 结果（第 {index + 1} 条断言）")
        output.append({"claim": claim, "noul": float(answer["noul"])})
    output[0]["latency_ms"] = latency_ms
    output[0]["model"] = result.get("model")
    return output