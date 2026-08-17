#!/usr/bin/env python3
"""喷气流程唯一启动入口。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _actions(argv: list[str]) -> int:
    from flow_control.generator.schedule_generator import main

    original = sys.argv
    try:
        sys.argv = ["workflow.py actions", *argv]
        main()
    finally:
        sys.argv = original
    return 0


def _mock(argv: list[str]) -> int:
    from flow_control.cli.run_mock_dynamic24x6 import main

    return main(argv)


def _ccm(argv: list[str]) -> int:
    from flow_control.cli.run_starccm import main

    return main(argv)


def _organize(argv: list[str]) -> int:
    from flow_control.cli.organize_outputs import main

    return main(argv)


def _check(argv: list[str]) -> int:
    from flow_control.cli.check_case import main

    return main(argv)


def _figures(argv: list[str]) -> int:
    from flow_control.cli.generate_figures import main

    return main(argv)


COMMANDS: dict[str, tuple[str, Callable[[list[str]], int]]] = {
    "actions": ("根据 YAML 生成动作表", _actions),
    "mock": ("对动作输入运行 MockDynamic24x6", _mock),
    "ccm": ("生成宏、运行 STAR-CCM+ 或打包/校验已有结果", _ccm),
    "organize": ("将 CCM 输出整理为标准 Case", _organize),
    "check": ("执行质量检查并生成诊断 PNG", _check),
    "figures": ("根据标准 Case 生成 5 张 SVG 汇总图", _figures),
}


def _print_help() -> None:
    print("usage: python scripts/workflow.py <command> [options]\n")
    print("commands:")
    for name, (description, _) in COMMANDS.items():
        print(f"  {name:10s} {description}")
    print("\nUse 'python scripts/workflow.py <command> --help' for command options.")


def _print_unknown_command_error(command: str) -> None:
    print(f"\nerror: unknown command {command!r}", file=sys.stderr)
    if command.startswith("-"):
        print(
            "hint: options must follow a command; for example:\n"
            "  python scripts/workflow.py actions --config <actions.yaml> "
            "--output-dir <case-dir>",
            file=sys.stderr,
        )


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    if not values or values[0] in {"-h", "--help"}:
        _print_help()
        return 0
    command = values.pop(0)
    if command not in COMMANDS:
        _print_help()
        _print_unknown_command_error(command)
        return 2
    return COMMANDS[command][1](values)


if __name__ == "__main__":
    raise SystemExit(main())
