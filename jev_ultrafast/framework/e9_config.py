"""E9 测试环境、账号与图谱 MCP 配置。

管理方式对齐 api-test-E9 的 config.json（同一套字段与同一套优先级），
但**本仓库的 config.json 必须保持 gitignore**，原因不同：

    api-test-E9 的 remote 是公司内网 GitLab，config.json 可随之入库；
    jev-ultrafast 的 remote 是 GitHub 上的公开仓库
    （实测 https://github.com/buer2233/jev-ultrafast 未鉴权即可读取），
    把真实账号与内网地址提交上去就是凭据泄漏。

于是：结构照搬、优先级照搬，只把落点换成"本地私有文件 + 模板入库"。
    base_url 优先级：环境变量 E9_BASE_URL        > config.json.base_url
    账号优先级    ：环境变量 E9_LOGINID/…PASSWORD > config.json.<role>

首次使用：
    cp config.example.json config.json   # 然后填入真实的 base_url 与账号
"""

import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config.json"

MCP_DEFAULTS = {
    "host": "",
    "port": 9750,
    "query_path": "/mcp",
    "ops_path": "/servers/e9-ops/mcp",
    "graph_project": "",
}

ROLE_HINT = "admin / employee1 ~ employee5"


def _load():
    """读取本地 config.json；缺失或格式错误时返回空 dict（调用方按缺配置处理）。"""
    if not CONFIG_PATH.exists():
        return {}
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def base_url():
    """E9 站点根地址（已去掉末尾斜杠）；未配置时返回空串，由调用方快速失败。"""
    env = os.environ.get("E9_BASE_URL")
    if env:
        return env.rstrip("/")
    return str(_load().get("base_url", "")).strip().rstrip("/")


def load_account(role="employee1"):
    """读取 E9 账号凭据。

    优先级（与 api-test-E9 的 load_account 一致）：
        1. 环境变量 E9_LOGINID / E9_USERPASSWORD（CI 用，避免本机文件残留）
        2. config.json 中该 role 的 user_name / password

    Args:
        role: 账号角色，见 config.example.json，如 "admin"、"employee1"。

    Returns:
        dict: {"user_name": str, "password": str}

    Raises:
        RuntimeError: 凭据缺失。消息里给出可执行的修复指引，且不回显任何凭据。
    """
    loginid = os.environ.get("E9_LOGINID")
    password = os.environ.get("E9_USERPASSWORD")
    if loginid or password:
        if not loginid or not password:
            raise RuntimeError("E9_LOGINID 与 E9_USERPASSWORD 必须同时配置")
        return {"user_name": loginid, "password": password}

    config = _load()
    account = config.get(role)
    if isinstance(account, dict) and account.get("user_name") and account.get("password"):
        return {"user_name": account["user_name"], "password": account["password"]}

    if not config:
        raise RuntimeError(
            f"缺少 E9 私有配置：请先 `cp config.example.json config.json` 并填入 base_url 与账号"
            f"（角色 {role}），或设置环境变量 E9_LOGINID / E9_USERPASSWORD。"
            "注意：config.json 已被 gitignore，因为本仓库是公开仓库，切勿提交真实凭据。"
        )
    raise RuntimeError(
        f"config.json 中缺少角色 {role!r} 的有效凭据（可用角色：{ROLE_HINT}），"
        "或设置环境变量 E9_LOGINID / E9_USERPASSWORD。"
    )


def mcp():
    """E9 知识图谱 MCP 配置（用于查询 E9 功能实现，辅助编写用例）。

    host 未配置时 URL 为空串，调用方据此快速失败，避免连到拼错的地址。
    """
    merged = dict(MCP_DEFAULTS)
    raw = _load().get("mcp")
    if isinstance(raw, dict):
        merged.update({k: v for k, v in raw.items() if v not in (None, "")})
    host, port = str(merged["host"]).strip(), merged["port"]
    return {
        **merged,
        "query_url": f"http://{host}:{port}{merged['query_path']}" if host else "",
        "ops_url": f"http://{host}:{port}{merged['ops_path']}" if host else "",
    }