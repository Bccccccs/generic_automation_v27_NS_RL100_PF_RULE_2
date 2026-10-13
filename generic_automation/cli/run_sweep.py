#!/usr/bin/env python3
import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

from generic_automation.core.project_config import load_config, resolve_result_root

_TOP_LEVEL_COLUMNS = {
    "case_name",
    "starccm_path", "template_sim", "num_cores", "pod_key",
    "region_name", "inlet_boundary", "outlet_boundary",
    "wall_boundary", "ground_boundary", "ground_sliding",
    "symmetry_boundary", "rotating_boundary",
    "domain_block_name", "zone1_name", "zone2_name",
    "train_surface_control_name", "prism_mesher_name",
    "max_steps_criterion_name",
}


def main():
    parser = argparse.ArgumentParser(description="Run from a CSV file.")
    parser.add_argument("--cases", default="cases/cases.csv")
    parser.add_argument("--config", default="configs/config.yaml")
    args = parser.parse_args()

    cases_path = Path(args.cases).resolve()
    config_path = Path(args.config).resolve()

    base_cfg = load_config(config_path)
    result_root = resolve_result_root(config_path, base_cfg)
    result_root.mkdir(parents=True, exist_ok=True)

    with cases_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            case_name = row["case_name"]

            cfg = dict(base_cfg)
            cfg["result_root"] = str(result_root)
            cfg["case_name"] = case_name
            cfg["case"] = dict(base_cfg.get("case", {}))

            for key, raw_val in row.items():
                if key == "case_name":
                    continue
                value = _cast(raw_val)
                if key in _TOP_LEVEL_COLUMNS:
                    cfg[key] = value
                else:
                    cfg["case"][key] = value

            cfg_path = result_root / case_name / "case_config.json"
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "generic_automation.cli.run_case",
                    "--config",
                    str(cfg_path),
                ],
                check=True,
            )

    return 0


def _cast(value: str):
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


if __name__ == "__main__":
    raise SystemExit(main())
