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