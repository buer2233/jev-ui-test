"""Allure 二开插件的资产与安装逻辑。

`step-video/` 是插件本体（跟着框架一起入库，纳入 Git）；
`install.py` 是"生成报告之后补一刀"的安装器，`scripts/install_report_plugin.py`
是它的命令行入口。

把安装逻辑放在框架层而不是脚本里，是为了**能被单测直接 import**——
`scripts/` 不是包，测试里 import 它要靠路径把戏，容易碎。
"""