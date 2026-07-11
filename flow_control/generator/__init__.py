"""激励计划的生成与验证入口包。

延迟导入（lazy import）设计，避免循环依赖：
  - generate_from_mapping:     从配置字典生成激励计划
  - generate_from_yaml:        从 YAML 文件加载配置并生成
  - resolve_input_dir:         返回 output_dir/input/ 路径
  - validate_actuation_schedule_csv: 验证生成的 CSV 格式
"""

__all__ = [
    "generate_from_mapping",
    "generate_from_yaml",
    "resolve_input_dir",
    "validate_actuation_schedule_csv",
]


def __getattr__(name: str):
    if name in {"generate_from_mapping", "generate_from_yaml", "resolve_input_dir"}:
        from . import schedule_generator

        return getattr(schedule_generator, name)
    if name == "validate_actuation_schedule_csv":
        from . import schedule_validator

        return schedule_validator.validate_actuation_schedule_csv
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
