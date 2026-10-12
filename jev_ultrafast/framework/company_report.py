"""把一次 Allure 运行结果转成**公司规范格式**的 xlsx 测试报告。

三张工作表，与公司模版一一对应（版式照模版逐项复刻，见本文件末尾的「版式对照」）:

  · 「测试方案」——测试环境 + 测试大纲及要点（每条用例一行：`<check项>（<测试项类别>）`）；
  · 「功能测试list及报告」——研发功能 / 测试项类别 / check项 / 检查点、要求及备注 / 确认列 / 其他备注；
  · 「测试截图」——用例编号 / 用例名称 / 结论·实际结果 / **断言截图**。

**输入只有两样**：Allure 原始结果目录（`report/allure-results`），以及（可选的）用例目录
（`cases/`）。执行事实（状态、截图、断言明细、重跑次数）全部来自 Allure；设计事实
（测试项类别、check项、预期）来自用例 YAML 的 `report:` 块——**两者按用例 id 对齐**。

三条不能违的规矩（与仓库契约一致）:

  1. **模版随仓库分发**（`.claude/skills/nl-report-export/assets/`，见 `BUNDLED_TEMPLATE`）。
     它是"版式对不对"的**校验基准**：新克隆的工作区必须有一份能参考的东西，
     否则 `--verify-template` 与 skill 的 evals 都无从谈起。
     ⚠️ 它含真实项目信息与内网截图——这是**已确认的取舍**（与 `examples/` 的演示素材同类，
     见 AGENTS.md「凭据与仓库边界」）。不要因为"里面有内网信息"就删掉或加 gitignore。
  2. **版式以代码常量复刻、模版不当生成输入**（本文件顶部的布局常量）。
     为什么不直接复制模版再填数据：模版里那张表是**别人那条业务**的记录，
     复制就得逐格清空、按本次用例条数重排合并区——那些"清理"动作一旦漏一处，
     生成出来的就是**混着别人数据的报告**，而它看起来完全正常。
     代码复刻 + 对照校验（`compare_with_template`）把这件事变成可验证的。
  3. **报告里不许出现凭据。** 环境块只写地址、浏览器、模型名这类信息；
     cookie / token / 密码一律不进（与 `reporting.attach_environment` 同一条规矩）。
"""

from __future__ import annotations

import ast
import json
import math
import platform
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

# ---------------------------------------------------------------- 版式常量
#
# 全部照公司模版量出来的（2026-10-10 逐单元格比对得出：字体名/字号/加粗、
# 底色、边框、列宽、合并区、冻结窗格）。**改这里等于改版式**：
# 改完必须跑 `tests/test_company_report.py` 与 `--verify-template` 对照校验。

#: 随仓库分发的**公司模版**。`--verify-template` 不带路径时用它，skill 的 evals 也用它。
#:
#: 为什么它必须入库：对照校验与 skill 的 evals 都要有"参照物"，而参照物是本机临时文件的话，
#: **新克隆的工作区根本没法验**（只能 SKIP，等于没有这条保障）。
#: ⚠️ 它含真实项目信息与内网系统截图，**这是已确认的取舍**（见 AGENTS.md
#: 「凭据与仓库边界」的例外）。不要因为"里面有内网信息"就删掉或加 gitignore。
#: 已逐表扫过：**无账号口令/密钥/cookie**；人名与账号已脱敏成占位符
#: （开发人员 / 测试人员 / 客户名，2026-10-12）。
BUNDLED_TEMPLATE = (
    Path(__file__).resolve().parents[2] / ".claude" / "skills" / "nl-report-export"
    / "assets" / "公司内部的测试报告模板.xlsx"
)


def bundled_template_path() -> Path:
    """随仓库分发的公司模版路径。

    ⚠️ 取不到时**响亮报错**，不静默跳过：对照校验被静默跳过与"校验通过"在输出上
    长得一样（都是没有 `[不一致]`），而这个校验的全部价值就在于它会出声。
    """
    if not BUNDLED_TEMPLATE.is_file():
        raise FileNotFoundError(
            f"仓库里没有公司模版（对照校验没有参照物）：{BUNDLED_TEMPLATE}。"
            "它随仓库分发，见 AGENTS.md「凭据与仓库边界」的例外——不要删它。"
        )
    return BUNDLED_TEMPLATE

SHEET_PLAN = "测试方案"
SHEET_LIST = "功能测试list及报告"
SHEET_SHOTS = "测试截图"

FONT_NAME = "微软雅黑"
#: 「测试截图」页正文的字体。**与另外两页不同**：模版那一页通体是工作簿默认的宋体 11，
#: 不是微软雅黑 12——照模版抄才叫一致，觉得"统一成雅黑更好看"是另一件事。
SHOTS_FONT_NAME = "宋体"
SHOTS_FONT_SIZE = 11.0
TITLE_SIZE = 20
BODY_SIZE = 12
NOTE_SIZE = 9            # 「检查点、要求及备注」列，模版比正文小两号
SQL_LABEL_SIZE = 10      # 数据库/中间件子表头

FILL_HEADER = "FFDCE6F1"     # 表头浅蓝灰（测试环境、序号行、check项行…）
FILL_TITLE = "FFFFFFFF"      # 标题行底（白）
FILL_BRANCH = "FFC00000"     # 分支确认（红）
FILL_TEST = "FF00B0F0"       # test确认（蓝）
FILL_RELEASE = "FF04C8A7"    # release确认（绿）

#: 确认列（模版 E7 / F7:L7 / L7:R7 的分布），本仓库没有多数据库矩阵，一律填 `√`。
CONFIRM_COLS = ["E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P", "Q"]

#: 数据库 + 中间件子表头（模版 F4:Q5 与 F8:Q9 两段完全一样）。
DB_HEADERS = [
    ("mysql+tomcat", "(微服务)"), ("sqlserver+tomcat", "(单体)"), ("oracle+tomcat", "(组合)"),
    ("pg+tomcat", "(微服务)"), ("dm+东方通", "(微服务)"), ("st+东方通", "(微服务)"),
]

#: 各列宽度（模版自定义过的那几列；其余用 Excel 默认）。
COL_WIDTHS = {
    SHEET_PLAN: {"F": 24.625, "J": 29.0},
    SHEET_LIST: {"B": 25.25, "C": 44.25, "D": 61.75, "R": 17.125},
    SHEET_SHOTS: {"A": 10.0, "B": 30.0, "C": 46.0, "D": 62.0},
}

#: Excel 默认列宽（字符）。合并区的可用宽度要按它算——算窄了行高会估小，
#: 估小则文字被 Excel **裁掉**（报告里看不出少了字，只看到"这一行到此为止"）。
DEFAULT_COL_CHARS = 9.0

#: 截图列的宽度（字符）→ 像素。Excel 的列宽单位是"默认字体下 0 的宽度"，
#: 换算 ≈ 7 px/字符 + 5 px 内边距（微软官方文档给的经验值）。
SHOTS_COL_CHARS = COL_WIDTHS[SHEET_SHOTS]["D"]
PX_PER_CHAR = 7
PX_PADDING = 5

STATUS_LABELS = {
    "passed": "【通过】",
    "failed": "【失败】",
    "broken": "【异常】",
    "skipped": "【跳过】",
    "unknown": "【未知】",
}

NO_SCREENSHOT = "（无截图）"

#: 单元格文本上限。Excel 硬上限是 32767；这里主动截断得更短——报告是给人读的，
#: 5000 字一格已经没人看，而全文在 Allure 报告里（截断处会写明这一点）。
CELL_TEXT_LIMIT = 1200


# ---------------------------------------------------------------- 读取层：Allure 结果


@dataclass
class Attachment:
    name: str
    type: str
    source: Path

    def read_bytes(self) -> bytes:
        return self.source.read_bytes()

    def read_text(self) -> str:
        return self.source.read_text(encoding="utf-8", errors="replace")

    def read_json(self) -> Any:
        try:
            return json.loads(self.read_text())
        except (json.JSONDecodeError, ValueError):
            return None


@dataclass
class Step:
    name: str
    status: str
    attachments: list[Attachment] = field(default_factory=list)
    steps: list["Step"] = field(default_factory=list)

    def walk(self) -> Iterable["Step"]:
        yield self
        for child in self.steps:
            yield from child.walk()

    def find_attachment(self, name: str) -> Attachment | None:
        """按名字找附件；同名多个时取**第一个**（调用方保证名字是唯一的）。"""
        for attachment in self.attachments:
            if attachment.name == name:
                return attachment
        return None


@dataclass
class Result:
    """一条 Allure 测试结果（已按 historyId 去重，重跑次数记在 `retries`）。"""

    name: str
    status: str
    history_id: str
    start: int
    stop: int
    steps: list[Step] = field(default_factory=list)
    attachments: list[Attachment] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)
    parameters: dict[str, str] = field(default_factory=dict)
    status_message: str = ""
    retries: int = 0

    def walk(self) -> Iterable[Step]:
        for step in self.steps:
            yield from step.walk()

    def find_attachment(self, name: str) -> Attachment | None:
        for attachment in self.attachments:
            if attachment.name == name:
                return attachment
        for step in self.walk():
            found = step.find_attachment(name)
            if found is not None:
                return found
        return None

    def find_step(self, name: str) -> Step | None:
        for step in self.walk():
            if step.name == name:
                return step
        return None

    @property
    def duration_ms(self) -> int:
        return max(0, int(self.stop) - int(self.start))


def _attachment(raw: dict, results_dir: Path) -> Attachment | None:
    source = raw.get("source")
    if not source:
        return None
    return Attachment(name=raw.get("name") or "", type=raw.get("type") or "",
                      source=results_dir / source)


def _steps(raw_steps: list | None, results_dir: Path) -> list[Step]:
    steps = []
    for raw in raw_steps or []:
        steps.append(Step(
            name=raw.get("name") or "",
            status=raw.get("status") or "unknown",
            attachments=[a for a in (_attachment(item, results_dir)
                                     for item in raw.get("attachments") or []) if a],
            steps=_steps(raw.get("steps"), results_dir),
        ))
    return steps


def load_results(results_dir) -> list[Result]:
    """读一个 `allure-results` 目录里的全部 `*-result.json`。

    **按 historyId 去重**：用例级重跑（`--reruns N`）会为同一条用例写多份结果文件，
    而报告里只能占一行。保留 `stop` 最新的那一份（＝最后一次尝试），
    其余的次数记进 `retries`——重跑才过的用例**必须被标出来**（AGENTS.md
    「读结果并如实汇报」第 2 条：把重跑兜住的报成"通过"是不准确的）。
    """
    directory = Path(results_dir)
    raw_results = []
    for path in sorted(directory.glob("*-result.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue                       # 半截文件（上一次运行被杀）不该把整份报告带崩
        raw_results.append(Result(
            name=data.get("name") or "",
            status=data.get("status") or "unknown",
            history_id=data.get("historyId") or data.get("uuid") or path.stem,
            start=int(data.get("start") or 0),
            stop=int(data.get("stop") or 0),
            steps=_steps(data.get("steps"), directory),
            attachments=[a for a in (_attachment(item, directory)
                                     for item in data.get("attachments") or []) if a],
            labels={item.get("name"): item.get("value") for item in data.get("labels") or []},
            parameters={item.get("name"): item.get("value") for item in data.get("parameters") or []},
            status_message=((data.get("statusDetails") or {}).get("message") or ""),
        ))

    grouped: dict[str, list[Result]] = {}
    for result in raw_results:
        grouped.setdefault(result.history_id, []).append(result)

    kept = []
    for group in grouped.values():
        group.sort(key=lambda item: item.stop)
        latest = group[-1]
        latest.retries = len(group) - 1
        kept.append(latest)
    # 报告顺序按**开始时间**排：与执行顺序一致（pytest 是顺序跑的），
    # 而 historyId 的字典序会把用例打乱成看不懂的顺序。
    kept.sort(key=lambda item: (item.start, item.name))
    return kept


#: pytest 参数化 id 就写在用例名末尾的方括号里（`test_natural_language_case[<id>]`）。
PARAMETERIZED_ID = re.compile(r"\[([^\]]+)\]\s*$")


def case_parameter(result: Result) -> dict:
    """结果里那份**收集期**的用例 dict（pytest 参数化的 `nl_case`）。

    pytest 把整份 case dict 记进了参数值，里面除了 `id` 还有 `_base_url`、
    `feature`、`epic` 这些**收集期才知道**的东西——报告要写"这次跑的是哪台环境"，
    唯一可靠的来源就是它（`load_cases()` 读出来的用例身上**没有** `_base_url`，
    那是 conftest 在收集期注入的）。

    值是 Python repr（不是 JSON），所以用 `ast.literal_eval` 解；解不出来给空 dict。
    """
    raw = result.parameters.get("nl_case")
    if not raw:
        return {}
    try:
        parsed = ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def case_id_of(result: Result) -> str:
    """从结果里取用例 id。

    ⚠️ **必须有第二条路**：`skip:` 的用例在**收集期之后、runner 之前**就被 pytest
    跳过了，根本没进过 runner——于是既没有 story 标签、也没有 `nl_case` 参数，
    结果名退化成 `test_natural_language_case[e9-wf-submit-tc06]`。
    这时按名字尾部的参数化 id 认领（那一串就是 YAML 里的 id），
    否则这条用例在报告里会变成"没有类别、没有 check项"的孤儿。
    """
    identifier = case_parameter(result).get("id")
    if identifier:
        return str(identifier)
    match = PARAMETERIZED_ID.search(result.name or "")
    return match.group(1) if match else ""


# ---------------------------------------------------------------- 组装层：报告模型


@dataclass
class ReportCase:
    index: int                  # 1 起（「测试截图」页是 T01 起，见 code）
    case_id: str
    name: str                   # 用例名称
    category: str               # 测试项类别
    check: str                  # check项（= 模版里「测试大纲及要点」与「用例名称」的那句话）
    expect: str                 # 检查点（「预期：」那一段）
    status: str                 # passed / failed / broken / skipped / unknown
    actual: str                 # 实际结果（含操作链与断言明细）
    notes: str                  # 其他备注
    screenshot: bytes | None = None
    retries: int = 0
    duration_ms: int = 0

    @property
    def code(self) -> str:
        return f"T{self.index:02d}"

    @property
    def verdict(self) -> str:
        return STATUS_LABELS.get(self.status, STATUS_LABELS["unknown"])

    @property
    def outline(self) -> str:
        """「测试大纲及要点」那一行：`<check项>（<测试项类别>）`（模版就是这个形状）。"""
        if self.category and self.check:
            return f"{self.check}（{self.category}）"
        return self.check or self.category or self.name


@dataclass
class ReportModel:
    cases: list[ReportCase]
    environment: dict[str, str]
    generated_at: datetime
    results_dir: str
    extra_note: str = ""

    @property
    def passed(self) -> int:
        return sum(1 for case in self.cases if case.status == "passed")

    @property
    def failed(self) -> int:
        return sum(1 for case in self.cases if case.status in {"failed", "broken"})

    @property
    def skipped(self) -> int:
        return sum(1 for case in self.cases if case.status == "skipped")


def _short(text: str, limit: int = CELL_TEXT_LIMIT) -> str:
    """单元格文本截断。**截断要留痕**——不然读报告的人以为原文就这么短。"""
    text = " ".join((text or "").split()) if "\n" not in (text or "") else (text or "")
    if len(text) <= limit:
        return text
    return text[:limit] + "…（已截断，全文见 Allure 报告）"


def _evidence_lines(verdict: dict | None) -> list[str]:
    """把「断言汇总」摊成一句一句的实际结果。"""
    if not isinstance(verdict, dict):
        return []
    lines = []
    for item in verdict.get("results") or []:
        detail = (item.get("detail") or "").strip()
        if detail:
            lines.append(detail.rstrip("。") + "。")
    return lines


def _actual_text(result: Result, verdict: dict | None, summary: dict | None) -> str:
    """「实际结果」正文：**先写结论，再写逐条断言的事实**。

    为什么不用模型去"总结"一遍：这里每一句都是执行留下的原始事实
    （`断言证据`/`断言汇总` 里的 detail），编不出来也改不了。
    用模型总结等于在报告里引入一个看不见的、会漂移的环节。
    """
    # 结论**另起一行**：模版的「结论 / 实际结果」就是「【通过】」换行再接实际操作与结果，
    # 一行到底会把结论淹没在证据里。
    headline = STATUS_LABELS.get(result.status, STATUS_LABELS["unknown"])
    if result.status == "skipped":
        return f"{headline}\n" + _short(result.status_message or "用例被跳过（未执行）。", 400)
    parts = [headline]

    lines = _evidence_lines(verdict)
    if lines:
        parts.append("\n" + " ".join(lines))
    elif result.status_message:
        parts.append("\n" + _short(result.status_message, 400))

    if isinstance(summary, dict):
        metrics = []
        if summary.get("浏览器动作数") is not None:
            metrics.append(f"浏览器动作 {summary['浏览器动作数']} 个")
        if summary.get("决策请求数") is not None:
            metrics.append(f"决策请求 {summary['决策请求数']} 次")
        if result.duration_ms:
            metrics.append(f"耗时 {result.duration_ms / 1000:.1f}s")
        if metrics:
            parts.append("（" + "，".join(metrics) + "）")
    return _short("".join(parts))


def _notes_text(case: ReportCase) -> str:
    """「其他备注」：模版是 `【通过】 <实际结果摘要>`，这里再补上重跑提示。

    `case.actual` **已经以【通过】开头**（它是给「结论 / 实际结果」那一列用的），
    所以这里不再补一次 verdict——补了会变成「【通过】 【通过】 …」。
    模版这一列只有一行，所以把结论后那个换行压成空格。
    """
    note = case.actual.replace("\n", " ")
    if case.retries:
        # 重跑才过的用例必须显眼：它说明这条用例**不稳定**，不能当稳定通过读。
        note += f"（本用例用例级重跑 {case.retries} 次后才得到这个结果）"
    return _short(note, 500)


def _design_meta(case: dict | None) -> dict:
    """用例 YAML 里的 `report:` 块（设计侧事实）。"""
    if not isinstance(case, dict):
        return {}
    meta = case.get("report")
    return meta if isinstance(meta, dict) else {}


def _fallback_check(case: dict | None, result: Result) -> str:
    """没写 `report.check` 时的回落：用例名去掉 `TCxx ` 前缀。"""
    name = (case or {}).get("name") or result.name
    head, _, tail = name.partition(" ")
    if tail and head.upper().startswith("TC"):
        return tail
    return name


def _fallback_expect(case: dict | None) -> str:
    """没写 `report.expect` 时的回落：把 expect 列表里的描述拼起来。

    ⚠️ 这是**回落**、不是等价物：`expect` 里是断言的技术口径
    （`URL 期望包含 requestid=`），而模版的「预期」是给人读的业务口径。
    两条都在时以 YAML 的 `report.expect` 为准——那才是写用例的人想说的话。
    """
    items = (case or {}).get("expect") or []
    descriptions = []
    for item in items:
        if not isinstance(item, dict):
            continue
        descriptions.append(item.get("desc") or item.get("claim") or item.get("type") or "")
    return "；".join(text for text in descriptions if text)


def _screenshot_of(result: Result) -> bytes | None:
    """取断言截图（`reporting.SCREENSHOT_ATTACHMENT`）的字节；没有就 None。

    只取字节、不引后缀：openpyxl 的 `Image` 自己从字节流认格式（CDP 给的是 jpeg），
    我们从不按后缀落盘——多存一个后缀字段就成了没人读的死数据。
    """
    from .reporting import SCREENSHOT_ATTACHMENT

    attachment = result.find_attachment(SCREENSHOT_ATTACHMENT)
    if attachment is None:
        return None
    try:
        return attachment.read_bytes()
    except OSError:
        return None


def _read_environment_properties(results_dir: Path) -> dict[str, str]:
    """读 Allure 的 `environment.properties`（有就读，没有就空着）。"""
    info: dict[str, str] = {}
    properties = Path(results_dir) / "environment.properties"
    if properties.is_file():
        for line in properties.read_text(encoding="utf-8", errors="replace").splitlines():
            key, sep, value = line.partition("=")
            if sep:
                info[key.strip()] = value.strip()
    return info


def _most_common(values: Iterable[str], limit: int = 2) -> str:
    """出现最多的几个值，按出现次数排（并列时按首次出现的先后）。"""
    ordered = [value for value in dict.fromkeys(values) if value]
    if not ordered:
        return ""
    counts = Counter(value for value in values if value)
    ordered.sort(key=lambda value: -counts[value])
    return " / ".join(ordered[:limit])


def _environment_block(results_dir: Path, matched: list[tuple[dict, dict]]) -> dict[str, str]:
    """环境块（「测试方案」页左上那块）用到的原始信息。

    **只取不猜**：地址与模块来自**本报告真正覆盖到的那些用例**
    （结果里那份收集期用例 dict 的 `_base_url` / `feature` / `epic`，见 `case_parameter`），
    源码版本与操作系统取生成时刻的，
    浏览器与视口来自各用例的「执行摘要」。取不到的一律写「本次未采集」，不编。

    ⚠️ 不要拿"加载到的第一条用例"当代表：`cases/` 里同时躺着演示用例
    （`cases/demo`，feature 是"通用演示"、没有 E9 地址），
    按第一条算出来的"被测系统"会是**演示站**——报告上完全看不出来这是错的。
    """
    info = _read_environment_properties(results_dir)
    info["测试地址"] = _most_common([runtime.get("_base_url") or case.get("_base_url")
                                     for runtime, case in matched]) or "本次未采集"
    info["研发模块"] = _most_common([runtime.get("feature") or case.get("feature")
                                     for runtime, case in matched])
    info["测试集"] = _most_common([runtime.get("epic") or case.get("epic")
                                   for runtime, case in matched])
    from .reporting import _git_revision

    info["源码版本"] = _git_revision() or "本次未采集"
    info["操作系统"] = f"{platform.system()} {platform.release()}"
    return info


def build_model(results_dir, cases_dir=None) -> ReportModel:
    """把 Allure 结果 + 用例设计信息组装成报告模型（不落盘）。"""
    from .loader import load_cases

    results = load_results(results_dir)
    by_id = {}
    try:
        for case in load_cases(cases_dir):
            by_id[case["id"]] = case
    except Exception:                      # noqa: BLE001  用例目录不在也要能出报告
        pass

    viewport = ""
    model_name = ""
    matched: list[tuple[dict, dict]] = []
    cases: list[ReportCase] = []
    for index, result in enumerate(results, 1):
        runtime = case_parameter(result)
        case_id = case_id_of(result)
        case = by_id.get(case_id) or {}
        matched.append((runtime, case))
        meta = _design_meta(case)

        summary_attachment = result.find_attachment("执行摘要")
        summary = summary_attachment.read_json() if summary_attachment else None
        if isinstance(summary, dict):
            viewport = viewport or "×".join(str(part) for part in summary.get("视口") or [])
            # 优先用「执行摘要」里回报的模型名（那是**这一次真正用的**那个）；
            # 旧结果没有这一项时退回 Allure 环境信息里的默认模型名。
            model_name = model_name or str(summary.get("决策模型") or "")
        if not model_name:
            probability = result.find_attachment("Jev 决策概率")
            payload = probability.read_json() if probability else None
            if isinstance(payload, dict):
                model_name = str(payload.get("model") or "")

        verdict_attachment = result.find_attachment("断言汇总")
        verdict = verdict_attachment.read_json() if verdict_attachment else None
        screenshot = _screenshot_of(result)

        report_case = ReportCase(
            index=index,
            case_id=case_id or result.name,
            name=case.get("name") or runtime.get("name") or result.name,
            # 类别回落三级：YAML 的 report.category → 用例自己的 feature → Allure 的 feature 标签。
            # ⚠️ 中间那一级不能少：**跳过的用例没有 Allure 标签**（它压根没进 runner），
            # 少了它，TC06 在报告里就是"没有类别"的孤儿。
            category=str(meta.get("category") or case.get("feature")
                         or runtime.get("feature") or result.labels.get("feature") or ""),
            check=str(meta.get("check") or _fallback_check(case, result)),
            expect=str(meta.get("expect") or _fallback_expect(case)),
            status=result.status,
            actual="",
            notes="",
            screenshot=screenshot,
            retries=result.retries,
            duration_ms=result.duration_ms,
        )
        report_case.actual = _actual_text(result, verdict, summary)
        report_case.notes = _notes_text(report_case)
        cases.append(report_case)

    environment = _environment_block(results_dir, matched)
    environment["浏览器视口"] = viewport or "本次未采集"
    environment["决策模型"] = model_name or environment.get("TypeSafe 模型") or "本次未采集"
    return ReportModel(cases=cases, environment=environment,
                       generated_at=datetime.now(), results_dir=str(results_dir))


# ---------------------------------------------------------------- 写出层：xlsx


def _style_cell(cell, *, size=BODY_SIZE, bold=False, fill=None, align="center",
                wrap=True, border=True, plain_font=False, font_name=None):
    """一个单元格的样式。**所有格子都从这一处出**——版式只有一处可改。

    `plain_font=True` 用工作簿默认字体（只加粗）——「测试截图」页的表头就是这样：
    模版里那四个表头没有指定字体名与字号（`font.size is None`），
    照抄过来的话检测项会报"字号 12 vs 模版 None"。
    """
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    # ⚠️ plain_font 只换"字体名/字号"（用工作簿默认），**加粗照旧由 `bold` 决定**。
    # 这里曾写成 `Font(bold=True)`——于是「测试截图」页的数据格全变粗体，
    # 而它看起来"只是有点抢眼"，眼睛扫过去不会觉得是错。
    cell.font = (Font(bold=bold) if plain_font
                 else Font(name=font_name or FONT_NAME, size=size, bold=bold))
    if fill:
        cell.fill = PatternFill(fill_type="solid", start_color=fill, end_color=fill)
    cell.alignment = Alignment(horizontal=align, vertical="center", wrap_text=wrap)
    if border:
        side = Side(style="thin")
        cell.border = Border(left=side, right=side, top=side, bottom=side)


def _put(ws, coordinate, value, **style):
    ws[coordinate] = value
    _style_cell(ws[coordinate], **style)
    return ws[coordinate]


def _merge_and_put(ws, cells, value, **style):
    ws.merge_cells(cells)
    _put(ws, cells.split(":")[0], value, **style)


def _display_width(text: str) -> int:
    """显示宽度（CJK 记 2）——用来估"这段文字要占几行"。"""
    return sum(2 if ord(char) > 0x2E80 else 1 for char in str(text))


def _text_height(*texts, width_chars: float, minimum: float = 30.0) -> float:
    """按内容估一个行高（磅）。估小了文字会被 Excel 裁掉，估大了只是多留白。"""
    lines = 0
    for text in texts:
        for segment in str(text or "").split("\n"):
            lines += max(1, math.ceil(_display_width(segment) / max(width_chars, 1)))
    return max(minimum, 15.0 * lines + 6.0)


def _column_px(chars: float) -> float:
    return chars * PX_PER_CHAR + PX_PADDING


def _merged_chars(ws, sheet, first: str, last: str) -> float:
    """一段合并区横跨多少"字符宽"——估行高要用它，不能用单列的宽度。"""
    start, end = ord(first), ord(last)
    total = 0.0
    for code in range(start, end + 1):
        column = chr(code)
        total += (ws.column_dimensions[column].width if column in COL_WIDTHS[sheet]
                  else DEFAULT_COL_CHARS)
    return total


def _write_plan_sheet(ws, model: ReportModel):
    """「测试方案」：标题 + 测试环境 + 测试大纲及要点。"""
    widths = COL_WIDTHS[SHEET_PLAN]
    for column, width in widths.items():
        ws.column_dimensions[column].width = width

    _merge_and_put(ws, "A1:J1", "测试方案", size=TITLE_SIZE, bold=True, fill=FILL_TITLE)
    ws.row_dimensions[1].height = 86.25

    # 测试环境：A2:C17 是一整块竖排标签，右边分「服务器 / 客户端」两栏
    _merge_and_put(ws, "A2:C17", "测试环境：", fill=FILL_HEADER)
    _merge_and_put(ws, "D2:F2", "服务器")
    _merge_and_put(ws, "G2:J2", "客户端")

    server_rows, client_rows = _environment_rows(model)
    server_width = _merged_chars(ws, SHEET_PLAN, "D", "F")
    client_width = _merged_chars(ws, SHEET_PLAN, "G", "J")
    for offset, (server, client) in enumerate(zip(server_rows, client_rows)):
        row = 3 + offset
        _merge_and_put(ws, f"D{row}:F{row}", server, align="left")
        _merge_and_put(ws, f"G{row}:J{row}", client, align="left")
        ws.row_dimensions[row].height = _text_height(
            server, client, width_chars=min(server_width, client_width), minimum=17.25)

    _merge_and_put(ws, "A18:J18", "测试大纲及要点", bold=True)
    outline_width = _merged_chars(ws, SHEET_PLAN, "B", "I")
    for offset, case in enumerate(model.cases):
        row = 19 + offset
        _put(ws, f"A{row}", str(case.index))
        _merge_and_put(ws, f"B{row}:I{row}", case.outline, align="left")
        ws.row_dimensions[row].height = _text_height(
            case.outline, width_chars=outline_width, minimum=17.25)
    return 18 + len(model.cases)


def _environment_rows(model: ReportModel) -> tuple[list[str], list[str]]:
    """环境块的 15 行（对应模版的第 3–17 行）。

    ⚠️ **没有采集到的项要写「本次未采集」，不要编**：模版那一栏是给服务器硬件/数据库
    留的，而本框架跑的是 UI 层自动化，那些信息它压根不产生。留白会让人以为"漏填了"，
    写"未采集"才是实话。
    """
    env = model.environment
    server = [
        "",
        "机器一：",
        "  软件环境：",
        f"    被测系统：{env.get('测试地址') or '本次未采集'}",
        f"    源码版本：{env.get('源码版本') or '本次未采集'}（生成报告时的仓库版本）",
        "    部署形态：本次未采集",
        "  硬件环境：本次未采集",
        "",
        "说明：",
        "  本报告由 jev-ultrafast 自然语言 UI 用例执行后自动生成，",
        "  数据来源为 Allure 原始结果（report/allure-results）。",
        "",
        "",
        "",
        "",
    ]
    client = [
        "",
        "机器一：",
        "  软件环境：",
        f"    操作系统：{env.get('操作系统') or '本次未采集'}",
        "    浏览器：Google Chrome（CDP 远程调试）",
        f"    浏览器视口：{env.get('浏览器视口') or '本次未采集'}",
        f"    决策模型：{env.get('决策模型') or '本次未采集'}",
        "",
        "  执行方式：",
        "    测试框架：pytest + Allure",
        "    驱动方式：Jev 概率决策（模型只选目标，不生成选择器/代码）",
        f"    生成时间：{model.generated_at:%Y-%m-%d %H:%M:%S}",
        "",
        "",
        "",
        "",
    ]
    return server[:15], client[:15]


def _write_list_sheet(ws, model: ReportModel, *, feature_title: str,
                      owners: dict | None = None):
    """「功能测试list及报告」：表头块 + 逐用例一行。"""
    owners = owners or {}
    for column, width in COL_WIDTHS[SHEET_LIST].items():
        ws.column_dimensions[column].width = width

    _merge_and_put(ws, "A1:R1", "测试用例清单", size=TITLE_SIZE, bold=True, fill=FILL_TITLE)
    ws.row_dimensions[1].height = 29.25

    _merge_and_put(ws, "A2:C2", "研发功能：", fill=FILL_HEADER, align="left")
    _merge_and_put(ws, "D2:R2", feature_title, size=NOTE_SIZE, align="left")
    # 模版在 A4:A5 也留了一处空合并（表头块的网格的一部分）。照抄：
    # 少了它，对照校验会报"生成里缺 A4:A5"——那是**版式**不一致，不是内容差异。
    ws.merge_cells("A4:A5")
    _style_cell(ws["A4"])

    _merge_and_put(ws, "B3:C5", "研发负责人", fill=FILL_HEADER, align="left")
    _merge_and_put(ws, "D3:D5", "测试负责人", fill=FILL_HEADER)
    _merge_and_put(ws, "E3:E5", "分支bug数", fill=FILL_HEADER)
    _merge_and_put(ws, "F3:K3", "test bug数", fill=FILL_HEADER)
    _merge_and_put(ws, "L3:Q3", "release bug数", fill=FILL_HEADER)
    _merge_and_put(ws, "R3:R5", "预计发布日期", fill=FILL_HEADER)

    for column, (name, kind) in zip("FGHIJK", DB_HEADERS):
        _put(ws, f"{column}4", name, size=SQL_LABEL_SIZE, bold=True, fill=FILL_HEADER)
        _put(ws, f"{column}5", kind, size=SQL_LABEL_SIZE, bold=True, fill=FILL_HEADER)
    for column, (name, kind) in zip("LMNOPQ", DB_HEADERS):
        _put(ws, f"{column}4", name, size=SQL_LABEL_SIZE, bold=True, fill=FILL_HEADER)
        _put(ws, f"{column}5", kind, size=SQL_LABEL_SIZE, bold=True, fill=FILL_HEADER)

    _merge_and_put(ws, "B6:C6", owners.get("dev") or "", align="left")
    _put(ws, "D6", owners.get("qa") or "")
    _put(ws, "E6", owners.get("branch_bugs") or "")
    _put(ws, "R6", owners.get("release_date") or "")

    _merge_and_put(ws, "A7:A9", "序号", fill=FILL_HEADER)
    _merge_and_put(ws, "B7:B9", "测试项类别", fill=FILL_HEADER)
    _merge_and_put(ws, "C7:C9", "check项", fill=FILL_HEADER)
    _merge_and_put(ws, "D7:D9", "检查点、要求及备注", fill=FILL_HEADER)
    _merge_and_put(ws, "E7:E9", "分支确认", fill=FILL_BRANCH)
    _merge_and_put(ws, "F7:K7", "test确认", fill=FILL_TEST)
    _merge_and_put(ws, "L7:Q7", "release确认", fill=FILL_RELEASE)
    _merge_and_put(ws, "R7:R9", "其他备注", fill=FILL_HEADER)

    for column, (name, kind) in zip("FGHIJK", DB_HEADERS):
        _put(ws, f"{column}8", name, size=SQL_LABEL_SIZE, bold=True, fill=FILL_TEST)
        _put(ws, f"{column}9", kind, size=SQL_LABEL_SIZE, bold=True, fill=FILL_TEST)
    for column, (name, kind) in zip("LMNOPQ", DB_HEADERS):
        _put(ws, f"{column}8", name, size=SQL_LABEL_SIZE, bold=True, fill=FILL_RELEASE)
        _put(ws, f"{column}9", kind, size=SQL_LABEL_SIZE, bold=True, fill=FILL_RELEASE)

    for row in range(7, 10):
        ws.row_dimensions[row].height = 33.0 if row == 7 else 16.5

    # ── 数据行 ────────────────────────────────────────────────────────────
    row = 10
    for case in model.cases:
        _put(ws, f"A{row}", str(case.index))
        _put(ws, f"B{row}", case.category, align="left")
        _put(ws, f"C{row}", case.check, align="left")
        _put(ws, f"D{row}", f"预期：{case.expect}\n实际：{case.actual}" if case.expect
             else f"实际：{case.actual}", size=NOTE_SIZE, align="left")
        for column in CONFIRM_COLS:
            # 确认列：**跳过没跑过的用例**不写 `√`——那是"确认过"的意思。
            if case.status == "skipped":
                _put(ws, f"{column}{row}", "")
            else:
                _put(ws, f"{column}{row}", "√")
        _put(ws, f"R{row}", case.notes, align="left")
        ws.row_dimensions[row].height = _text_height(
            ws[f"D{row}"].value,
            width_chars=_merged_chars(ws, SHEET_LIST, "D", "D"), minimum=60.0)
        row += 1

    _merge_same_category(ws, model)
    return row - 1


def _merge_same_category(ws, model: ReportModel):
    """相邻同类别（测试项类别）的格子纵向合并——模版就是这么排的。

    为什么值得做：E9 的用例天然成组（一条功能点下挂几条 check），不合并的话
    「测试项类别」会在每行重复一遍，读起来像一串噪声。

    只合并**连续的同一类**：中间夹了别的类就不能合并，否则读起来像是那一类也涵盖了
    中间那几行——那是把报告写错了，而且看不出错。
    """
    if not model.cases:
        return
    start = 0
    for index in range(len(model.cases) + 1):
        same = (index < len(model.cases)
                and model.cases[index].category == model.cases[start].category)
        if not same:
            first_row, last_row = 10 + start, 10 + index - 1
            if last_row > first_row:
                ws.merge_cells(f"B{first_row}:B{last_row}")
            start = index


def _write_shots_sheet(ws, model: ReportModel):
    """「测试截图」：一条用例一行，D 列嵌断言截图。"""
    from openpyxl.drawing.image import Image as XLImage

    for column, width in COL_WIDTHS[SHEET_SHOTS].items():
        ws.column_dimensions[column].width = width

    for column, title in zip("ABCD", ["用例编号", "用例名称", "结论 / 实际结果", "截图"]):
        _put(ws, f"{column}1", title, bold=True, border=False, plain_font=True)
    ws.row_dimensions[1].height = 22.0
    ws.freeze_panes = "A2"          # 模版就是这样：滚的时候表头不动

    for offset, case in enumerate(model.cases):
        row = 2 + offset
        _put(ws, f"A{row}", case.code, border=False,
             font_name=SHOTS_FONT_NAME, size=SHOTS_FONT_SIZE)
        _put(ws, f"B{row}", case.name, border=False,
             font_name=SHOTS_FONT_NAME, size=SHOTS_FONT_SIZE)
        _put(ws, f"C{row}", case.actual, border=False,
             font_name=SHOTS_FONT_NAME, size=SHOTS_FONT_SIZE)

        text_height = _text_height(case.actual, width_chars=_merged_chars(ws, SHEET_SHOTS, "C", "C"),
                                   minimum=40.0)
        image_height = 0.0
        if case.screenshot:
            image = _embed_screenshot(ws, row, case, XLImage)
            image_height = image[1] if image else 0.0
        else:
            _put(ws, f"D{row}", NO_SCREENSHOT, border=False,
                 font_name=SHOTS_FONT_NAME, size=SHOTS_FONT_SIZE)
        ws.row_dimensions[row].height = max(text_height, image_height, 60.0)


def _embed_screenshot(ws, row: int, case: ReportCase, image_class):
    """把截图按列宽等比缩放后锚在 D 列；返回 (宽px, 高pt)。"""
    import io

    target_width = _column_px(SHOTS_COL_CHARS)
    try:
        image = image_class(io.BytesIO(case.screenshot))
    except Exception:            # noqa: BLE001  图坏了不该让整份报告生成不了
        ws[f"D{row}"] = NO_SCREENSHOT
        _style_cell(ws[f"D{row}"], border=False,
                    font_name=SHOTS_FONT_NAME, size=SHOTS_FONT_SIZE)
        return None
    ratio = (image.height or 1) / (image.width or 1)
    image.width = target_width
    image.height = target_width * ratio
    ws.add_image(image, f"D{row}")
    return image.width, image.height * 0.75      # 像素 → 磅


def write_report(model: ReportModel, path, *, feature_title: str = "", owners: dict | None = None):
    """把报告模型写成 xlsx。返回写好的路径。"""
    from openpyxl import Workbook

    workbook = Workbook()
    plan = workbook.active
    plan.title = SHEET_PLAN
    list_sheet = workbook.create_sheet(SHEET_LIST)
    shots = workbook.create_sheet(SHEET_SHOTS)

    _write_plan_sheet(plan, model)
    _write_list_sheet(list_sheet, model,
                      feature_title=feature_title or _default_feature_title(model),
                      owners=owners)
    _write_shots_sheet(shots, model)

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(target)
    return target


def _default_feature_title(model: ReportModel) -> str:
    module = model.environment.get("研发模块") or ""
    suite = model.environment.get("测试集") or ""
    title = " · ".join(part for part in (suite, module) if part)
    return title or "E9 UI 自动化测试"


# ---------------------------------------------------------------- 入口


@dataclass
class ExportSummary:
    path: Path
    cases: int
    passed: int
    failed: int
    skipped: int
    #: **跑过但没有断言截图**的用例编号。⚠️ 跳过的不算——它压根没跑过，
    #: 永远不会有图，模版自己也有「（无截图）」的行。把它也报出来就是假警报，
    #: 而假警报多了，真缺图那一次就没人看了。
    without_screenshot: list[str]


def export(results_dir, out_path, *, cases_dir=None, feature_title: str = "",
           owners: dict | None = None) -> ExportSummary:
    """一步到位：读结果 → 组装 → 写 xlsx。"""
    model = build_model(results_dir, cases_dir)
    path = write_report(model, out_path, feature_title=feature_title, owners=owners)
    return ExportSummary(
        path=path, cases=len(model.cases), passed=model.passed, failed=model.failed,
        skipped=model.skipped,
        without_screenshot=[case.code for case in model.cases
                            if not case.screenshot and case.status != "skipped"],
    )


def default_output_path(root=None, timestamp: datetime | None = None) -> Path:
    """输出路径：`<root>/公司格式测试报告_<YYYYMMDD_HHMMSS>.xlsx`。

    一次一份、不覆盖（与 `reporting.timestamped_report_dir` 同一条理由：
    覆盖掉旧报告，回归对比就只能靠人记）。
    """
    base = Path(root or (Path(__file__).resolve().parents[2] / "report"))
    stamp = (timestamp or datetime.now()).strftime("%Y%m%d_%H%M%S")
    candidate = base / f"公司格式测试报告_{stamp}.xlsx"
    index = 1
    while candidate.exists():
        candidate = base / f"公司格式测试报告_{stamp}_{index:03d}.xlsx"
        index += 1
    return candidate


def path_for_run(report_dir) -> Path:
    """某一次运行的**公司报告**输出路径：**放进那次运行的 Allure 报告目录里**。

    一次运行 = 一个时间戳目录，两种报告（Allure 站点 + 公司格式 xlsx）都归在它下面，
    验收时打开一个目录就齐了——不必在两个地方按两个时间戳对号。

    ⚠️ 文件名仍带时间戳（`公司格式测试报告_<时间戳>.xlsx`），不是多余的：
    这份 xlsx 一定会被**拷走**（发邮件、进评审附件），离开这个目录之后，
    文件名上的时间戳就是它唯一的"出自哪一次运行"的线索。

    Args:
        report_dir: 那次运行的 Allure 报告目录（`reporting.timestamped_report_dir()` 选出来的）。
    """
    directory = Path(report_dir)
    return directory / f"公司格式测试报告_{directory.name}.xlsx"


# ---------------------------------------------------------------- 对照层：与模版比对
#
# 它回答的是"生成出来的版式与公司模版像不像"，而不是"内容对不对"——内容对不对要看 Allure。
# 缺省就拿**随仓库分发的那份**（`bundled_template_path()`）；调用方可以传别的路径覆盖
# （公司换了模版时先拿新模版验一遍，再决定要不要改布局常量）。


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


def compare_with_template(generated, template=None) -> list[Check]:
    """逐项比对两份工作簿的**版式**（表名、列宽、表头、合并、样式）。

    只比版式，不比内容：内容必然不同（一份是模版的业务数据、一份是本次运行的结果）。
    `template=None` 时用**随仓库分发的那份**（新克隆的工作区也验得起来）。
    """
    from openpyxl import load_workbook

    checks: list[Check] = []
    ours = load_workbook(Path(generated))
    theirs = load_workbook(Path(template) if template else bundled_template_path())

    checks.append(Check("工作表名称与顺序", ours.sheetnames == theirs.sheetnames,
                        f"生成={ours.sheetnames} 模版={theirs.sheetnames}"))

    for sheet in theirs.sheetnames:
        if sheet not in ours.sheetnames:
            checks.append(Check(f"「{sheet}」存在", False, "生成文件里没有这张表"))
            continue
        checks.append(_compare_widths(ours[sheet], theirs[sheet], sheet))
        checks.append(_compare_header_styles(ours[sheet], theirs[sheet], sheet))
        checks.append(_compare_data_styles(ours[sheet], theirs[sheet], sheet))
        checks.append(_compare_merges(ours[sheet], theirs[sheet], sheet))
        checks.append(_compare_freeze(ours[sheet], theirs[sheet], sheet))
    return checks


def _compare_widths(ours, theirs, sheet) -> Check:
    """列宽：模版自定义过的那几列必须一致（其余用默认宽，两边都不管）。"""
    wanted = {}
    for column, dimension in theirs.column_dimensions.items():
        if dimension.width:
            wanted[column] = round(dimension.width, 3)
    actual = {}
    for column, dimension in ours.column_dimensions.items():
        if dimension.width:
            actual[column] = round(dimension.width, 3)
    same = all(actual.get(column) == width for column, width in wanted.items())
    return Check(f"「{sheet}」列宽", same, f"模版={wanted} 生成={actual}")


#: 表头样板格：这些格子承载"这张表是什么"，值与样式都要与模版一致。
HEADER_SAMPLES = {
    SHEET_PLAN: ["A1", "A2", "D2", "G2", "A18"],
    SHEET_LIST: ["A1", "A2", "B3", "D3", "E3", "F3", "L3", "R3", "A7", "B7", "C7", "D7",
                 "E7", "F7", "L7", "R7", "F4", "F5"],
    SHEET_SHOTS: ["A1", "B1", "C1", "D1"],
}


def _compare_header_styles(ours, theirs, sheet) -> Check:
    """表头样板格的值 + 字体 + 底色逐项比。"""
    problems = []
    for coordinate in HEADER_SAMPLES.get(sheet, []):
        mine, reference = ours[coordinate], theirs[coordinate]
        if str(mine.value or "") != str(reference.value or ""):
            problems.append(f"{coordinate} 值: 生成={mine.value!r} 模版={reference.value!r}")
        if (mine.font.name or "") != (reference.font.name or "") and reference.font.name:
            problems.append(f"{coordinate} 字体名: 生成={mine.font.name} 模版={reference.font.name}")
        if mine.font.size != reference.font.size:
            problems.append(f"{coordinate} 字号: 生成={mine.font.size} 模版={reference.font.size}")
        if bool(mine.font.bold) != bool(reference.font.bold):
            problems.append(f"{coordinate} 加粗: 生成={mine.font.bold} 模版={reference.font.bold}")
        if _fill_of(mine) != _fill_of(reference):
            problems.append(f"{coordinate} 底色: 生成={_fill_of(mine)} 模版={_fill_of(reference)}")
    return Check(f"「{sheet}」表头值/字体/底色", not problems, "；".join(problems[:8]))


#: 数据区样板格：**内容必然不同**（一份是模版的业务数据、一份是本次运行的结果），
#: 但样式必须一致——字体名/字号/加粗/底色。
#:
#: 为什么要单独查一遍数据格：表头查得再细也**看不见数据格**，而这里正是出过 bug 的地方
#: （`plain_font` 分支把 `bold=True` 写死，「测试截图」页整页数据格变粗体，
#: 眼睛扫过去只觉得"有点抢眼"，不会觉得是错）。
DATA_SAMPLES = {
    SHEET_PLAN: ["A19", "B19"],
    SHEET_LIST: ["A10", "B10", "C10", "D10", "E10", "R10"],
    SHEET_SHOTS: ["A2", "B2", "C2"],
}


def _compare_data_styles(ours, theirs, sheet) -> Check:
    problems = []
    for coordinate in DATA_SAMPLES.get(sheet, []):
        mine, reference = ours[coordinate], theirs[coordinate]
        for label, got, want in (
            ("字体名", mine.font.name, reference.font.name),
            ("字号", mine.font.size, reference.font.size),
            ("加粗", bool(mine.font.bold), bool(reference.font.bold)),
            ("底色", _fill_of(mine), _fill_of(reference)),
        ):
            if got != want:
                problems.append(f"{coordinate} {label}: 生成={got!r} 模版={want!r}")
    return Check(f"「{sheet}」数据格样式", not problems, "；".join(problems[:8]))


def _fill_of(cell) -> str:
    fill = cell.fill
    if not fill or fill.fill_type != "solid":
        return ""
    color = fill.fgColor.rgb
    return str(color) if color else ""


#: 各表「表头块」到第几行。数据区的合并随用例条数变（还随"同一类别有几条"变），
#: 拿它比对只会得到一堆"必然不同"的噪声；表头块则必须一字不差。
HEADER_ROWS = {SHEET_PLAN: 18, SHEET_LIST: 9, SHEET_SHOTS: 1}


def _compare_merges(ours, theirs, sheet) -> Check:
    """合并区域只比**表头块**——数据区行数不同，必然不一样。"""
    limit = HEADER_ROWS.get(sheet, 9)
    mine = {str(rng) for rng in ours.merged_cells.ranges if rng.min_row <= limit}
    reference = {str(rng) for rng in theirs.merged_cells.ranges if rng.min_row <= limit}
    missing = sorted(reference - mine)
    return Check(f"「{sheet}」表头合并", not missing, f"生成里缺：{missing}" if missing else "")


def _compare_freeze(ours, theirs, sheet) -> Check:
    return Check(f"「{sheet}」冻结窗格", ours.freeze_panes == theirs.freeze_panes,
                 f"生成={ours.freeze_panes} 模版={theirs.freeze_panes}")


def format_checks(checks: list[Check]) -> str:
    """对照结果的可读输出。

    ⚠️ 用 `[通过]/[不一致]` 而**不用 ✅/❌**：本机控制台是 GBK（cp936），
    那两个字符编不出来，会被 `errors="replace"` 一起打成 `?`——
    于是"通过"与"不一致"在屏幕上长得一模一样，校验就等于没做。
    """
    lines = []
    for check in checks:
        mark = "[通过]  " if check.ok else "[不一致]"
        lines.append(f"{mark} {check.name}" + (f" —— {check.detail}" if not check.ok else ""))
    failed = sum(1 for check in checks if not check.ok)
    lines.append(f"\n对照结果：{len(checks) - failed}/{len(checks)} 项一致")
    return "\n".join(lines)
