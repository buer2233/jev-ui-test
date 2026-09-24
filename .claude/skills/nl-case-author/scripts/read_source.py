"""把各种格式的功能测试用例读成纯文本，供转换成 YAML 用例时阅读。

支持：.xmind / .docx / .xlsx / .csv / .txt / .md / .json
- .xmind 与 .docx 本质都是 zip，用标准库直接解，不需要装依赖。
- .xlsx 需要 openpyxl，本仓库没装。用 uv 临时带上：
      uv run --with openpyxl python .claude/skills/nl-case-author/scripts/read_source.py 用例.xlsx
  uv 会把包装进临时环境，不污染项目的依赖。

输出【写文件】而不是打屏：本机控制台是 cp936，中文打屏会变问号。
脚本把 UTF-8 文本写到 artifacts/case-source/<文件名>.txt，然后打印该路径。
"""

import argparse
import csv
import io
import json
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree

REPO_ROOT = Path(__file__).resolve().parents[4]
OUT_DIR = REPO_ROOT / "artifacts" / "case-source"

# 本机控制台是 cp936，而这里的报错信息全是中文。直接往 stderr 写会变成乱码，
# 调用方（agent 或另一个 Python 进程）按 UTF-8 解码还会直接抛 UnicodeDecodeError
# —— subprocess 的读取线程会死在那里，然后返回一个【空的 stderr】，
# 让"脚本拒绝了 .xls 并给出替代方案"看起来像"脚本什么都没说"。
# 与仓库里 tests/test_encoding.py 守的是同一类 bug，所以显式切到 UTF-8。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def from_xmind(path):
    """XMind：Zen/2020+ 是 content.json，XMind 8 是 content.xml。"""
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        if "content.json" in names:
            sheets = json.loads(archive.read("content.json").decode("utf-8"))
            lines = []

            def walk(topic, depth):
                if not isinstance(topic, dict):
                    return
                title = (topic.get("title") or "").strip()
                if title:
                    lines.append("  " * depth + "- " + title)
                children = topic.get("children") or {}
                for group in children.values():
                    for child in group if isinstance(group, list) else []:
                        # 摘要有 html 片段，去掉标签只留文字
                        walk(child, depth + 1)

            for sheet in sheets:
                lines.append(f"## 画布：{sheet.get('title') or '(未命名)'}")
                walk(sheet.get("rootTopic") or {}, 0)
            return "\n".join(lines)

        if "content.xml" in names:
            root = ElementTree.fromstring(archive.read("content.xml"))

            # XMind 8 的 content.xml 带【默认命名空间】urn:xmind:xmap:xmlns:content:2.0，
            # 标签名实际是 {urn:...}sheet。用 findall("sheet") 一条都匹配不到，
            # 整个文件会被读成空的——而且不报错。所以按【本地名】匹配。
            # 另外嵌套话题包在 <children><topics> 里，findall 只找直接子节点，
            # 必须显式走进去，否则所有子话题都会丢。
            def local(element):
                return element.tag.rsplit("}", 1)[-1]

            def children(element, name):
                return [child for child in element if local(child) == name]

            def text_of(element, name):
                found = children(element, name)
                return (found[0].text or "").strip() if found else ""

            def topics_in(container):
                """容器里的话题。

                子话题不是直接挂在 topic 下的，中间隔着 <children><topics> 两层
                （XMind 8 的固定结构）。只找直接子节点的话，第一层之后全丢。
                """
                found = children(container, "topic")
                for wrapper in children(container, "children"):
                    for group in children(wrapper, "topics"):
                        found += children(group, "topic")
                return found

            lines = []

            def walk_xml(container, depth):
                for topic in topics_in(container):
                    title = text_of(topic, "title")
                    if title:
                        lines.append("  " * depth + "- " + title)
                    walk_xml(topic, depth + 1)

            for sheet in children(root, "sheet"):
                lines.append(f"## 画布：{text_of(sheet, 'title') or '(未命名)'}")
                walk_xml(sheet, 0)
            return "\n".join(lines)

    raise ValueError(f"{path.name}: 这个 xmind 里既没有 content.json 也没有 content.xml，无法解析")


def from_docx(path):
    """docx 也是 zip：正文在 word/document.xml，段落以 </w:p> 分隔。"""
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml").decode("utf-8")
    # 段落边界换成换行，再去掉所有标签
    xml = re.sub(r"</w:p>", "\n", xml)
    text = re.sub(r"<[^>]+>", "", xml)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def from_xlsx(path):
    try:
        import openpyxl
    except ImportError:
        raise SystemExit(
            "读 .xlsx 需要 openpyxl，本仓库没装。请改用：\n"
            f"  uv run --with openpyxl python {Path(__file__).as_posix()} {path.name}"
        ) from None

    book = openpyxl.load_workbook(path, data_only=True)
    lines = []
    for sheet in book.worksheets:
        lines.append(f"## 工作表：{sheet.title}")
        for row in sheet.iter_rows(values_only=True):
            cells = ["" if c is None else str(c).strip() for c in row]
            if any(cells):
                lines.append(" | ".join(cells).rstrip(" |"))
    return "\n".join(lines)


def from_delimited(path):
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    if path.suffix.lower() != ".csv":
        return raw
    rows = list(csv.reader(io.StringIO(raw)))
    return "\n".join(" | ".join(cell.strip() for cell in row).rstrip(" |") for row in rows if any(row))


def read(path):
    suffix = path.suffix.lower()
    if suffix == ".xmind":
        return from_xmind(path)
    if suffix == ".docx":
        return from_docx(path)
    if suffix == ".xlsx":
        return from_xlsx(path)
    if suffix in {".csv", ".txt", ".md", ".json", ".yaml", ".yml"}:
        return from_delimited(path)
    if suffix == ".xls":
        raise SystemExit(
            "老的 .xls 格式读不了。请在 Excel 里另存为 .xlsx，或先复制成 CSV 再喂进来。"
        )
    raise SystemExit(
        f"暂不支持 {suffix or '(无扩展名)'}。可以改用：纯文本 / Markdown / CSV / xlsx / xmind / docx。\n"
        "如果源是截图或 PDF，请先把里面的用例【手抄】成文本——转写这一步必须由人来做，"
        "不要凭图片猜测断言。"
    )


def main():
    parser = argparse.ArgumentParser(description="把功能测试用例源文件读成 UTF-8 纯文本")
    parser.add_argument("source", help="源文件路径")
    args = parser.parse_args()

    path = Path(args.source).expanduser()
    if not path.is_file():
        raise SystemExit(f"找不到文件：{path}")

    text = read(path)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUT_DIR / (path.stem + ".txt")
    target.write_text(text, encoding="utf-8")
    print(f"已写出 {target}  ({len(text)} 字符)")


if __name__ == "__main__":
    sys.exit(main())