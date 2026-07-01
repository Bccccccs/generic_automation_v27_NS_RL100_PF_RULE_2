from __future__ import annotations

import csv
import json
import logging
from pathlib import Path

from adapter_base import Case
from starccm_log_reader import StarCCMLogReader
from starccm_solver_profiling import finalize_solver_profiling

logger = logging.getLogger(__name__)

_ARTIFACT_RELATIVE_PATHS = (
    "run_context.json",
    "result_reports.csv",
    "logs/starccm.log",
    "profiling/profiling_timeseries.jsonl",
    "profiling/profiling_timeseries.csv",
    "profiling/profiling_actions.jsonl",
    "profiling/profiling_summary.json",
    "profiling/solver_profiling.csv",
    "profiling/solver_profiling_summary.json",
    "rl/observations.jsonl",
    "rl/actions.jsonl",
    "rl/action_ack_events.jsonl",
    "rl/param_update_ack.json",
    "rl/rl_observation_stream.jsonl",
    "rl/rl_action_events.jsonl",
    "rl/rl_controller_trace.jsonl",
    "rl/rl_controller_state.json",
    "rl/ai_update_history.jsonl",
    "experiment_summary.json",
)


def parse_reports(case: Case, case_dir: Path) -> dict[str, float | str]:
    return collect_reports(case, case_dir)


def _read_report_csv_rows(report_csv: Path) -> list[dict[str, str]]:
    if not report_csv.exists():
        return []

    with report_csv.open(encoding="utf-8") as handle:
        return [
            {
                "report_name": str(row.get("report_name", "") or "").strip(),
                "value": str(row.get("value", "") or "").strip(),
                "units": str(row.get("units", "") or "").strip(),
            }
            for row in csv.DictReader(handle)
            if str(row.get("report_name", "") or "").strip()
        ]


def _parse_reports_from_rows(case: Case, rows: list[dict[str, str]]) -> dict[str, float | str]:
    results: dict[str, float | str] = {}
    for row in rows:
        name = row["report_name"]
        if case.report_names and name not in case.report_names:
            continue
        try:
            results[name] = float(row["value"])
        except ValueError:
            results[name] = row.get("value", "")
    return results


def _configured_report_order(case: Case) -> list[str]:
    names: list[str] = []
    for candidate in (
        str(getattr(case, "drag_report_name", "") or "").strip(),
        str(getattr(case, "total_report_name", "") or "").strip(),
        str(getattr(case, "train_surface_pressure_report_name", "") or "").strip(),
    ):
        if candidate and candidate not in names:
            names.append(candidate)
    return names


def _default_report_units(case: Case, report_name: str) -> str:
    pressure_name = str(getattr(case, "train_surface_pressure_report_name", "") or "").strip()
    return "Pa" if report_name == pressure_name else "N"


def _format_report_value(value: float | str) -> str:
    if isinstance(value, (int, float)):
        return f"{float(value):.15g}"
    return str(value)


def _extract_iteration_table_reports(case: Case, case_dir: Path) -> dict[str, float]:
    log_path = case_dir / "logs" / "starccm.log"
    if not log_path.exists():
        return {}

    rows = StarCCMLogReader(case).read_all_rows(log_path)
    if not rows:
        return {}

    last_row = rows[-1]
    reports: dict[str, float] = {}

    drag_name = str(getattr(case, "drag_report_name", "") or "").strip()
    total_name = str(getattr(case, "total_report_name", "") or "").strip()
    pressure_name = str(getattr(case, "train_surface_pressure_report_name", "") or "").strip()

    for report_name in (drag_name, total_name, pressure_name):
        if not report_name:
            continue
        value = last_row.get(report_name)
        if isinstance(value, (int, float)):
            reports[report_name] = float(value)
    return reports


def _build_canonical_report_rows(
    case: Case,
    raw_rows: list[dict[str, str]],
    canonical_reports: dict[str, float | str],
) -> list[dict[str, str]]:
    ordered_names: list[str] = []
    rows_by_name: dict[str, dict[str, str]] = {}

    for row in raw_rows:
        name = row["report_name"]
        if name in rows_by_name:
            continue
        ordered_names.append(name)
        rows_by_name[name] = dict(row)

    for name in _configured_report_order(case):
        if name not in ordered_names:
            ordered_names.append(name)
        if name not in rows_by_name:
            rows_by_name[name] = {
                "report_name": name,
                "value": "",
                "units": _default_report_units(case, name),
            }

    for name, value in canonical_reports.items():
        row = rows_by_name.setdefault(
            name,
            {
                "report_name": name,
                "value": "",
                "units": _default_report_units(case, name),
            },
        )
        if name not in ordered_names:
            ordered_names.append(name)
        row["value"] = _format_report_value(value)
        if not row.get("units"):
            row["units"] = _default_report_units(case, name)

    return [rows_by_name[name] for name in ordered_names if name in canonical_reports]


def _write_report_csv_rows(report_csv: Path, rows: list[dict[str, str]]) -> None:
    report_csv.parent.mkdir(parents=True, exist_ok=True)
    with report_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("report_name", "value", "units"))
        writer.writeheader()
        writer.writerows(rows)


def collect_reports(case: Case, case_dir: Path) -> dict[str, float | str]:
    report_csv = case_dir / "result_reports.csv"
    raw_rows = _read_report_csv_rows(report_csv)
    raw_reports = _parse_reports_from_rows(case, raw_rows)
    iteration_reports = _extract_iteration_table_reports(case, case_dir)

    canonical_reports: dict[str, float | str] = dict(raw_reports)
    canonical_reports.update(iteration_reports)

    if canonical_reports:
        canonical_rows = _build_canonical_report_rows(case, raw_rows, canonical_reports)
        _write_report_csv_rows(report_csv, canonical_rows)

    return canonical_reports


def cleanup_intermediate_outputs(case_dir: Path, macro_path: Path) -> None:
    for path in (macro_path,):
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError as exc:
            logger.warning("Failed to remove intermediate file %s: %s", path, exc)


def plot_csv_suffix(case: Case) -> str:
    return f"{case.max_iterations:05d}"


def write_residuals_plot_csv(case: Case, case_dir: Path) -> None:
    log_path = case_dir / "logs" / "starccm.log"
    if not log_path.exists():
        logger.warning("starccm.log not found in %s", case_dir / "logs")
        return

    rows: list[list[str]] = []
    current_block: list[list[str]] = []
    capture = False
    residual_headers = [
        "迭代",
        "Continuity: Residual",
        "Sdr: Residual",
        "Tke: Residual",
        "X-momentum: Residual",
        "Y-momentum: Residual",
        "Z-momentum: Residual",
    ]

    with log_path.open(encoding="utf-8", errors="ignore") as f:
        for raw_line in f:
            line = raw_line.rstrip("\n")
            if (
                "Iteration" in line
                and "Continuity" in line
                and "X-momentum" in line
                and "Sdr" in line
            ):
                capture = True
                current_block = []
                continue

            if not capture:
                continue

            stripped = line.strip()
            if not stripped:
                if current_block:
                    rows = current_block
                capture = False
                continue

            parts = stripped.split()
            if len(parts) < 7:
                if current_block:
                    rows = current_block
                capture = False
                continue

            if not parts[0].replace(".", "", 1).isdigit():
                if current_block:
                    rows = current_block
                capture = False
                continue

            current_block.append(
                [
                    parts[0],
                    parts[1],
                    parts[6],
                    parts[5],
                    parts[2],
                    parts[3],
                    parts[4],
                ]
            )

    if current_block:
        rows = current_block

    if not rows:
        logger.warning("No residual table found in %s", log_path)
        return

    out_path = case_dir / f"Residuals_image_{plot_csv_suffix(case)}.csv"
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(residual_headers)
        writer.writerows(rows)


def collect_result_artifacts(case: Case, case_dir: Path) -> list[str]:
    artifacts: list[str] = []

    residual_csv = case_dir / f"Residuals_image_{plot_csv_suffix(case)}.csv"
    if residual_csv.exists():
        artifacts.append(residual_csv.name)

    for artifact_name in _ARTIFACT_RELATIVE_PATHS:
        artifact_path = case_dir / artifact_name
        if artifact_path.exists():
            artifacts.append(artifact_name)

    return artifacts


def build_semantic_report_summary(
    case: Case,
    reports: dict[str, float | str],
) -> dict[str, float | str | None]:
    drag_name = str(getattr(case, "drag_report_name", "") or "").strip()
    total_name = str(getattr(case, "total_report_name", "") or "").strip()
    pressure_name = str(getattr(case, "train_surface_pressure_report_name", "") or "").strip()
    return {
        "drag_report_name": drag_name or None,
        "drag_value": reports.get(drag_name) if drag_name else None,
        "total_force_report_name": total_name or None,
        "total_force_value": reports.get(total_name) if total_name else None,
        "train_surface_pressure_report_name": pressure_name or None,
        "train_surface_pressure_value": reports.get(pressure_name) if pressure_name else None,
        "drag_value_source": "result_reports.csv" if drag_name and drag_name in reports else None,
        "total_force_value_source": (
            "result_reports.csv" if total_name and total_name in reports else None
        ),
        "pressure_value_source": (
            "result_reports.csv" if pressure_name and pressure_name in reports else None
        ),
    }


def write_output(case: Case, case_dir: Path, reports: dict[str, float | str]) -> None:
    finalize_solver_profiling(case, case_dir)
    payload = {
        "status": "ok",
        "adapter": "starccm",
        "case": case.__dict__,
        "reports": reports,
        "semantic_reports": build_semantic_report_summary(case, reports),
        "artifacts": collect_result_artifacts(case, case_dir),
    }
    (case_dir / "result.json").write_text(
        json.dumps(payload, indent=2, default=str),
        encoding="utf-8",
    )
