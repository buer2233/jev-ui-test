"""框架级可调配置。

所有默认值集中在此，避免散落到各处。日常调参只改这一个文件，
或用同名环境变量在 CI 上临时覆盖。
"""

import os

# 语义断言（expect 的 ai 类型）的默认通过阈值。
#
# 依据（实测，见 docs/一期改造/一期改造可行性分析报告.md §3.7）：
#   真值为真的断言 → noul 约 0.97
#   真值为假的断言 → noul 约 0.01
#   语义模糊/部分为真的断言 → 会落到 0.53
# 0.75 落在 0.53 与 0.97 之间：比 0.6 严格，又给模糊但成立的断言留了余量。
# 调松会放过"做了一半"的结果，调紧会让表述不够精确的断言失败——改之前请先看
# Allure 报告里的概率分布，再决定动哪个方向的哪个数量级。
DEFAULT_AI_THRESHOLD = float(os.environ.get("JEV_NL_THRESHOLD", "0.75"))

# 单用例的内层预算：最多多少个浏览器动作、多少次决策请求。
DEFAULT_MAX_STEPS = int(os.environ.get("JEV_NL_MAX_STEPS", "60"))
DEFAULT_MAX_DECISIONS = int(os.environ.get("JEV_NL_MAX_DECISIONS", "120"))

# 单用例整体超时（秒）。E9 这类重页面首屏 + 表单渲染较慢，故默认给得比库默认宽。
DEFAULT_TIMEOUT_S = int(os.environ.get("JEV_NL_TIMEOUT_S", "300"))

# E9 用例默认入口：/wui/index.html 是 E9 SPA 主壳（实测 200 并进入工作台）。
# 见 docs/一期改造/一期改造实施方案.md §6.1。
E9_ENTRY_PATH = "/wui/index.html"

# 后端引擎应用中心 → 流程引擎 → 路径管理 → 路径设置（流程列表）。
#
# **这不是同一个 SPA**：员工工作台在 /wui/index.html，而后端引擎在 /wui/engine.html
# （实测：把 #/main/backend/index 之类的 hash 打进 /wui/index.html，渲染出来的
# 仍是门户首页，body 文字长度一模一样）。搭流程定义必须走这一个入口。
#
# 页面上的关键约定（2026-09-24 实测）：
#   · 「添 加」按钮的文字是【带 CJK 空格】的"添 加"，不是"添加"——用例里别逐字抠字面量；
#   · 列表右上角的「保存并进入详细设置」是进入流转设置（设计器）的门。
E9_WF_PATH_LIST_PATH = "/wui/engine.html#/workflowengine/path/pathSet/pathList"


# --------------------------- 报告层开关的内置默认 ---------------------------
#
# 这三个是四级优先级的【最后一级】：
#     pytest 参数  >  环境变量 JEV_NL_*  >  config.json  >  本文件
#
# ⚠️ 刻意**不在这里读环境变量**（本文件其余常量都是 `os.environ.get(...)` 的写法）。
# 原因：那些常量是 **import 期求值**的，而 pytest 参数要到 `pytest_configure`
# 之后才可用。也就是说"pytest 传值 > 环境变量"这一条**根本没法**在模块级常量里实现
# ——若在这里读环境变量，pytest 传的值永远覆盖不过它。
# 所以环境变量与 pytest 参数统一交给 `runner.resolve_report_options()`，
# 在**每个用例开始前**解析一次（实施方案 §5.2）。
#
# 上面三个旧常量保持不动：它们是已上线、且被 YAML 逐用例覆盖着的，只新增不改旧。

# 录屏档位：0 不记录 / 1 记录全部 / -1 仅保留失败用例的录屏。
DEFAULT_VIDEO_RECORD = 1

# 页面稳定等待是否启用。
# ⚠️ 关掉它**省不了钱，可能更贵**：它存在的理由是防"渲染中途决策 → StalePage →
# 重观察 → 重决策"，而每轮重决策都是一次【付费】请求；它省下的只是免费的 observe()。
# 这个开关定位是"调试/诊断"，不是"回归省钱"。详见实施方案 §7.5。
DEFAULT_WAIT_STABLE = True

# 等待耗时超过多少毫秒才独立成报告步骤。低于门槛的并进所在执行步骤的参数里
# ——否则报告会被 50 ms 级的输入同步等待塞成流水账。
DEFAULT_WAIT_STEP_MS = 200