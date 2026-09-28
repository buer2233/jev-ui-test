"""执行录屏：只读地用 CDP 连续抓帧，并把帧时间轴锚到与 Allure 步骤同一基准上。

## 为什么不复用 `browser.record_dir`

`browser.py` 已有 `record_dir` 机制，但它**每个动作只存一张截图**，不是连续录屏，
合成不出视频。所以这里新写一个框架侧组件，只读地用 CDP：

  · `Page.startScreencast` 是**协议级截屏**，不碰页面内容、不碰用户可见标签页；
  · 与 `scripts/record_flights.py`、`artifacts/probe-video/` 是同一套机制
    （那两份都已在线跑过）。

放在框架层而不是库里：库本体（`agent.py` / `browser.py`）不该沾录屏与报告。

## epoch 锚点是全套对齐的地基

CDP 给的 `metadata.timestamp` 是 **Unix 纪元秒**，与 Allure 步骤时间戳同基准。
所以只要记下录制起点的 epoch，就能把每帧换算成"相对录制起点多少毫秒"，
插件再用 `(step.start − epochMs)/1000` 就能 seek 到对的画面（实施方案 §4.2.1）。

⚠️ 这里必须用 `time.time()`，**不是** `time.monotonic()`：
后者只适合量间隔，与墙钟对不上，换算出来的时间轴会整体偏移。
"""

import base64
import os
import threading
import time
from pathlib import Path

from browser_harness.helpers import drain_events

REPO_ROOT = Path(__file__).resolve().parents[2]


def frames_dir_for(case_id):
    """本次录屏的帧目录。

    放 `artifacts/` 下（AGENTS.md 规则 3）：测试临时产物不进版本管理。
    名字带用例 id、时间戳与进程号——批量跑时不会互相覆盖，
    也便于事后认出"这一堆帧是哪次运行留下的"。
    """
    stamp = time.strftime("%Y%m%d-%H%M%S")
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(case_id))
    return REPO_ROOT / "artifacts" / "video-frames" / f"{safe_id}-{stamp}-{os.getpid()}"

# 实测参数（分析报告 §4.1）：everyNthFrame=2 / q80 / 1120×780
#   → 约 20 fps、帧约 70 KB、原始帧约 92 MB/分钟。
# 帧率再高只是让原始帧更大，对"点步骤跳进度"没有帮助——对齐精度由 cfr 重采样保证。
DEFAULT_QUALITY = 80
DEFAULT_EVERY_NTH_FRAME = 2


def should_record(mode):
    """档位 0 连**开始录**都不该做——录了再删是白付开销（观察阶段 +3–6 ms/次、采集线程持续轮询）。"""
    return mode != 0


def should_keep(mode, *, ok, note, interrupted):
    """要不要**编码并附加**录屏。返回 `(是否保留, 原因)`。

    三档（用户拍板的语义，实施方案 §5.1）：

    | 档位 | 含义 |
    |---|---|
    | `0` | 不记录 |
    | `1` | 记录全部 |
    | `-1` | **仅保留失败用例**的录屏 |

    ## `-1` 会删证据，所以"通过"的边界必须画严

    只有**明确通过**才不保留。下面这些"通过了但不健康"的一律保留——
    它们恰恰最值得看，正是下一轮要查的对象：

      · 终局判定没过（自然要留）；
      · 执行提前停止（达到步数上限 / 超时），`note` 非空；
      · 过程里出现过页面过期、或决策重发（`interrupted`）
        ——"看着过了、过程不健康"是下一轮最该复盘的对象。

    > `-1` 与 pytest 的 `--reruns` 天然配合：第一次失败那轮 `ok=False`，
    > 会保留并附加；重跑通过那轮才不保留。所以不需要额外识别 RERUN。

    原因要**写进报告**（调用方负责）——删除动作不能静默。
    """
    if mode == 0:
        return False, "档位 0（不记录）"
    if mode == 1:
        return True, "档位 1（记录全部）"
    if not ok:
        return True, "仅失败档：终局判定未通过 → 保留"
    if note:
        return True, f"仅失败档：但执行提前停止（{note}）→ 保留"
    if interrupted:
        return True, "仅失败档：但过程里出现过页面过期或决策重发 → 保留（看着过了，过程不健康）"
    return False, "仅失败档且明确通过 → 不保留（本次未留录屏，见实施方案 §6.4）"


def any_interrupted(snapshot):
    """这一轮跑下来有没有"不健康"的痕迹。

    两个信号都来自库里已经记着的事实，不额外探测：
      · `decisions[i]["decision_attempts"] > 1`：响应不合法、**重发过**
        （AGENTS.md 要求重发要看得见、不静默自愈）；
      · 历史里出现过页面已变化的动作——`page_changed` 被显式记成 False 的次数
        不足以说明问题，所以再看 `decisions` 与 `history` 的数量差：
        决策多于动作，说明有决策被丢弃过（页面过期）。
    """
    if any((d.get("decision_attempts") or 1) > 1 for d in snapshot.get("decisions", ())):
        return True
    return len(snapshot.get("decisions", ())) > len(snapshot.get("history", ()))


class Recorder:
    """后台标签页的连续录屏。

    用法::

        recorder = Recorder(agent.browser, frames_dir)
        ...跑用例...
        result = recorder.stop()          # {"frames": [...], "errors": [...]}
        encode.to_mp4(result["frames"], result["times_ms"], out)

    `stop()` 之后帧已经全部落盘，可以放心地慢悠悠编码；原始帧由调用方清。
    """

    def __init__(self, browser, frames_dir, *, quality=DEFAULT_QUALITY,
                 every_nth_frame=DEFAULT_EVERY_NTH_FRAME):
        self.browser = browser
        self.frames_dir = Path(frames_dir)
        self.frames_dir.mkdir(parents=True, exist_ok=True)
        # epoch 必须**在 startScreencast 之前**取：它是时间轴的零点，
        # 取晚了会让视频整体比真实时间"晚开始"，越忙的系统偏得越多。
        self.epoch = time.time()
        # 视口尺寸随录屏一起记下来：插件要靠它把「操作坐标」换算成画面上的位置。
        #
        # ⚠️ 不在插件里从帧尺寸推断。帧是 `startScreencast` 按 maxWidth/maxHeight
        # **等比**缩小出来的，等比时"用帧宽当分母"恰好也对——可一旦缩放不成比例，
        # 光标就会**静默偏**，而报告照常打开、什么提示都没有。
        # 记原始视口、由插件自己做一致性自查，才不会在改视口尺寸时悄悄坏掉。
        self.viewport = tuple(getattr(browser, "viewport", ()) or ())
        self.frames = []
        self.errors = []
        self.session_switches = 0
        self._quality = quality
        self._every_nth_frame = every_nth_frame
        self._stop = threading.Event()
        self._worker = None
        # 录过屏的**所有**会话。
        #
        # ⚠️ 这个集合就是"丢帧 bug"的修复点。原实现按 `session_id == browser.session`
        # 过滤，而录屏是绑在 **target** 上的：同一个标签页换会话时帧还在发，
        # 只是带着旧会话 id → 全被丢掉。实测一条 26 秒的真实运行因此只拿到 **5 帧**。
        # 改成"接受所有录过屏的会话"之后，同一条用例拿到 **277 帧**。
        self._sessions = set()

        self._start_screencast(browser.session)
        self._worker = threading.Thread(target=self._drain, daemon=True)
        self._worker.start()

    def _start_screencast(self, session):
        self.browser.call("Page.startScreencast", format="jpeg", quality=self._quality,
                          maxWidth=1120, maxHeight=780, everyNthFrame=self._every_nth_frame)
        self._sessions.add(session)

    def _follow_session(self):
        """会话变了：把新会话记进"算数的集合"，必要时在新 target 上重开录屏。

        ## 真正丢帧的是**过滤器**，不是录屏本身

        `browser.session` 会在 `Browser.follow_new_tab` / `_reattach` 之后变化，
        而**录屏是绑在 target 上的**：同一个标签页换会话时，screencast 继续在跑，
        帧照样发出来——但带着**旧会话 id**。原来按 `session_id != browser.session`
        过滤，于是切换那一刻之后的帧**全部被丢掉**。

        实测证据（一次 26 秒的真实运行）：切换后帧数停在 **5**，视频只有 4.77 秒。
        改成"接受**所有录过屏的会话**的帧"之后，同一条用例拿到 **277 帧 / 3.0 MB**。

        所以顺序是：**先收会话，再尝试重开**。
        重开只在"真的换了标签页"（`follow_new_tab` 切到新 target）时才必要
        ——探针 11 验过：在新 target 上强制重绘 3 次，重开前新增 **0** 帧，重开后恢复。

        `Screencast is already active` 是**正常情况**（同一个标签页换会话），
        不是错误——记进 `errors` 会让报告看起来像坏了。
        """
        session = self.browser.session
        if not session or session == getattr(self, "_session", None):
            return
        self._session = session
        self.session_switches += 1
        # 先收进来：无论能不能重开，来自这个会话的帧都该保留
        self._sessions.add(session)
        try:
            self._start_screencast(session)
        except Exception as error:             # noqa: BLE001
            if "already active" in str(error).lower():
                return                          # 同一个 target，录屏本来就在跑
            self.errors.append(f"会话切换后重开录屏失败：{type(error).__name__}: {error}")

    def _drain(self):
        """采集线程：把 screencastFrame 事件落成 jpg，并回 ack。

        不回 `screencastFrameAck` 浏览器就不再发下一帧——所以 ack 是必须的，
        而且要在**写完盘之后**再 ack，否则帧可能还没落地就被下一帧覆盖。

        ⚠️ CDP screencast 是**重绘驱动**的：页面不动就没有合成提交，也就没有帧。
        所以真实用例录出来往往是"稀疏幻灯片"而不是连续视频（实测一条 26 秒的用例
        只抓到个位数帧）。这不是 bug——空闲期的画面本来就没变，
        而 `concat` 用每帧的真实时长，时间轴仍然忠实：seek 到某一刻，
        看到的就是那一刻页面的样子。详见分析报告 §4.1 的补充说明。
        """
        try:
            while not self._stop.is_set():
                self._follow_session()
                for event in drain_events():
                    if event.get("method") != "Page.screencastFrame":
                        continue
                    if event.get("session_id") not in self._sessions:
                        continue
                    params = event["params"]
                    self.frames.append({
                        "file": self.frames_dir / f"{len(self.frames) + 1:06d}.jpg",
                        "ts": params["metadata"]["timestamp"],     # Unix 纪元**秒**
                    })
                    self.frames[-1]["file"].write_bytes(base64.b64decode(params["data"]))
                    self.browser.call("Page.screencastFrameAck", sessionId=params["sessionId"])
                self._stop.wait(0.01)          # 轮询间隔小一点，帧不至于积压
        except Exception as error:             # noqa: BLE001 - 采集线程死了必须留痕，不能静默
            self.errors.append(f"{type(error).__name__}: {error}")

    def stop(self):
        """停录并等采集线程收尾。

        返回 `{"epoch", "end_epoch", "frames", "times_ms", "errors", "session_switches"}`。
        `times_ms` 是每帧相对 `epoch` 的毫秒偏移，正是插件 seek 要用的那个量。

        ⚠️ `end_epoch` 一定要带着：帧是重绘驱动的，可能集中在前几秒；
        合成时靠它让最后一帧**保持到录制结束**，视频时长才等于真实跨度。
        否则视频会在用例还没跑完时就结束，seek 到后段的步骤会落到末尾。
        """
        # 先给浏览器一点时间把在途的帧发完，再停——停太早会丢最后几帧，
        # 而丢的恰好是"用例结束那一刻"，正好是最想看的部分。
        time.sleep(0.1)
        end_epoch = time.time()
        self._stop.set()
        if self._worker is not None:
            self._worker.join(timeout=5)
        try:
            self.browser.call("Page.stopScreencast")
        except Exception as error:             # noqa: BLE001 - 浏览器可能已经关了
            self.errors.append(f"stopScreencast: {type(error).__name__}: {error}")

        frames = self.frames
        return {
            "epoch": self.epoch,
            "end_epoch": end_epoch,
            "frames": [f["file"] for f in frames],
            "times_ms": [round((f["ts"] - self.epoch) * 1000, 3) for f in frames],
            "errors": list(self.errors),
            "session_switches": self.session_switches,
            "viewport": self.viewport,
        }

    def frames_dir_size_mb(self):
        """原始帧占了多少 MB（用于报告留痕与"无残留"断言）。"""
        if not self.frames_dir.exists():
            return 0.0
        total = sum(f.stat().st_size for f in self.frames_dir.glob("*.jpg"))
        return round(total / 1024 / 1024, 1)