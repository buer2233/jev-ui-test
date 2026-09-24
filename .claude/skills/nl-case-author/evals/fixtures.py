"""为 nl-case-author 的 evals 现场生成各格式的源用例样本。

为什么现场生成而不是入库二进制：.xmind / .docx / .xlsx 都是 zip，入库后
diff 不可读、评审看不到内容。同一个功能用例在这里有一份可读的定义，
生成器再把它落成各格式。

内容取自【已实测验证过】的 E9 流程：新建流程 → 点「BYM专用测试」→
签字意见填「同意流程」→ 提交。元素名都是真的，不是编的。
"""

import csv
import json
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

GOAL_STEPS = (
    "1. 打开新建流程页面 /wui/index.html#/main/workflow/add；\n"
    "2. 在流程列表里点击「BYM专用测试」；\n"
    "3. 在「签字意见」里输入「同意流程」；\n"
    "4. 点击「提交」。"
)

# 一份"功能测试用例文档"应有的样子：模块 / 编号 / 名称 / 前置 / 步骤 / 预期 / 优先级
ROWS = [
    ["模块", "用例编号", "用例名称", "前置条件", "操作步骤", "预期结果", "优先级"],
    ["流程管理", "TC-01", "新建流程并提交", "已用 employee1 登录 E9", GOAL_STEPS,
     "提交成功后表单页自动关闭，回到新建流程列表页", "高"],
    ["流程管理", "TC-02", "流程名称按分组展示", "已用 employee1 登录 E9",
     "1. 打开新建流程页面；\n2. 观察流程名称的排布。",
     "各种流程名称按分组列在页面上", "中"],
]

# XMind 的话题树（与 ROWS 同源）
TOPIC = {
    "title": "流程管理",
    "children": [
        {
            "title": "TC-01 新建流程并提交",
            "children": [
                {"title": "前置：已用 employee1 登录 E9"},
                {"title": "步骤：打开新建流程页面"},
                {"title": "步骤：点击「BYM专用测试」"},
                {"title": "步骤：签字意见里输入「同意流程」"},
                {"title": "步骤：点击「提交」"},
                {"title": "预期：表单页自动关闭，回到新建流程列表页"},
                {"title": "优先级：高"},
            ],
        },
        {
            "title": "TC-02 流程名称按分组展示",
            "children": [
                {"title": "步骤：打开新建流程页面"},
                {"title": "预期：流程名称按分组列出"},
                {"title": "优先级：中"},
            ],
        },
    ],
}

TIMESTAMP = "2026-09-23T10:00:00.000+08:00"


def _xmind_zen(topic):
    """XMind Zen / 2020+ 的 content.json 结构。"""
    def node(item):
        children = item.get("children") or []
        return {
            "id": f"topic-{abs(hash(item['title'])) % 10**8}",
            "title": item["title"],
            "children": {"attached": [node(child) for child in children]} if children else {},
        }

    return json.dumps([{
        "id": "sheet-1", "class": "sheet", "title": "画布 1",
        "rootTopic": node(topic),
    }], ensure_ascii=False)


def _xmind8(topic):
    """XMind 8 的 content.xml 结构：<topic><title> 递归。"""
    def node(item):
        children = item.get("children") or []
        inner = "".join(node(child) for child in children)
        return (
            "<topic><title>" + escape(item["title"]) + "</title>"
            + (f"<children><topics type=\"attached\">{inner}</topics></children>" if children else "")
            + "</topic>"
        )

    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="no"?>'
        '<xmap-content xmlns="urn:xmind:xmap:xmlns:content:2.0" version="2.0">'
        f'<sheet id="sheet-1"><title>画布 1</title>{node(topic)}</sheet>'
        "</xmap-content>"
    )


def _docx(rows):
    """最小可用的 docx：word/document.xml 里每行一个段落，单元格用制表符分列。"""
    body = "".join(
        "<w:p>" + "".join(
            f"<w:r><w:t xml:space=\"preserve\">{escape(cell)}</w:t></w:r>"
            for cell in " | ".join(row).split("\n")
        ) + "</w:p>"
        for row in rows
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )


def _write_zip(path, members):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members.items():
            archive.writestr(name, payload.encode("utf-8") if isinstance(payload, str) else payload)


def build(target: Path) -> dict:
    """把所有样本写到 target 目录，返回 {格式: 路径}。"""
    target.mkdir(parents=True, exist_ok=True)
    made = {}

    path = target / "流程管理.xmind"
    _write_zip(path, {
        "content.json": _xmind_zen(TOPIC),
        "metadata.json": json.dumps({"creator": {"name": "eval"}}),
    })
    made["xmind-zen"] = path

    path = target / "流程管理-xmind8.xmind"
    _write_zip(path, {"content.xml": _xmind8(TOPIC)})
    made["xmind8"] = path

    path = target / "流程管理.docx"
    _write_zip(path, {
        "[Content_Types].xml": '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        "word/document.xml": _docx(ROWS),
    })
    made["docx"] = path

    path = target / "流程管理.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        csv.writer(handle).writerows(ROWS)
    made["csv"] = path

    path = target / "流程管理.txt"
    path.write_text(
        "\n\n".join(" | ".join(row) for row in ROWS), encoding="utf-8",
    )
    made["txt"] = path

    # 两个"看起来像用例、其实读不了"的输入：必须给出明确替代方案，而不是崩掉或猜
    (target / "旧格式.xls").write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1not-a-real-xls")
    made["xls"] = target / "旧格式.xls"
    (target / "截图.png").write_bytes(
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    )
    made["png"] = target / "截图.png"

    # xlsx 需要 openpyxl（本仓库没装），单独放，由 evals 用 uv --with 提供
    made["xlsx"] = target / "流程管理.xlsx"
    return made


def build_xlsx(target: Path) -> Path | None:
    """用 openpyxl 写一个真 xlsx；没装就返回 None（由 eval 用 uv --with 重跑）。"""
    try:
        import openpyxl
    except ImportError:
        return None
    book = openpyxl.Workbook()
    sheet = book.worksheets[0] if book.worksheets else book.create_sheet()
    sheet.title = "流程管理"
    for row in ROWS:
        sheet.append(row)
    path = target / "流程管理.xlsx"
    book.save(path)
    return path


if __name__ == "__main__":
    import sys

    out = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    made = build(out)
    xlsx = build_xlsx(out)
    if xlsx:
        made["xlsx"] = xlsx
    for kind, path in made.items():
        print(f"{kind:<10} {path}")