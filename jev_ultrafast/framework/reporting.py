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


def attach_decision(decision):
    """把 Jev 的决策概率作为附件，报告里能直接看到模型"有多确定"。"""
    allure.attach(
        json.dumps(
            {
                "operation": decision.get("operation"),
                "choice": decision.get("choice"),
                "target": decision.get("target"),
                "confidence": decision.get("confidence"),
                "probabilities": decision.get("probabilities"),
                "latency_ms": decision.get("latency_ms"),
                "usage": decision.get("usage"),
            },
            ensure_ascii=False, indent=2,
        ),
        "Jev 决策概率", allure.attachment_type.JSON,
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