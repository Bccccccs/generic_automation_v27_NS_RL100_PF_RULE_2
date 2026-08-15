#!/usr/bin/env python3
"""Run the final acceptance gate for the three week4 B3 STAR cases."""

from __future__ import annotations

from _common import configure_project_root, reexec_with_project_python


def main() -> None:
    reexec_with_project_python()
    configure_project_root()

    from flow_control.star_ingest.b3_acceptance import write_b3_acceptance_report

    report = write_b3_acceptance_report()
    for case in report["cases"]:
        status = "PASS" if case["passed"] else "FAIL"
        print(f"{status} {case['case_id']} segments={case['segments']}")
        for error in case["errors"]:
            print(f"  - {error}")
    print("report: runs/real_star/B3_acceptance_report.json")
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
