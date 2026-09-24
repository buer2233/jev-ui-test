"""自然语言用例的 pytest 接入。

职责：
  · 在【收集期之前】起一个只监听回环的 fixture 服务，供 {{ fixture_url }} 解析；
  · 把 cases/ 下的 YAML 收集成参数化用例；
  · 把 E9 环境地址注入用例，供 {{ base_url }} 解析。
"""

import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from jev_ultrafast.framework import e9_config
from jev_ultrafast.framework.loader import load_cases

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_HTML = REPO_ROOT / "jev_ultrafast" / "static" / "fixture.html"

_server = None


class _FixtureHandler(BaseHTTPRequestHandler):
    """只服务 fixture.html；只监听 127.0.0.1，不对外暴露。"""

    def do_GET(self):
        payload = FIXTURE_HTML.read_text(encoding="utf-8").encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        pass


def pytest_configure(config):
    """收集期就要能解析 {{ fixture_url }}，所以服务在这里起，而不是用 session fixture。"""
    global _server
    if os.environ.get("JEV_FIXTURE_URL") or _server is not None:
        return
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    _server = ThreadingHTTPServer(("127.0.0.1", port), _FixtureHandler)
    threading.Thread(target=_server.serve_forever, daemon=True).start()
    os.environ["JEV_FIXTURE_URL"] = f"http://127.0.0.1:{port}"


def pytest_unconfigure(config):  # noqa: ARG001  (pluggy 要求参数名与 hookspec 一致)
    global _server
    if _server is not None:
        _server.shutdown()
        _server = None


def pytest_addoption(parser):
    parser.addoption("--cases-dir", default=None, help="自然语言用例目录（默认 cases/）")
    parser.addoption(
        "--case", action="append", default=None, metavar="ID",
        help="只执行指定 id 的用例，可重复。用于本地调试，不改变用例内容。",
    )
    parser.addoption(
        "--nl", action="store_true", default=False,
        help="执行自然语言用例（会调用付费模型 API，并接管一个 Chrome 标签页）。",
    )


def pytest_collection_modifyitems(config, items):
    """收集后的两件事：逐用例重跑设置、以及默认跳过 NL 用例。

    默认跳过：项目契约是「测试离线、不调付费 API」（见 README 与 AGENTS.md），
    所以 `uv run pytest` 必须保持离线；NL 用例要通过 --nl 显式请求才会跑。
    """
    run_nl = bool(config.getoption("--nl")) or os.environ.get("JEV_NL_RUN") == "1"
    skip = pytest.mark.skip(reason="自然语言用例默认跳过（会调用付费 API）；用 --nl 运行")
    for item in items:
        if "nl_case" not in item.keywords:
            continue
        # 逐用例覆盖重跑次数。负向对照设计上就该失败，重跑只是白跑一遍。
        case = getattr(getattr(item, "callspec", None), "params", {}).get("nl_case")
        if not isinstance(case, dict):
            # 参数化列表为空时 pytest 会塞一个 NotSet 占位对象进来
            # （例如 cases/ 为空）。它不是用例，直接 .get 会炸成 INTERNALERROR，
            # 把"没有匹配的用例"报成 pytest 内部错误。
            continue
        if case.get("reruns") is not None:
            item.add_marker(pytest.mark.flaky(reruns=int(case["reruns"])))
        if not run_nl:
            item.add_marker(skip)


def pytest_generate_tests(metafunc):
    """把 YAML 用例收集成参数化用例。

    用 pytest_generate_tests 而不是 @pytest.mark.parametrize：
    用例是运行时从 YAML 读的，数量与内容都不在代码里，装饰器要静态列表。
    ids 固定用 case["id"]，这样 Allure 历史趋势与 -k 选择都稳定。
    """
    if "nl_case" not in metafunc.fixturenames:
        return

    cases = load_cases(metafunc.config.getoption("--cases-dir"))
    selected = metafunc.config.getoption("--case")
    if selected:
        wanted = list(dict.fromkeys(selected))
        available = {c["id"] for c in cases}
        unknown = [case_id for case_id in wanted if case_id not in available]
        # 写错的 id 必须【响亮地】失败。以前这里是静默过滤成空列表，
        # pytest 会塞一个 NotSet 占位、随后在 modifyitems 里炸成 INTERNALERROR，
        # 报错完全看不出是 id 写错了。
        if unknown:
            raise pytest.UsageError(
                f"--case 指定的用例不存在：{unknown}；当前可用：{sorted(available) or '（没有加载到任何用例）'}"
            )
        cases = [c for c in cases if c["id"] in set(wanted)]

    base_url = e9_config.base_url()
    for case in cases:
        case["_base_url"] = base_url

    metafunc.parametrize("nl_case", cases, ids=[c["id"] for c in cases])


@pytest.fixture(scope="session")
def e9_base_url():
    return e9_config.base_url()