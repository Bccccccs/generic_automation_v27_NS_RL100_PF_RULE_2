"""CLI for organizing CCM outputs into the standard Week4 case structure."""

from __future__ import annotations

import argparse

from flow_control.star_ingest.output_organizer import organize_ccm_outputs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Organize a directory of STAR monitor CSV outputs into a standard Week4 case."
    )
    parser.add_argument(
        "--input-dir",
        required=True,
        help="Input work directory containing actuation_schedule.csv and CCM monitor outputs.",
    )
    parser.add_argument("--output-dir", required=True, help="Target standard case directory.")
    parser.add_argument("--force", action="store_true", help="Allow overwriting generated files in the target case.")
    args = parser.parse_args(argv)

    result = organize_ccm_outputs(
        input_dir=args.input_dir,
        output_dir=args.output_dir,
        overwrite=args.force,
    )
    print(f"case_dir: {result['case_dir']}")
    print(f"timeseries: {result['timeseries_path']}")
    print(f"schedule: {result['schedule_path']}")
    print(f"raw_star: {result['raw_star_dir']}")
    print("next: python scripts/workflow.py check --case-dir " + str(result["case_dir"]) + " --mode ccm")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
