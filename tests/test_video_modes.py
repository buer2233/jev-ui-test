"""报告层开关的**优先级解析**与**录屏三档的清理决策**单测。

两件都不碰浏览器、不调模型，所以能离线全绿：

  · `resolve_report_options` —— 四级优先级 `pytest 参数 > 环境变量 > config.json > 内置默认`；
  · 三档清理决策 —— 见文件后半，`-1` 档"什么情况才敢不保留录屏"的边界。

config.json 由 `e9_config.REPO_ROOT` 定位，所以这里**不能**真的去写仓库根目录的文件。
下面用 monkeypatch 换掉 `e9_config._load`，读的是一份假的配置。
"""

import argparse

import pytest

from jev_ultrafast.framework import e9_config, encode, video
from jev_ultrafast.framework.runner import resolve_report_options


class _Config:
    """够真的 pytest config 的最小替身：只需要 getoption。

    没加的选项要抛 ValueError —— 真 config 找不到选项时就是这么抛的，
    `_option()` 也正是靠这个把"选项不存在"当成"没传"。
    """

    def __init__(self, **options):
        self.options = options

    def getoption(self, name):
        if name not in self.options:
            raise ValueError(f"no option named {name}")
        return self.options[name]


@pytest.fixture
def no_file_config(monkeypatch):
    """让 config.json 看起来不存在（不读仓库里那份真实私有配置）。"""
    monkeypatch.setattr(e9_config, "_load", lambda: {})


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """清掉三个环境变量，避免开发机上的设置渗进断言。"""
    for name in ("JEV_NL_VIDEO_RECORD", "JEV_NL_WAIT_STABLE", "JEV_NL_WAIT_STEP_MS"):
        monkeypatch.delenv(name, raising=False)


# --------------------------- 四级优先级 ---------------------------

def test_builtin_defaults(no_file_config):
    """四级全空时的兜底值。"""
    options = resolve_report_options(_Config())
    assert options == {"video_record": 1, "wait_stable": True, "wait_step_threshold_ms": 200}


def test_no_pytest_config_at_all(no_file_config):
    """`pytest_config=None` 也要能用（脚本/单测直接调 run_case 时就是这样）。"""
    assert resolve_report_options(None)["video_record"] == 1


def test_missing_options_are_treated_as_not_passed(no_file_config):
    """选项根本没注册时（如精简的假 config）当作"没传"，不是错误。"""
    assert resolve_report_options(_Config()) == resolve_report_options(None)


def test_file_config_beats_builtin_default(no_file_config, monkeypatch):
    monkeypatch.setattr(e9_config, "_load", lambda: {"video_record": 0, "wait_stable": False,
                                                     "wait_step_threshold_ms": 1500})
    options = resolve_report_options(_Config())
    assert options == {"video_record": 0, "wait_stable": False, "wait_step_threshold_ms": 1500}


def test_env_beats_file_config(no_file_config, monkeypatch):
    monkeypatch.setattr(e9_config, "_load", lambda: {"video_record": 0})
    monkeypatch.setenv("JEV_NL_VIDEO_RECORD", "-1")
    assert resolve_report_options(_Config())["video_record"] == -1


def test_pytest_option_beats_env_and_file(no_file_config, monkeypatch):
    monkeypatch.setattr(e9_config, "_load", lambda: {"video_record": 0})
    monkeypatch.setenv("JEV_NL_VIDEO_RECORD", "-1")
    assert resolve_report_options(_Config(video_record="1"))["video_record"] == 1


def test_explicit_false_beats_env_true(no_file_config, monkeypatch):
    """**`--no-wait-stable` 必须压过环境变量里的 true。**

    这条守的是 `_first()` 里"按 `is not None` 判而不是按真值判"。
    按真值判的话 False 会被跳过，于是"显式关闭"反倒用上了环境变量的 True
    ——用户看到的报告是本该关掉的等待还在跑，而报告上完全看不出来。
    """
    monkeypatch.setenv("JEV_NL_WAIT_STABLE", "true")
    assert resolve_report_options(_Config(wait_stable=False))["wait_stable"] is False
    # 对照组：没传时才轮到环境变量
    assert resolve_report_options(_Config())["wait_stable"] is True


def test_env_accepts_common_spellings(no_file_config, monkeypatch):
    for written in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("JEV_NL_WAIT_STABLE", written)
        assert resolve_report_options(_Config())["wait_stable"] is True
    for written in ("0", "false", "no", "off"):
        monkeypatch.setenv("JEV_NL_WAIT_STABLE", written)
        assert resolve_report_options(_Config())["wait_stable"] is False


def test_video_mode_accepts_all_three_modes(no_file_config):
    for mode in ("0", "1", "-1"):
        assert resolve_report_options(_Config(video_record=mode))["video_record"] == int(mode)


# ------------------- 选项名是一条【字符串契约】，必须端到端验一遍 -------------------

class _CapturingParser:
    """把 conftest 的 `addoption` 调用原样抄进一个真的 argparse 里。

    刻意不用 `_pytest.config.argparsing.Parser`：那是私有类，用它会触发
    PytestDeprecationWarning，把测试套件的输出弄脏。而 `addoption` 的参数
    与 `add_argument` 基本同形，抄一份反而更直白。
    """

    def __init__(self):
        self.argparse = argparse.ArgumentParser(prog="probe")

    def addoption(self, *args, **kwargs):
        self.argparse.add_argument(*args, **kwargs)


class _NamespaceConfig:
    """把 argparse 的 Namespace 包成 pytest config 的样子（只需要 getoption）。"""

    def __init__(self, namespace):
        self.namespace = namespace

    def getoption(self, name):
        try:
            return getattr(self.namespace, name)
        except AttributeError:
            raise ValueError(f"no option named {name}") from None


def _conftest_config(*argv):
    import conftest  # tests/ 在 sys.path 上（pytest 的 prepend 导入模式），可以直接 import

    parser = _CapturingParser()
    conftest.pytest_addoption(parser)
    return _NamespaceConfig(parser.argparse.parse_args(list(argv)))


def test_option_names_match_conftest():
    """conftest 注册的名字必须与 `resolve_report_options` 读的名字一致。

    ⚠️ 这条守的是一条**字符串契约**，写错了**不会报错**：
    `_option()` 把"选项不存在"（ValueError）当成"没传"，于是
    `--video-record=0` 传了等于没传——照样录屏、照样占磁盘，而报告上只有
    「录屏档位=1」，看不出用户的意思被丢了。所以必须用**真的 conftest 定义**
    端到端跑一遍，而不是在测试里重抄一遍名字（重抄就测不出漂移了）。
    """
    assert resolve_report_options(_conftest_config()) == {
        "video_record": 1, "wait_stable": True, "wait_step_threshold_ms": 200,
    }
    assert resolve_report_options(_conftest_config(
        "--video-record=0", "--no-wait-stable", "--wait-step-ms=50",
    )) == {"video_record": 0, "wait_stable": False, "wait_step_threshold_ms": 50}
    assert resolve_report_options(_conftest_config("--wait-stable"))["wait_stable"] is True


# --------------------------- 值写错要响亮报错 ---------------------------

def test_bad_video_mode_fails_loudly(no_file_config, monkeypatch):
    """档位写错不能静默退回默认值。

    静默退回会让"仅保留失败用例"变成"留下全部录屏"，磁盘与证据口径都跟着错，
    而报告上看不出来。
    """
    with pytest.raises(ValueError, match="0 / 1 / -1"):
        resolve_report_options(_Config(video_record="2"))
    with pytest.raises(ValueError, match="0 / 1 / -1"):
        resolve_report_options(_Config(video_record="1-"))
    monkeypatch.setenv("JEV_NL_VIDEO_RECORD", "全部")
    with pytest.raises(ValueError, match="0 / 1 / -1"):
        resolve_report_options(_Config())


def test_bad_threshold_fails_loudly(no_file_config):
    with pytest.raises(ValueError, match="非负整数"):
        resolve_report_options(_Config(wait_step_ms="两百"))
    with pytest.raises(ValueError, match="不能是负数"):
        resolve_report_options(_Config(wait_step_ms="-1"))


# --------------------------- 三档录屏的取舍（会删证据的地方） ---------------------------

def test_mode_0_never_records():
    """档位 0 连**开始录**都不该做——录了再删是白付录屏开销。"""
    assert video.should_record(0) is False
    assert video.should_record(1) is True
    assert video.should_record(-1) is True


def test_mode_1_keeps_everything():
    assert video.should_keep(1, ok=True, note="", interrupted=False)[0] is True
    assert video.should_keep(1, ok=False, note="", interrupted=False)[0] is True


def test_mode_0_keeps_nothing():
    assert video.should_keep(0, ok=False, note="", interrupted=True)[0] is False


def test_mode_minus_one_keeps_only_unhealthy_runs():
    """`-1` 的核心边界：**只有"明确通过"才不保留**。

    下面四种"通过了但不健康"一律保留。它们恰恰最值得看——正是下一轮要查的对象。
    """
    keep = video.should_keep
    # 明确通过：不保留
    assert keep(-1, ok=True, note="", interrupted=False)[0] is False
    # 终局判定没过
    assert keep(-1, ok=False, note="", interrupted=False)[0] is True
    # 提前停止（步数上限 / 超时）
    assert keep(-1, ok=True, note="达到用例超时 300s，提前停止", interrupted=False)[0] is True
    # 过程不健康（页面过期 / 决策重发）
    assert keep(-1, ok=True, note="", interrupted=True)[0] is True


def test_every_decision_carries_a_reason():
    """**删除动作不能静默**：无论留不留，都必须能说清为什么（§6.4）。

    这条同时是"实现不能退化成 `return True`"的反向断言——
    一个永远保留的实现会让前一条测试的 False 分支失败，而这条保证
    那个 False 分支也带着说明。
    """
    for mode in (0, 1, -1):
        for ok in (True, False):
            for note in ("", "超时"):
                for interrupted in (True, False):
                    keep, reason = video.should_keep(mode, ok=ok, note=note, interrupted=interrupted)
                    assert isinstance(keep, bool)
                    assert reason.strip(), f"{mode}/{ok}/{note}/{interrupted} 没给原因"
                    if not keep:
                        # 档位 0 的措辞是「不记录」（它压根没录），其余是「不保留」
                        assert ("不保留" in reason) or ("不记录" in reason), \
                            f"决定不留录屏却没说清：{reason}"


def test_interrupted_detection():
    """两个信号都来自库里已记着的事实。"""
    clean = {"decisions": [{"decision_attempts": 1}], "history": [{"action": "a"}]}
    assert video.any_interrupted(clean) is False
    # 决策重发过
    assert video.any_interrupted({"decisions": [{"decision_attempts": 2}], "history": [{}]}) is True
    # 决策数多于动作数 → 有决策被丢弃过
    assert video.any_interrupted({"decisions": [{}, {}], "history": [{}]}) is True
    # 空 snapshot 不能炸（崩溃路径上拿到的就是空的）
    assert video.any_interrupted({}) is False


# --------------------------- 合成：时间轴要忠实 ---------------------------

def test_concat_listing_gives_every_frame_its_own_duration():
    """**每帧带自己的 duration**——这就是"忠实还原时间轴"的全部机制。

    写成统一时长就等于退回固定帧率（实测中位错位 85 ms、最坏 451 ms），
    那正是"点第 N 步跳到别的画面"的成因。
    """
    text = encode.concat_listing(["/f/1.jpg", "/f/2.jpg"], [0.034, 0.397])
    lines = text.strip().splitlines()
    assert "duration 0.0340" in lines
    assert "duration 0.3970" in lines, "长间隔必须原样带进去，不能被平均掉"
    assert len(set(lines[1::2])) == 2, "两帧的时长不该被写成同一个值"


def test_concat_listing_repeats_the_last_frame():
    """末尾要重复最后一帧，否则 concat 会**丢尾帧**（实测 553 → 530）。

    concat 靠"下一帧的起点"界定当前帧时长，最后一行没有后继就没有时长。
    """
    text = encode.concat_listing(["/f/1.jpg", "/f/2.jpg"], [0.03, 0.03])
    assert text.strip().splitlines()[-1] == "file '/f/2.jpg'"


def test_concat_listing_of_nothing_is_empty():
    assert encode.concat_listing([], []) == ""


def test_frame_durations_use_median_for_the_tail():
    """拿不到 `end_ms` 时，最后一帧用**中位**间隔补，不用平均。

    实测帧间隔有长尾（最大 397 ms），平均值会被少数长间隔拉偏。
    注意这是**降级路径**：真实运行一定会给 `end_ms`（见下一条）。
    """
    assert encode.frame_durations_ms([0, 100, 200]) == [100, 100, 100]
    # 长尾：平均值会被 900 拉高，中位数不会
    assert encode.frame_durations_ms([0, 100, 200, 1100]) == [100, 100, 900, 100]
    assert encode.frame_durations_ms([]) == []
    # 只有一帧且没有 end_ms：盖住 [0, 它自己的时间戳]
    assert encode.frame_durations_ms([500]) == [500]


def test_last_frame_holds_until_the_recording_ends():
    """最后一帧必须**保持到录制结束**——这是需求 3 能不能成立的关键。

    CDP screencast 是**重绘驱动**的（实测：空闲 3 秒 0 帧），所以帧可能只集中在
    开头几秒。若最后一帧只补一个中位间隔，视频会在用例还没跑完时就结束——
    实测那条 26 秒的用例编出来只有 **4.77 秒**，于是"点第 12 步"的 seek 会
    clamp 到视频末尾，**跳到完全无关的画面上**，比没有同步更糟。

    给了 `end_ms` 之后，视频总时长 == 真实录制跨度。
    """
    # 帧都在前 300 ms 内，但录制跑到 26000 ms
    durations = encode.frame_durations_ms([0, 100, 200], end_ms=26_000)
    assert durations == [100, 100, 25_800]
    assert sum(durations) == 26_000, "总时长必须等于录制跨度"

    # 不给 end_ms 时退回中位间隔（只在拿不到结束时刻的降级路径上用）
    assert encode.frame_durations_ms([0, 100, 200]) == [100, 100, 100]


def test_end_ms_never_produces_a_negative_tail():
    """结束时刻早于最后一帧（时钟抖动）时不能算出负时长。"""
    assert encode.frame_durations_ms([0, 5000], end_ms=4000) == [5000, 0]


def test_video_starts_at_the_recording_epoch_not_at_the_first_frame():
    """**视频的 0 秒必须对应录制起点 `epoch`，不是第一帧到达的时刻。**

    插件的 seek 公式是 `(step.start − epochMs)/1000`——它假定视频 0 秒 = 录制起点。
    若第一帧从自己的时间戳起算，视频的 0 秒实际是 `t0`，
    于是**每一步都偏 `t0`**（从开始录制到第一帧到达的耗时）。

    实测：一条 48.57 秒的用例编出 34.07 秒的视频，差额就是第一帧来得晚。
    这种"整体偏移一个固定量"最难怀疑——每步都错，且错得一样多。
    """
    # 第一帧在 800 ms 才到；录制跑到 10000 ms
    durations = encode.frame_durations_ms([800, 1300], end_ms=10_000)
    assert durations == [1_300, 8_700], "第一帧要从 0 盖到 t1"
    assert sum(durations) == 10_000
    # 单帧也要从 0 起算
    assert encode.frame_durations_ms([800], end_ms=10_000) == [10_000]


def test_file_config_may_write_real_types(no_file_config, monkeypatch):
    """config.json 里写的是 JSON 类型（int/bool），不是字符串——必须两种都收。"""
    monkeypatch.setattr(e9_config, "_load", lambda: {
        "video_record": -1, "wait_stable": False, "wait_step_threshold_ms": 0,
    })
    options = resolve_report_options(_Config())
    assert options == {"video_record": -1, "wait_stable": False, "wait_step_threshold_ms": 0}
    # 门槛 0 是合法的（等于"每次等待都独立成步骤"），不能被当成"没配"
    assert options["wait_step_threshold_ms"] == 0