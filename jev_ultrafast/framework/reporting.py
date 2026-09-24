"""Allure 报告的内容组织：附件、环境信息。

关于安全：cookie 值、token、密码一律不进附件。
截图可能含业务数据，因此默认只在失败时附加。
"""

import base64
import json
import os
import platform
import subprocess
from pathlib import Path

import allure

REPO_ROOT = Path(__file__).resolve().parents[2]


def _prob(distribution, key):
    """从概率分布里取某一条路径的概率；不适用时给「—」。

    给「—」而不是 0，是因为这两种情况必须能分开读：多级决策下不是每条路径都有
    目标概率（DONE / BLOCKED 没有 target），0 会被读成"模型认为不可能"。
    """
    if key is None:
        return "—"
    value = (distribution or {}).get(key)
    return "—" if value is None else round(value, 3)


def decision_params(decision):
    """决策步骤【输出侧】的参数表内容——进入 `with` 之后才知道的量（路 4）。

    与 `attach_decision` 的分工：参数表给"一眼要看到的几个数"，附件给完整载荷。
    全量概率分布**不进参数表**——E9 场景下候选可能有 10+ 个，参数表会变得很长，
    反而看不清重点（实施方案 §4.3）。
    """
    return {
        "选中操作": decision.get("operation"),
        "目标索引": decision.get("target") if decision.get("target") is not None else "—",
        "操作概率": _prob(decision.get("operation_probabilities"), decision.get("operation")),
        "目标概率": _prob(decision.get("target_probabilities"), decision.get("target")),
        "置信度": decision.get("confidence"),
        "决策耗时ms": decision.get("latency_ms"),
        # >1 表示这次决策**重发过**：响应不合法但浏览器没被动过。
        # AGENTS.md 要求"重发要看得见，不静默自愈"，所以直接给数字。
        "重发次数": decision.get("decision_attempts"),
        # 用响应里回报的模型名，而不是环境变量里的默认值：同一个用例昨天过今天没过时，
        # 一看模型名变了就明白原因（这也是 attach_environment 记模型版本的同一理由）。
        "模型": decision.get("model"),
    }


def _strip_page_text(request):
    """复制一份请求体并去掉 `state.page.text`，返回副本。

    深拷贝而不是就地 pop：那个 dict 是 model.choose() 交给 API 的那一份，
    还留在 snapshot["decisions"] 里，改它就是改调用方的对象。
    """
    if not request:
        return request
    body = json.loads(json.dumps(request, ensure_ascii=False))
    page = (body.get("state") or {}).get("page")
    if isinstance(page, dict):
        page.pop("text", None)
    return body


def attach_decision(decision):
    """把 Jev 的决策概率与完整载荷作为附件，报告里能直接看到模型"有多确定"。

    拆成两个附件：概率是每次都要看的，请求体只在出问题时才翻。
    请求体里**剥掉 `state.page.text`**（实测单次请求体 9–20 KB，页面正文占一半以上），
    并把"剥掉了多少字符"写进附件——否则会让人误以为模型根本没看到页面正文。
    """
    request = decision.get("request") or {}
    text_len = len(((request.get("state") or {}).get("page") or {}).get("text") or "")
    allure.attach(
        json.dumps(
            {
                "operation": decision.get("operation"),
                "choice": decision.get("choice"),
                "target": decision.get("target"),
                "confidence": decision.get("confidence"),
                "target_confidence": decision.get("target_confidence"),
                "probabilities": decision.get("probabilities"),
                "operation_probabilities": decision.get("operation_probabilities"),
                "target_probabilities": decision.get("target_probabilities"),
                "decision_attempts": decision.get("decision_attempts"),
                "model": decision.get("model"),
                "latency_ms": decision.get("latency_ms"),
                "usage": decision.get("usage"),
            },
            ensure_ascii=False, indent=2,
        ),
        "Jev 决策概率", allure.attachment_type.JSON,
    )
    allure.attach(
        json.dumps(
            {
                "说明": f"request.state.page.text 已省略（原长 {text_len} 字符），其余原样。",
                "request": _strip_page_text(request),
                "response": decision.get("raw_answers"),
            },
            ensure_ascii=False, indent=2,
        ),
        "Jev 请求与响应", allure.attachment_type.JSON,
    )


def attach_execution(action, last):
    """执行步骤的请求与返回。

    ⚠️ 「写入文本」「文本模型耗时」**只能进附件，进不了参数表**：
    TYPE_TEXT 要写的内容是在 `agent.command("act")` **内部**由文本模型生成的
    （`agent.py:106-115`），执行者在进入步骤之前根本不知道要写什么。
    不要为了让它进参数表去把文本生成从库里挪出来——那会改变 `pending_text`
    复用与新鲜度校验的既有语义（实施方案 §4.4）。
    """
    allure.attach(
        json.dumps(
            {
                "action": action,            # 就是交给 browser_operation 的那一份
                "写入文本": last.get("text"),
                "文本生成": (
                    {"模型": last.get("text_helper"), "耗时ms": last.get("text_latency_ms")}
                    if last.get("text")
                    else None
                ),
            },
            ensure_ascii=False, indent=2,
        ),
        "执行请求", allure.attachment_type.JSON,
    )
    allure.attach(
        json.dumps(
            {
                "浏览器返回": last.get("execute_result"),
                "页面已变化": last.get("page_changed"),
                "执行后URL": last.get("url"),
                "累计耗时ms": last.get("elapsed_ms"),
            },
            ensure_ascii=False, indent=2,
        ),
        "执行返回", allure.attachment_type.JSON,
    )


def attach_screenshot(page, *, when_failed=False):
    """附加页面截图。没有截图数据时静默跳过（screenshots=False 的用例就是如此）。"""
    data = page.get("screenshot")
    if not data:
        return
    allure.attach(
        base64.b64decode(data),
        "失败时页面截图" if when_failed else "页面截图",
        allure.attachment_type.PNG,
    )


def attach_trace(snapshot):
    """附加完整执行轨迹，失败时用于定位"卡在哪一步"。"""
    allure.attach(
        json.dumps(snapshot.get("history", []), ensure_ascii=False, indent=2),
        "执行轨迹", allure.attachment_type.JSON,
    )
    text = (snapshot.get("page") or {}).get("text") or ""
    if text:
        allure.attach(text[:6000], "最终页面可见文本", allure.attachment_type.TEXT)


def _git_revision():
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def attach_environment(case, _agent_module=None):
    """写入 Allure 的环境信息（--alluredir 下的 environment.properties）。

    记录模型版本与源码版本：同一个用例昨天过了今天没过时，
    一看模型名变了就明白原因——这正是 scripts/measure_flights.py 的既有做法。
    """
    entries = {
        "E9 入口": case.get("_base_url") or "(本地 fixture)",
        "TypeSafe 模型": os.environ.get("TYPESAFE_MODEL", "jev-latest"),
        "文本模型": os.environ.get("TEXT_MODEL", "deepseek-chat"),
        "文本模型端点": os.environ.get("TEXT_MODEL_BASE_URL", ""),
        "文本模型思考": os.environ.get("TEXT_MODEL_REASONING", "none"),
        "断言默认阈值": os.environ.get("JEV_NL_THRESHOLD", "0.75"),
        "Python": platform.python_version(),
        "源码版本": _git_revision(),
    }
    allure.attach(
        "\n".join(f"{k}={v}" for k, v in entries.items()),
        "environment", allure.attachment_type.TEXT,
    )
    return entries