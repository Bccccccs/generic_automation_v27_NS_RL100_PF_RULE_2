from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import Any

from generic_automation.core.adapter_base import Case
from starccm.runtime.starccm_log_parser import (
    parse_starccm_log_rows,
    parse_starccm_mesh_cell_count,
    parse_starccm_solver_metric_diagnostics,
)
from generic_automation.core.runtime_value_utils import safe_float as _safe_float, safe_int as _safe_int

logger = logging.getLogger(__name__)

_SOLVER_PROFILING_FILENAME = "profiling/solver_profiling.csv"
_SOLVER_PROFILING_SUMMARY_FILENAME = "profiling/solver_profiling_summary.json"
_BACKFILL_FIELDS = (
    "max_residual",
    "continuity_residual",
    "x_momentum_residual",
    "y_momentum_residual",
    "z_momentum_residual",
    "tke_residual",
    "sdr_residual",
    "energy_residual",
    "pressure_solver_iterations",
    "velocity_solver_iterations",
    "tke_solver_iterations",
    "sdr_solver_iterations",
    "energy_solver_iterations",
    "pressure_amg_cycles",
    "velocity_amg_cycles",
    "tke_amg_cycles",
    "sdr_amg_cycles",
    "energy_amg_cycles",
)
_SUMMARY_COVERAGE_FIELDS = (
    "starccm_version",
    "solver_type",
    "physics_models",
    "mesh_cells",
    "time_step",
    "iteration",
    "chunk_wall_time_s",
    "cumulative_wall_time_s",
    "drag",
    "total_force",
    "train_surface_pressure",
    "inlet_mass_flow",
    "outlet_mass_flow",
    "mass_imbalance_abs",
    "mass_imbalance_relative",
    "max_cfl",
    "mean_cfl",
    "max_residual",
    "continuity_residual",
    "x_momentum_residual",
    "y_momentum_residual",
    "z_momentum_residual",
    "tke_residual",
    "sdr_residual",
    "energy_residual",
    "pressure_final_residual",
    "x_momentum_final_residual",
    "y_momentum_final_residual",
    "z_momentum_final_residual",
    "tke_final_residual",
    "sdr_final_residual",
    "energy_final_residual",
    "pressure_current_urf",
    "velocity_current_urf",
    "tke_current_urf",
    "sdr_current_urf",
    "energy_current_urf",
    "pressure_relaxation_scheme",
    "velocity_relaxation_scheme",
    "pressure_current_cycle_label",
    "velocity_current_cycle_label",
    "tke_current_cycle_label",
    "sdr_current_cycle_label",
    "energy_current_cycle_label",
    "pressure_current_tolerance",
    "velocity_current_tolerance",
    "tke_current_tolerance",
    "sdr_current_tolerance",
    "energy_current_tolerance",
    "pressure_current_max_cycles",
    "velocity_current_max_cycles",
    "tke_current_max_cycles",
    "sdr_current_max_cycles",
    "energy_current_max_cycles",
    "pressure_solver_iterations",
    "velocity_solver_iterations",
    "tke_solver_iterations",
    "sdr_solver_iterations",
    "energy_solver_iterations",
    "pressure_equation_time_s",
    "velocity_equation_time_s",
    "tke_equation_time_s",
    "sdr_equation_time_s",
    "energy_equation_time_s",
    "pressure_amg_cycles",
    "velocity_amg_cycles",
    "tke_amg_cycles",
    "sdr_amg_cycles",
    "energy_amg_cycles",
    "pressure_hit_max_cycles",
    "velocity_hit_max_cycles",
    "tke_hit_max_cycles",
    "sdr_hit_max_cycles",
    "energy_hit_max_cycles",
)


_STATUS_NOTE_BY_CODE: dict[str, str] = {
    "available_and_populated": "Field has usable values in the current profiling outputs.",
    "available_as_linear_solver_elapsed_time_proxy": (
        "Field is populated using AMG linear solver elapsed-time deltas, "
        "which are a best-effort proxy rather than exact full equation wall time."
    ),
    "not_applicable_for_steady": "Field is not applicable for steady simulations.",
    "not_applicable_energy_equation_disabled": "Energy equation is disabled in the current case.",
    "not_available_for_current_solver_model": (
        "Current solver/model combination does not expose a usable runtime data source."
    ),
    "candidate_lines_seen_but_unparsed": (
        "STAR log contains candidate solver-metric lines, but current parser rules did not "
        "extract a stable numeric value."
    ),
    "partially_exposed_but_not_mapped_for_iterations": (
        "Some solver metric log lines were matched, but they did not yield the expected "
        "iteration field for this target."
    ),
    "not_observed_yet": "Run is still in progress or the field has not appeared yet.",
    "not_exposed_in_current_star_runtime": "No reliable runtime source has been observed for this field.",
}


def _coverage_count(coverage: dict[str, dict[str, float]], field_name: str) -> int:
    entry = coverage.get(field_name) or {}
    return int(entry.get("nonempty_rows", 0) or 0)


def _any_coverage(coverage: dict[str, dict[str, float]], *field_names: str) -> bool:
    return any(_coverage_count(coverage, name) > 0 for name in field_names)


def _resolve_status(
    is_available: bool,
    is_running: bool,
    *,
    available_label: str = "available_and_populated",
) -> str:
    if is_available:
        return available_label
    if is_running:
        return "not_observed_yet"
    return "not_exposed_in_current_star_runtime"


def finalize_solver_profiling(case: Case, case_dir: Path) -> None:
    csv_path = case_dir / _SOLVER_PROFILING_FILENAME
    if not csv_path.exists():
        return

    rows = _read_solver_profiling_rows(csv_path)
    if not rows:
        _write_or_update_summary(
            case=case,
            case_dir=case_dir,
            rows=[],
            matched_log_rows=0,
            enriched_row_count=0,
            mesh_cells_from_log=None,
            solver_metric_diagnostics={
                "matched_lines": 0,
                "sample_lines": [],
                "rejected_candidate_lines": 0,
                "rejected_candidate_sample_lines": [],
                "rejected_candidate_reasons_sample": [],
            },
        )
        return

    matched_log_rows = 0
    enriched_row_count = 0
    log_path = case_dir / "logs" / "starccm.log"
    mesh_cells_from_log: int | None = None
    solver_metric_diagnostics: dict[str, Any] = {
        "matched_lines": 0,
        "sample_lines": [],
        "rejected_candidate_lines": 0,
        "rejected_candidate_sample_lines": [],
        "rejected_candidate_reasons_sample": [],
    }
    if log_path.exists():
        log_rows = parse_starccm_log_rows(case, log_path)
        mesh_cells_from_log = parse_starccm_mesh_cell_count(log_path)
        solver_metric_diagnostics = parse_starccm_solver_metric_diagnostics(log_path)
        log_rows_by_iteration = {
            int(row["iteration"]): row
            for row in log_rows
            if row.get("iteration") is not None
        }
        for row in rows:
            iteration = _safe_int(row.get("iteration"))
            if iteration is None:
                continue
            log_row = log_rows_by_iteration.get(iteration)
            if log_row is None:
                continue
            matched_log_rows += 1
            if _backfill_row_from_log(row, log_row):
                enriched_row_count += 1
        if mesh_cells_from_log is not None:
            for row in rows:
                if _clean_cell(row.get("mesh_cells")) in ("", None):
                    row["mesh_cells"] = str(mesh_cells_from_log)
        if enriched_row_count > 0:
            _write_solver_profiling_rows(csv_path, rows)
        elif mesh_cells_from_log is not None and any(
            _clean_cell(row.get("mesh_cells")) not in ("", None)
            for row in rows
        ):
            _write_solver_profiling_rows(csv_path, rows)

    _write_or_update_summary(
        case=case,
        case_dir=case_dir,
        rows=rows,
        matched_log_rows=matched_log_rows,
        enriched_row_count=enriched_row_count,
        mesh_cells_from_log=mesh_cells_from_log,
        solver_metric_diagnostics=solver_metric_diagnostics,
    )


def _read_solver_profiling_rows(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return [dict(row) for row in reader]


def _write_solver_profiling_rows(csv_path: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        return
    fieldnames = list(rows[0].keys())
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _backfill_row_from_log(row: dict[str, str], log_row: dict[str, Any]) -> bool:
    changed = False
    for field_name in _BACKFILL_FIELDS:
        current_value = _clean_cell(row.get(field_name))
        if current_value not in ("", None):
            continue
        log_value = log_row.get(field_name)
        if log_value is None:
            continue
        row[field_name] = _stringify_csv_value(log_value)
        changed = True
    return changed


def _write_or_update_summary(
    case: Case,
    case_dir: Path,
    rows: list[dict[str, str]],
    matched_log_rows: int,
    enriched_row_count: int,
    mesh_cells_from_log: int | None,
    solver_metric_diagnostics: dict[str, Any],
) -> None:
    summary_path = case_dir / _SOLVER_PROFILING_SUMMARY_FILENAME
    summary: dict[str, Any] = {}
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to read existing solver profiling summary %s: %s", summary_path, exc)

    row_count = len(rows)
    coverage = {
        field_name: {
            "nonempty_rows": _count_nonempty(rows, field_name),
            "coverage_ratio": (
                _count_nonempty(rows, field_name) / float(row_count)
                if row_count > 0
                else 0.0
            ),
        }
        for field_name in _SUMMARY_COVERAGE_FIELDS
    }

    summary.setdefault("case_id", str(getattr(case, "case_name", "")))
    summary.setdefault("case_name", str(getattr(case, "case_name", "")))
    summary.setdefault("profiling_phase", "phase2_macro_solver_tree")
    summary.setdefault(
        "artifacts",
        {
            "solver_profiling_csv": _SOLVER_PROFILING_FILENAME,
            "solver_profiling_summary_json": _SOLVER_PROFILING_SUMMARY_FILENAME,
        },
    )
    summary["postprocessed_from_starccm_log"] = bool(enriched_row_count > 0)
    summary["rows_in_solver_profiling_csv"] = row_count
    summary["rows_matched_against_starccm_log"] = matched_log_rows
    summary["rows_enriched_from_starccm_log"] = enriched_row_count
    summary["solver_metric_log_matched_lines"] = int(
        solver_metric_diagnostics.get("matched_lines", 0) or 0
    )
    summary["solver_metric_log_sample_lines"] = list(
        solver_metric_diagnostics.get("sample_lines") or []
    )
    summary["solver_metric_log_rejected_candidate_lines"] = int(
        solver_metric_diagnostics.get("rejected_candidate_lines", 0) or 0
    )
    summary["solver_metric_log_rejected_candidate_sample_lines"] = list(
        solver_metric_diagnostics.get("rejected_candidate_sample_lines") or []
    )
    summary["solver_metric_log_rejected_candidate_reasons_sample"] = list(
        solver_metric_diagnostics.get("rejected_candidate_reasons_sample") or []
    )
    if mesh_cells_from_log is not None and _safe_int(summary.get("mesh_cells")) is None:
        summary["mesh_cells"] = mesh_cells_from_log
    summary["field_coverage"] = coverage
    summary["field_status"] = _build_field_status_map(
        case=case,
        summary=summary,
        coverage=coverage,
        row_count=row_count,
    )
    summary["field_status_notes"] = _build_field_status_notes(
        field_status=summary["field_status"],
    )
    summary["artifacts_present"] = {
        "solver_profiling_csv": (case_dir / _SOLVER_PROFILING_FILENAME).exists(),
        "solver_profiling_summary_json": True,
        "starccm_log": (case_dir / "logs" / "starccm.log").exists(),
        "result_reports_csv": (case_dir / "result_reports.csv").exists(),
    }

    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _count_nonempty(rows: list[dict[str, str]], field_name: str) -> int:
    return sum(
        1
        for row in rows
        if _clean_cell(row.get(field_name)) not in ("", None)
    )


def _build_field_status_map(
    case: Case,
    summary: dict[str, Any],
    coverage: dict[str, dict[str, float]],
    row_count: int,
) -> dict[str, str]:
    run_status = str(summary.get("run_status", "") or "").strip().lower()
    simulation_type = str(getattr(case, "simulation_type", "") or "").strip().lower()
    energy_equation = bool(getattr(case, "energy_equation", False))
    cfl_status = str(summary.get("cfl_status", "") or "").strip().lower()
    is_running = run_status == "running"
    is_running_with_rows = is_running and row_count > 0

    statuses: dict[str, str] = {}

    statuses["mesh_cells"] = _resolve_status(
        _safe_int(summary.get("mesh_cells")) is not None or _coverage_count(coverage, "mesh_cells") > 0,
        is_running,
    )

    if simulation_type == "steady":
        statuses["physical_time"] = "not_applicable_for_steady"
    else:
        statuses["physical_time"] = _resolve_status(
            _coverage_count(coverage, "physical_time") > 0, is_running
        )

    io_breakdown_s = summary.get("io_breakdown_s")
    known_io_total = _safe_float(
        (io_breakdown_s if isinstance(io_breakdown_s, dict) else {}).get("known_total_io_s")
    )
    statuses["io_breakdown"] = (
        "not_observed_yet"
        if is_running
        else _resolve_status(known_io_total is not None, is_running=False)
    )

    if _any_coverage(
        coverage,
        "pressure_solver_iterations",
        "velocity_solver_iterations",
        "tke_solver_iterations",
        "sdr_solver_iterations",
        "energy_solver_iterations",
    ):
        statuses["solver_iterations"] = "available_and_populated"
    else:
        rejected = int(summary.get("solver_metric_log_rejected_candidate_lines", 0) or 0)
        matched = int(summary.get("solver_metric_log_matched_lines", 0) or 0)
        if rejected > 0:
            statuses["solver_iterations"] = "candidate_lines_seen_but_unparsed"
        elif matched > 0:
            statuses["solver_iterations"] = "partially_exposed_but_not_mapped_for_iterations"
        else:
            statuses["solver_iterations"] = _resolve_status(False, is_running_with_rows)

    rejected = int(summary.get("solver_metric_log_rejected_candidate_lines", 0) or 0)
    if _coverage_count(coverage, "pressure_solver_iterations") > 0:
        statuses["pressure_solver_iterations"] = "available_and_populated"
    elif rejected > 0:
        statuses["pressure_solver_iterations"] = "candidate_lines_seen_but_unparsed"
    else:
        statuses["pressure_solver_iterations"] = _resolve_status(False, is_running_with_rows)

    statuses["equation_time"] = _resolve_status(
        _any_coverage(
            coverage,
            "pressure_equation_time_s",
            "velocity_equation_time_s",
            "tke_equation_time_s",
            "sdr_equation_time_s",
            "energy_equation_time_s",
        ),
        is_running_with_rows,
        available_label="available_as_linear_solver_elapsed_time_proxy",
    )

    if cfl_status in {"not_available_for_current_solver_model", "field_not_found"}:
        statuses["cfl"] = "not_available_for_current_solver_model"
    else:
        statuses["cfl"] = _resolve_status(
            _any_coverage(coverage, "max_cfl", "mean_cfl"), is_running_with_rows
        )

    if not energy_equation:
        statuses["energy_chain"] = "not_applicable_energy_equation_disabled"
    else:
        statuses["energy_chain"] = _resolve_status(
            _any_coverage(
                coverage,
                "energy_residual",
                "energy_solver_iterations",
                "energy_amg_cycles",
                "energy_equation_time_s",
            ),
            is_running_with_rows,
        )

    return statuses


def _build_field_status_notes(field_status: dict[str, str]) -> dict[str, str]:
    return {
        field_name: _STATUS_NOTE_BY_CODE.get(status, status)
        for field_name, status in field_status.items()
    }


def _clean_cell(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return ""
    if text.lower() in {"null", "none", "nan"}:
        return ""
    return text


def _stringify_csv_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return format(value, ".15g")
    return str(value)
