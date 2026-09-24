"""E9 后端接口调用：前置数据（建模模块 / EB 表单）的建立与回收。

为什么需要这一层：自然语言用例的起点是"页面已经打开"，但有些用例的前置是
**环境里必须先存在某条业务数据**（例如工作流必须能关联到一个建模表单）。
让 agent 在 UI 里现建这条数据，会把用例的步数预算耗在和被测目标无关的准备上，
而且失败原因会混在一起。所以前置数据走接口、在 fixture 里一次性建好。

接口契约的来源（两处，都已核对）：
  · E9 主干源码 `src/com/engine/cube/web/ModeAppAction.java:373` → `cmd/app/SaveModeInfo.java`
    读出的参数名与新增/删除分支；
  · 同作者项目 api-test-E9 的 `tools/prepare_formmode_test_data.py` 的调用约定
    （sessionkey → table/datas 两段式取数）。

2026-09-24 在 10.12.21.26:8080 上实测确认：
  · 新增只需 `modename` + `formid` + `modetype` 三个参数，返回 `{"id":<modeid>,"status":"1"}`；
  · 同一接口传 `id` + `operation=delete` 即回收，是干净的往返。
"""

import requests

from . import e9_config, e9_login

# 建模模块的保存接口。`id` 为空即新增，`id` + operation=delete 即删除。
MODE_SAVE_PATH = "/api/cube/mode/mode/saveModeInfo"

# 建模模块绑定的表单 id。取 -7（环境里的虚拟表单）——本项目的用途是给工作流
# 提供一个可关联的表单壳，不关心表单字段，所以复用现成的虚拟表单即可，
# 不必先建 form 层（见 config.json 与 2026-09-24 与用户的确认）。
DEFAULT_FORM_ID = "-7"

# 模块类型。实测 modetype=1 可正常新增。
DEFAULT_MODE_TYPE = "1"

# 前置建模模块的名字。**必须是固定值**：用例的 goal 通过 {{ eb_mode_name }}
# 引用它，而变量替换发生在【收集期】、fixture 运行在【执行期】，
# 两者不可能用运行时返回值通信，只能约定同一个常量。
# loader 的 _default_variables() 读的就是这个常量，保证单一事实来源。
EB_MODE_NAME = "UI自动化_EB表单"


class E9ApiError(RuntimeError):
    """E9 接口调用失败。"""


def _post(session, base_url, path, data, *, timeout=30):
    """向 E9 发一个表单编码的 POST，返回解析后的 JSON。

    E9 的业务接口一律用 x-www-form-urlencoded，且**必须在站内 Referer 下**调用，
    否则会被判成非法请求（与 e9_login 的请求头约定一致）。
    """
    base = base_url.rstrip("/")
    response = session.post(
        base + path,
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": base,
            "Referer": f"{base}/wui/index.html",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    try:
        return response.json()
    except ValueError as error:
        raise E9ApiError(
            f"E9 接口 {path} 返回的不是 JSON（HTTP {response.status_code}）：{response.text[:200]}"
        ) from error


def admin_session(base_url=None, *, timeout=30, proxy=None):
    """用 config.json 里的 admin 账号建一个已登录的接口会话。

    前置数据属于"管理员配置"范畴（`SaveModeInfo` 内部校验 `ModeSetting:All`），
    与用例本身的执行账号（employee1~5）不是同一个人，所以单独登录。
    """
    base = (base_url or e9_config.base_url()).rstrip("/")
    if not base:
        raise E9ApiError("未配置 E9 地址：请设置 E9_BASE_URL 或在 config.json 填 base_url")
    account = e9_config.load_account("admin")
    return e9_login.open_session(
        base, account["user_name"], account["password"], timeout=timeout, proxy=proxy
    )


def create_mode(
    session,
    base_url,
    *,
    modename,
    formid=DEFAULT_FORM_ID,
    modetype=DEFAULT_MODE_TYPE,
    timeout=30,
):
    """新建一个建模模块（EB 表单），返回它的 modeid。

    Raises:
        E9ApiError: 新增失败。E9 失败时不抛 HTTP 错误，而是把 `status` 置成 -1
            （见 `ModeAppAction.saveModeInfo` 的 catch 分支），所以这里必须查 status。
    """
    data = _post(
        session,
        base_url,
        MODE_SAVE_PATH,
        {"modename": modename, "formid": str(formid), "modetype": str(modetype)},
        timeout=timeout,
    )
    if not isinstance(data, dict) or str(data.get("status")) != "1":
        raise E9ApiError(f"新建建模模块失败：{data}")
    modeid = data.get("id")
    if not modeid:
        raise E9ApiError(f"新建建模模块未返回 id：{data}")
    return int(modeid)


def delete_mode(session, base_url, modeid, *, timeout=30):
    """回收一个建模模块。删除失败只返回 False，不抛异常。

    刻意不抛：回收发生在用例收尾，此时即使删除失败也不该把一条已经跑完的用例
    判成失败——那会让"用例通过了但没有干净收场"看起来像功能缺陷。
    """
    if not modeid:
        return False
    try:
        data = _post(
            session,
            base_url,
            MODE_SAVE_PATH,
            {"id": str(modeid), "operation": "delete"},
            timeout=timeout,
        )
    except (requests.RequestException, E9ApiError):
        return False
    return isinstance(data, dict) and str(data.get("status")) == "1"