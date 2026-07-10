"""Generate sparse24 schedule/mock cases for ARX ROM training datasets."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from flow_control.config import load_config_with_system_defaults, load_system_config
from flow_control.excitation_patterns import (
    ActuationConfig,
    generate_pattern_table,
    write_pattern_outputs,
)
from flow_control.mock import write_mock_dynamic_case


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate many sparse24 schedule + mock cases for ARX ROM training."
    )
    parser.add_argument(
        "--actuation-config",
        default="configs/actions/pilot_sparse24.yaml",
        help="Base sparse24 actuation YAML.",
    )
    parser.add_argument(
        "--mock-config",
        default="configs/mock_dynamic24x6.yaml",
        help="Base mock dynamic 24x6 YAML.",
    )
    parser.add_argument(
        "--system-config",
        default=None,
        help="Shared system YAML that owns the global random seed. Defaults to configs/system.yaml or FLOW_CONTROL_SYSTEM_CONFIG.",
    )
    parser.add_argument(
        "--out",
        default="runs/arx_test",
        help="Output dataset directory.",
    )
    parser.add_argument("--count", type=int, default=100, help="Number of cases to generate.")
    parser.add_argument(
        "--start-seed",
        type=int,
        default=None,
        help="First global random seed. Defaults to system.random_seed from the shared system config.",
    )
    parser.add_argument(
        "--case-prefix",
        default="sparse24_seed",
        help="Prefix for per-case directories.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite an existing output directory.",
    )
    args = parser.parse_args(argv)

    records = generate_arx_sparse24_dataset(
        actuation_config_path=args.actuation_config,
        mock_config_path=args.mock_config,
        system_config_path=args.system_config,
        output_dir=args.out,
        count=args.count,
        start_seed=args.start_seed,
        case_prefix=args.case_prefix,
        overwrite=args.overwrite,
    )

    print(f"generated ARX dataset cases: {len(records)}")
    print(f"dataset directory: {Path(args.out)}")
    print(f"index csv: {Path(args.out) / 'index.csv'}")
    print(f"index json: {Path(args.out) / 'index.json'}")
    return 0


def generate_arx_sparse24_dataset(
    *,
    actuation_config_path: str | Path = "configs/actions/pilot_sparse24.yaml",
    mock_config_path: str | Path = "configs/mock_dynamic24x6.yaml",
    system_config_path: str | Path | None = None,
    output_dir: str | Path = "runs/arx_test",
    count: int = 100,
    start_seed: int | None = None,
    case_prefix: str = "sparse24_seed",
    overwrite: bool = False,
) -> list[dict[str, Any]]:
    """Create many sparse24 cases by incrementing one global random seed.

    Each case goes through both steps needed for ARX training data:

    1. generate ``actuation_schedule.csv`` from the sparse24 actuation config;
    2. run ``MockDynamic24x6`` to produce a standard case with ``timeseries.csv``.

    By default, each case uses the same seed value for schedule generation and
    mock dynamics. For example, case 0 uses 20260618 for both, case 1 uses
    20260619 for both, and so on.
    """

    if count <= 0:
        raise ValueError("count must be positive")

    dataset_dir = Path(output_dir)
    _prepare_output_dir(dataset_dir, overwrite=overwrite)

    system_config = load_system_config(system_config_path)
    first_seed = _system_seed(system_config) if start_seed is None else int(start_seed)
    base_actuation = load_config_with_system_defaults(
        actuation_config_path,
        system_config_path=system_config_path,
    )
    base_mock = load_config_with_system_defaults(
        mock_config_path,
        system_config_path=system_config_path,
    )

    records: list[dict[str, Any]] = []
    for idx in range(count):
        global_seed = first_seed + idx
        case_id = f"{case_prefix}_{global_seed}"
        case_dir = dataset_dir / case_id
        schedule_dir = case_dir / "actuation_input"
        mock_config_used = case_dir / "mock_config_used.yaml"

        _generate_schedule_case(
            base_config=base_actuation,
            global_seed=global_seed,
            schedule_dir=schedule_dir,
        )
        _write_mock_config(
            base_config=base_mock,
            global_seed=global_seed,
            path=mock_config_used,
        )
        result = write_mock_dynamic_case(
            schedule_path=schedule_dir / "actuation_schedule.csv",
            config_path=mock_config_used,
            output_dir=case_dir,
        )

        record = {
            "case_index": idx,
            "case_id": case_id,
            "global_seed": global_seed,
            "schedule_seed": global_seed,
            "mock_seed": global_seed,
            "case_dir": str(case_dir),
            "schedule_dir": str(schedule_dir),
            "schedule_path": str(schedule_dir / "actuation_schedule.csv"),
            "case_schedule_path": str(case_dir / "actuation_schedule.csv"),
            "timeseries_path": str(case_dir / "timeseries.csv"),
            "quality_report_path": str(case_dir / "quality_report.json"),
            "mock_config_path": str(mock_config_used),
            "run_success_flag": bool(result["quality_report"].get("run_success_flag", False)),
        }
        records.append(record)
        print(
            f"[{idx + 1:03d}/{count:03d}] {case_id} "
            f"global_seed={global_seed}"
        )

    _write_index(dataset_dir, records)
    return records


def _prepare_output_dir(path: Path, *, overwrite: bool) -> None:
    if path.exists() and any(path.iterdir()) and not overwrite:
        raise FileExistsError(
            f"{path} already exists and is not empty; pass --overwrite to regenerate it"
        )
    if path.exists() and overwrite:
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _generate_schedule_case(
    *,
    base_config: dict[str, Any],
    global_seed: int,
    schedule_dir: Path,
) -> None:
    config_data = deepcopy(base_config)
    config_data.setdefault("system", {})["random_seed"] = int(global_seed)
    config_data.setdefault("actuation", {}).pop("random_seed", None)
    config_data.setdefault("output", {})["run_dir"] = str(schedule_dir)
    config = ActuationConfig.from_mapping(config_data)
    if config.mode != "sparse_random_groups":
        raise ValueError(
            "ARX sparse24 dataset generation requires actuation.mode=sparse_random_groups"
        )
    table, extra, errors = generate_pattern_table(config)
    write_pattern_outputs(config, table, validation_errors=errors, extra=extra)


def _write_mock_config(
    *,
    base_config: dict[str, Any],
    global_seed: int,
    path: Path,
) -> None:
    config = deepcopy(base_config)
    config.setdefault("system", {})["random_seed"] = int(global_seed)
    config.setdefault("mock_dynamic24x6", {}).pop("random_seed", None)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(config, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def _write_index(dataset_dir: Path, records: list[dict[str, Any]]) -> None:
    json_path = dataset_dir / "index.json"
    csv_path = dataset_dir / "index.csv"
    json_path.write_text(
        json.dumps({"cases": records}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    if not records:
        return
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)


def _system_seed(config: dict[str, Any]) -> int:
    system = config.get("system", {})
    if "random_seed" not in system:
        raise ValueError("shared system config must define system.random_seed")
    return int(system["random_seed"])


if __name__ == "__main__":
    raise SystemExit(main())
