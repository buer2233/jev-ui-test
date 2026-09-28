"""把 CDP 抓到的 JPEG 帧合成 mp4，并尽量忠实还原时间轴。

## 为什么不能简单地按固定帧率合成

screencast 的帧间隔**不均匀**（实测中位 33.8 ms、最大 397 ms、标准差 62 ms）。
`-framerate <平均帧率>` 会假定"每帧间隔都一样"，于是误差**累积**——
实测中位错位 **85 ms**、最坏 **451 ms**。

需求 3 要的是"点第 N 步 → 进度条跳到那一刻"，时间轴不忠实就等于跳过去是别的画面，
**整套同步功能就是错的**。所以宁可多写几行。

## 实测最好的组合

`concat`（每帧带自己的真实 `duration`）+ `-fps_mode cfr -r 30`（交给 ffmpeg 按真实
时间戳重采样到均匀网格）：中位 **17 ms** / 最坏 **33 ms**，约等于 ±1 帧。

试过的另外三种都不如它（见分析报告 §4.2.2）：
  · 朴素 concat（逐帧 duration，不重采样）：反而**丢帧**（553 → 530）；
  · concat + passthrough：保住源时间戳但帧数对不齐；
  · 固定帧率：就是上面那个累积漂移。
"""

import json
import shutil
import statistics
import subprocess
from pathlib import Path

# 这三条都不能省（分析报告 §4.2.2 与 Allure issue #1676）：
#   · -fps_mode cfr -r 30 ：按真实时间戳重采样到均匀网格，对齐精度 ±1 帧
#   · -pix_fmt yuv420p    ：否则浏览器可能报 "does not support video tag"
#   · -movflags +faststart：moov 前置，报告里能边下边播
VIDEO_ARGS = [
    "-c:v", "libx264",
    "-pix_fmt", "yuv420p",
    "-movflags", "+faststart",
]
DEFAULT_FPS = 30


def concat_listing(frames, durations_s):
    """生成 ffmpeg concat demuxer 的清单文本。

    纯函数，便于单测——而这里有**两个必须是这样的**细节：

      · 每帧带**自己的** `duration`：这正是"忠实还原时间轴"的全部机制。
        写成统一时长就等于退回固定帧率，白折腾。
      · 末尾要**重复最后一帧**：concat 靠"下一帧的起点"来界定当前帧的时长，
        不补这一行会丢掉尾帧（实测 553 → 530）。
    """
    if not frames:
        return ""
    lines = []
    for path, duration in zip(frames, durations_s):
        lines.append(f"file '{Path(path).as_posix()}'")
        lines.append(f"duration {duration:.4f}")
    lines.append(f"file '{Path(frames[-1]).as_posix()}'")
    return "\n".join(lines) + "\n"


def frame_durations_ms(times_ms, end_ms=None):
    """由每帧的时间戳算出每帧的显示时长（毫秒）。

    两条都是**需求 3 能不能成立的关键**，各堵一个会让"跳到错画面"的坑：

    ## 一、视频的 `t=0` 必须对应录制起点（`epoch`）

    第一帧盖住的是 `[0, t1)`，**不是** `[t0, t1)`。因为插件的 seek 公式是
    `(step.start − epochMs)/1000`——它假定视频 0 秒 = 录制起点。

    若按"第一帧从自己的时间戳起算"来编，视频的 0 秒实际是 `t0`，
    于是**每一步都会偏 `t0`**：偏移量等于"从开始录制到第一帧到达"的耗时。
    实测那条用例因此出现视频 34.07 s、用例 48.57 s 的差（第一帧来得晚）。
    这种"整体偏移一个固定量"最难怀疑——每步都错，且错得一样多。

    ## 二、最后一帧必须「保持到录制结束」

    CDP screencast 是**重绘驱动**的（实测：空闲 3 秒 0 帧），帧可能只集中在
    开头几秒。若最后一帧只补一个中位间隔，视频会在用例还没跑完时就结束
    ——实测那条 26 秒的用例编出来只有 **4.77 秒**，于是"点第 12 步"的 seek
    会 clamp 到视频末尾，**跳到完全无关的画面上**，比没有同步更糟。

    `end_ms=None` 时退回中位间隔——不用平均值：实测帧间隔有长尾
    （最大 397 ms），平均值会被少数长间隔拉偏。
    """
    if not times_ms:
        return []
    gaps = [b - a for a, b in zip(times_ms, times_ms[1:])]
    if end_ms is not None:
        # 夹一下：时钟抖动可能让结束时刻早于最后一帧，那时不能算出负时长
        stop = max(end_ms, times_ms[-1])
    else:
        stop = times_ms[-1] + (statistics.median(gaps) if gaps else 0.0)
    boundaries = [0.0, *times_ms[1:], stop]
    return [b - a for a, b in zip(boundaries, boundaries[1:])]


def resolve_tool(tool):
    """把工具名解析成真实路径。

    与 `report_plugin/install.py` 的 `resolve_tool` 同一个理由（那边有实测记录）：
    Windows 上 `subprocess` 不认**没有扩展名的 `.bat`**——
    `["allure"]` 报 WinError 2，而 `shutil.which` 明明找得到 `allure.BAT`。
    ffmpeg/ffprobe 通常是 `.exe`，但走同一条路不必区分。
    """
    return shutil.which(tool) or tool


def ffmpeg_version():
    """本机 ffmpeg 版本串；取不到就返回 None（由调用方决定怎么报）。"""
    try:
        result = subprocess.run(
            [resolve_tool("ffmpeg"), "-version"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    first = (result.stdout or "").splitlines()[:1]
    if not first:
        return None
    # "ffmpeg version 8.1.1-full_build-www.gyan.dev Copyright ..." → "8.1.1-full_build-..."
    parts = first[0].split()
    return parts[2] if len(parts) > 2 else None


def to_mp4(frames, times_ms, out_path, *, end_ms=None, fps=DEFAULT_FPS):
    """把有序的 jpg 帧合成 mp4。

    Args:
        frames:   按拍摄顺序排列的 jpg 路径。
        times_ms: 与 `frames` 一一对应的**时间戳**（Unix 纪元毫秒，
                  与 Allure 步骤时间同基准）。
        out_path: 产物路径。
        end_ms:   录制结束时刻（Unix 纪元毫秒）。给了就让最后一帧保持到这一刻，
                  **视频时长才等于真实录制跨度**——否则 seek 到后段的步骤会落到
                  视频末尾（见 `frame_durations_ms` 的说明）。
        fps:      重采样网格帧率。

    Returns:
        dict: {"耗时s", "帧数", "体积kb", "时长s"}，供报告留痕。

    Raises:
        RuntimeError: ffmpeg 缺失或返回非零。**不静默返回 None**——
            没有视频的报告看起来一切正常，"只是没有录屏"，最难发现。
    """
    frames = [Path(frame) for frame in frames]
    if not frames:
        raise ValueError("没有帧可合成")

    durations_s = [d / 1000 for d in frame_durations_ms(list(times_ms), end_ms)]
    listing = out_path.with_suffix(".concat.txt")
    listing.write_text(concat_listing(frames, durations_s), encoding="utf-8")

    import time
    started = time.perf_counter()
    try:
        subprocess.run(
            [resolve_tool("ffmpeg"), "-y", "-loglevel", "error",
             "-f", "concat", "-safe", "0", "-i", str(listing),
             "-fps_mode", "cfr", "-r", str(fps), *VIDEO_ARGS, str(out_path)],
            check=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    except FileNotFoundError as error:
        raise RuntimeError(
            "合成录屏失败：找不到 ffmpeg。装好后重试，或用 --video-record=0 关掉录屏。"
        ) from error
    except subprocess.CalledProcessError as error:
        tail = (error.stderr or "")[-400:]
        raise RuntimeError(f"合成录屏失败（ffmpeg 退出码 {error.returncode}）：{tail}") from error
    finally:
        listing.unlink(missing_ok=True)

    return {
        "耗时s": round(time.perf_counter() - started, 2),
        "帧数": len(frames),
        "体积kb": round(out_path.stat().st_size / 1024, 1),
        "时长s": probe_duration(out_path),
    }


def probe_duration(path):
    """读成品时长（秒）；取不到返回 None。

    这个数字进「录屏说明」留痕，也是插件自检的基准之一。

    ⚠️ 插件里的自检**不是**拿"末步骤"比（方案初稿 §8.2 写的是那个，M5 改掉了）：
    断言与终局判定的步骤发生在录屏结束【之后】（录屏在用例收尾前就停了，那时浏览器已关），
    拿末步骤比会**误报**。真正要守的是用户会点的那些步骤，即**执行**步骤——
    插件比的是"最后一个执行步骤的 seek 是否超出视频时长"。
    """
    try:
        result = subprocess.run(
            [resolve_tool("ffprobe"), "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    try:
        return round(float(result.stdout.strip()), 3)
    except ValueError:
        return None


def cleanup_frames(frames_dir):
    """删掉原始帧目录。**必须放 finally**：帧约 92 MB/分钟，30 秒用例先落 ~46 MB。"""
    shutil.rmtree(frames_dir, ignore_errors=True)


def attach_video(mp4_path, name="执行录屏"):
    """把 mp4 作为附件挂进当前步骤/用例。"""
    import allure
    allure.attach.file(str(mp4_path), name=name, attachment_type=allure.attachment_type.MP4)


def format_summary(info):
    """把 to_mp4 的返回值排成报告里那行字。"""
    return json.dumps(info, ensure_ascii=False)