#!/usr/bin/env python3
"""统一的项目启动器 —— 将 generic_automation 子命令聚合到一个入口。

使用方法：
    python ga.py <command> [command arguments]

支持的命令：
    case          运行一个 STAR-CCM+ 自动化 case
    sweep         运行一个 CSV 驱动的参数扫描
    monitor       仅运行外部监视器
    replay        从已有性能数据回放 RL 决策
    force-update  写入一个手动参数更新
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence

from generic_automation.cli import force_param_update
from generic_automation.cli import offline_replay
from generic_automation.cli import run_case
from generic_automation.cli import run_monitor_only
from generic_automation.cli import run_sweep


# 命令主函数类型
CommandMain = Callable[[], int | None]

# 注册的命令字典：{命令名: (描述, 主函数)}
COMMANDS: dict[str, tuple[str, CommandMain]] = {
    "case": ("Run one STAR-CCM+ automation case.", run_case.main),
    "sweep": ("Run a CSV-driven parameter sweep.", run_sweep.main),
    "monitor": ("Run the external monitor only.", run_monitor_only.main),
    "replay": ("Replay RL decisions from existing profiling data.", offline_replay.main),
    "force-update": ("Write one manual parameter update.", force_param_update.main),
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Unified launcher for generic_automation commands.",
        usage="python ga.py <command> [command arguments]",
    )
    parser.add_argument("command", choices=COMMANDS)

    import sys

    args_list = list(sys.argv[1:] if argv is None else argv)
    if not args_list or args_list[0] in {"-h", "--help"}:
        parser.print_help()
        return 0

    # 解析命令名，其余参数透传给子命令
    args = parser.parse_args(args_list[:1])
    command_args = args_list[1:]

    _, command_main = COMMANDS[args.command]
    original_argv = None
    try:
        original_argv = sys.argv
        # 模拟子命令的 argv，方便子命令使用 argparse
        sys.argv = [f"ga.py {args.command}", *command_args]
        result = command_main()
    finally:
        if original_argv is not None:
            sys.argv = original_argv
    return int(result or 0)


if __name__ == "__main__":
    raise SystemExit(main())
