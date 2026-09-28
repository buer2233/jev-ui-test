# step-video —— Allure 报告二开插件

在用例详情页插一个区块：**执行录屏 + 步骤时间轴**。

- **点某一步 → 跳到那一刻并停住；再点同一步 → 继续播。**
- 画面上**合成一个看得见的光标**（大号箭头 + 圆点 + 点击涟漪），
  外加**可开关的圆形放大镜**（3×）。
- 播放时高亮当前步骤。

## 它依赖哪一版 Allure

**不是"大概兼容"，是锁定的。** 二开依赖的是**报告运行时的内部结构**，不是稳定协议：

| 组件 | 锁定版本 | 插件依赖它的什么 | 失配后果 |
|---|---|---|---|
| Allure CLI | `2.13.8` | `window.allure.api.addTestResultBlock`、`index.html` 的 `<script>` 装载点、`plugins/<id>/` 约定、`data/test-cases/*.json` 里的 `testStage.steps[].time` | 插件不加载，或区块渲染不出来 |
| Backbone | `1.3.3` | `View` 必须继承**页面上那一份** | 自己打包一份会版本冲突，**可能整个报告白屏** |
| Marionette | `3.3.1` | `Backbone.Marionette.View.extend(...)` | 同上 |
| allure-pytest | `2.13.5` | 步骤结果 JSON 的字段 | ≥2.14 写 `titlePath`，2.13.8 的 CLI **静默丢全部结果** |
| ffmpeg | `8.1.1` | `-fps_mode cfr` / `-movflags +faststart` / `-pix_fmt yuv420p` | 参数不识别，或编码放不出来 |

完整的机器可读版本表在 [`versions.json`](./versions.json)：
`scripts/install_report_plugin.py` 装之前逐项校验；**Backbone / Marionette 由本插件在浏览器里自查**
（它们在报告页面里，装的时候读不到），不一致会在页面上出横幅。

## 怎么装

```bash
rm -rf report/allure-report
allure generate report/allure-results -o report/allure-report --clean
uv run python scripts/install_report_plugin.py report/allure-report
```

**必须在 `allure generate` 之后**——它要改的是生成结果里的 `index.html`。
幂等：重复执行不会重复插 `<script>`。

## 几个必须是这样的写法（都是实测出来的）

| 写法 | 为什么 |
|---|---|
| 继承 `Backbone.Marionette.View`（挂在 `window.Backbone` 下） | 页面**没有**独立的 `window.Marionette`。实测取值：Backbone 1.3.3 / Marionette 3.3.1 |
| 视频**先 fetch 成 Blob URL** 再喂 `<video>` | `python -m http.server` 不支持 HTTP Range，直接给相对路径拖不动进度条。playwright 的 Trace Viewer 也是这么做的（其源码注释："so that the player can seek without range requests"） |
| **不用 `#t=` 媒体片段** | 实测会**静默失效**（大文件 + 服务器不支持 Range 时 `currentTime` 停在 0），失败时没有任何提示 |
| seek 用 `(step.start − epochMs)/1000` | 抄自 Playwright Trace Viewer 的 `videoFrame.tsx` |
| 数据从 `this.model.toJSON().testStage` 取 | `testStage.steps[].time.start/stop` 与 `testStage.attachments[]` 都在里面 |
| `<script>` 插在 `</body>` **之前** | 插件要用 `window.allure` 与 `window.Backbone`，它们由 `app.js` 建立；插早了拿不到 |
| CSS 里**显式写 `object-fit: contain`** | 光标位置按"内容矩形"算。默认值 `fill` 是**拉伸**，靠浏览器默认值会让光标在宽高比不一致时整体偏移 |
| 光标位置的分母用**报告里的 `videoViewport` 标签** | 不是写死的 1120，也不是 `video.videoWidth`（等比缩小时恰好对，不成比例就**静默偏**） |
| 放大镜**从同一个 `<video>` 抓帧** | 两个 video 对拷会漂移，而且暂停时未必停在同一点 |
| 用 `<video>` 播放时用 **rAF** 驱动重绘 | `timeupdate` 只有 ~4 Hz，涟漪会一格一格跳 |

## 自检：错位跳转比没有同步更糟

插件做了三层自检，都**在页面上显式报错**，不静默：

1. **运行时版本**：`window.Backbone` / `Backbone.Marionette` 缺失或版本与 `versions.json` 不一致 → 红色/黄色横幅。
2. **录屏覆盖范围**：拿"**最后一个执行步骤**的 seek 位置"与视频时长比，超出就报错。

   > ⚠️ 判据**不是"末步骤"**。断言与终局判定的步骤发生在录屏结束**之后**
   > ——录屏在用例收尾前就停了（那时浏览器已经关了，没什么可录），
   > 拿末步骤比会**误报**。真正要守的是用户会点的那些步骤，即**执行**步骤。

3. **视口比例与帧比例**：报告记的 `videoViewport` 与录屏帧的宽高比不一致时出红横幅
   ——那时"操作坐标 → 画面位置"的换算会**静默偏**，报告照常打开、只是光标停在错的地方。
   缺 `videoViewport` 标签（旧报告）时退回用帧尺寸推断，并在状态行**说明是推断来的**。

> ⚠️ **"哪些是执行步骤"的判据是「有 `操作` 参数」，不是标题里有没有"执行"。**
> 标题判据会把「终局：**执行**结果」也算进来，而它在**录屏结束之后**——
> 拿它当"最后一个执行步骤"比视频时长，会弹一条**假的**"录屏没覆盖到"红横幅。
> 第一次真跑就只差 0.57 s（容差 1.0 s），**越慢的机器越会误报**。
> 执行步骤都带 `操作` 参数（连"页面过期被丢弃"的也有），终局/决策/等待都没有。

另外，点到**落在录屏范围之外**的步骤时，插件会说明原因（"发生在录屏开始之前/之后"），
**不静默 clamp 到末尾**——那会显示完全无关的画面。

### 光标是**合成**的，不是录下来的

`Page.startScreencast` 截的是渲染器的合成结果，**不含系统光标**（光标由 OS/浏览器 UI
层画，不在合成器输出里）。所以不存在"把光标录进去"的选项，只能按执行记录把坐标画上去。

坐标来自执行步骤参数里的「操作坐标」，由 `browser.py` 的 act 路径带出——
它比"真鼠标位置"还真：那边是**先 `scrollIntoView`、再用 `elementFromPoint` 做遮挡校验**、
通过了才 dispatch，所以记下的就是真正被点中的那个点。旁边的「坐标来源」说清来路：

| 值 | 含义 |
|---|---|
| 鼠标点击 | 真的发了 `mousePressed`/`mouseReleased` |
| 滚轮 | `mouseWheel` 用的固定坐标，**没有真实鼠标位置** |
| 直接赋值（无鼠标事件） | `select` 直接设 `value`，这个点是控件位置 |

**没有「操作坐标」时不画光标**，并在开关区说明原因——不静默什么都不显示。

> ⚠️ 验证"再点一次开始播放"必须发**真鼠标事件**（`Input.dispatchMouseEvent`）。
> 用合成的 `li.click()` 会**假失败**：它不算用户激活，`play()` 被自动播放策略拒绝。
> 被拒时插件会在状态行说出来，不静默。
>
> 驱动侧的另一个坑：后台标签页会把**媒体解码**节流到 `readyState` 恒为 0
> （不报错、看着像"视频坏了"），要靠 `Emulation.setFocusEmulationEnabled` 解开
> ——库本体对后台页做的是同一件事（`browser.py` 的"让后台标签页保持渲染"）。

## 录屏为什么是"稀疏幻灯片"

CDP screencast 是**重绘驱动**的：页面不动就没有合成提交，也就没有帧。
实测一条 26 秒的用例只抓到个位数帧（在连续滚动下才会到 20 fps）。

这不是缺陷：时间轴用每帧的真实时长，**seek 到某一刻看到的就是那一刻页面的样子**
——空闲期画面本来就没变。

所以：
- 报告里会显示**帧数**（见「录屏说明」附件），否则会让人以为录屏坏了；
- 视频的最后一帧会**保持到录制结束**，视频时长因此等于真实录制跨度
  （否则 seek 到后段的步骤会落到视频末尾）。

## 升级 Allure 时的回归清单

1. `uv run pytest tests/test_report_plugin.py tests/test_report_params.py tests/test_wait_steps.py`
   —— 这几条钉住了 `start_step`/`stop_step` 的行为与落盘格式，它们是二开的地基。
2. `uv run python scripts/install_report_plugin.py <report> --check-only`
3. 生成一份报告并**在浏览器里打开**，确认：区块出现、点步骤能跳、播放时高亮、**零 JS 报错**。
4. 用 `allure serve`（不是 `python -m http.server`）确认进度条能拖——后者不支持 Range。

第 3 条有脚本版（真浏览器、不调模型），封板后补做的两条行为也在里面：

```bash
# 造一份带「操作坐标」的受控样本 → 装插件 → 起 http 服务 → 20 条断言
uv run python artifacts/plugin-cursor/build_fixture.py report/allure-report artifacts/plugin-cursor/report
uv run python scripts/install_report_plugin.py artifacts/plugin-cursor/report
cd artifacts/plugin-cursor/report && python -m http.server 8899 --bind 127.0.0.1
uv run python artifacts/plugin-cursor/verify.py      # 20 passed
```

`artifacts/` 是 gitignore 的，所以这套是**本机验证工具**，不是入库的测试。
入库的负向断言在 `tests/test_report_plugin.py`（版本表、幂等、拒绝安装）。