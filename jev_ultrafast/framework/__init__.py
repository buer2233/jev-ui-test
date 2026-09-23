"""自然语言驱动的 UI 自动化测试框架层。

库本体（agent / browser / snapshot.js）保持通用；本层负责
用例加载、断言判定、Allure 报告与 E9 登录接入。
"""

from .config import DEFAULT_AI_THRESHOLD
from .loader import CaseError, load_cases
from .runner import run_case

__all__ = ["DEFAULT_AI_THRESHOLD", "CaseError", "load_cases", "run_case"]