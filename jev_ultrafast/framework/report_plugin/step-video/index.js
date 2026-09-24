/**
 * Allure 二开插件：把「执行录屏」与用例的步骤时间轴接起来。
 *
 * 做什么：
 *   1. 在用例详情页插一个区块（`allure.api.addTestResultBlock`）；
 *   2. 列出每一步的标题与时长，**点某一步跳到那一刻并停住**，再点同一步才继续播；
 *   3. 播放时高亮当前步骤；
 *   4. 在画面上**合成一个看得见的光标**——含点击涟漪，以及可开关的圆形放大镜；
 *   5. 自检：录屏没覆盖到最后一个执行步骤、或视口与帧比例不一致时，**显式报错**。
 *
 * 为什么必须是这个形态（都是实测出来的，见实施方案 §8.2）：
 *   · `View` 继承 **`Backbone.Marionette.View`**——它挂在 `window.Backbone` 下，
 *     页面**没有**独立的 `window.Marionette`（实测 Backbone 1.3.3 / Marionette 3.3.1）。
 *     自己打包一份会版本冲突，**可能整个报告白屏**。
 *   · 数据从 `this.model.toJSON().testStage` 取，`steps[].time.start/stop` 就在里面。
 *   · 视频**先 fetch 成 Blob URL 再喂 `<video>`**：`python -m http.server` 不支持
 *     HTTP Range，直接给相对路径的话拖不动进度条。playwright 的 Trace Viewer
 *     也是这么做的（其源码注释："so that the player can seek without range requests"）。
 *   · **不用 `#t=` 媒体片段**：实测会**静默失效**（大文件 + 服务器不支持 Range 时
 *     `currentTime` 停在 0），而且失败时没有任何提示。
 *   · seek 公式 `(step.start − epochMs)/1000` 抄自 Playwright Trace Viewer。
 *
 * ## 光标是**合成**的，不是录下来的
 *
 * `Page.startScreencast` 截的是渲染器的合成结果，**不含系统光标**（光标由
 * OS/浏览器 UI 层画，不在合成器输出里）。所以不存在"把光标录进去"的选项，
 * 只能按执行记录把坐标重新画上去。
 *
 * 好在那个坐标比真鼠标位置还真：`browser.py` 的 act 路径是**先 `scrollIntoView`、
 * 再用 `elementFromPoint` 做遮挡校验**、通过了才 dispatch 的，所以记下的
 * 「操作坐标」就是真正被点中的那个点。`via` 字段说明它的来路：
 *   · `mouse` —— 真的发了 mousePressed/mouseReleased；
 *   · `wheel` —— 滚动，用的是固定坐标（没有真实鼠标位置）；
 *   · `js`    —— `select` 直接设 `value`，**压根没发鼠标事件**，这个点是控件位置。
 */
(function () {
    'use strict';

    var PLUGIN_DIR = 'plugins/step-video/';
    var EXECUTION_MARK = '执行';        // 退路：老报告里靠标题认执行步骤
    var OPERATION_PARAM = '操作';       // 执行步骤的**结构**判据：这个参数只有它有
    var POINT_PARAM = '操作坐标';       // 执行步骤里那条 "x, y"（视口坐标）
    var SLACK_S = 1.0;                  // 自检容差：1 秒
    var PARK_TOLERANCE_S = 0.25;        // "还停在那一刻"的判定容差
    var RIPPLE_S = 0.7;                 // 点击涟漪持续多久（视频秒）
    var LOUPE_SIZE = 168;               // 放大镜直径（CSS px）
    var LOUPE_ZOOM = 3;                 // 放大倍数
    var LOUPE_GAP = 16;                 // 放大镜与光标的间距

    // ---------------------------------------------------------------- 小工具

    function loadStyles() {
        if (document.querySelector('link[data-step-video]')) return;
        var link = document.createElement('link');
        link.rel = 'stylesheet';
        link.href = PLUGIN_DIR + 'styles.css';
        link.setAttribute('data-step-video', '1');
        document.head.appendChild(link);
    }

    function flatten(steps, depth, out) {
        (steps || []).forEach(function (step) {
            out.push({ step: step, depth: depth });
            flatten(step.steps, depth + 1, out);
        });
        return out;
    }

    /**
     * 递归找附件——**不能只看用例级**。
     *
     * 实测踩过：运行期把录屏挂在「终局判定」步骤里（它与「录屏说明」是一起产生的，
     * 放一起最便于对照），于是 `testStage.attachments` 里没有它，
     * 插件渲染出一个**空区块**——看起来像插件坏了。
     * 递归查找对"附件挂在哪一层"不敏感，更抗改动。
     */
    function collectAttachments(stage) {
        var found = [];
        (function walk(steps) {
            (steps || []).forEach(function (step) {
                (step.attachments || []).forEach(function (a) { found.push(a); });
                walk(step.steps);
            });
        })(stage && stage.steps);
        ((stage && stage.attachments) || []).forEach(function (a) { found.push(a); });
        return found;
    }

    function findVideoAttachment(stage) {
        var list = collectAttachments(stage);
        for (var i = 0; i < list.length; i++) {
            if (String(list[i].type || '').indexOf('video/') === 0) return list[i];
        }
        return null;
    }

    function findNoteAttachment(stage) {
        var list = collectAttachments(stage);
        for (var i = 0; i < list.length; i++) {
            if (list[i].name === '录屏说明') return list[i];
        }
        return null;
    }

    function labelValue(data, name) {
        var labels = data.labels || [];
        for (var i = 0; i < labels.length; i++) {
            if (labels[i].name === name) return labels[i].value;
        }
        return null;
    }

    function fmt(seconds) {
        if (!isFinite(seconds)) return '—';
        var m = Math.floor(seconds / 60), s = seconds - m * 60;
        return (m ? m + ':' : '') + (m && s < 10 ? '0' : '') + s.toFixed(2);
    }

    function clamp(value, low, high) {
        // high < low（元素比放大镜还小）时取 low，避免出 NaN 或反向
        return Math.max(low, Math.min(Math.max(low, high), value));
    }

    function hasParam(step, name) {
        var params = (step && step.parameters) || [];
        for (var i = 0; i < params.length; i++) {
            if (params[i].name === name) return true;
        }
        return false;
    }

    /** 从执行步骤的参数里取「操作坐标」。取不到返回 null（旧报告本来就没有）。 */
    function parsePoint(step) {
        var params = (step && step.parameters) || [];
        for (var i = 0; i < params.length; i++) {
            if (params[i].name !== POINT_PARAM) continue;
            var match = /(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)/.exec(String(params[i].value));
            if (match) return { x: Number(match[1]), y: Number(match[2]) };
        }
        return null;
    }

    /**
     * 哪些步骤是「执行」步骤——**首选结构判据（有「操作」参数），不是标题里有没有"执行"**。
     *
     * ⚠️ 标题判据会**误报**：终局步骤叫「终局：记录执行结果」，名字里也有"执行"，
     * 而它发生在**录屏结束之后**。自检拿"最后一个执行步骤"比视频时长，
     * 于是随时可能弹一条假的"录屏没覆盖到"红横幅——实测那条差的只有 0.57 s，
     * 而容差是 1.0 s，就是说下一跑换台机器就中了。
     *
     * 每个执行步骤都带「操作」参数（runner 在进入步骤**之前**就设好了，
     * 所以连"页面过期、动作被丢弃"的那些也有）；终局/决策/等待步骤都没有。
     *
     * 退路：整份报告里一个「操作」都没有（很老的数据）时，才回到标题判据。
     */
    function makeExecutionJudge(flat) {
        var structured = flat.some(function (entry) { return hasParam(entry.step, OPERATION_PARAM); });
        if (!structured) {
            return function (entry) { return String(entry.step.name || '').indexOf(EXECUTION_MARK) >= 0; };
        }
        return function (entry) { return hasParam(entry.step, OPERATION_PARAM); };
    }

    function parseViewport(raw) {
        var match = /^(\d+)\s*[x×]\s*(\d+)$/.exec(String(raw || ''));
        return match ? { w: Number(match[1]), h: Number(match[2]) } : null;
    }

    // ---------------------------------------------------- 运行时版本自查

    /**
     * 在浏览器里核对 Backbone / Marionette 版本。
     *
     * 装的时候读不到这两个（它们在报告页面里，是 CLI 自带的），所以只能运行时查。
     * 查不到新版就出横幅：**静默失效**（报告照常打开、只是没有时间轴）是最难发现的失败。
     */
    function checkRuntimeVersions(root) {
        var problems = [];
        var BB = window.Backbone;
        if (!BB) {
            problems.push('页面里没有 window.Backbone —— 插件无法挂载');
        } else if (!BB.Marionette) {
            problems.push('页面里没有 window.Backbone.Marionette（插件按实测只用这个入口）');
        }
        if (problems.length) {
            var box = document.createElement('div');
            box.className = 'step-video-error';
            box.textContent = '⚠️ step-video 插件无法工作：' + problems.join('；') +
                '。报告可能升级过 Allure，请重跑 scripts/install_report_plugin.py。';
            root.appendChild(box);
            return false;
        }
        // 版本号来自插件自己的 versions.json（同目录，fetch 得到）
        fetch(PLUGIN_DIR + 'versions.json').then(function (r) { return r.json(); }).then(function (cfg) {
            var want = cfg.runtime_expected || {};
            var got = { backbone: BB.VERSION, marionette: BB.Marionette.VERSION };
            var mismatch = Object.keys(want).filter(function (k) {
                return want[k] && got[k] && String(got[k]) !== String(want[k]);
            });
            if (!mismatch.length) return;
            var box = document.createElement('div');
            box.className = 'step-video-warn';
            box.textContent = '⚠️ 报告内置库版本与插件锁定的不一致：' +
                mismatch.map(function (k) { return k + ' 期望 ' + want[k] + '、实际 ' + got[k]; }).join('；') +
                '。报告可能升级过 Allure，请重验插件（见 report_plugin/step-video/README.md）。';
            root.appendChild(box);
        }).catch(function () { /* 取不到版本表就不拦，不影响主功能 */ });
        return true;
    }

    /** 在 `before` 之前插一条横幅。放视频正下方——自检失败要让**一眼看得见**，
     *  追加到区块末尾会被步骤列表挤到看不见的地方去。 */
    function banner(body, before, className, text) {
        var box = document.createElement('div');
        box.className = className;
        box.textContent = text;
        body.insertBefore(box, before);
        return box;
    }

    // ------------------------------------------------------------------ 视图

    var StepVideoView = Backbone.Marionette.View.extend({
        template: function () {
            return '<div class="step-video"><h3 class="step-video-title">执行录屏与步骤时间轴</h3>' +
                   '<div class="step-video-body"></div></div>';
        },

        onRender: function () {
            loadStyles();
            var body = this.$('.step-video-body')[0];
            if (!body) return;
            if (!checkRuntimeVersions(body)) return;

            var data = this.model.toJSON();
            var stage = data.testStage || {};
            var attachment = findVideoAttachment(stage);
            if (!attachment) {
                // 没录屏（档位 0，或 -1 档通过了）——不显示播放器，也不报错。
                // 但**要说清为什么**：否则用户会以为录屏坏了。
                var note = document.createElement('div');
                note.className = 'step-video-note';
                note.textContent = findNoteAttachment(stage)
                    ? '本次没有录屏，原因见报告里的「录屏说明」附件。'
                    : '本次没有录屏（录屏档位为 0，或用例通过且用了"仅保留失败"档）。';
                body.appendChild(note);
                return;
            }

            var epochRaw = labelValue(data, 'videoEpochMs');
            if (!epochRaw) {
                body.innerHTML = '<div class="step-video-error">⚠️ 有录屏附件，但找不到 ' +
                    'videoEpochMs 标签，无法把步骤时间换算成视频位置。</div>';
                return;
            }
            var epochMs = Number(epochRaw);

            // 视口尺寸：插件把「操作坐标」换算成画面位置的分母。
            // 缺了就从帧尺寸推断——等比缩放下这是对的，所以旧报告仍然能用，
            // 但要在状态行里说明是推断来的（见下面的 assumed）。
            var viewport = parseViewport(labelValue(data, 'videoViewport'));

            var stageBox = document.createElement('div');
            stageBox.className = 'step-video-stage';
            body.appendChild(stageBox);

            var video = document.createElement('video');
            video.controls = true;
            video.preload = 'metadata';
            video.className = 'step-video-player';
            stageBox.appendChild(video);

            // 光标叠加层。锚点是一个 0×0 的盒子，子元素都以它为原点定位
            // ——这样"箭头尖、圆点、涟漪中心"天然共点，不用各自算偏移。
            var anchor = document.createElement('div');
            anchor.className = 'step-video-anchor';
            anchor.hidden = true;
            anchor.innerHTML =
                '<div class="step-video-ripple"></div>' +
                '<div class="step-video-dot"></div>' +
                '<div class="step-video-pointer"><svg viewBox="0 0 24 24">' +
                '<path d="M4 2 L4 20 L9.5 15 L12.8 21.8 L15.6 20.4 L12.4 14 L19 13.6 Z" ' +
                'fill="#ff2d55" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg></div>';
            stageBox.appendChild(anchor);
            var ripple = anchor.querySelector('.step-video-ripple');

            var loupe = document.createElement('canvas');
            loupe.className = 'step-video-loupe';
            loupe.hidden = true;
            stageBox.appendChild(loupe);

            var list = document.createElement('ol');
            list.className = 'step-video-steps';
            body.appendChild(list);

            var tools = document.createElement('div');
            tools.className = 'step-video-tools';
            body.appendChild(tools);

            var status = document.createElement('div');
            status.className = 'step-video-status';
            body.appendChild(status);

            // 先把视频抓成 Blob URL —— 绕开"服务器不支持 Range 就拖不动"的坑
            fetch('data/attachments/' + attachment.source)
                .then(function (r) { return r.blob(); })
                .then(function (blob) {
                    video.src = URL.createObjectURL(blob);
                })
                .catch(function () {
                    // fetch 失败（例如用 file:// 打开报告）时退回相对路径，
                    // 至少能播，只是可能拖不动。
                    video.src = 'data/attachments/' + attachment.source;
                    status.textContent = '（视频没能预加载，进度条可能拖不动：请用 allure serve 或 http 服务打开）';
                });

            // ------------------------------------------------- 步骤列表与轨迹
            var items = [];        // 每一步：{ li, seek, name, exec }
            var cursors = [];      // 每一步记下的操作点：{ seek, x, y, index }
            var flat = flatten(stage.steps, 1, []);
            var isExecution = makeExecutionJudge(flat);
            flat.forEach(function (entry) {
                var time = entry.step.time || {};
                var seek = (time.start - epochMs) / 1000;
                var index = items.length;
                var li = document.createElement('li');
                li.className = 'step-video-step';
                li.style.paddingLeft = (entry.depth - 1) * 14 + 'px';
                li.innerHTML = '<span class="step-video-name"></span>' +
                               '<span class="step-video-time"></span>';
                li.querySelector('.step-video-name').textContent = entry.step.name;
                li.querySelector('.step-video-time').textContent = time.duration + ' ms';
                li.title = '跳到 ' + fmt(seek) + '（' + (time.start - epochMs) + ' ms 相对录制起点）';

                // 「点一次跳过去并停住，再点同一步继续播」。
                // 看回放时"跳过去就自动播"会立刻跑过关键的那一瞬——而大多数步骤
                // 只有 1 秒上下，等你反应过来已经播完了。
                //
                // 判据是**状态**不是简单 toggle：任何"正在播"的时候点某一步，
                // 都应该是"跳过去并停住"。所以第三下点同一步 = 回到那一刻并停。
                // 只认"停在同一位置"（paused 且没被拖走过）才算"再点一次"。
                var parked = null;
                li.addEventListener('click', function () {
                    if (seek < 0) {
                        status.textContent = '该步骤发生在录屏开始之前（相差 ' +
                            (-seek).toFixed(2) + ' s），无法跳转。';
                        return;
                    }
                    if (isFinite(video.duration) && seek > video.duration) {
                        // ⚠️ 不静默 clamp：跳到末尾会显示**完全无关的画面**，比不跳更糟
                        status.textContent = '该步骤发生在录屏结束之后（' + fmt(seek) +
                            ' vs 视频 ' + fmt(video.duration) + '），无法跳转。' +
                            '录屏在用例收尾前就停了（那时浏览器已关闭）。';
                        return;
                    }
                    var target = Math.max(0, seek);
                    var sameSpot = parked && video.paused &&
                        Math.abs(video.currentTime - parked) <= PARK_TOLERANCE_S;
                    if (sameSpot) {
                        // ⚠️ 被自动播放策略拒绝时要**说出来**。静默失败的话状态行
                        // 明明写着"继续播放"、画面却纹丝不动，看着像插件坏了。
                        // （实测：用合成事件 `li.click()` 触发就会这样——它不算
                        // 用户激活；真人用鼠标点不会。）
                        video.play().catch(function () {
                            status.textContent = '浏览器拒绝了自动播放，请用播放器上的播放按钮';
                        });
                        status.textContent = '从 ' + fmt(target) + ' 继续播放';
                        return;
                    }
                    video.pause();
                    video.currentTime = target;
                    parked = target;
                    syncHighlight();
                    paint(target);
                    status.textContent = '已跳到 ' + fmt(target) + '（已暂停；再点这一步继续播放）';
                });

                items.push({ li: li, seek: seek, name: entry.step.name, exec: isExecution(entry) });
                var point = parsePoint(entry.step);
                if (point && isFinite(seek)) {
                    cursors.push({ seek: seek, x: point.x, y: point.y, index: index });
                }
                list.appendChild(li);
            });

            // 播放时高亮当前步骤：取"最后一个起点 ≤ 当前播放位置"的那一步
            function syncHighlight() {
                var now = video.currentTime, active = -1;
                for (var i = 0; i < items.length; i++) {
                    if (items[i].seek <= now) active = i;
                }
                items.forEach(function (item, i) {
                    item.li.classList.toggle('step-video-active', i === active);
                });
            }

            // ------------------------------------------------------ 光标叠加层

            var showCursor = true, showLoupe = true;

            /**
             * `<video>` 的内容矩形（CSS px，相对 stage）。
             *
             * ⚠️ 必须按**内容矩形**算，不能拿元素框当画面：`object-fit: contain`
             * （见 styles.css，插件显式钉住）会在元素框里居中留黑边，
             * 用元素框当画面会让光标在宽高比不一致时整体偏移。
             */
            function videoBox() {
                var vw = video.videoWidth || 1, vh = video.videoHeight || 1;
                var ew = video.clientWidth || 1, eh = video.clientHeight || 1;
                var scale = Math.min(ew / vw, eh / vh);
                return { x: (ew - vw * scale) / 2, y: (eh - vh * scale) / 2,
                         w: vw * scale, h: vh * scale };
            }

            /** 当前时刻该显示哪个操作点：最后一个"起点 ≤ t"的。之前没有就返回 null。 */
            function cursorAt(t) {
                var found = null;
                for (var i = 0; i < cursors.length; i++) {
                    if (cursors[i].seek > t + 0.001) break;   // seek 递增，不必看后面的
                    found = cursors[i];
                }
                return found;
            }

            function drawLoupe(box, point) {
                var vw = video.videoWidth, vh = video.videoHeight;
                if (!vw || !vh) return;
                var dpr = window.devicePixelRatio || 1;
                if (loupe.width !== Math.round(LOUPE_SIZE * dpr)) {
                    loupe.width = Math.round(LOUPE_SIZE * dpr);
                    loupe.height = Math.round(LOUPE_SIZE * dpr);
                    loupe.style.width = LOUPE_SIZE + 'px';
                    loupe.style.height = LOUPE_SIZE + 'px';
                }
                // 光标在【帧像素】里的位置，以及要放大那一块的源矩形（帧像素）
                var fx = (point.x / viewport.w) * vw, fy = (point.y / viewport.h) * vh;
                var sw = (LOUPE_SIZE / LOUPE_ZOOM) * (vw / box.w);
                var sh = (LOUPE_SIZE / LOUPE_ZOOM) * (vh / box.h);
                var sx = clamp(fx - sw / 2, 0, vw - sw);
                var sy = clamp(fy - sh / 2, 0, vh - sh);

                var ctx = loupe.getContext('2d');
                ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
                ctx.clearRect(0, 0, LOUPE_SIZE, LOUPE_SIZE);
                ctx.save();
                ctx.beginPath();
                ctx.arc(LOUPE_SIZE / 2, LOUPE_SIZE / 2, LOUPE_SIZE / 2 - 3, 0, Math.PI * 2);
                ctx.clip();
                ctx.fillStyle = '#000';
                ctx.fillRect(0, 0, LOUPE_SIZE, LOUPE_SIZE);
                // 直接从 `<video>` 抓帧：与播放进度天然同步，不需要第二个 video 元素
                // （两个 video 对拷画面会漂移，且暂停时未必都停在同一点）
                ctx.drawImage(video, sx, sy, sw, sh, 0, 0, LOUPE_SIZE, LOUPE_SIZE);
                ctx.restore();
            }

            function placeLoupe(px, py) {
                var width = stageBox.clientWidth, height = stageBox.clientHeight;
                var left = px + LOUPE_GAP, top = py + LOUPE_GAP;
                // 越界就翻到另一侧，再不行就贴着边——放大镜盖住的那块正好是
                // 用户要找的地方，不能让它跑到视频外面去
                if (left + LOUPE_SIZE > width) left = px - LOUPE_GAP - LOUPE_SIZE;
                if (top + LOUPE_SIZE > height) top = py - LOUPE_GAP - LOUPE_SIZE;
                loupe.style.left = clamp(left, 0, width - LOUPE_SIZE) + 'px';
                loupe.style.top = clamp(top, 0, height - LOUPE_SIZE) + 'px';
            }

            function paint(t) {
                // 元数据没到就没有 videoWidth —— 元素虽已可见，但用户此刻点步骤
                // 会落到一个还没初始化的 viewport 上。宁可什么都不画。
                if (!viewport || !video.videoWidth) return;
                var point = cursors.length ? cursorAt(t) : null;
                if (!point) {
                    anchor.hidden = true;
                    loupe.hidden = true;
                    return;
                }
                var box = videoBox();
                var px = box.x + (point.x / viewport.w) * box.w;
                var py = box.y + (point.y / viewport.h) * box.h;
                anchor.hidden = !showCursor;
                if (showCursor) {
                    anchor.style.left = px + 'px';
                    anchor.style.top = py + 'px';
                }
                // 涟漪按"离这一步开始多久"推进。锚在**步骤起点**而不是某个精确的
                // dispatch 时刻：步骤起点与实际点击相差一次新鲜度校验 + 一次几何
                // evaluate（几十到几百毫秒），对"提示这里发生了一次操作"足够，
                // 而坐标本身是精确的——那才是这个功能要看的东西。
                var age = t - point.seek;
                var phase = age >= 0 && age < RIPPLE_S ? age / RIPPLE_S : -1;
                ripple.style.opacity = phase < 0 ? '0' : String(1 - phase);
                ripple.style.transform = 'scale(' + (phase < 0 ? 1 : 1 + 1.4 * phase) + ')';

                loupe.hidden = !(showLoupe && showCursor);
                if (!loupe.hidden) {
                    placeLoupe(px, py);
                    drawLoupe(box, point);
                }
            }

            // 播放时用 rAF 驱动（`timeupdate` 只有 ~4 Hz，涟漪会一格一格跳）。
            // 暂停时停掉循环，免得空转。
            var rafId = null;

            video.addEventListener('timeupdate', function () {
                syncHighlight();
                if (rafId === null) paint(video.currentTime);
            });
            video.addEventListener('seeked', function () {
                syncHighlight();
                paint(video.currentTime);
            });

            function loop() {
                if (!video.isConnected) { rafId = null; return; }   // 报告页已切走，别泄漏
                paint(video.currentTime);
                rafId = requestAnimationFrame(loop);
            }
            video.addEventListener('play', function () { if (rafId === null) loop(); });
            video.addEventListener('pause', function () {
                if (rafId !== null) { cancelAnimationFrame(rafId); rafId = null; }
                paint(video.currentTime);
            });

            // ---------------------------------------------------------- 开关

            function mkToggle(text, initial, apply) {
                var button = document.createElement('button');
                button.type = 'button';
                button.className = 'step-video-toggle';
                button.dataset.on = initial ? '1' : '0';
                button.textContent = text;
                function sync() {
                    button.classList.toggle('step-video-toggle-on', button.dataset.on === '1');
                }
                button.addEventListener('click', function () {
                    button.dataset.on = button.dataset.on === '1' ? '0' : '1';
                    sync();
                    apply(button.dataset.on === '1');
                    paint(video.currentTime);
                });
                sync();
                tools.appendChild(button);
                return button;
            }

            if (cursors.length) {
                mkToggle('光标', true, function (on) { showCursor = on; });
                mkToggle('放大镜', true, function (on) { showLoupe = on; });
            } else {
                tools.appendChild(document.createTextNode(
                    '步骤里没有「操作坐标」，无法在画面上标出鼠标位置' +
                    '（旧报告，或这次运行还没有这个字段）。'));
            }

            // ---------------------------------------------------- 覆盖范围自检
            //
            // 判据**不能是"末步骤"**：断言与终局判定发生在录屏结束之后
            // （录屏在用例收尾前停，那时浏览器已经关了）。拿末步骤比会误报。
            // 真正要守的是"用户会点的那些步骤"——即**执行**步骤。
            video.addEventListener('loadedmetadata', function () {
                var duration = video.duration;

                // 视口比例与帧比例必须一致，否则「操作坐标 → 画面位置」的换算会**静默偏**：
                // 报告照常打开，只是光标停在错的地方——比不画光标更坏。
                if (!viewport) {
                    viewport = { w: video.videoWidth, h: video.videoHeight, assumed: true };
                } else if (viewport.h &&
                           Math.abs(viewport.w / viewport.h - video.videoWidth / video.videoHeight) > 0.02) {
                    banner(body, list, 'step-video-error',
                        '⚠️ 录屏帧的比例（' + video.videoWidth + '×' + video.videoHeight +
                        '）与报告记的视口比例（' + viewport.w + '×' + viewport.h +
                        '）不一致，光标位置会偏。请检查录屏的 startScreencast 参数与视口设置。');
                }

                var execs = items.filter(function (i) { return i.exec; });
                var lastExec = execs.length ? execs[execs.length - 1] : null;
                if (lastExec && lastExec.seek > duration + SLACK_S) {
                    banner(body, list, 'step-video-error',
                        '⚠️ 录屏没覆盖到最后一个执行步骤：视频 ' + fmt(duration) +
                        '，而该步骤需要 ' + fmt(lastExec.seek) + '。按步骤跳转会对不上，' +
                        '请检查录屏是否中途停了（见「录屏说明」里的帧数与采集错误）。');
                }
                status.textContent = '视频 ' + fmt(duration) + '｜共 ' + items.length +
                    ' 个步骤' + (execs.length ? '（执行步骤 ' + execs.length + ' 个）' : '') +
                    (cursors.length ? '｜光标轨迹 ' + cursors.length + ' 个点' : '') +
                    (!cursors.length ? '' :
                        (viewport.assumed ? '｜视口尺寸由帧尺寸推断（缺 videoViewport 标签）' : ''));
                paint(video.currentTime);
            });
        },
    });

    allure.api.addTestResultBlock(StepVideoView, { position: 'before' });
})();