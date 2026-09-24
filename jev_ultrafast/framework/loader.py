"""自然语言用例的加载与校验。

用例用 YAML 写：非技术同事可读可改，同时保持机器可校验。
在【加载期】就把错误报出来，而不是等跑到一半才炸。
"""

import os
import re
from pathlib import Path

import yaml

from .assertions import CHECKERS

# 与 demo.py:51 的约束保持一致
MAX_GOAL_LENGTH = 2000
EXPECT_MODES = {"and", "or", "min_pass"}
SEVERITIES = {"blocker", "critical", "normal", "minor", "trivial"}

CASE_KEYS = {
    "id", "name", "feature", "epic", "severity", "tags", "url", "login", "goal",
    "expect", "expect_mode", "min_pass", "threshold", "timeout_s", "max_steps", "on_failure",
    # 逐用例的重跑次数（覆盖 pytest --reruns）。负向对照这类"本来就该失败"的用例
    # 设 reruns: 0，避免白跑一遍。只做【用例级】重跑，绝不做步骤级。
    "reruns",
}
DEFAULTS_KEYS = {
    "login", "timeout_s", "max_steps", "expect_mode", "min_pass", "threshold",
    "tags", "feature", "epic", "severity",
}

# 变量：{{ base_url }} 指向 E9，{{ fixture_url }} 指向本地样例页。
# 用例里【不得】写真实内网地址——本仓库是公网可访问的 fork。
VARIABLE_PATTERN = re.compile(r"\{\{\s*(\w+)\s*\}\}")


class CaseError(ValueError):
    """用例文件不合法。"""


def _default_variables():
    from . import e9_api, e9_config
    from .config import E9_ENTRY_PATH, E9_WF_PATH_LIST_PATH

    # base_url 的读取收敛在 e9_config（环境变量 > 本地 config.json）
    base = e9_config.base_url()
    return {
        "base_url": base,
        "e9_entry": f"{base}{E9_ENTRY_PATH}" if base else "",
        # 后端引擎的流程列表（搭流程定义的入口）。与 e9_entry 分属两个 SPA，
        # 不能互相替代——见 config.E9_WF_PATH_LIST_PATH 的注释。
        "wf_path_list": f"{base}{E9_WF_PATH_LIST_PATH}" if base else "",
        # 本地样例页由 conftest 的 fixture 服务器提供
        "fixture_url": os.environ.get("JEV_FIXTURE_URL", ""),
        # 前置建模模块的名字。取值收敛在 e9_api，让 fixture（执行期建数据）
        # 与用例 goal（收集期替换变量）引用同一个常量，不会各写一份而漂移。
        "eb_mode_name": e9_api.EB_MODE_NAME,
    }


def _substitute(value, variables, missing):
    """替换 {{ 变量 }}；未解析的记入 missing，由调用方决定跳过而不是整体报错。

    这样"只想跑本地样例"时不会因为 E9_BASE_URL 没配而连收集都失败。
    """
    if not isinstance(value, str):
        return value

    def replace(match):
        name = match.group(1)
        resolved = variables.get(name)
        if not resolved:
            missing.add(name)
            return match.group(0)  # 保留原样，报错信息里能直接看到是哪个变量
        return resolved

    return VARIABLE_PATTERN.sub(replace, value)


def _require(mapping, key, where):
    value = mapping.get(key)
    if value in (None, "", []):
        raise CaseError(f"{where}: 缺少必填字段 {key!r}")
    return value


def _validate_expect(expect, where):
    if not isinstance(expect, list) or not expect:
        raise CaseError(f"{where}: expect 必须是非空列表")
    for index, item in enumerate(expect, 1):
        spot = f"{where} 第 {index} 条断言"
        if not isinstance(item, dict):
            raise CaseError(f"{spot}: 必须是映射")
        kind = item.get("type")
        if kind not in CHECKERS:
            raise CaseError(f"{spot}: 未知的 type={kind!r}；可用：{sorted(CHECKERS)}")
        if kind == "ai" and not item.get("claim"):
            raise CaseError(f"{spot}: ai 类型必须提供 claim")
        if kind in {"text_contains", "text_not_contains", "url_contains", "url_equals"} and "value" not in item:
            raise CaseError(f"{spot}: {kind} 必须提供 value")
        if kind in {"element_exists", "element_absent", "element_enabled"} and not item.get("label"):
            raise CaseError(f"{spot}: {kind} 必须提供 label")
        if kind == "element_value" and not (item.get("equals") or item.get("contains")):
            raise CaseError(f"{spot}: element_value 必须提供 equals 或 contains")
        if kind == "element_count" and not (
            item.get("equals") is not None or item.get("min") is not None or item.get("max") is not None
        ):
            raise CaseError(f"{spot}: element_count 必须提供 equals / min / max 之一")
        threshold = item.get("threshold")
        if threshold is not None and not 0 <= float(threshold) <= 1:
            raise CaseError(f"{spot}: threshold 必须在 0–1 之间")


def _validate_case(case, defaults, where):
    unknown = set(case) - CASE_KEYS
    if unknown:
        # 宁可报错也不要静默忽略：字段名拼错会让整条断言悄悄失效
        raise CaseError(f"{where}: 未知字段 {sorted(unknown)}；可用：{sorted(CASE_KEYS)}")

    mode = case.get("expect_mode", defaults.get("expect_mode", "and"))
    if mode not in EXPECT_MODES:
        raise CaseError(f"{where}: expect_mode={mode!r} 非法；可用 {sorted(EXPECT_MODES)}")
    if mode == "min_pass":
        need = case.get("min_pass", defaults.get("min_pass"))
        total = len(case.get("expect") or [])
        if need is None:
            raise CaseError(f"{where}: expect_mode=min_pass 时必须提供 min_pass")
        if not 1 <= int(need) <= total:
            raise CaseError(f"{where}: min_pass={need} 必须落在 1–{total} 之间")

    threshold = case.get("threshold", defaults.get("threshold"))
    if threshold is not None and not 0 <= float(threshold) <= 1:
        raise CaseError(f"{where}: threshold 必须在 0–1 之间")

    severity = case.get("severity")
    if severity and severity not in SEVERITIES:
        raise CaseError(f"{where}: severity={severity!r} 非法；可用 {sorted(SEVERITIES)}")

    reruns = case.get("reruns")
    if reruns is not None and (not isinstance(reruns, int) or isinstance(reruns, bool) or reruns < 0):
        raise CaseError(f"{where}: reruns 必须是非负整数，实际为 {reruns!r}")

    goal = case.get("goal") or ""
    if len(goal) > MAX_GOAL_LENGTH:
        raise CaseError(f"{where}: goal 长度 {len(goal)} 超过上限 {MAX_GOAL_LENGTH}")


def load_file(path, variables):
    """加载单个 YAML 文件，返回用例列表。"""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise CaseError(f"{path.name}: 顶层必须是映射")

    unknown = set(raw) - {"version", "defaults", "cases"}
    if unknown:
        raise CaseError(f"{path.name}: 未知的顶层字段 {sorted(unknown)}；可用 version / defaults / cases")

    defaults = raw.get("defaults") or {}
    unknown_defaults = set(defaults) - DEFAULTS_KEYS
    if unknown_defaults:
        raise CaseError(f"{path.name}: defaults 中未知字段 {sorted(unknown_defaults)}")

    cases = raw.get("cases")
    if not isinstance(cases, list) or not cases:
        raise CaseError(f"{path.name}: cases 必须是非空列表")

    output, seen_here = [], {}
    for index, case in enumerate(cases, 1):
        where = f"{path.name} cases[{index}]"
        if not isinstance(case, dict):
            raise CaseError(f"{where}: 必须是映射")

        merged = {**{k: v for k, v in defaults.items() if k not in {"tags"}}, **case}
        merged["tags"] = list(dict.fromkeys([*(defaults.get("tags") or []), *(case.get("tags") or [])]))

        _require(merged, "id", where)
        # 同一文件内也要查重。load_cases 只在【跨文件】层面查，一个文件里写了
        # 两条同 id 的用例会双双加载成功，然后 --case 选中两条、Allure 历史混在一起。
        # 这类错误必须和别的用例错误一样在加载期报出来。
        if merged["id"] in seen_here:
            raise CaseError(
                f"{where}: id 重复 {merged['id']!r}，本文件第 {seen_here[merged['id']]} 条已用过"
            )
        seen_here[merged["id"]] = index
        _require(merged, "name", where)
        _require(merged, "url", where)
        _require(merged, "goal", where)
        _validate_case(merged, defaults, where)
        _validate_expect(merged.get("expect"), where)

        missing = set()
        merged["url"] = _substitute(merged["url"], variables, missing)
        merged["goal"] = _substitute(merged["goal"], variables, missing)
        if missing:
            # 不在这里抛错：让用例照常被收集，执行时以明确原因 skip。
            merged["_skip"] = (
                f"用例依赖的变量未配置：{sorted(missing)}。"
                + ("请在 .env 中设置 E9_BASE_URL。" if "base_url" in missing else "")
            )
        merged["login"] = merged.get("login", "none")
        merged["expect_mode"] = merged.get("expect_mode", defaults.get("expect_mode", "and"))
        if merged["expect_mode"] != "min_pass":
            merged.pop("min_pass", None)
        merged["__source__"] = path.name
        output.append(merged)
    return output


def load_cases(directory=None):
    """加载目录下所有用例，并校验 id 全局唯一。"""
    root = Path(directory) if directory else Path(__file__).resolve().parents[2] / "cases"
    if not root.exists():
        return []

    variables = _default_variables()
    cases, seen = [], {}
    for path in sorted(root.rglob("*.yaml")):
        for case in load_file(path, variables):
            if case["id"] in seen:
                raise CaseError(
                    f"用例 id 重复：{case['id']!r} 同时出现在 {seen[case['id']]} 和 {case['__source__']}"
                )
            seen[case["id"]] = case["__source__"]
            cases.append(case)
    return cases