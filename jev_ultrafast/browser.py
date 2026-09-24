"""Observed actions through Browser Harness; one CDP session, no per-step subprocess."""

import hashlib
import json
import sys
import time
from pathlib import Path

from browser_harness.admin import ensure_daemon
from browser_harness.helpers import cdp

# Atomically read visible content and controls, preserving actual DOM node identity.
READ_STATE = Path(__file__).with_name("snapshot.js").read_text(encoding="utf-8")
MARKER = f"(() => {{ const state={READ_STATE}; return state?.marker ?? null; }})()"

# 重页面上的 CDP 调用 IPC 响应超时（秒）。
#
# browser_harness 的默认值是 5 秒，对 E9 这类重页面不够：
#   · Page.captureScreenshot 实测约 3.3 秒，页面更重时超时；
#   · 观察用的 Runtime.evaluate 要遍历整个 DOM（E9 工作台 600+ 个 div），实测也会超过 5 秒。
# 超时设长不会拖慢正常调用——只有真正卡住时才会等满。
CDP_TIMEOUT = 30

class StalePage(ValueError):
    """A decision no longer refers to the observed page."""


class Browser:
    def __init__(self, url, cookies=None):
        ensure_daemon()
        self.target = cdp("Target.createTarget", url="about:blank", background=True)["targetId"]
        self.session = cdp("Target.attachToTarget", targetId=self.target, flatten=True)["sessionId"]
        self._owned = {self.target}
        # 先注入登录态再导航：避免首屏落到登录页，也省掉一次 reload。
        # cookie 由框架层从接口登录结果转换而来，形如 CDP Network.setCookie 的参数。
        for cookie in cookies or ():
            self.call("Network.setCookie", **cookie)
        self.call("Emulation.setDeviceMetricsOverride", width=1120, height=780, deviceScaleFactor=1, mobile=False)
        # Keep rAF/menus rendering in an owned background tab, without activating the user's Chrome tab.
        self.call("Emulation.setFocusEmulationEnabled", enabled=True)
        self.call("Page.navigate", url=url)
        self._settle()
        # 首屏也要等稳定：重 SPA（E9）的 readyState=complete 只代表外壳完成，
        # 真正的列表/表单还要几秒才渲染出来。若不等，第一次决策会落在空页面上，
        # 模型很可能直接判 BLOCKED，整条用例白跑。
        self.wait_until_stable(timeout=25, interval=0.6)

    def _settle(self, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.evaluate("document.readyState") == "complete":
                break
            time.sleep(0.02)

    @staticmethod
    def page_targets():
        """当前 Chrome 里所有 page 类型 target 的 id 集合。"""
        return {t["targetId"] for t in cdp("Target.getTargets")["targetInfos"] if t["type"] == "page"}

    def follow_new_tab(self, before, *, appear_timeout=0.6, url_timeout=4.0):
        """把焦点切到【本次动作】新开出来的标签页。

        E9 的流程表单以 <a target="_blank"> 打开，现有实现只观察自己创建的那一个标签页，
        点击后新表单页完全不可见。这里在动作执行后检测新出现的 page target 并切换，
        这样「点开新标签页 → 在新页面继续操作」就成为常规能力，而不是站点专用逻辑。

        必须传入动作【执行前】的 target 快照：共用的 Chrome 里本来就有别的标签页
        （用户自己的、或其它用例的），只比较「自己拥有的 target」会把它们误判成新开的。

        分两步，避免两个相反方向的坑：
          1) 先等新 target 出现——点击到 target 创建有几毫秒延迟，看太早会以为没开新页；
          2) 再等它拿到真实地址——刚创建时 url 是 about:blank，此时不能因为"不是真实地址"
             就放弃跟随（那样永远跟不上）；但若一直空白，多半是会被立刻关掉的临时页
             （打印预览、下载助手），跟进去会话就会失效。
        """
        def fresh_targets():
            return [
                t for t in cdp("Target.getTargets")["targetInfos"]
                if t["type"] == "page" and t["targetId"] not in before
            ]

        deadline = time.monotonic() + appear_timeout
        while not fresh_targets():
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.1)

        deadline = time.monotonic() + url_timeout
        while True:
            real = [t for t in fresh_targets() if (t.get("url") or "") not in ("", "about:blank")]
            if real:
                break
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.15)

        self.target = real[-1]["targetId"]
        self.session = cdp("Target.attachToTarget", targetId=self.target, flatten=True)["sessionId"]
        self._owned.add(self.target)
        self.call("Emulation.setDeviceMetricsOverride", width=1120, height=780, deviceScaleFactor=1, mobile=False)
        self.call("Emulation.setFocusEmulationEnabled", enabled=True)
        self._settle()
        # 新标签页刚打开时往往只渲染了骨架，等它稳定下来再交给决策层
        self.wait_until_stable()
        return True

    def wait_until_stable(self, *, timeout=15, interval=0.4, steady_samples=3):
        """等到连续若干次观察到的动作空间不再变化为止。

        重 SPA（E9 的流程表单就是）会分多批渲染。在渲染中途做的决策会立刻被判为过期，
        于是"决策→过期→重观察→再决策"空转，白烧步数与模型调用。
        这里让页面先稳定下来，再把控制权交还给决策层。

        Returns:
            bool: 是否在超时前等到稳定。
        """
        last, steady = None, 0
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                page = self.observe(screenshot=False)
            except StalePage:
                steady, last = 0, None
                time.sleep(interval)
                continue
            signature = (len(page["actions"]), page["marker"])
            if signature == last:
                steady += 1
                if steady >= steady_samples:
                    return True
            else:
                steady, last = 0, signature
            time.sleep(interval)
        return False

    def _reattach(self):
        """当前会话失效时重新挂载。

        站点可能在动作后关闭或替换我们正看着的标签页，此时 CDP 报
        "Session with given id not found"。优先挂回当前 target；它若已死，
        就回退到自己拥有过的、仍然存活的那个标签页（例如打开表单前的那一页），
        这样一次意外关页不会直接判死整条用例。
        """
        alive = {t["targetId"] for t in cdp("Target.getTargets")["targetInfos"] if t["type"] == "page"}
        for target in [self.target, *getattr(self, "_owned", ())]:
            if not target or target not in alive:
                continue
            try:
                # 用 .get 而不是 []：这里只有 except RuntimeError 兜着，而
                # dict 缺键抛的是 KeyError——它会直接穿透，把"这个 target 挂不上"
                # 变成整条用例崩溃，而不是像下面那样去试下一个 target。
                # 说明：这是防御性收紧，不是修一个已观测到的故障——当初怀疑这里
                # 是某次 KeyError 的来源，后来查明那次失败是调用方漏传 --env-file。
                session = cdp("Target.attachToTarget", targetId=target, flatten=True).get("sessionId")
            except RuntimeError:
                continue
            if not session:
                continue
            self.session = session
            self.target = target
            return True
        return False

    def _operation(self, request):
        """执行一次 browser_operation，并在会话失效时重挂重试一次。

        browser_operation 内部直接用裸 cdp()，绕过了 call() 的保护，所以要在这一层兜。
        """
        try:
            return browser_operation({**request, "session": self.session})
        except RuntimeError as error:
            if "Session with given id not found" not in str(error) or not self._reattach():
                raise
            return browser_operation({**request, "session": self.session})

    def call(self, method, **params):
        try:
            return cdp(method, session_id=self.session, **params)
        except RuntimeError as error:
            # 会话失效（标签页被站点关掉/替换）时重挂一次再试，避免整条用例因一次抖动失败。
            if "Session with given id not found" not in str(error) or not self._reattach():
                raise
            return cdp(method, session_id=self.session, **params)

    def evaluate(self, expression):
        response = self.call("Runtime.evaluate", expression=expression, returnByValue=True)
        if response.get("exceptionDetails"):
            raise StalePage("Document changed during evaluation")
        return response.get("result", {}).get("value")

    def observe(self, screenshot=True):
        if getattr(self, "after_input", None):
            action, self.after_input = self.after_input, None
            # This is read-only and happens after execution was logged, even if navigation interrupts it.
            try:
                self.call(
                    "Runtime.evaluate",
                    expression="""(action => new Promise(resolve => {
                      const field=window.__jevFast?.nodes.get(action.node);
                      const autocomplete=action.kind==='fill' && field?.getAttribute('role')==='combobox';
                      // 同源 frame 里的富文本编辑区（CKEditor）：输入后编辑器要把内容同步进
                      // 自己的数据模型，站点提交时读的就是那份数据。等满这个界限再进入下一步，
                      // 否则紧接着的"提交"可能读到还没同步的内容。与 combobox 的 200ms 同理。
                      const richText=action.kind==='fill' && field?.tagName==='IFRAME';
                      let frames=0, stopped=false;
                      const finish=()=>{stopped=true;resolve()};
                      setTimeout(finish,richText ? 500 : autocomplete ? 200 : 50);
                      const ready=()=>{
                        if (stopped || richText) return;
                        const ids=(field?.getAttribute('aria-controls')||field?.getAttribute('aria-owns')||'')
                          .split(/\\s+/).filter(Boolean);
                        const roots=ids.length ? ids.map(id=>document.getElementById(id)).filter(Boolean) : [document];
                        const options=roots.flatMap(root=>[...root.querySelectorAll('[role="option"]')]);
                        if (++frames>=2 && (!autocomplete || options.some(e=>{
                          const r=e.getBoundingClientRect();
                          return r.width && r.height && r.bottom>0 && r.top<innerHeight &&
                            e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
                        }))) finish();
                        else requestAnimationFrame(ready);
                      };
                      requestAnimationFrame(ready);
                    }))(""" + json.dumps(action) + ")",
                    awaitPromise=True,
                    returnByValue=True,
                )
            except RuntimeError:
                pass
        for attempt in range(10):
            try:
                return self._operation({"operation": "observe", "screenshot": screenshot})
            except StalePage:
                if attempt == 9:
                    raise
                time.sleep(0.02)
        raise StalePage("Page did not settle")

    def fresh(self, page, action=None):
        if action is not None and action["kind"] in {"click", "select"}:
            node = action["node"]
            if type(node) is not int:
                return False
            current = self.evaluate(
                "(() => { const c=window.__jevFast; "
                f"return c ? [c.pageKey(),c.guard(c.nodes.get({node}))] : null; }})()"
            )
            return current == [page["page_key"], page["guards"].get(str(node))]
        return self.evaluate(MARKER) == page["marker"]

    def act(self, action, page, text=None):
        if not self.fresh(page, action):
            raise StalePage("Page changed since this decision. Observe again.")
        if action["kind"] == "wait":
            time.sleep(0.1)
        # 点击可能以 target="_blank" 打开新标签页（E9 的流程表单就是如此）。
        # 先记录执行前的 target 快照，执行后再跟随，保证下一次观察与输入落在新页面上。
        before = self.page_targets() if action["kind"] == "click" else None
        result = self._operation({"operation": "act", "action": action, "text": text})
        self.after_input = action if action["kind"] != "wait" else None
        if before is not None:
            self.follow_new_tab(before)
        return result

    def close(self):
        for target in list(getattr(self, "_owned", ()) or ([self.target] if self.target else [])):
            try:
                cdp("Target.closeTarget", targetId=target)
            except RuntimeError:
                pass  # 标签页可能已被页面自身关闭
        if hasattr(self, "_owned"):
            self._owned.clear()
        self.target = None


def fingerprint(state):
    # 用归一化 URL 参与指纹：SPA 的会话随机参数（E9 的 _key 等）不代表页面变化，
    # 否则每次点击都会被判成"页面已变"。快照没提供 fresh_url 时退回原始 url。
    content = {k: state[k] for k in ("text", "actions", "scroll")}
    content["url"] = state.get("fresh_url") or state["url"]
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def browser_operation(request):
    operation = request["operation"]
    session = request["session"]

    def call(method, **params):
        return cdp(method, session_id=session, **params)

    def evaluate(expression):
        result = call(
            "Runtime.evaluate", expression=expression, returnByValue=True, _response_timeout=CDP_TIMEOUT
        )
        if result.get("exceptionDetails"):
            if operation == "act" and request["action"]["kind"] == "select":
                raise RuntimeError("Dropdown execution was interrupted; inspect before retrying.")
            raise StalePage("Document changed during evaluation")
        return result.get("result", {}).get("value")

    if operation == "act":
        action = request["action"]
        kind = action["kind"]
        if kind == "scroll":
            call("Input.dispatchMouseEvent", type="mouseWheel", x=550, y=650, deltaX=0, deltaY=action["delta"])
        elif kind != "wait":
            if type(action["node"]) is not int:
                raise ValueError("Invalid observed node")
            # Code-owned node IDs refer to actual observed elements, never model-generated selectors.
            target = evaluate("""(action => {
              const e=window.__jevFast?.nodes.get(action.node);
              if (!e?.isConnected || e.matches(':disabled') || e.closest('[aria-disabled="true"],[inert]') ||
                  !e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true})) return null;
              // 先滚动到视野内再取几何：高表单里的字段（E9 的签字意见就在首屏下方）
              // 若因超出视口而拒绝执行，就地读取坐标会落空。滚动后再命中测试才准。
              e.scrollIntoView({block:'center', inline:'nearest'});
              if (action.kind==='fill' && (e.readOnly || e.getAttribute('aria-readonly')==='true')) return null;
              const r=e.getBoundingClientRect(), x=r.x+r.width/2, y=r.y+r.height/2;
              if (!r.width || !r.height || x<0 || y<0 || x>=innerWidth || y>=innerHeight) return null;
              if (!e.contains(document.elementFromPoint(x,y))) return null;
              if (action.kind==='select') {
                if (e.tagName!=='SELECT' || ![...e.options].some(o=>o.value===action.value &&
                    !o.disabled && !o.closest('optgroup[disabled]'))) return null;
                e.value=action.value;
                e.dispatchEvent(new Event('input',{bubbles:true}));
                e.dispatchEvent(new Event('change',{bubbles:true}));
              }
              return {x,y};
            })(""" + json.dumps(action) + ")")
            if target is None:
                if kind == "select":
                    raise RuntimeError("Dropdown execution was not confirmed; inspect before retrying.")
                raise StalePage("Target changed or is covered. Observe again.")
            if kind != "select":
                x, y = target["x"], target["y"]
                for event in ("mousePressed", "mouseReleased"):
                    call("Input.dispatchMouseEvent", type=event, x=x, y=y, button="left", clickCount=1)
                if kind == "fill":
                    call(
                        "Input.dispatchKeyEvent",
                        type="keyDown",
                        key="a",
                        code="KeyA",
                        modifiers=4 if sys.platform == "darwin" else 2,
                        commands=["selectAll"],
                    )
                    call(
                        "Input.dispatchKeyEvent",
                        type="keyUp",
                        key="a",
                        code="KeyA",
                        modifiers=4 if sys.platform == "darwin" else 2,
                    )
                    call("Input.insertText", text=request["text"])
        return {"executed": action["id"]}

    info = evaluate(READ_STATE)
    if info is None:
        raise StalePage("Document is navigating")
    info["fingerprint"] = fingerprint(info)
    if request.get("screenshot", True):
        # browser_harness 的 IPC 响应超时默认 5 秒；E9 这类重页面实测截图约 3.3 秒，
        # 更重的页面会直接超时抛错。这里显式放宽。
        info["screenshot"] = call(
            "Page.captureScreenshot", format="jpeg", quality=72, _response_timeout=CDP_TIMEOUT
        )["data"]
    return info
