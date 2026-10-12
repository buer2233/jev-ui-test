"""给 nl-report-export 的 evals 造一份**合成**的 allure-results。

写进 `artifacts/skill-evals/nl-report-export/`（已 gitignore）——skill 的 evals
要能在**新克隆的工作区**里跑，所以夹具是**运行期写出来**的，不是入库的二进制。

刻意只造三态（通过 / 失败 / 跳过）+ 有图/无图各一：
eval 要验的是"报告把这三态都写对、图该嵌的嵌、该写占位写占位"，
多造几种只会让失败信息变长。
"""

import json
from io import BytesIO
from pathlib import Path

CASES_YAML = """\
version: 1
defaults:
  epic: E9-UI自动化
  feature: 流程提交
cases:
  - id: probe-pass
    name: 探针·通过的用例
    report:
      category: 发起·提交
      check: 提交后表单关闭并回到列表
      expect: 点「提 交」后表单关闭、回到流程名称列表
    url: "{{ base_url }}/wui/index.html"
    goal: 演示
    expect:
      - type: url_contains
        value: "requestid="
  - id: probe-fail
    name: 探针·失败的用例
    report:
      category: 发起·提交
      check: 提交后页面文本出现流程标题
      expect: 提交后页面文本里出现流程标题
    url: "{{ base_url }}/wui/index.html"
    goal: 演示
    expect:
      - type: text_contains
        value: "标题"
  - id: probe-skip
    name: 探针·跳过的用例
    skip: eval 夹具里的跳过用例
    report:
      category: 负向·环境前提不成立
      check: 跳过用例也要在报告里占一行
      expect: 本环境下该前提不成立，用例不执行
    url: "{{ base_url }}/wui/index.html"
    goal: 演示
    expect:
      - type: url_contains
        value: "none"
  - id: probe-noshot
    name: 探针·跑过但没有截图的用例
    report:
      category: 权限·归档态
      check: 跑过却没有断言截图时导出要喊出来
      expect: 这一条只用来验"缺图必须响亮"
    url: "{{ base_url }}/wui/index.html"
    goal: 演示
    expect:
      - type: url_contains
        value: "requestid="
"""


def _png(width=200, height=100) -> bytes:
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (width, height), (210, 225, 240)).save(buffer, format="PNG")
    return buffer.getvalue()


def _write(results: Path, name, payload: bytes) -> dict:
    (results / name).write_bytes(payload)
    return {"name": name, "source": name, "type": "application/json"}


def write_results(root) -> Path:
    """写出合成结果目录，返回它的路径。"""
    root = Path(root)
    results = root / "allure-results"
    results.mkdir(parents=True, exist_ok=True)
    cases_dir = root / "cases"
    cases_dir.mkdir(parents=True, exist_ok=True)
    (cases_dir / "probe.yaml").write_text(CASES_YAML, encoding="utf-8")

    shot = {"name": "断言截图", "source": "shot.png", "type": "image/png"}
    (results / "shot.png").write_bytes(_png())
    verdict = {"ok": True, "mode": "and", "detail": "1/1 条断言通过（要求全部通过）", "results": [
        {"ok": True, "detail": "URL 期望包含 'requestid='，实际为 'http://e9/#/req?requestid=9'",
         "evidence": {}},
    ]}
    verdict_file = _write(results, "pass-verdict.json",
                          json.dumps(verdict, ensure_ascii=False).encode())

    def result(uuid, name, status, start, steps=None, case=None, message=None):
        data = {"uuid": uuid, "historyId": uuid, "name": name,
                "fullName": "tests.test_nl_cases#test_natural_language_case",
                "status": status, "start": start, "stop": start + 1000,
                "steps": steps or [], "attachments": [], "labels": [], "parameters": []}
        if case:
            data["parameters"] = [{"name": "nl_case", "value": repr({**case, "_base_url": "http://e9.example:8080"})}]
        if message:
            data["statusDetails"] = {"message": message, "trace": ""}
        (results / f"{uuid}-result.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    result("pass", "探针·通过的用例", "passed", 1000, steps=[
        {"name": "终局判定", "status": "passed",
         "attachments": [dict(verdict_file, name="断言汇总"), shot]},
    ], case={"id": "probe-pass", "name": "探针·通过的用例"})

    result("fail", "探针·失败的用例", "failed", 2000, steps=[
        {"name": "终局判定", "status": "failed", "attachments": [shot]},
    ], case={"id": "probe-fail", "name": "探针·失败的用例"}, message="断言未通过")

    # 跳过的用例**没有 nl_case 参数**（它没进过 runner），只剩参数化的名字——
    # 报告得靠名字尾部的 id 把它认领回来（否则那一行没有类别、没有 check项）。
    result("skip", "test_natural_language_case[probe-skip]", "skipped", 3000,
           message="eval 夹具里的跳过用例")

    # 跑过、但结果里没有「断言截图」——旧版 runner 跑出来的结果就是这样。
    # 它才是"缺图要响亮"那条 eval 的**真正对象**（跳过那条不算：它压根没跑过，
    # 永远不会有图，模版自己也有「（无截图）」的行）。
    result("noshot", "探针·跑过但没有截图的用例", "passed", 4000, steps=[
        {"name": "终局判定", "status": "passed", "attachments": [dict(verdict_file, name="断言汇总")]},
    ], case={"id": "probe-noshot", "name": "探针·跑过但没有截图的用例"})
    return results