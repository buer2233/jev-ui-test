# nl-case-author 的 evals

两类断言：**读者契约**（`scripts/read_source.py` 说支持什么、拒绝什么）和
**产出契约**（转出来的 YAML 必须过 loader，并守住 AGENTS.md 里写明的规则）。

```bash
uv run python .claude/skills/nl-case-author/evals/run.py
uv run python .claude/skills/nl-case-author/evals/run.py --list
```

除最后一条外全部免费，不调用模型。

## 样本是现场生成的

`evals/fixtures.py` 把同一份功能用例（取自已实测的 E9 流程）落成 xmind / xmind8 /
docx / csv / txt / xlsx，另加两个"读不了"的输入（`.xls`、`.png`）。
不往仓库塞二进制：zip 格式入不了库，diff 不可读。

xlsx 由 `uv run --with openpyxl python fixtures.py` 生成——这正是 SKILL 里给 xlsx 的用法，
所以生成样本这一步顺带也在验它。

## 断言的是什么

| 用例 | 断言 |
|---|---|
| `reads-xmind-zen` / `reads-xmind8-xml` | 两条解析分支都抽得出话题层级（`content.json` / `content.xml`） |
| `reads-docx` / `reads-csv` / `reads-xlsx-with-openpyxl` | 三种格式都抽得出关键字段 |
| `rejects-xls-with-alternative` | `.xls` 被拒，且指明另存为 xlsx 或 CSV |
| `rejects-image-without-guessing` | 图片被拒，**要求人工转写并明确禁止猜测** |
| `output-is-utf8` | 产出按 UTF-8 可解码（本机控制台是 cp936，最易在这里回归） |
| `loader-rejects-unknown-field` / `-duplicate-id` / `-bad-expect` | 三类写法错误都在**加载期**报出来 |
| `repo-cases-follow-project-rules` | 仓库用例无内网字面量、无 ecid、负向对照 `reruns: 0` |
| `converted-fixture-is-loadable` | 模型转出的 YAML 能过 loader（需先真的跑一次 skill，见下） |

最后一条需要模型的产出，脚本替不了，所以它默认 **SKIP** 并说清怎么产出。产出方式：

```bash
# 用 nl-case-author 把 xmind 样本转成 YAML，放到 artifacts/skill-evals/converted/
uv run python .claude/skills/nl-case-author/scripts/read_source.py \
  artifacts/skill-evals/sources/流程管理.xmind
```

## 这套 evals 查出来的真缺陷

它们不是走过场，写的时候连查出三个：

1. **XMind 8 的文件被读成空的，而且不报错。** `content.xml` 带默认命名空间
   （`urn:xmind:xmap:xmlns:content:2.0`），`findall("sheet")` 一条都匹配不到；
   子话题还隔着 `<children><topics>` 两层。已修。
2. **单文件内重复 id 不被检出。** `load_cases` 只查跨文件重复，同一文件里写两条同 id
   会双双加载成功，然后 `--case` 选中两条、Allure 历史混在一起。已在 `load_file` 里补上。
3. **报错信息不是 UTF-8。** 本机控制台是 cp936，脚本的中文报错以 cp936 写进 stderr，
   父进程按 UTF-8 解码时读取线程直接死掉，**返回空 stderr**——
   "脚本拒绝了 .xls 并给了替代方案"看起来像"脚本什么都没说"。已显式切到 UTF-8。

还有一条是 eval 自己踩的：**规则检查必须扫 YAML 原文，不能扫 `load_cases()` 的返回值**。
加载期会把 `{{ base_url }}` 替换成真实地址，拿替换后的 url 查内网 IP，
会把"写法正确"判成"写了内网地址"。这个坑踩了两次。