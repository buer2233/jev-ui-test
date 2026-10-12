"""合造一份 `allure-results` + `cases/`，供公司报告生成器做**离线**测试。

为什么不用真跑一次的结果：真跑要花钱、要 E9 环境、四十多分钟，而且**没法造边界**
（重跑、跳过的用例没有参数、截图缺失）——那些恰恰是最容易写错的地方。

夹具刻意用**手工 JSON** 而不是调 allure API：报告的输入契约就是"一堆 `*-result.json`"，
按这个契约写出来才真的验到了读取层（用 allure API 写反而是在验 allure 自己）。
"""

import json
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

CASES_YAML = """\
version: 1
defaults:
  epic: E9-UI自动化
  feature: 流程提交
cases:
  - id: e9-pass-case
    name: 通过的用例
    report:
      category: 发起·提交
      check: 提交后表单关闭并回到列表
      expect: 点「提 交」后表单表单关闭、回到流程名称列表
    url: "{{ base_url }}/wui/index.html"
    goal: 演示
    expect:
      - type: url_contains
        value: "requestid="
  - id: e9-fail-case
    name: 失败的用例
    report:
      category: 发起·提交
      check: 提交后页面文本出现流程标题
      expect: 提交后页面文本里出现流程标题
    url: "{{ base_url }}/wui/index.html"
    goal: 演示
    expect:
      - type: text_contains
        value: "标题"
  - id: e9-skip-case
    name: 跳过用例
    skip: 环境前提不成立
    report:
      category: 负向·环境前提不成立
      check: 跳过用例的 check 项
      expect: 本环境上该前提不成立，用例不执行
    url: "{{ base_url }}/wui/index.html"
    goal: 演示
    expect:
      - type: url_contains
        value: "none"
  - id: e9-retry-case
    name: 重跑才过的用例
    report:
      category: 发起·提交
      check: 重跑后通过的那条
      expect: 同通过的用例
    url: "{{ base_url }}/wui/index.html"
    goal: 演示
    expect:
      - type: url_contains
        value: "requestid="
  - id: e9-noshot-case
    name: 没有截图的用例
    report:
      category: 权限·归档态
      check: 没有截图的行也要写清楚
      expect: 这一条只用来验"缺图不崩"
    url: "{{ base_url }}/wui/index.html"
    goal: 演示
    expect:
      - type: url_contains
        value: "requestid="
"""


@dataclass
class Fixture:
    results: Path          # allure-results 目录
    cases: Path            # 对应这一批结果的用例目录


def _png(width=200, height=100, color=(200, 220, 240)) -> bytes:
    """一张真 PNG。用 Pillow 生成而不是塞 base64 常量——openpyxl 真要去解码它，
    常量写错会得到"报告打不开那张图"，而单测看不出来。"""
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _label(name, value):
    return {"name": name, "value": value}


def _result(uuid, *, name, status, start, stop, steps=None, attachments=None,
            parameters=None, labels=None, message=None, history=None):
    data = {
        "uuid": uuid, "historyId": history or f"history-{uuid}", "name": name,
        "fullName": "tests.test_nl_cases#test_natural_language_case",
        "status": status, "start": start, "stop": stop,
        "steps": steps or [], "attachments": attachments or [],
        "parameters": parameters or [], "labels": labels or [],
    }
    if message:
        data["statusDetails"] = {"message": message, "trace": ""}
    return data


def _nl_case_parameter(case: dict) -> dict:
    """pytest 参数化时记下来的那份**收集期用例 dict**（值是 Python repr，不是 JSON）。"""
    return {"name": "nl_case", "value": repr({**case, "_base_url": "http://e9.example:8080"})}


def _attachment(name, kind, path: Path, payload: bytes):
    path.write_bytes(payload)
    return {"name": name, "source": path.name, "type": kind}


def write_results(root) -> Fixture:
    """写出夹具，返回 (结果目录, 用例目录)。"""
    root = Path(root)
    results = root / "allure-results"
    results.mkdir(parents=True, exist_ok=True)
    cases_dir = root / "cases"
    cases_dir.mkdir(parents=True, exist_ok=True)
    (cases_dir / "fixture.yaml").write_text(CASES_YAML, encoding="utf-8")

    shot = _png()
    labels = [_label("epic", "E9-UI自动化"), _label("feature", "流程提交")]

    # ① 通过：断言汇总 + 断言截图 + 执行摘要，三个附件齐全
    verdict = {"ok": True, "mode": "and", "detail": "2/2 条断言通过（要求全部通过）", "results": [
        {"ok": True, "detail": "URL 期望包含 'requestid='，实际为 'http://e9/#/req?requestid=1'",
         "evidence": {}},
        {"ok": True, "detail": "语义断言通过：页面上显示着流程名称列表；noul=0.970，阈值=0.75",
         "evidence": {}},
    ]}
    summary = {"终止状态": "done", "浏览器动作数": 7, "决策请求数": 8, "文本模型调用数": 0,
               "耗时ms": 41000, "录屏档位": 1, "页面稳定等待": True, "等待成步骤门槛ms": 200,
               "视口": [1120, 780], "决策模型": "jev-1.13.0", "备注": ""}
    steps = [{"name": "终局判定", "status": "passed", "attachments": [
        _attachment("断言汇总", "application/json", results / "a1-verdict.json",
                    json.dumps(verdict, ensure_ascii=False).encode()),
        _attachment("断言截图", "image/png", results / "a2-shot.png", shot),
    ]}, {"name": "终局：记录执行结果", "status": "passed", "attachments": [
        _attachment("执行摘要", "application/json", results / "a3-summary.json",
                    json.dumps(summary, ensure_ascii=False).encode()),
    ]}]
    pass_case = {"id": "e9-pass-case", "name": "通过的用例", "feature": "流程提交"}
    (results / "r1-result.json").write_text(json.dumps(_result(
        "r1", name="通过的用例", status="passed", start=1000, stop=42000, steps=steps,
        parameters=[_nl_case_parameter(pass_case)], labels=labels + [_label("story", "通过的用例")],
    ), ensure_ascii=False), encoding="utf-8")

    # ② 失败：断言汇总里有一条不成立
    failed_verdict = {"ok": False, "mode": "and", "detail": "1/2 条断言通过（要求全部通过）", "results": [
        {"ok": True, "detail": "URL 期望包含 'requestid='，实际为 'http://e9/#/req'", "evidence": {}},
        {"ok": False, "detail": "页面文本期望出现 '标题' 至少 1 次，实际 0 次", "evidence": {}},
    ]}
    fail_case = {"id": "e9-fail-case", "name": "失败的用例", "feature": "流程提交"}
    (results / "r2-result.json").write_text(json.dumps(_result(
        "r2", name="失败的用例", status="failed", start=100000, stop=150000,
        steps=[{"name": "终局判定", "status": "failed", "attachments": [
            _attachment("断言汇总", "application/json", results / "b1-verdict.json",
                        json.dumps(failed_verdict, ensure_ascii=False).encode()),
            _attachment("断言截图", "image/png", results / "b2-shot.png", shot),
        ]}],
        parameters=[_nl_case_parameter(fail_case)], labels=labels,
        message="1/2 条断言通过（要求全部通过）",
    ), ensure_ascii=False), encoding="utf-8")

    # ③ 跳过：**没有参数、没有标签**（它压根没进过 runner），只剩参数化的名字
    (results / "r3-result.json").write_text(json.dumps(_result(
        "r3", name="test_natural_language_case[e9-skip-case]", status="skipped",
        start=200000, stop=200001, message="环境前提不成立，跳过",
    ), ensure_ascii=False), encoding="utf-8")

    # ④ 重跑：同一 historyId 两份结果，第一份失败、第二份通过
    retry_case = {"id": "e9-retry-case", "name": "重跑才过的用例", "feature": "流程提交"}
    (results / "r4a-result.json").write_text(json.dumps(_result(
        "r4a", name="重跑才过的用例", status="broken", start=300000, stop=301000,
        history="history-retry", parameters=[_nl_case_parameter(retry_case)], labels=labels,
        message="Model connection failed",
    ), ensure_ascii=False), encoding="utf-8")
    (results / "r4b-result.json").write_text(json.dumps(_result(
        "r4b", name="重跑才过的用例", status="passed", start=310000, stop=350000,
        history="history-retry", steps=steps, parameters=[_nl_case_parameter(retry_case)],
        labels=labels,
    ), ensure_ascii=False), encoding="utf-8")

    # ⑤ 没有截图（旧版 runner 的结果就是这样）→ 应当写「（无截图）」而不是崩
    noshot_case = {"id": "e9-noshot-case", "name": "没有截图的用例", "feature": "流程提交"}
    (results / "r5-result.json").write_text(json.dumps(_result(
        "r5", name="没有截图的用例", status="passed", start=400000, stop=410000,
        steps=[{"name": "终局判定", "status": "passed", "attachments": [
            _attachment("断言截图缺失", "text/plain", results / "c1-note.txt",
                        "本条用例没有留下断言截图".encode()),
        ]}],
        parameters=[_nl_case_parameter(noshot_case)], labels=labels,
    ), ensure_ascii=False), encoding="utf-8")

    return Fixture(results=results, cases=cases_dir)