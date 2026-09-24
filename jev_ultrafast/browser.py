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

# 视口尺寸（CSS px；DPR=1 所以也等于设备 px）。
#
# 这是**一个基准三处共用**：`setDeviceMetricsOverride` 拿它定视口、截图与录屏帧的尺寸
# 由它决定、点击坐标也以它为参照。所以必须是同一个数，不能散成字面量。
# 报告层的插件要把「操作坐标」映射回录屏画面，映射关系就是 `坐标 / 视口尺寸`
# ——插件从报告的 videoViewport 标签读这个尺寸，不写死，改了这里插件才跟得上。
VIEWPORT = (1120, 780)

# 滚动没有真实鼠标位置（`Input.dispatchMouseEvent` 的 mouseWheel 需要一个坐标，
# 这里用的是既有实现的固定值）。记下来只为让回放里的光标不至于凭空消失。
SCROLL_POINT = (550, 650)

class StalePage(ValueError):
    """A decision no longer refers to the observed page."""


class Browser:
    # 类属性而不是实例属性：报告层（`framework/video.py` 的 Recorder）要从浏览器上读它，
    # 记进报告让插件换算点击坐标。与 `VIEWPORT` 是同一个元组，不允许有两份。
    viewport = VIEWPORT

    def __init__(self, url, cookies=None, *, wait_stable=True):
        ensure_daemon()
        self.target = cdp("Target.createTarget", url="about:blank", background=True)["targetId"]
        self.session = cdp("Target.attachToTarget", targetId=self.target, flatten=True)["sessionId"]
        self._owned = {self.target}
        # 本轮动作里发生的所有等待（观测值，供上层渲染成报告步骤）。
        # 必须在 _settle() 之前建好——它是第一个会记录的调用。
        self.waits = []
        self._wait_stable = wait_stable
        # 先注入登录态再导航：避免首屏落到登录页，也省掉一次 reload。
        # cookie 由框架层从接口登录结果转换而来，形如 CDP Network.setCookie 的参数。
        for cookie in cookies or ():
            self.call("Network.setCookie", **cookie)
        self.call("Emulation.setDeviceMetricsOverride", width=self.viewport[0], height=self.viewport[1],
                  deviceScaleFactor=1, mobile=False)
        # Keep rAF/menus rendering in an owned background tab, without activating the user's Chrome tab.
        self.call("Emulation.setFocusEmulationEnabled", enabled=True)
        self.call("Page.navigate", url=url)
        self._settle()
        # 首屏也要等稳定：重 SPA（E9）的 readyState=complete 只代表外壳完成，
        # 真正的列表/表单还要几秒才渲染出来。若不等，第一次决策会落在空页面上，
        # 模型很可能直接判 BLOCKED，整条用例白跑。
        if wait_stable:
            self.wait_until_stable(timeout=25, interval=0.6)

    def _record_wait(self, kind, started, **extra):
        """记下一次等待——**只记观测值，与 Allure 无关**。

        怎么显示（独立成步骤、还是并进别的步骤的参数）是 `framework/runner.py` 的事。
        库本体不 import allure，这条边界在 AGENTS.md 里有明文。

        时间基准用 `time.time()`（Unix 纪元）而不是 `time.monotonic()`：
        前者与 Allure 的步骤时间戳同基准，runner 才能把步骤区间改成真实区间。
        monotonic 只适合量间隔，跨工具对不上。

        会记录的三类（对应浏览器里三处会真正等待的地方）：

        只有**达到门槛**的才会在报告里独立成步骤（门槛默认 200 ms，见实施方案 §7.2）；
        短的并进所在执行步骤的参数里——否则报告会被 50 ms 级的输入同步等待塞成流水账。
        """
        self.waits.append({
            "kind": kind,                        # stable / settle / input_sync
            "started_ms": int(started * 1000),
            "elapsed_ms": round((time.time() - started) * 1000),
            **extra,
        })

    def _settle(self, timeout=15):
        started = time.time()
        ready = False
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.evaluate("document.readyState") == "complete":
                ready = True
                break
            time.sleep(0.02)
        # 刚导航完时 readyState 是 loading/interactive，这里会真等一会儿；
        # 重页面（E9）上可能超过门槛，所以在报告里是可见的。
        self._record_wait("settle", started, ready=ready, timeout=timeout)

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
        self.call("Emulation.setDeviceMetricsOverride", width=self.viewport[0], height=self.viewport[1],
                  deviceScaleFactor=1, mobile=False)
        self.call("Emulation.setFocusEmulationEnabled", enabled=True)
        self._settle()
        # 新标签页刚打开时往往只渲染了骨架，等它稳定下来再交给决策层。
        # 与首屏一样受 wait_stable 管：它存在的理由同样是防"渲染中途决策 → 页面过期
        # → 重决策"，而重决策是付费的。关掉开关就该真的关掉，不能只关首屏那一次。
        if self._wait_stable:
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
        started = time.time()
        stable, polls = False, 0
        last, steady = None, 0
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                page = self.observe(screenshot=False)
                polls += 1
            except StalePage:
                steady, last = 0, None
                time.sleep(interval)
                continue
            signature = (len(page["actions"]), page["marker"])
            if signature == last:
                steady += 1
                if steady >= steady_samples:
                    stable = True
                    break
            else:
                steady, last = 0, signature
            time.sleep(interval)
        self._record_wait("stable", started, stable=stable, polls=polls, timeout=timeout)
        return stable

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
            #
            # ⚠️ 这个等待【不受 wait_stable 管】，因为它是为了**正确性**而不是为了省钱：
            # 上面那段注释写了理由——不等满这个界限，紧接着的"提交"可能读到还没同步的内容。
            # 关掉稳定等待是"用更多付费决策换更少等待"，不该顺带把提交的正确性也关掉。
            #
            # 时序上有个细节：若上一步点击开出了新标签页，`act` 会先设 after_input、
            # 再调 follow_new_tab，而后者内部的 wait_until_stable 会调 observe()——
            # 于是**这个输入后同步等待发生在稳定等待内部**，两者的时间区间是重叠的。
            # 报告里由门槛各自决定是否独立成步骤（前者约 50 ms，通常并进参数）。
            started = time.time()
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
            self._record_wait("input_sync", started, trigger=action["kind"])
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
        # 这次动作落在画面的哪个点（视口坐标）。**以前是算完就丢的**，于是报告里
        # 只能看到"点了个链接"，看不到点在哪——而回放里连光标都没有
        # （CDP 录屏只截渲染器的合成结果，不含系统光标），事后补不回来。
        # 每个分支都要给出它真正用的那个点，`wait` 没有点，如实给 None。
        point = None
        if kind == "scroll":
            call("Input.dispatchMouseEvent", type="mouseWheel", x=SCROLL_POINT[0], y=SCROLL_POINT[1],
                 deltaX=0, deltaY=action["delta"])
            point = {"x": SCROLL_POINT[0], "y": SCROLL_POINT[1], "via": "wheel"}
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
            # `select` 是**直接设 value**（不发鼠标事件），所以它的点不是"鼠标去过的地方"，
            # 而是"这个控件在哪"。用 via 把两者分开记，报告里就不会把 js 说成鼠标点击。
            point = {"x": round(target["x"], 1), "y": round(target["y"], 1),
                     "via": "js" if kind == "select" else "mouse"}
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
        return {"executed": action["id"], "point": point}

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
