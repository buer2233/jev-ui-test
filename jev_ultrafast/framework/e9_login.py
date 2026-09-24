"""E9 接口登录 + cookie 导出。

移植自 api-test-E9 的 page_api/login_api/login_api.py（同一作者），只保留 UI 自动化
需要的最小链路：RSA 配置 → 账号密码登录 → 登录提醒。**只依赖 requests**，
不引入该框架的 config / APIContext / curl_cffi，避免两个仓库互相耦合。

接口字段与调用顺序依据 E9 真实抓包；表单字段必须原样保留，少一个都可能被判为非法请求。
"""

import time

import requests

from . import e9_config

RSA_PATH = "/rsa/weaver.rsa.GetRsaInfo"
LOGIN_PATH = "/api/hrm/login/checkLogin"
REMIND_PATH = "/api/hrm/login/remindLogin"

# E9 各 Ajax 接口共用的请求头模板，依据 HAR 约定。
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:151.0) Gecko/20100101 Firefox/151.0"


def _timestamp():
    """毫秒级时间戳，匹配 E9 抓包里的 ts / __random__ 参数格式。"""
    return time.time_ns() // 1_000_000


def _headers(base_url, *, form=False, origin=False):
    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01" if not form else "*/*",
        "Referer": f"{base_url}/wui/index.html",
        "X-Requested-With": "XMLHttpRequest",
    }
    if form:
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=utf-8"
    if origin:
        headers["Origin"] = base_url
    return headers


def load_credentials(role="employee1"):
    """读取 E9 账号凭据。

    账号管理统一收敛在 e9_config（对齐 api-test-E9 的 config.json 方式）：
        环境变量 E9_LOGINID / E9_USERPASSWORD  >  config.json 中的 <role>
    见 docs/一期改造/一期改造实施方案.md §6.4。
    """
    return e9_config.load_account(role)


def open_session(base_url, user_name, password, *, timeout=30, proxy=None):
    """接口登录并返回**已登录的 `requests.Session`**。

    与 `login()` 走完全相同的三步链路，区别只在返回值：`login()` 交的是浏览器要用的
    cookie 列表，这里交的是会话本体，供框架自己发接口请求（例如前置数据 fixture）。

    为什么要暴露 session 而不是让调用方拿 cookie 再拼一遍：E9 的部分接口会把会话状态
    绑在 cookie 之外（登录提醒、语言设置等），复用同一条 session 才能保证与浏览器
    看到的是同一个会话。

    Args:
        base_url: E9 站点根地址，如 http://10.12.21.26:8080。
        user_name / password: 账号与密码。
        timeout: 单次请求超时（秒）。
        proxy: 传给 requests 的代理配置；None 表示不使用代理。

    Returns:
        requests.Session: 已登录、可直接调 E9 接口的会话。

    Raises:
        RuntimeError: 登录业务码或登录态异常。消息中只含非敏感诊断字段。
    """
    base = base_url.rstrip("/")
    session = requests.Session()
    session.headers["User-Agent"] = BROWSER_UA
    if proxy:
        session.proxies.update(proxy if isinstance(proxy, dict) else {"http": proxy, "https": proxy})

    # 1) RSA 登录配置（E9 登录页前置）
    rsa = session.get(
        base + RSA_PATH, params={"ts": _timestamp()}, headers=_headers(base), timeout=timeout
    )
    rsa.raise_for_status()

    # 2) 提交登录表单。字段名与取值来自真实抓包，不要精简。
    login = session.post(
        base + LOGIN_PATH,
        data={
            "islanguid": "7",
            "loginid": user_name,
            "userpassword": password,
            "dynamicPassword": "",
            "tokenAuthKey": "",
            "validatecode": "",
            "validateCodeKey": "",
            "logintype": "1",
            "messages": "",
            "isie": "false",
            "appid": "",
            "service": "",
            "isRememberPassword": "false",
        },
        headers=_headers(base, form=True, origin=True),
        timeout=timeout,
    )
    login.raise_for_status()
    data = login.json()
    if data.get("msgcode") != "0" or data.get("loginstatus") != "true":
        # 只报非敏感诊断字段，避免 token / 凭据进入日志与报告
        raise RuntimeError(
            f"E9 登录失败：msgcode={data.get('msgcode')} loginstatus={data.get('loginstatus')} "
            f"userid={data.get('userid')}"
        )

    # 3) 登录提醒：真实抓包链路的一环，缺失可能影响后续会话
    session.post(
        base + REMIND_PATH,
        data={"logintype": "1", "appid": "", "service": ""},
        headers=_headers(base, form=True, origin=True),
        timeout=timeout,
    )
    return session


def login(base_url, user_name, password, *, timeout=30, proxy=None):
    """接口登录并返回可直接交给 CDP 的 cookie 列表。

    Args:
        base_url: E9 站点根地址，如 http://10.12.21.26:8080。
        user_name / password: 账号与密码。
        timeout: 单次请求超时（秒）。
        proxy: 传给 requests 的代理配置；None 表示不使用代理。

    Returns:
        list[dict]: 形如 CDP `Network.setCookie` 的参数，
                    调用方直接展开即可，无需再转换。

    Raises:
        RuntimeError: 登录业务码或登录态异常。消息中只含非敏感诊断字段。
    """
    base = base_url.rstrip("/")
    session = open_session(base_url, user_name, password, timeout=timeout, proxy=proxy)

    # CDP 要求 url 字段；用 base 保证 cookie 域与目标站一致
    return [
        {"name": c.name, "value": c.value, "url": base, "path": c.path or "/"}
        for c in session.cookies
    ]