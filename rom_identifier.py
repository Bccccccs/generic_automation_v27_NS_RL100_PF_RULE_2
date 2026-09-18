"""向后兼容的 ARX ROM 识别工具导入模块。

为了保证代码重构的向后兼容性，这个模块重新导出
flow_control.rom.identifier 中的所有公开符号。

新代码应直接从 flow_control.rom 或 flow_control.rom.identifier 导入。
"""

from flow_control.rom.identifier import *  # noqa: F401,F403
