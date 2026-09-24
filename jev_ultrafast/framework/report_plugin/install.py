"""把二开插件装进已生成的 Allure 报告，并先校验版本。

## 为什么只能"生成之后补一刀"

`allure generate` 是 **Java CLI**，Python 侧注册不了插件（Allure 的正式插件机制是
`allure.plugins.directory` 指向 JVM 插件目录，那要打包 jar）。
所以我们走**后处理**：拷插件目录 + 往 `index.html` 里插一行 `<script>`。

形态参照 Allure 官方最小插件 `custom-logo-plugin`（实测其结构为
`allure-plugin.yml` + `<id>-<version>.jar` + `static/**`）。我们不需要 jar，
但目录保持同构，便于将来升级成正式插件。

## 两条必须是这样的

1. **幂等**：报告会反复重新生成再重装。重复执行不能重复插 `<script>`
   （插两次会让插件加载两遍，区块出现两次）。
2. **版本不符就拒绝安装并报错**：二开依赖的是**报告运行时的内部结构**，不是稳定协议。
   装了不匹配的版本，插件可能**静默失效**——报告照常打开，只是没有时间轴。
   宁可装不上。
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

PLUGIN_NAME = "step-video"
PLUGIN_DIR = Path(__file__).resolve().parent / PLUGIN_NAME
SCRIPT_TAG = f'<script src="plugins/{PLUGIN_NAME}/index.js"></script>'


# ------------------------------- 版本探测 -------------------------------

def resolve_tool(tool):
    """把工具名解析成**真实路径**。

    ⚠️ Windows 上必须走这一步：`shutil.which("allure")` 找到的是 `allure.BAT`，
    而 `subprocess.run(["allure", ...])` 会直接 **FileNotFoundError**
    —— CreateProcess 不认没有扩展名的 `.bat`。实测：

        shutil.which("allure")            → D:\\...\\allure-2.13.8\\bin\\allure.BAT
        subprocess(["allure", "--version"])    → FileNotFoundError (WinError 2)
        subprocess(["allure.bat", "--version"]) → rc=0, '2.13.8'

    症状很有误导性：看起来像"allure 没装"，其实是装了但叫不出来。
    """
    return shutil.which(tool) or tool


def _run(args, **kwargs):
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=30, **kwargs)


def _allure_cli_version():
    try:
        result = _run([resolve_tool("allure"), "--version"])
    except (OSError, subprocess.SubprocessError):
        return None
    return (result.stdout or result.stderr or "").strip().splitlines()[0].strip() or None


def _java_version():
    """`java -version` 把版本打到 **stderr** 上（这是它多年的习惯）。"""
    try:
        result = _run([resolve_tool("java"), "-version"])
    except (OSError, subprocess.SubprocessError):
        return None
    text = (result.stderr or "") + (result.stdout or "")
    marker = 'version "'
    if marker not in text:
        return None
    return text.split(marker, 1)[1].split('"', 1)[0] or None


def _ffmpeg_version():
    from ..encode import ffmpeg_version
    return ffmpeg_version()


def _dist_version(name):
    from importlib.metadata import PackageNotFoundError, version
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def detect_versions():
    """探测本机相关组件版本。取不到的项**不出现在结果里**（不编一个假值）。"""
    import platform

    import pytest

    detected = {
        "allure_cli": _allure_cli_version(),
        "java": _java_version(),
        "ffmpeg": _ffmpeg_version(),
        "python": platform.python_version(),
        "pytest": pytest.__version__,
        "allure_pytest": _dist_version("allure-pytest"),
        "allure_python_commons": _dist_version("allure-python-commons"),
        "browser_harness": _dist_version("browser-harness"),
    }
    return {k: v for k, v in detected.items() if v}


# ------------------------------- 版本比对 -------------------------------

def verify_versions(expected, detected, prefix_match=()):
    """比对期望与实际，返回**不匹配项**的列表（空列表 = 全对）。

    纯函数，便于单测——这是"拒绝安装"的判据，写松了插件就会静默失效。

    `prefix_match` 里的项用**前缀**比：它们的版本串带构建后缀
    （ffmpeg 的 `8.1.1-full_build-www.gyan.dev`、java 的 `1.8.0_201-b09`）。
    """
    mismatches = []
    for name, want in expected.items():
        got = detected.get(name)
        if got is None:
            mismatches.append({"name": name, "expected": want, "detected": "（探测不到）"})
        elif name in prefix_match:
            if not str(got).startswith(str(want)):
                mismatches.append({"name": name, "expected": want, "detected": got})
        elif str(got) != str(want):
            mismatches.append({"name": name, "expected": want, "detected": got})
    return mismatches


def load_versions(plugin_dir=PLUGIN_DIR):
    return json.loads((Path(plugin_dir) / "versions.json").read_text(encoding="utf-8"))


def check_versions(plugin_dir=PLUGIN_DIR, detected=None):
    """按 versions.json 校验；返回不匹配项列表。"""
    config = load_versions(plugin_dir)
    return verify_versions(
        config.get("expected", {}),
        detected if detected is not None else detect_versions(),
        config.get("prefix_match", ()),
    )


def describe_mismatches(mismatches):
    return "；".join(f"{m['name']} 期望 {m['expected']}、实际 {m['detected']}" for m in mismatches)


# ------------------------------- 安装 -------------------------------

def is_installed(report_dir):
    """报告里是否已经装好了插件（脚本标签在、插件文件也在）。"""
    report_dir = Path(report_dir)
    index = report_dir / "index.html"
    if not index.is_file():
        return False
    if SCRIPT_TAG not in index.read_text(encoding="utf-8"):
        return False
    return (report_dir / "plugins" / PLUGIN_NAME / "index.js").is_file()


def install(report_dir, plugin_dir=PLUGIN_DIR, *, detected=None, allow_mismatch=False):
    """把插件装进报告目录。幂等：重复调用不会重复插 `<script>`。

    Args:
        allow_mismatch: 版本不匹配时是否仍然安装。默认 False（**拒绝安装**）——
            装了不匹配的版本会静默失效，比装不上更糟。开了会在返回值里标注。

    Returns:
        dict：装了没有、是不是新插的、版本校验结果。

    Raises:
        NotADirectoryError: 目标不是报告目录。
        RuntimeError: 版本不匹配且未放行。
    """
    report_dir = Path(report_dir)
    index = report_dir / "index.html"
    if not index.is_file():
        raise NotADirectoryError(f"不是 Allure 报告目录（没有 index.html）：{report_dir}")

    mismatches = check_versions(plugin_dir, detected)
    if mismatches and not allow_mismatch:
        raise RuntimeError(
            f"插件与当前环境版本不匹配，**拒绝安装**：{describe_mismatches(mismatches)}。"
            "装了不匹配的版本可能静默失效（报告照常打开，只是没有时间轴）。"
            "确认能接受的话，加 --allow-mismatch 重跑。"
        )

    target = report_dir / "plugins" / PLUGIN_NAME
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(plugin_dir, target)

    html = index.read_text(encoding="utf-8")
    already = SCRIPT_TAG in html
    if not already:
        # 插在 </body> 之前，保证在 app.js 之后加载——插件要用 window.allure 与 window.Backbone，
        # 它们由 app.js 建立。插早了会拿不到。
        html = html.replace("</body>", f"    {SCRIPT_TAG}\n</body>")
        index.write_text(html, encoding="utf-8")

    # 别让"插件没装上"变成静默失败——报告会照常打开、照常没有时间轴
    assert SCRIPT_TAG in index.read_text(encoding="utf-8"), "script 标签没插进去"

    return {
        "报告目录": str(report_dir),
        "插件目录": str(target.relative_to(report_dir)),
        "script 标签": "原本就有（幂等命中）" if already else "本次新插入",
        "版本校验": f"通过（{len(load_versions(plugin_dir).get('expected', {}))} 项）"
                    if not mismatches else f"⚠️ 已放行 {len(mismatches)} 项不匹配："
                    + describe_mismatches(mismatches),
    }


def _main(argv):
    """CLI 入口。用法：python scripts/install_report_plugin.py <report_dir> [--allow-mismatch]"""
    import argparse

    parser = argparse.ArgumentParser(description="把 step-video 插件装进 Allure 报告")
    parser.add_argument("report_dir", help="allure generate 的输出目录")
    parser.add_argument("--allow-mismatch", action="store_true",
                        help="版本不匹配也继续装（默认拒绝）")
    parser.add_argument("--check-only", action="store_true", help="只做版本校验，不装")
    args = parser.parse_args(argv)

    # 探测【一次】，校验与安装都用这一份结果——分别探测会读到不同的时刻，
    # 也可能像第一版那样把空字典当成"什么都没探测到"，把全部项判成不匹配。
    detected = detect_versions()
    mismatches = check_versions(detected=detected)
    if mismatches:
        print(f"版本校验发现 {len(mismatches)} 项不匹配：{describe_mismatches(mismatches)}")
    else:
        print(f"版本校验通过（{len(detected)} 项）")

    if args.check_only:
        return 1 if mismatches else 0
    if mismatches and not args.allow_mismatch:
        print("拒绝安装（见上）。确认可接受请加 --allow-mismatch。", file=sys.stderr)
        return 1

    try:
        info = install(args.report_dir, detected=detected, allow_mismatch=args.allow_mismatch)
    except (NotADirectoryError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    for key, value in info.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))