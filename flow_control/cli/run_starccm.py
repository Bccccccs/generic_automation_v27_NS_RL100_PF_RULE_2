"""/run_starccm CLI：将激励计划在 STAR-CCM+ 中执行并打包结果。

工作流程：
  1. 通过 --schedule 传入已生成的 actuation_schedule.csv，
     或通过 --actuation-config 实时生成
  2. 连接 STAR-CCM+ 运行仿真
  3. 提取结果打包为标准 case 目录
  4. 执行质量检查

数据流：
  schedule CSV → FlowControlStarCCMRunner → STAR-CCM+ macro → timeseries
    → package_ccm_run_case → 标准 case 目录 + quality_report
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import os
import re
from pathlib import Path

import yaml
from flow_control.adapters.starccm_runner import (
    FlowControlStarCCMRunConfig,
    FlowControlStarCCMRunner,
)
from flow_control.generator import generate_from_yaml
from flow_control.sampling import resolve_schedule_time_step
from flow_control.star_ingest import package_ccm_run_case
from flow_control.star_ingest.case_data_loader import current_git_commit
from starccm.control.control_spec import DEFAULT_STARCCM_SPEC


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate a STAR-CCM+ macro from actuation_schedule.csv and launch the simulation."
    )
    # --- 激励来源：已有 CSV 或实时生成 ---
    # 这两者互斥，用户必须指定其一
    schedule_source = parser.add_mutually_exclusive_group(required=True)
    schedule_source.add_argument("--schedule", help="Existing actuation_schedule.csv path.")
    schedule_source.add_argument(
        "--actuation-config",
        help="Actuation YAML to generate into <out>/input before starting STAR-CCM+.",
    )
    # --- STAR-CCM+ 参数 ---
    parser.add_argument("--sim", required=True, help="Input STAR-CCM+ .sim file.")
    parser.add_argument("--out", required=True, help="Output directory for macro, logs, and results.")
    parser.add_argument(
        "--manifest-template",
        default="configs/week4/case_manifest_template.yaml",
        help="Manifest template to prefill before STAR; use an empty string to disable.",
    )
    parser.add_argument(
        "--starccm-path",
        default=os.environ.get("STARCCM_PATH", "starccm+"),
        help="STAR-CCM+ executable path. Defaults to $STARCCM_PATH or starccm+.",
    )
    parser.add_argument("--np", type=int, default=1, help="Number of STAR-CCM+ processes.")
    parser.add_argument("--podkey", default="", help="STAR-CCM+ pod key/license token.")
    parser.add_argument("--region", default="Region", help="Region containing STAR J01..J24 nozzle boundaries.")
    parser.add_argument(
        "--time-step",
        type=float,
        default=None,
        help=(
            "Solver time step inside each actuation window. Priority: this argument, "
            "then schedule config_summary, then the template simulation setting."
        ),
    )
    parser.add_argument(
        "--report",
        action="append",
        default=[],
        help="Report name to sample after each window. Can be repeated.",
    )
    # --- 行为控制 ---
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
    parser.add_argument(
        "--execution-mode",
        choices=("run", "dry-run", "package-only", "validate-only"),
        default="run",
        help=(
            "run launches STAR; dry-run only generates the macro; package-only "
            "packages and validates an existing runtime CSV; validate-only keeps "
            "compatibility for validating an already packaged case."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compatibility alias for --execution-mode dry-run.",
    )
    args = parser.parse_args(argv)
    if args.dry_run and args.execution_mode not in {"run", "dry-run"}:
        parser.error("--dry-run cannot be combined with package-only or validate-only")
    execution_mode = "dry-run" if args.dry_run else args.execution_mode

    output_dir = Path(args.out)
    standard_case_dir = _standard_case_dir_for_output(output_dir)
    # 确定激励计划 CSV 的路径：来自已有文件或实时生成
    schedule_path = (
        Path(args.schedule)
        if args.schedule
        else generate_from_yaml(
            args.actuation_config,
            output_dir=output_dir,
        ).output_dir
        / "actuation_schedule.csv"
    )
    solver_time_step, solver_time_step_source = resolve_schedule_time_step(
        schedule_path,
        explicit_time_step=args.time_step,
    )

    # 运行 STAR-CCM+ 仿真，生成宏、运行计划和结果
    result = FlowControlStarCCMRunner().run(
        FlowControlStarCCMRunConfig(
            schedule_path=schedule_path,
            sim_path=Path(args.sim),
            output_dir=output_dir,
            starccm_path=args.starccm_path,
            num_cores=args.np,
            pod_key=args.podkey,
            region_name=args.region,
            manifest_template_path=Path(args.manifest_template) if args.manifest_template else None,
            time_step=solver_time_step,
            report_names=tuple(args.report) or DEFAULT_STARCCM_SPEC.load_report_names,
            strict_boundaries=not args.non_strict_boundaries,
            save_result_sim=not args.no_save_result_sim,
            execution_mode=execution_mode,
            case_dir=standard_case_dir,
        )
    )
    # --- 输出报告 ---
    print(f"macro: {result.macro_path}")
    print(f"runtime_plan: {result.runtime_plan_path}")
    print(f"log: {result.log_path}")
    if standard_case_dir != output_dir:
        print(f"standard_case_dir: {standard_case_dir}")
    # 如果生成了 timeseries，进行 case 打包和质量检查
    if execution_mode == "package-only":
        print(f"standard_timeseries: {standard_case_dir / 'processed' / 'timeseries.csv'}")
        print(f"quality_report: {standard_case_dir / 'quality_report.json'}")
        print(f"figures: {standard_case_dir / 'figures'}")
    elif execution_mode == "validate-only":
        print(f"quality_report: {standard_case_dir / 'quality_report.json'}")
    elif result.timeseries_path is not None:
        print(f"timeseries: {result.timeseries_path}")
        if result.timeseries_path.exists():
            checked_case = package_ccm_run_case(
                ccm_timeseries_path=result.timeseries_path,
                schedule_path=schedule_path,
                case_dir=standard_case_dir,
                manifest=_build_runtime_manifest(
                    args=args,
                    result=result,
                    schedule_path=schedule_path,
                    raw_output_dir=output_dir,
                    solver_time_step=solver_time_step,
                    solver_time_step_source=solver_time_step_source,
                ),
                require_complete_schema=True,
            )
            print(f"standard_timeseries: {checked_case['timeseries_path']}")
            print(f"quality_report: {checked_case['quality_report_path']}")
            print(f"figures: {standard_case_dir / 'figures'}")
            print(f"run_success_flag: {checked_case['quality_report'].get('run_success_flag')}")
    print("command:", " ".join(result.command))
    if result.result_sim_path is not None:
        print(f"result_sim: {result.result_sim_path}")
    if result.returncode is not None:
        print(f"returncode: {result.returncode}")
    return 0


def _standard_case_dir_for_output(output_dir: Path) -> Path:
    """Use the parent directory as the standard case when --out ends in raw_star."""

    if output_dir.name == "raw_star":
        return output_dir.parent
    return output_dir


def _build_runtime_manifest(
    *,
    args: argparse.Namespace,
    result: object,
    schedule_path: Path,
    raw_output_dir: Path,
    solver_time_step: float | None = None,
    solver_time_step_source: str = "template_simulation",
) -> dict[str, object]:
    sim_path = Path(args.sim).expanduser().resolve()
    starccm_path = str(args.starccm_path)
    raw_dir = raw_output_dir.expanduser().resolve()
    case_dir = _standard_case_dir_for_output(raw_output_dir).expanduser().resolve()
    case_type = _infer_case_type(schedule_path)
    snapshot_manifest_path = raw_dir / "case_manifest.yaml"
    base: dict[str, object] = {}
    if snapshot_manifest_path.is_file():
        loaded = yaml.safe_load(snapshot_manifest_path.read_text(encoding="utf-8")) or {}
        if isinstance(loaded, dict) and loaded.get("manifest_status") == "finalized_from_star_template_snapshot":
            base = loaded
    base_star = dict(base.get("star") or {})
    sim_hash = _sha256_file(sim_path)
    star_metadata: dict[str, object] = {
        **base_star,
        "starccm_path": starccm_path,
        "sim_file": str(sim_path),
        "sim_file_name": sim_path.name,
        "sim_file_hash_sha256": sim_hash,
        "region_names": [str(args.region)],
    }
    star_metadata.setdefault("version", _infer_starccm_version(starccm_path))
    star_metadata.setdefault("version_source", "starccm_executable_path")
    star_metadata.setdefault("geometry_version", "starccm-runtime-template")
    star_metadata.setdefault("mesh_version", f"sim-{sim_hash[:12]}-topology-unavailable")
    runtime_manifest: dict[str, object] = {
        "case_id": case_dir.name,
        "case_type": case_type,
        "description": _case_description(case_dir.name, case_type),
        "git_commit": current_git_commit(),
        "source_product_dir": _manifest_path(case_dir, raw_dir),
        "raw_star_dir": str(raw_dir),
        "processed_timeseries": "processed/timeseries.csv",
        "actuation_schedule": "actuation_schedule.csv",
        "quality_report": "quality_report.json",
        "figures_dir": "figures",
        "source_schedule": "actuation_schedule.csv",
        "raw_csv_count": _count_csv_files(raw_dir),
        "timeseries_csv_count": 1 if result.timeseries_path is not None else 0,
        "status": "complete" if case_type == "no_jet" else "runtime_output_pending_quality_check",
        "starccm_version": star_metadata["version"],
        "geometry_version": star_metadata["geometry_version"],
        "mesh_version": star_metadata["mesh_version"],
        "star": star_metadata,
        "runtime": {
            "num_cores": int(args.np),
            "podkey_set": bool(args.podkey),
            "region": str(args.region),
            "time_step_override": args.time_step,
            "solver_time_step": solver_time_step,
            "solver_time_step_source": solver_time_step_source,
            "strict_boundaries": not bool(args.non_strict_boundaries),
            "save_result_sim": not bool(args.no_save_result_sim),
            "raw_output_dir": str(raw_dir),
            "macro_path": str(result.macro_path),
            "runtime_plan_path": str(result.runtime_plan_path),
            "log_path": str(result.log_path),
            "result_sim_path": str(result.result_sim_path) if result.result_sim_path is not None else "",
            "command": list(result.command),
        },
    }
    # Preserve the preflight template and STAR-inspected surface/report data.
    base.update(runtime_manifest)
    return base


def _case_description(case_id: str, case_type: str) -> str:
    if case_type == "no_jet":
        return f"{case_id} STAR-CCM+ no-jet runtime case"
    return f"{case_id} STAR-CCM+ jet runtime case"


def _infer_case_type(schedule_path: Path) -> str:
    with Path(schedule_path).open("r", encoding="utf-8", newline="") as handle:
        rows = csv.DictReader(handle)
        for row in rows:
            for idx in range(1, 25):
                jet = _float_or_zero(row.get(f"JET_{idx:02d}"))
                massflow = _float_or_zero(row.get(f"cmd_massflow_{idx:02d}"))
                if abs(jet) > 0.5 or abs(massflow) > 1.0e-15:
                    return "jet_on"
    return "no_jet"


def _float_or_zero(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _infer_starccm_version(starccm_path: str) -> str:
    match = re.search(r"(\d{2}\.\d{2}\.\d{3}(?:-[A-Za-z0-9]+)?)", starccm_path)
    return match.group(1) if match else "unknown"


def _manifest_path(case_dir: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(case_dir.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _count_csv_files(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for item in path.rglob("*.csv") if item.is_file())


if __name__ == "__main__":
    raise SystemExit(main())
