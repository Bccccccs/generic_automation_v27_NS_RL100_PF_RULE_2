"""Run a flow-control actuation schedule in STAR-CCM+."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from flow_control.adapters.starccm_runner import (
    FlowControlStarCCMRunConfig,
    FlowControlStarCCMRunner,
)
from starccm.control.control_spec import DEFAULT_STARCCM_SPEC


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate a STAR-CCM+ macro from actuation_schedule.csv and launch the simulation."
    )
    parser.add_argument("--schedule", required=True, help="Path to actuation_schedule.csv.")
    parser.add_argument("--sim", required=True, help="Input STAR-CCM+ .sim file.")
    parser.add_argument("--out", required=True, help="Output directory for macro, logs, and results.")
    parser.add_argument(
        "--starccm-path",
        default=os.environ.get("STARCCM_PATH", "starccm+"),
        help="STAR-CCM+ executable path. Defaults to $STARCCM_PATH or starccm+.",
    )
    parser.add_argument("--np", type=int, default=1, help="Number of STAR-CCM+ processes.")
    parser.add_argument("--podkey", default="", help="STAR-CCM+ pod key/license token.")
    parser.add_argument("--region", default="Region", help="Region containing fc_jet_XX boundaries.")
    parser.add_argument(
        "--time-step",
        type=float,
        default=None,
        help="Solver time step inside each control window. Default: one solver step per CSV window.",
    )
    parser.add_argument(
        "--report",
        action="append",
        default=[],
        help="Report name to sample after each window. Can be repeated.",
    )
    parser.add_argument(
        "--non-strict-boundaries",
        action="store_true",
        help="Warn and skip missing jet boundaries instead of failing the run.",
    )
    parser.add_argument(
        "--no-save-result-sim",
        action="store_true",
        help="Do not save flow_control_result.sim at the end.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Generate files and print command without launching CCM.")
    args = parser.parse_args(argv)

    result = FlowControlStarCCMRunner().run(
        FlowControlStarCCMRunConfig(
            schedule_path=Path(args.schedule),
            sim_path=Path(args.sim),
            output_dir=Path(args.out),
            starccm_path=args.starccm_path,
            num_cores=args.np,
            pod_key=args.podkey,
            region_name=args.region,
            time_step=args.time_step,
            report_names=tuple(args.report) or DEFAULT_STARCCM_SPEC.load_report_names,
            strict_boundaries=not args.non_strict_boundaries,
            save_result_sim=not args.no_save_result_sim,
            dry_run=args.dry_run,
        )
    )
    print(f"macro: {result.macro_path}")
    print(f"runtime_plan: {result.runtime_plan_path}")
    print(f"log: {result.log_path}")
    print("command:", " ".join(result.command))
    if result.result_sim_path is not None:
        print(f"result_sim: {result.result_sim_path}")
    if result.returncode is not None:
        print(f"returncode: {result.returncode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
