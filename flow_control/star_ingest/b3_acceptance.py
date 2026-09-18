"""Acceptance gate for the three week4 B3 STAR cases."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import yaml

from flow_control.sampling import actuation_time_value


CASE_ORDER: tuple[tuple[str, int | None], ...] = (
    ("G00_nojet_baseline", None),
    ("G01_J02_pulse", 2),
    ("G02_J06_pulse", 6),
)
REQUIRED_DIRS = ("raw_star", "processed", "figures", "logs")
REQUIRED_FILES = (
    "processed/timeseries.csv",
    "actuation_schedule.csv",
    "case_manifest.yaml",
    "quality_report.json",
)
REGIONAL_FORCE_ALIASES = (
    ("underbody_lift_s1l", "Fz_S1L"),
    ("underbody_lift_s1r", "Fz_S1R"),
    ("underbody_lift_s2l", "Fz_S2L"),
    ("underbody_lift_s2r", "Fz_S2R"),
    ("underbody_lift_s3l", "Fz_S3L"),
    ("underbody_lift_s3r", "Fz_S3R"),
)
VEHICLE_REPORT_ALIASES = (
    ("vehicle_lift", "Fz_Total"),
    ("vehicle_drag", "Drag_Total"),
    ("vehicle_pitch_moment", "Pitch_Moment"),
    ("vehicle_roll_moment", "Roll_Moment"),
)


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.is_file():
        return [], []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def _number(row: dict[str, str], *names: str) -> float | None:
    for name in names:
        value = row.get(name)
        if value not in {None, ""}:
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
    return None


def _switch(row: dict[str, str], jet: int) -> float | None:
    return _number(row, f"J{jet:02d}_switch", f"JET_{jet:02d}")


def _command(row: dict[str, str], jet: int) -> float | None:
    return _number(row, f"J{jet:02d}_cmd_massflow_kg_s", f"cmd_massflow_{jet:02d}")


def _actual(row: dict[str, str], jet: int) -> float | None:
    return _number(row, f"J{jet:02d}_actual_massflow_kg_s", f"actual_massflow_{jet:02d}")


def _active_jets(row: dict[str, str], tolerance: float) -> set[int]:
    active: set[int] = set()
    for jet in range(1, 25):
        switch = _switch(row, jet)
        command = _command(row, jet)
        if (switch is not None and switch > 0.5) or (command is not None and command > tolerance):
            active.add(jet)
    return active


def validate_b3_schedule(
    case_id: str,
    rows: list[dict[str, str]],
    *,
    expected_jet: int | None,
    tolerance: float = 1e-9,
) -> dict[str, Any]:
    """Validate no-jet or one contiguous pulse with pre/post off segments."""

    errors: list[str] = []
    if not rows:
        return {"passed": False, "errors": ["actuation_schedule.csv is empty"], "segments": {}}

    active_by_row = [_active_jets(row, tolerance) for row in rows]
    if expected_jet is None:
        bad = [index for index, active in enumerate(active_by_row) if active]
        if bad:
            errors.append(f"{case_id} must be no-jet, but active commands occur at rows {bad[:5]}")
        return {
            "passed": not errors,
            "errors": errors,
            "segments": {"baseline_rows": len(rows), "pulse_rows": 0, "recovery_rows": 0},
        }

    wrong = [
        {"row": index, "active_jets": sorted(active)}
        for index, active in enumerate(active_by_row)
        if active and active != {expected_jet}
    ]
    if wrong:
        errors.append(f"only J{expected_jet:02d} may be active; examples: {wrong[:5]}")

    on_rows = [index for index, active in enumerate(active_by_row) if expected_jet in active]
    if not on_rows:
        errors.append(f"J{expected_jet:02d} pulse is missing")
        first_on = last_on = -1
    else:
        first_on, last_on = on_rows[0], on_rows[-1]
        if first_on == 0:
            errors.append("pulse has no pre-jet baseline segment")
        if last_on == len(rows) - 1:
            errors.append("pulse has no post-jet recovery segment")
        if on_rows != list(range(first_on, last_on + 1)):
            errors.append("pulse must be one contiguous active segment")

    return {
        "passed": not errors,
        "errors": errors,
        "segments": {
            "baseline_rows": max(first_on, 0),
            "pulse_rows": len(on_rows),
            "recovery_rows": max(len(rows) - last_on - 1, 0) if on_rows else 0,
        },
    }


def _manifest_errors(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not manifest.get("git_commit") and not manifest.get("provenance", {}).get("git_commit"):
        errors.append("case_manifest.yaml must record git_commit")
    star = manifest.get("star") if isinstance(manifest.get("star"), dict) else {}
    sim_id = star.get("sim_file_identifier") or star.get("sim_file")
    sim_hash = star.get("sim_file_hash_sha256")
    if not sim_id or str(sim_id).lower() in {"unknown", "unconfirmed", "待浩坤确认"}:
        errors.append("manifest must record the external checkpoint/template identifier")
    if not sim_hash or str(sim_hash).lower() in {"unknown", "unconfirmed", "待浩坤确认"}:
        errors.append("manifest must record the external checkpoint/template SHA-256")
    mesh = star.get("mesh_version") or manifest.get("mesh_version")
    if not mesh or str(mesh).lower() in {"unknown", "unconfirmed", "待浩坤确认"}:
        errors.append("manifest must record mesh_version")
    if not (manifest.get("time_step") or manifest.get("solver_time", {}).get("time_step_s")):
        errors.append("manifest must record solver time_step")
    solver_time = manifest.get("solver_time") if isinstance(manifest.get("solver_time"), dict) else {}
    if not (solver_time.get("inner_iterations_per_step") or manifest.get("inner_iterations_per_step")):
        errors.append("manifest must record inner_iterations_per_step")
    if not (solver_time.get("report_sampling_interval_s") or manifest.get("report_sampling_interval_s")):
        errors.append("manifest must record report_sampling_interval_s")
    solver_settings = manifest.get("solver_settings")
    if not isinstance(solver_settings, dict) or not solver_settings:
        errors.append("manifest must record non-empty solver_settings")
    return errors


def validate_b3_case(case_dir: str | Path, *, expected_jet: int | None) -> dict[str, Any]:
    case_path = Path(case_dir)
    errors: list[str] = []
    for directory in REQUIRED_DIRS:
        if not (case_path / directory).is_dir():
            errors.append(f"missing required directory: {directory}/")
    for relative in REQUIRED_FILES:
        if not (case_path / relative).is_file():
            errors.append(f"missing required file: {relative}")

    manifest: dict[str, Any] = {}
    manifest_path = case_path / "case_manifest.yaml"
    if manifest_path.is_file():
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        errors.extend(_manifest_errors(manifest))

    schedule_columns, schedule = _read_csv(case_path / "actuation_schedule.csv")
    schedule_result = validate_b3_schedule(
        case_path.name,
        schedule,
        expected_jet=expected_jet,
    )
    errors.extend(schedule_result["errors"])

    timeseries_columns, timeseries = _read_csv(case_path / "processed" / "timeseries.csv")
    if timeseries and len(timeseries) != len(schedule):
        errors.append(
            f"timeseries/schedule row count mismatch: {len(timeseries)} != {len(schedule)}"
        )
    for aliases in REGIONAL_FORCE_ALIASES:
        if not any(column in timeseries_columns for column in aliases):
            errors.append(f"missing regional force column: {' or '.join(aliases)}")
    for aliases in VEHICLE_REPORT_ALIASES:
        if not any(column in timeseries_columns for column in aliases):
            errors.append(f"missing vehicle report column: {' or '.join(aliases)}")
    if expected_jet is not None:
        missing_actual = [
            jet for jet in range(1, 25)
            if not any(
                name in timeseries_columns
                for name in (f"J{jet:02d}_actual_massflow_kg_s", f"actual_massflow_{jet:02d}")
            )
        ]
        if missing_actual:
            errors.append(f"missing actual massflow columns for jets: {missing_actual}")
        elif timeseries:
            tracking_errors: list[dict[str, Any]] = []
            for index, (schedule_row, result_row) in enumerate(zip(schedule, timeseries)):
                expected_on = expected_jet in _active_jets(schedule_row, 1e-9)
                actual = _actual(result_row, expected_jet)
                if actual is None or (expected_on and actual <= 0.0) or (not expected_on and abs(actual) > 1e-9):
                    tracking_errors.append(
                        {"row": index, "expected_on": expected_on, "actual_massflow": actual}
                    )
                    if len(tracking_errors) >= 5:
                        break
                for other_jet in range(1, 25):
                    if other_jet == expected_jet:
                        continue
                    other_actual = _actual(result_row, other_jet)
                    if other_actual is not None and abs(other_actual) > 1e-9:
                        tracking_errors.append(
                            {"row": index, "unexpected_jet": other_jet, "actual_massflow": other_actual}
                        )
                        break
            if tracking_errors:
                errors.append(f"actual massflow does not follow the pulse schedule: {tracking_errors}")

    quality_path = case_path / "quality_report.json"
    if quality_path.is_file():
        try:
            quality = json.loads(quality_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            errors.append("quality_report.json is not valid JSON")
        else:
            if quality.get("run_success_flag") is not True:
                errors.append("quality_report.json has not passed")
            b04 = quality.get("B04_real_quality")
            if not isinstance(b04, dict):
                errors.append("quality_report.json is missing B04_real_quality")
            elif b04.get("summary", {}).get("run_success_flag") is not True:
                errors.append("B04_real_quality has not passed")

    return {
        "case_id": case_path.name,
        "passed": not errors,
        "errors": errors,
        "segments": schedule_result["segments"],
        "schedule_columns": schedule_columns,
    }


def validate_b3_case_set(root: str | Path = "runs/real_star") -> dict[str, Any]:
    """Validate all three cases and enforce the G00 -> G01 -> G02 gate."""

    root_path = Path(root)
    cases: list[dict[str, Any]] = []
    for case_id, expected_jet in CASE_ORDER:
        result = validate_b3_case(root_path / case_id, expected_jet=expected_jet)
        cases.append(result)

    manifests: list[dict[str, Any]] = []
    for case_id, _ in CASE_ORDER:
        path = root_path / case_id / "case_manifest.yaml"
        manifests.append(
            (yaml.safe_load(path.read_text(encoding="utf-8")) or {})
            if path.is_file()
            else {}
        )

    def settings(manifest: dict[str, Any]) -> dict[str, Any]:
        star = manifest.get("star") if isinstance(manifest.get("star"), dict) else {}
        solver = manifest.get("solver_time") if isinstance(manifest.get("solver_time"), dict) else {}
        return {
            "checkpoint": star.get("sim_file_hash_sha256"),
            "mesh_version": star.get("mesh_version") or manifest.get("mesh_version"),
            "time_step_s": solver.get("time_step_s") or manifest.get("time_step"),
            "inner_iterations_per_step": solver.get("inner_iterations_per_step") or manifest.get("inner_iterations_per_step"),
            "report_sampling_interval_s": solver.get("report_sampling_interval_s") or manifest.get("report_sampling_interval_s"),
            "solver_settings": manifest.get("solver_settings"),
        }

    reference_settings = settings(manifests[0])
    for index in range(1, len(cases)):
        current_settings = settings(manifests[index])
        mismatches = {
            key: {"G00": reference_settings[key], cases[index]["case_id"]: current_settings[key]}
            for key in reference_settings
            if reference_settings[key] != current_settings[key]
        }
        if mismatches:
            cases[index]["errors"].append(
                f"solver/checkpoint settings differ from G00: {mismatches}"
            )
            cases[index]["passed"] = False

    def pulse_signature(case_id: str, jet: int) -> list[tuple[str, str, float]]:
        _, rows = _read_csv(root_path / case_id / "actuation_schedule.csv")
        return [
            (
                row.get("t_start", actuation_time_value(row, "")),
                row.get("t_end", ""),
                _command(row, jet) or 0.0,
            )
            for row in rows
        ]

    if pulse_signature("G01_J02_pulse", 2) != pulse_signature("G02_J06_pulse", 6):
        cases[2]["errors"].append("J02 and J06 pulse timing/massflow signatures differ")
        cases[2]["passed"] = False

    previous_passed = True
    for case in cases:
        if not previous_passed:
            case["errors"].insert(0, f"blocked: previous B3 case did not pass before {case['case_id']}")
            case["passed"] = False
        previous_passed = case["passed"]
    return {
        "schema_version": "B3_three_standard_cases_v1",
        "root": str(root_path),
        "passed": all(case["passed"] for case in cases),
        "cases": cases,
    }


def write_b3_acceptance_report(
    root: str | Path = "runs/real_star",
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    report = validate_b3_case_set(root)
    target = Path(output_path) if output_path is not None else Path(root) / "B3_acceptance_report.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report
