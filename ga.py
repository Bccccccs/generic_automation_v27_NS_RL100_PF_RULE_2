#!/usr/bin/env python3
"""Unified project launcher for common automation commands."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence

from generic_automation.cli import force_param_update
from generic_automation.cli import offline_replay
from generic_automation.cli import run_case
from generic_automation.cli import run_monitor_only
from generic_automation.cli import run_sweep


CommandMain = Callable[[], int | None]


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

    args = parser.parse_args(args_list[:1])
    command_args = args_list[1:]

    _, command_main = COMMANDS[args.command]
    original_argv = None
    try:
        original_argv = sys.argv
        sys.argv = [f"ga.py {args.command}", *command_args]
        result = command_main()
    finally:
        if original_argv is not None:
            sys.argv = original_argv
    return int(result or 0)


if __name__ == "__main__":
    raise SystemExit(main())
