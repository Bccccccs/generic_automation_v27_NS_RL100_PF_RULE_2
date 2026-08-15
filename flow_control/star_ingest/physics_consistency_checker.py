"""Physics-facing consistency checks for standard STAR ingest cases.

This checker sits one layer above the week-2 CSV hygiene checks.  It verifies
that readable data also makes sense against the declared physical interface:
time alignment, jet-boundary uniqueness, force direction metadata, massflow
tracking, and force accounting.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any

import yaml

from flow_control.case_paths import find_case_timeseries_path
from flow_control.star_ingest.star_export_reader import read_star_export_csv


REGIONAL_FORCE_COLUMNS = ("Fz_S1L", "Fz_S1R", "Fz_S2L", "Fz_S2R", "Fz_S3L", "Fz_S3R")
VEHICLE_FORCE_COLUMNS = ("Fz_Total", "Drag_Total", "Pitch_Moment", "Roll_Moment")
JET_COLUMNS = tuple(f"JET_{idx:02d}" for idx in range(1, 25))
CMD_MASSFLOW_COLUMNS = tuple(f"cmd_massflow_{idx:02d}" for idx in range(1, 25))
ACTUAL_MASSFLOW_COLUMNS = tuple(f"actual_massflow_{idx:02d}" for idx in range(1, 25))

CATEGORY_KEYS = (
    "format_errors",
    "name_or_coordinate_errors",
    "massflow_errors",
    "force_accounting_errors",
    "numerical_instability_warnings",
    "physical_questions_for_haokun",
)

UNKNOWN_MARKERS = ("待浩坤确认", "待确认", "unknown", "todo", "tbd")


def check_case(
    case_dir: str | Path,
    *,
    boundary_mapping_path: str | Path | None = None,
    report_mapping_path: str | Path | None = None,
    zero_tolerance: float = 1e-8,
    time_tolerance: float = 1e-9,
    instability_ratio_threshold: float = 1e3,
) -> dict[str, Any]:
    """Run physics-facing checks for one standard case directory."""

    case_path = Path(case_dir)
    report = _empty_report(case_path)
    timeseries_path = find_case_timeseries_path(case_path)
    timeseries = _read_csv_rows(timeseries_path)
    schedule = _read_csv_rows(case_path / "actuation_schedule.csv")
    manifest = _read_yaml(case_path / "case_manifest.yaml")
    quality_report = _read_json(case_path / "quality_report.json")
    boundary_path = _configured_mapping_path(
        case_path,
        explicit_path=boundary_mapping_path,
        manifest=manifest,
        key="boundary_mapping_file",
        default_path="docs/week3/B02_boundary_mapping.csv",
    )
    report_path = _configured_mapping_path(
        case_path,
        explicit_path=report_mapping_path,
        manifest=manifest,
        key="report_mapping_file",
        default_path="docs/week3/B02_report_mapping.csv",
    )

    if not timeseries:
        _add_issue(report, "format_errors", "error", f"{timeseries_path.relative_to(case_path) if timeseries_path.is_relative_to(case_path) else timeseries_path} is missing or empty")
        return _finalize_report(report)
    if not manifest:
        _add_issue(report, "format_errors", "error", "case_manifest.yaml is missing or empty")

    _check_monotonic_time(report, timeseries, str(timeseries_path.relative_to(case_path) if timeseries_path.is_relative_to(case_path) else timeseries_path), time_tolerance)
    if schedule:
        _check_monotonic_time(report, schedule, "actuation_schedule.csv", time_tolerance)
        _check_time_alignment(report, timeseries, schedule, time_tolerance)
    else:
        _add_issue(report, "format_errors", "error", "actuation_schedule.csv is missing or empty")

    source_files = [
        _resolve_case_relative_path(case_path, p)
        for p in quality_report.get("source_files", [])
        if isinstance(p, str)
    ]
    if not source_files:
        source_dir = manifest.get("source_product_dir")
        if isinstance(source_dir, str):
            source_files = sorted(_resolve_case_relative_path(case_path, source_dir).glob("*.csv"))
    _check_star_csv_alignment(report, source_files, timeseries, time_tolerance)

    boundary_rows = _read_csv_rows(boundary_path, parse_numbers=False)
    if boundary_rows:
        _check_boundary_mapping(report, boundary_rows)
    else:
        _add_issue(report, "format_errors", "error", f"boundary mapping file missing or empty: {boundary_path}")

    mapping_rows = _read_csv_rows(report_path, parse_numbers=False)
    if mapping_rows:
        _check_report_directions(report, manifest, mapping_rows)
    else:
        _add_issue(report, "format_errors", "error", f"report mapping file missing or empty: {report_path}")

    case_type = str(manifest.get("case_type", "")).lower()
    has_jet_case = case_type in {"jet", "jet_on", "with_jet", "active_jet"} or _has_active_jet(timeseries, schedule)
    _check_massflow(report, timeseries, schedule, has_jet_case, zero_tolerance)
    _check_force_accounting(report, timeseries, zero_tolerance, instability_ratio_threshold)

    report["inputs"] = {
        "case_dir": str(case_path),
        "boundary_mapping_path": str(boundary_path),
        "report_mapping_path": str(report_path),
        "zero_tolerance": zero_tolerance,
        "time_tolerance": time_tolerance,
    }
    return _finalize_report(report)


def check_cases(
    case_dirs: list[str | Path],
    *,
    boundary_mapping_path: str | Path | None = None,
    report_mapping_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run checks for multiple cases and aggregate category counts."""

    cases = [
        check_case(
            case_dir,
            boundary_mapping_path=boundary_mapping_path,
            report_mapping_path=report_mapping_path,
        )
        for case_dir in case_dirs
    ]
    aggregate = {key: 0 for key in CATEGORY_KEYS}
    blocking = 0
    for case_report in cases:
        for key in CATEGORY_KEYS:
            aggregate[key] += len(case_report["categories"][key])
        blocking += case_report["summary"]["blocking_issue_count"]
    return {
        "schema_version": "B04_physics_consistency_v1",
        "summary": {
            "num_cases": len(cases),
            "category_counts": aggregate,
            "blocking_issue_count": blocking,
            "run_success_flag": blocking == 0,
        },
        "cases": cases,
    }


def write_report(report: dict[str, Any], output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")


def _resolve_case_relative_path(case_path: Path, value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    case_relative = case_path / path
    if case_relative.exists():
        return case_relative
    return path


def _configured_mapping_path(
    case_path: Path,
    *,
    explicit_path: str | Path | None,
    manifest: dict[str, Any],
    key: str,
    default_path: str,
) -> Path:
    if explicit_path is not None:
        return Path(explicit_path)
    configured = manifest.get(key)
    if configured is None and isinstance(manifest.get("star"), dict):
        configured = manifest["star"].get(key)
    if isinstance(configured, str) and configured:
        return _resolve_case_relative_path(case_path, configured)
    return Path(default_path)


def _empty_report(case_path: Path) -> dict[str, Any]:
    return {
        "schema_version": "B04_physics_consistency_v1",
        "case_id": case_path.name,
        "categories": {key: [] for key in CATEGORY_KEYS},
        "summaries": {},
    }


def _add_issue(report: dict[str, Any], category: str, severity: str, message: str, **extra: Any) -> None:
    issue = {"severity": severity, "message": message}
    issue.update(extra)
    report["categories"][category].append(issue)


def _finalize_report(report: dict[str, Any]) -> dict[str, Any]:
    counts = {key: len(report["categories"][key]) for key in CATEGORY_KEYS}
    blocking = 0
    for key in ("format_errors", "name_or_coordinate_errors", "massflow_errors", "force_accounting_errors"):
        blocking += sum(1 for issue in report["categories"][key] if issue.get("severity") == "error")
    report["summary"] = {
        "category_counts": counts,
        "blocking_issue_count": blocking,
        "run_success_flag": blocking == 0,
    }
    return report


def _read_csv_rows(path: Path, *, parse_numbers: bool = True) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows: list[dict[str, Any]] = []
        for row in reader:
            parsed: dict[str, Any] = {}
            for key, value in row.items():
                if key is None:
                    continue
                stripped = value.strip() if isinstance(value, str) else value
                parsed[key] = _to_float(stripped) if parse_numbers else stripped
            rows.append(parsed)
        return rows


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _to_float(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, str) and value.strip() == "":
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    return number


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _check_monotonic_time(report: dict[str, Any], rows: list[dict[str, Any]], source: str, tol: float) -> None:
    previous: float | None = None
    for index, row in enumerate(rows):
        current = _num(row.get("physical_time"))
        if current is None:
            _add_issue(report, "format_errors", "error", f"{source}: physical_time missing or non-numeric", row=index)
            return
        if previous is not None and current <= previous + tol:
            _add_issue(
                report,
                "format_errors",
                "error",
                f"{source}: physical_time is not strictly increasing",
                row=index,
                previous=previous,
                current=current,
            )
            return
        previous = current


def _check_time_alignment(report: dict[str, Any], timeseries: list[dict[str, Any]], schedule: list[dict[str, Any]], tol: float) -> None:
    if len(timeseries) != len(schedule):
        _add_issue(
            report,
            "format_errors",
            "error",
            "timeseries.csv and actuation_schedule.csv row counts differ",
            timeseries_rows=len(timeseries),
            schedule_rows=len(schedule),
        )
    checks = min(len(timeseries), len(schedule))
    start_matches = 0
    end_matches = 0
    inside = 0
    outside_examples: list[dict[str, Any]] = []
    for index in range(checks):
        sample = _num(timeseries[index].get("physical_time"))
        start = _num(schedule[index].get("t_start", schedule[index].get("physical_time")))
        end = _num(schedule[index].get("t_end"))
        if sample is None or start is None:
            continue
        if abs(sample - start) <= tol:
            start_matches += 1
        if end is not None and abs(sample - end) <= tol:
            end_matches += 1
        if end is not None and start - tol <= sample <= end + tol:
            inside += 1
        elif len(outside_examples) < 5:
            outside_examples.append({"row": index, "sample": sample, "t_start": start, "t_end": end})
    if outside_examples:
        _add_issue(
            report,
            "format_errors",
            "error",
            "STAR sample time falls outside the paired actuation window",
            examples=outside_examples,
        )
    report["summaries"]["time_alignment"] = {
        "paired_rows_checked": checks,
        "sample_matches_t_start_rows": start_matches,
        "sample_matches_t_end_rows": end_matches,
        "sample_inside_window_rows": inside,
    }


def _check_star_csv_alignment(report: dict[str, Any], source_files: list[Path], timeseries: list[dict[str, Any]], tol: float) -> None:
    if not source_files:
        _add_issue(report, "physical_questions_for_haokun", "warning", "No STAR source CSV list found; only standard timeseries alignment was checked")
        return
    reference_times = [_num(row.get("physical_time")) for row in timeseries]
    reference_times = [t for t in reference_times if t is not None]
    checked: list[str] = []
    for path in source_files:
        if not path.exists() or path.name in {"timeseries.csv", "actuation_schedule.csv"}:
            continue
        try:
            source_data = read_star_export_csv(path)
        except (FileNotFoundError, ValueError):
            continue
        rows = source_data["rows"]
        if not rows:
            continue
        times = [_num(row.get("physical_time")) for row in rows]
        times = [t for t in times if t is not None]
        if len(times) != len(reference_times):
            _add_issue(
                report,
                "format_errors",
                "error",
                "STAR source CSV sample count does not match standard timeseries",
                source_file=str(path),
                source_rows=len(times),
                timeseries_rows=len(reference_times),
            )
            continue
        max_delta = max((abs(a - b) for a, b in zip(times, reference_times)), default=0.0)
        if max_delta > tol:
            _add_issue(
                report,
                "format_errors",
                "error",
                "STAR source CSV physical_time does not align with standard timeseries",
                source_file=str(path),
                max_delta=max_delta,
            )
        checked.append(str(path))
    report["summaries"]["star_csv_alignment"] = {"checked_source_csv_count": len(checked), "checked_source_csvs": checked}


def _check_boundary_mapping(report: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    by_id: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        jet_id = _num(row.get("unified_jet_id"))
        if jet_id is None:
            _add_issue(report, "format_errors", "error", "boundary mapping row has non-numeric unified_jet_id", row=row)
            continue
        by_id.setdefault(int(jet_id), []).append(row)
    missing = [idx for idx in range(1, 25) if idx not in by_id]
    duplicates = [idx for idx, matched in by_id.items() if len(matched) > 1]
    if missing:
        _add_issue(report, "name_or_coordinate_errors", "error", "Missing unified jet ids in boundary mapping", missing_ids=missing)
    if duplicates:
        _add_issue(report, "name_or_coordinate_errors", "error", "Duplicate unified jet ids in boundary mapping", duplicate_ids=duplicates)

    boundary_to_ids: dict[str, list[int]] = {}
    for jet_id, matched_rows in by_id.items():
        for row in matched_rows:
            boundary = str(row.get("star_boundary_fully_qualified_candidate") or row.get("star_boundary_candidate") or "").strip()
            if _is_unknown(boundary):
                _add_issue(report, "physical_questions_for_haokun", "warning", "Jet boundary still requires STAR confirmation", unified_jet_id=jet_id)
                continue
            boundary_to_ids.setdefault(boundary, []).append(jet_id)
            if _is_unknown(str(row.get("normal_vector_xyz", ""))) or _is_unknown(str(row.get("area_m2", ""))):
                _add_issue(
                    report,
                    "physical_questions_for_haokun",
                    "warning",
                    "Jet boundary area or normal vector is not confirmed",
                    unified_jet_id=jet_id,
                    boundary=boundary,
                )
    repeated_boundaries = {name: ids for name, ids in boundary_to_ids.items() if len(ids) > 1}
    if repeated_boundaries:
        _add_issue(report, "name_or_coordinate_errors", "error", "Multiple unified jet ids map to the same STAR boundary", repeated_boundaries=repeated_boundaries)


def _check_report_directions(report: dict[str, Any], manifest: dict[str, Any], mapping_rows: list[dict[str, Any]]) -> None:
    expected = _expected_directions_from_manifest(manifest)
    for row in mapping_rows:
        column = str(row.get("unified_column_name", "")).strip()
        if column not in expected:
            continue
        actual = _parse_vector(row.get("direction_vector_xyz"))
        wanted = expected[column]
        if actual is None:
            _add_issue(
                report,
                "physical_questions_for_haokun",
                "warning",
                f"{column} report direction vector is not confirmed; column name cannot prove physical direction",
                unified_column_name=column,
            )
            continue
        if wanted is None:
            _add_issue(
                report,
                "physical_questions_for_haokun",
                "warning",
                f"{column} expected direction is missing from case_manifest; cannot validate STAR report direction",
                unified_column_name=column,
                actual_direction=actual,
            )
            continue
        if not _vectors_close(actual, wanted):
            _add_issue(
                report,
                "name_or_coordinate_errors",
                "error",
                f"{column} STAR report direction does not match case_manifest",
                unified_column_name=column,
                actual_direction=actual,
                manifest_direction=wanted,
            )


def _expected_directions_from_manifest(manifest: dict[str, Any]) -> dict[str, list[float] | None]:
    coordinates = manifest.get("coordinates") if isinstance(manifest.get("coordinates"), dict) else manifest
    return {
        "Fz_Total": _parse_vector(coordinates.get("lift_direction_vector")),
        "Drag_Total": _parse_vector(coordinates.get("drag_direction_vector")),
        "Pitch_Moment": _parse_vector(coordinates.get("pitch_moment_axis")),
        "Roll_Moment": _parse_vector(coordinates.get("roll_moment_axis")),
    }


def _parse_vector(value: Any) -> list[float] | None:
    if isinstance(value, (list, tuple)) and len(value) == 3:
        parsed = [_num(item) for item in value]
        return [float(item) for item in parsed] if all(item is not None for item in parsed) else None
    if value is None or _is_unknown(str(value)):
        return None
    text = str(value).strip().strip("[]()")
    parts = [part.strip() for part in text.replace(";", ",").split(",")]
    if len(parts) != 3:
        parts = text.split()
    if len(parts) != 3:
        return None
    parsed = [_num(part) for part in parts]
    if any(part is None for part in parsed):
        return None
    return [float(part) for part in parsed]


def _vectors_close(a: list[float], b: list[float], tol: float = 1e-9) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def _check_massflow(
    report: dict[str, Any],
    timeseries: list[dict[str, Any]],
    schedule: list[dict[str, Any]],
    has_jet_case: bool,
    zero_tolerance: float,
) -> None:
    first = timeseries[0] if timeseries else {}
    actual_present = [col for col in ACTUAL_MASSFLOW_COLUMNS if col in first]
    cmd_present = [col for col in CMD_MASSFLOW_COLUMNS if col in first]
    jet_present = [col for col in JET_COLUMNS if col in first]

    if has_jet_case and len(actual_present) < 24:
        missing = [col for col in ACTUAL_MASSFLOW_COLUMNS if col not in first]
        _add_issue(
            report,
            "massflow_errors",
            "error",
            "actual_massflow columns are missing; the jet case is incomplete and cmd_massflow was not used as a substitute",
            missing_columns=missing,
        )

    max_abs_errors: dict[str, float] = {}
    mean_abs_errors: dict[str, float] = {}
    off_leak_examples: list[dict[str, Any]] = []
    on_missing_examples: list[dict[str, Any]] = []
    for idx in range(1, 25):
        jet_col = f"JET_{idx:02d}"
        cmd_col = f"cmd_massflow_{idx:02d}"
        actual_col = f"actual_massflow_{idx:02d}"
        abs_errors: list[float] = []
        for row_index, row in enumerate(timeseries):
            jet = _num(row.get(jet_col))
            cmd = _num(row.get(cmd_col))
            actual = _num(row.get(actual_col))
            jet_on = bool(jet is not None and abs(jet) > 0.5)
            if jet_on and actual is None and has_jet_case and len(on_missing_examples) < 5:
                on_missing_examples.append({"row": row_index, "jet": jet_col, "cmd_massflow": cmd})
            if actual is not None:
                if not jet_on and abs(actual) > zero_tolerance and len(off_leak_examples) < 5:
                    off_leak_examples.append({"row": row_index, "jet": jet_col, "actual_massflow": actual})
                if cmd is not None:
                    abs_errors.append(abs(cmd - actual))
        if abs_errors:
            max_abs_errors[actual_col] = max(abs_errors)
            mean_abs_errors[actual_col] = sum(abs_errors) / len(abs_errors)

    if off_leak_examples:
        _add_issue(report, "massflow_errors", "error", "JET is OFF but actual_massflow is not near zero", examples=off_leak_examples, tolerance=zero_tolerance)
    if on_missing_examples:
        _add_issue(report, "massflow_errors", "error", "JET is ON but actual_massflow is missing; cmd_massflow was not substituted", examples=on_missing_examples)

    if not has_jet_case:
        _check_no_jet_massflow_and_reaction(report, timeseries, zero_tolerance)

    report["summaries"]["massflow_tracking"] = {
        "jet_columns_present": len(jet_present),
        "cmd_massflow_columns_present": len(cmd_present),
        "actual_massflow_columns_present": len(actual_present),
        "max_abs_cmd_minus_actual_by_jet": max_abs_errors,
        "mean_abs_cmd_minus_actual_by_jet": mean_abs_errors,
    }


def _check_no_jet_massflow_and_reaction(report: dict[str, Any], rows: list[dict[str, Any]], zero_tolerance: float) -> None:
    examples: list[dict[str, Any]] = []
    for row_index, row in enumerate(rows):
        # Historical Jet_Reaction_Z is a J-surface pressure/shear force, not
        # momentum reaction, so a no-jet case does not require it to be zero.
        for col in (*JET_COLUMNS, *CMD_MASSFLOW_COLUMNS, *ACTUAL_MASSFLOW_COLUMNS):
            if col not in row or row.get(col) in {"", "NA", "N/A", "not_applicable"}:
                continue
            value = _num(row.get(col))
            if value is not None and abs(value) > zero_tolerance and len(examples) < 5:
                examples.append({"row": row_index, "column": col, "value": value})
    if examples:
        _add_issue(report, "massflow_errors", "error", "No-jet case has non-zero jet massflow/switch values", examples=examples, tolerance=zero_tolerance)


def _check_force_accounting(
    report: dict[str, Any],
    rows: list[dict[str, Any]],
    zero_tolerance: float,
    instability_ratio_threshold: float,
) -> None:
    regional_sum: list[float] = []
    vehicle: dict[str, list[float]] = {column: [] for column in VEHICLE_FORCE_COLUMNS}
    jet_reaction: list[float] = []
    mismatch_examples: list[dict[str, Any]] = []
    for row_index, row in enumerate(rows):
        regional_values = [_num(row.get(col)) for col in REGIONAL_FORCE_COLUMNS]
        if all(value is not None for value in regional_values):
            local_sum = sum(float(value) for value in regional_values if value is not None)
            regional_sum.append(local_sum)
            total = _num(row.get("Fz_Total"))
            if total is not None and abs(local_sum - total) > max(zero_tolerance, 0.01 * max(abs(local_sum), 1.0)) and len(mismatch_examples) < 5:
                mismatch_examples.append({"row": row_index, "regional_lift_sum": local_sum, "Fz_Total": total, "difference": total - local_sum})
        for column in VEHICLE_FORCE_COLUMNS:
            value = _num(row.get(column))
            if value is not None:
                vehicle[column].append(value)
        reaction = _num(row.get("Jet_Reaction_Z"))
        if reaction is not None:
            jet_reaction.append(reaction)

    if any(not values for values in (regional_sum, jet_reaction)) or any(not vehicle[col] for col in VEHICLE_FORCE_COLUMNS):
        _add_issue(report, "force_accounting_errors", "error", "Missing one or more force accounting series", present_series=_present_force_series(regional_sum, vehicle, jet_reaction))

    if mismatch_examples:
        _add_issue(
            report,
            "physical_questions_for_haokun",
            "warning",
            "Fz_Total differs from six regional force sum; not treated as an error because they may be different STAR reports",
            examples=mismatch_examples,
        )

    force_summary = {
        "regional_lift_sum": _series_stats(regional_sum),
        "vehicle_aerodynamics": {column: _series_stats(values) for column, values in vehicle.items()},
        "jet_reaction": _series_stats(jet_reaction),
    }
    report["summaries"]["force_accounting"] = force_summary

    for name, stats in _flatten_force_stats(force_summary).items():
        if stats["count"] < 2:
            continue
        typical = max(abs(stats["mean"]), abs(stats["median"]), 1.0)
        if abs(stats["max"] - stats["min"]) / typical > instability_ratio_threshold:
            _add_issue(
                report,
                "numerical_instability_warnings",
                "warning",
                "Force/moment series has a very large dynamic range; inspect for transient startup or solver instability",
                series=name,
                min=stats["min"],
                max=stats["max"],
                typical_scale=typical,
            )


def _series_stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0}
    sorted_values = sorted(values)
    mid = len(sorted_values) // 2
    median = sorted_values[mid] if len(sorted_values) % 2 else 0.5 * (sorted_values[mid - 1] + sorted_values[mid])
    return {
        "count": len(values),
        "min": min(values),
        "max": max(values),
        "mean": sum(values) / len(values),
        "median": median,
        "first": values[0],
        "last": values[-1],
    }


def _flatten_force_stats(force_summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    flattened = {"regional_lift_sum": force_summary["regional_lift_sum"], "jet_reaction": force_summary["jet_reaction"]}
    flattened.update(force_summary["vehicle_aerodynamics"])
    return flattened


def _present_force_series(regional_sum: list[float], vehicle: dict[str, list[float]], jet_reaction: list[float]) -> dict[str, bool]:
    present = {"regional_lift_sum": bool(regional_sum), "jet_reaction": bool(jet_reaction)}
    present.update({column: bool(values) for column, values in vehicle.items()})
    return present


def _has_active_jet(timeseries: list[dict[str, Any]], schedule: list[dict[str, Any]]) -> bool:
    for rows in (timeseries, schedule):
        for row in rows:
            for column in JET_COLUMNS:
                value = _num(row.get(column))
                if value is not None and abs(value) > 0.5:
                    return True
            for column in CMD_MASSFLOW_COLUMNS:
                value = _num(row.get(column))
                if value is not None and abs(value) > 0.0:
                    return True
    return False


def _is_unknown(value: str) -> bool:
    normalized = str(value).strip().lower()
    if not normalized:
        return True
    return any(marker.lower() == normalized or marker.lower() in normalized for marker in UNKNOWN_MARKERS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run B04 physics consistency checks.")
    parser.add_argument("case_dirs", nargs="+", help="standard case directories to check")
    parser.add_argument("--boundary-mapping", default="docs/week3/B02_boundary_mapping.csv")
    parser.add_argument("--report-mapping", default="docs/week3/B02_report_mapping.csv")
    parser.add_argument("--output", default="B04_physics_QC_report.json")
    args = parser.parse_args(argv)

    report = check_cases(
        args.case_dirs,
        boundary_mapping_path=args.boundary_mapping,
        report_mapping_path=args.report_mapping,
    )
    write_report(report, args.output)
    return 0 if report["summary"]["run_success_flag"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
