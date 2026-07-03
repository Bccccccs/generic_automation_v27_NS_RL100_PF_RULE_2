"""Run the existing actuation workflow into the B4 mock dynamic 24x6 plant."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flow_control.mock import write_mock_dynamic_case
from flow_control.workflow import run_actuation_to_mock


def main() -> None:
    parser = argparse.ArgumentParser(description="Run actuation schedule generation into MockDynamic24x6.")
    parser.add_argument(
        "--schedule",
        help="Existing actuation_schedule.csv. If omitted, --actuation-config is generated first.",
    )
    parser.add_argument(
        "--actuation-config",
        default="configs/pilot_sparse24.yaml",
        help="Existing actuation YAML config used to generate actuation_schedule.csv.",
    )
    parser.add_argument(
        "--schedule-out",
        default="runs/mock_dynamic24x6_demo/actuation_input",
        help="Directory for generated actuation_schedule.csv when --schedule is omitted.",
    )
    parser.add_argument(
        "--config",
        default="configs/mock_dynamic24x6.yaml",
        help="Path to mock dynamic 24x6 YAML config.",
    )
    parser.add_argument(
        "--out",
        default="runs/mock_dynamic24x6_demo",
        help="Output run directory.",
    )
    args = parser.parse_args()

    if args.schedule:
        schedule_path = Path(args.schedule)
        result = write_mock_dynamic_case(
            schedule_path=schedule_path,
            config_path=Path(args.config),
            output_dir=Path(args.out),
        )
    else:
        result = run_actuation_to_mock(
            actuation_config_path=Path(args.actuation_config),
            mock_config_path=Path(args.config),
            schedule_output_dir=Path(args.schedule_out),
            mock_output_dir=Path(args.out),
        )
        schedule_path = Path(args.schedule_out) / "actuation_schedule.csv"
    print(f"actuation_schedule.csv: {schedule_path}")
    print(f"MockDynamic24x6 complete: {result['run_dir']}")
    print(f"timeseries.csv: {result['files']['timeseries']}")
    print(f"quality_report.json: {result['files']['quality_report']}")


if __name__ == "__main__":
    main()
