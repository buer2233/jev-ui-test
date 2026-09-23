"""单条自然语言用例的执行：登录注入 → 逐步执行（Allure 步骤）→ 终局断言。

步骤包装刻意放在这一层，而不是给 agent.py 打装饰器：
  · agent.py 是库，demo.py（inspector）也依赖它，不应被 Allure 污染；
  · 步骤标题需要动态内容（第几步、什么操作），装饰器做不到。
"""

import json
import time

import allure

import jev_ultrafast.agent as agent_module

from ..browser import StalePage
from . import e9_login
from .assertions import check, combine, resolve_threshold
from .config import DEFAULT_MAX_STEPS, DEFAULT_TIMEOUT_S
from .judge import judge
from .reporting import attach_decision, attach_environment, attach_screenshot, attach_trace


def _tick_once(agent):
    """执行一轮 预测 → 动作，复刻 Agent.command("tick") 的过期处理语义。

    刻意不直接调用 command("tick")：那样预测与动作会落在同一个 Allure 步骤里，
    报告里就看不出"模型选了什么"和"实际执行了什么"。
    """
    agent.command("predict")
    if agent.state["status"] in {"done", "blocked"}:
        return None
    try:
        return agent.command("act", {"fingerprint": agent.state["page"]["fingerprint"]})
    except StalePage:
        # 决策绑定的页面已变：丢弃决策并重新观察，下一步重新决策。
        # 与 agent.command("tick") 的 catch 分支保持一致。
        state = agent.state
        state["decision"] = None
        state["status"] = "ready"
        state["page"] = state["browser"].observe(screenshot=agent.screenshots)
        state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
        return None


def _login_cookies(case, base_url):
    """按用例声明的 login 角色完成接口登录，返回可注入浏览器的 cookie 列表。"""
    role = case.get("login", "none")
    if role == "none":
        return []
    if not base_url:
        raise RuntimeError(f"用例 {case['id']} 声明了 login={role}，但未配置 E9_BASE_URL")
    account = e9_login.load_credentials(role)
    cookies = e9_login.login(base_url, account["user_name"], account["password"])
    # 只把 cookie 的【名字】写进报告，值绝不落盘
    allure.attach(
        json.dumps([c["name"] for c in cookies], ensure_ascii=False),
        "注入的 Cookie 名称", allure.attachment_type.JSON,
    )
    return cookies


def run_case(case):
    """执行一条用例。断言失败时由 pytest 抛出，这里不吞异常。"""
    from ..agent import Agent

    timeout_s = int(case.get("timeout_s", DEFAULT_TIMEOUT_S))
    max_steps = int(case.get("max_steps", DEFAULT_MAX_STEPS))
    threshold = resolve_threshold(case, case)
    screenshot_every_step = bool((case.get("on_failure") or {}).get("screenshot", False))

    cookies = []
    with allure.step("前置：接口登录并注入浏览器登录态"):
        cookies = _login_cookies(case, case["_base_url"])

    started = time.perf_counter()
    agent = Agent(case["url"], case["goal"], cookies=cookies, screenshots=screenshot_every_step)
    steps, note = 0, ""
    try:
        # 初始观察也留痕，便于报告里看到"起点长什么样"
        with allure.step("观察初始页面"):
            allure.attach(
                f"{agent.state['page'].get('title')}\n{agent.state['page'].get('url')}",
                "初始页面", allure.attachment_type.TEXT,
            )

        while agent.state["status"] not in {"done", "blocked"}:
            if steps >= max_steps:
                note = f"达到用例步数上限 {max_steps}，提前停止"
                allure.attach(note, "提前停止", allure.attachment_type.TEXT)
                break
            if time.perf_counter() - started > timeout_s:
                note = f"达到用例超时 {timeout_s}s，提前停止"
                allure.attach(note, "提前停止", allure.attachment_type.TEXT)
                break

            steps += 1
            before_actions = len(agent.state["history"])
            with allure.step(f"第 {steps} 步 · 决策（Jev 概率判断）"):
                _tick_once(agent)
                decision = agent.state.get("decision") or (
                    agent.state["decisions"][-1] if agent.state["decisions"] else None
                )
                if decision:
                    attach_decision(decision)

            if agent.state["status"] in {"done", "blocked"}:
                break

            history = agent.state["history"]
            # 只有真的执行了动作才建"执行"步骤：DONE/BLOCKED 决策不产生动作，
            # 若照样建步骤，报告里会把上一步的动作重复显示成本步的结果。
            if len(history) <= before_actions:
                continue
            last = history[-1]
            with allure.step(f"第 {steps} 步 · 执行 {last.get('operation')} → {last.get('action')}"):
                if last.get("text"):
                    allure.attach(str(last["text"]), "写入的文本", allure.attachment_type.TEXT)
                if screenshot_every_step:
                    attach_screenshot(agent.state["page"])

        snapshot = agent.snapshot()
    finally:
        agent.close()

    status = snapshot["status"]
    with allure.step("终局：记录执行结果"):
        allure.attach(
            json.dumps(
                {
                    "终止状态": status,
                    "浏览器动作数": len(snapshot["history"]),
                    "决策请求数": len(snapshot["decisions"]),
                    "文本模型调用数": len(snapshot["text_calls"]),
                    "耗时ms": snapshot.get("elapsed_ms"),
                    "备注": note,
                },
                ensure_ascii=False, indent=2,
            ),
            "执行摘要", allure.attachment_type.JSON,
        )
        attach_trace(snapshot)

    page = snapshot["page"]
    ctx = {"threshold": threshold, "judge": lambda claims: judge(claims, page, history=snapshot["history"])}

    results = []
    for expect in case["expect"]:
        title = expect.get("desc") or expect.get("claim") or expect["type"]
        with allure.step(f"断言：{title}"):
            result = check(page, expect, ctx=ctx)
            results.append(result)
            allure.attach(
                json.dumps(result["evidence"], ensure_ascii=False, indent=2),
                "断言证据", allure.attachment_type.JSON,
            )

    verdict = combine(results, case.get("expect_mode", "and"), case.get("min_pass"))
    with allure.step("终局判定"):
        allure.attach(
            json.dumps(verdict, ensure_ascii=False, indent=2),
            "断言汇总", allure.attachment_type.JSON,
        )
        # 失败时补一张最终截图，报告里能直接看到当时页面
        attach_screenshot(page, when_failed=not verdict["ok"])
        assert verdict["ok"], (
            f"{verdict['detail']}；明细见「断言汇总」附件"
            + (f"；执行备注：{note}" if note else "")
        )


def attach_environment_once(case):
    """会话级环境信息，供 Allure 的 Environment 区块展示。"""
    attach_environment(case, agent_module)