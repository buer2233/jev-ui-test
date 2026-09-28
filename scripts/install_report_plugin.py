"""命令行入口：把 step-video 插件装进已生成的 Allure 报告。

**必须在 `allure generate` 【之后】跑**——它要改的是生成结果里的 index.html。

    rm -rf report/allure-report
    allure generate report/allure-results -o report/allure-report --clean
    uv run python scripts/install_report_plugin.py report/allure-report

只做版本校验（不装）：

    uv run python scripts/install_report_plugin.py report/allure-report --check-only

逻辑全在 `jev_ultrafast/framework/report_plugin/install.py`——
放那里是为了能被单测直接 import，见那个模块的说明。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jev_ultrafast.framework.report_plugin.install import _main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))